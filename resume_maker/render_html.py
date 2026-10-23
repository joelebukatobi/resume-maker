"""HTML renderer, used for on-screen preview and as the source for the PDF.

Mirrors :mod:`resume_maker.render_docx` measurement for measurement, reading
the same :class:`~resume_maker.theme.Theme`. Where Word uses a right-aligned
tab stop, CSS uses ``justify-content: space-between`` on a flex row: both pin
the left text to the left margin and the right text to the right margin, and
neither depends on font metrics.

Every value interpolated into the page is either a number derived from the
theme or a string passed through :func:`html.escape`.
"""

from __future__ import annotations

from html import escape
from urllib.parse import urlencode
from typing import Iterable

from .model import (
    header_items,
    CV,
    ContactItem,
    CoverLetter,
    Entry,
    EntrySection,
    LabeledSection,
    ParagraphSection,
    Section,
    Budget,
    shown,
)
from .theme import RULE_COLOR, RULE_GAP_PT, RULE_WEIGHT_PT, Theme

# Reports any two-column line whose halves do not fit on one line, so an
# overflow is caught before export rather than discovered in the PDF.
# Preview only: white sheet beyond the paper edge; the edge itself is dashed.
_SHEET_BLEED_IN = 0.25

# Preview only. Paginates like Word's print layout, then scales the stack of
# sheets to the pane.
#
# Pagination: the page content is poured into fixed-height CSS columns, one
# text area tall and one text area wide. Column breaks come from the same
# fragmentation engine Chrome uses to paginate the PDF, so page N of the
# preview holds the same lines as page N of the export. Each extra sheet is a
# copy of the first, slid sideways so its column sits in the text area.
#
# Scaling: a CSS transform, not zoom, so layout (and therefore every line
# break) stays exactly as printed.
_FIT_SCRIPT = """
(function () {
  var first = document.querySelector('.page');
  if (!first) { return; }
  var stage = document.createElement('div');
  stage.id = 'stage';
  var sheets = document.createElement('div');
  sheets.id = 'sheets';
  first.parentNode.insertBefore(stage, first);
  stage.appendChild(sheets);
  sheets.appendChild(first);
  var flow = document.createElement('div');
  flow.className = 'flow';
  while (first.firstChild) { flow.appendChild(first.firstChild); }
  first.appendChild(flow);

  var zoom = 1;
  var PAD = 24;
  var pageCount = 1;

  function paginate() {
    while (sheets.children.length > 1) { sheets.removeChild(sheets.lastChild); }
    var cs = getComputedStyle(first);
    var textW = first.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    var textH = first.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
    // Wider than any margin, so a neighbouring column never shows on a sheet.
    var gap = first.offsetWidth;
    flow.style.cssText = 'width:' + textW + 'px;height:' + textH + 'px;' +
      'column-width:' + textW + 'px;column-gap:' + gap + 'px;column-fill:auto;';
    var step = textW + gap;
    pageCount = Math.max(1, Math.round((flow.scrollWidth + gap) / step));
    for (var k = 1; k < pageCount; k++) {
      var sheet = first.cloneNode(true);
      sheet.querySelector('.flow').style.transform = 'translateX(' + (-k * step) + 'px)';
      sheets.appendChild(sheet);
    }
    if (window.parent !== window) {
      window.parent.postMessage({ type: 'resume-pages', count: pageCount }, '*');
    }
  }

  function layout() {
    sheets.style.transform = 'none';
    var vw = document.documentElement.clientWidth;
    var vh = document.documentElement.clientHeight;
    var pw = first.offsetWidth;
    var ph = first.offsetHeight;
    var fit = Math.min((vw - PAD * 2) / pw, (vh - PAD * 2) / ph);
    if (!isFinite(fit) || fit <= 0) { fit = 1; }
    var scale = fit * zoom;
    // Same on-screen gap between sheets at every scale.
    var gapPx = PAD / scale;
    sheets.style.display = 'flex';
    sheets.style.flexDirection = 'column';
    sheets.style.gap = gapPx + 'px';
    sheets.style.width = pw + 'px';
    sheets.style.transformOrigin = '0 0';
    sheets.style.transform = 'scale(' + scale + ')';
    var scaledW = pw * scale;
    var scaledH = (pageCount * ph + (pageCount - 1) * gapPx) * scale;
    stage.style.width = scaledW + 'px';
    stage.style.height = scaledH + 'px';
    stage.style.overflow = 'hidden';
    stage.style.margin = '0 auto';
    document.body.style.boxSizing = 'border-box';
    var scrolls = zoom > 1 || scaledH + PAD * 2 > vh + 1;
    if (!scrolls) {
      document.documentElement.style.overflow = 'hidden';
      document.body.style.display = 'flex';
      document.body.style.flexDirection = 'column';
      document.body.style.justifyContent = 'center';
      document.body.style.alignItems = 'center';
      document.body.style.overflow = 'hidden';
      document.body.style.padding = '0';
      document.body.style.height = vh + 'px';
    } else {
      document.body.style.display = 'block';
      document.body.style.justifyContent = '';
      document.body.style.alignItems = '';
      document.documentElement.style.overflow = 'auto';
      document.body.style.overflow = 'auto';
      document.body.style.padding = PAD + 'px 0';
      document.body.style.height = (scaledH + PAD * 2) + 'px';
    }
    // The editor lines its toast up with the top of the sheet.
    if (window.parent !== window) {
      var box = stage.getBoundingClientRect();
      window.parent.postMessage({ type: 'resume-sheet', top: box.top, right: box.right }, '*');
    }
  }

  function refresh() { paginate(); layout(); }
  window.addEventListener('resize', layout);
  window.addEventListener('message', function (event) {
    if (!event.data || event.data.type !== 'resume-zoom') { return; }
    var next = Number(event.data.zoom);
    if (!isFinite(next)) { return; }
    zoom = Math.min(3, Math.max(1, next));
    layout();
  });
  refresh();
  // Web fonts change line widths, and so page breaks: paginate again.
  if (document.fonts) { document.fonts.ready.then(refresh); }
})();
"""

# How many lines a bullet renders to. A range over the text reports one
# rectangle per line fragment, so counting distinct tops counts lines at the
# real font and column width. The list marker sits in ::before and has no text
# node of its own, so it is not counted.
_LINE_COUNT = """
function lineCount(item) {
  var range = document.createRange();
  range.selectNodeContents(item);
  var tops = {}, n = 0;
  var rects = range.getClientRects();
  for (var i = 0; i < rects.length; i += 1) {
    if (rects[i].width === 0 && rects[i].height === 0) { continue; }
    var top = Math.round(rects[i].top * 4) / 4;
    if (!tops[top]) { tops[top] = true; n += 1; }
  }
  return n || 1;
}
"""

_MEASURE_SCRIPT = """
(function () {
%s
function measure() {
  var offenders = [];
  var over = [];
  var scope = document.querySelector('.page') || document;
  scope.querySelectorAll('.row').forEach(function (row) {
    if (row.scrollWidth > row.clientWidth + 1) {
      row.classList.add('overflow');
      offenders.push(row.getAttribute('data-label') || '');
    }
  });
  scope.querySelectorAll('.entry').forEach(function (entry) {
    var label = entry.getAttribute('data-label') || '';
    entry.querySelectorAll('li').forEach(function (item, index) {
      var allowed = Number(item.getAttribute('data-allowed'));
      if (!isFinite(allowed) || allowed <= 0) { return; }
      var lines = lineCount(item);
      if (lines > allowed) {
        item.classList.add('over-budget');
        over.push({ label: label, bullet: index + 1, lines: lines, allowed: allowed });
      }
    });
  });
  var payload = { offenders: offenders, over: over };
  var report = document.getElementById('overflow-report');
  if (report) { report.textContent = JSON.stringify(offenders); }
  document.title = 'OVERFLOW:' + JSON.stringify(payload);
  // The preview runs in a sandboxed iframe with no same-origin access, so
  // the measurement is posted out rather than read back through the DOM.
  if (window.parent !== window) {
    window.parent.postMessage(
      { type: 'resume-overflow', offenders: offenders, over: over }, '*');
  }
}
measure();
if (document.fonts) { document.fonts.ready.then(measure); }
})();
""" % _LINE_COUNT


class HtmlRenderer:
    """Turns a :class:`CV` into a standalone HTML page."""

    def __init__(self, theme: Theme) -> None:
        self._theme = theme

    def render(
        self,
        cv: CV,
        *,
        preview: bool = False,
        letter: CoverLetter | None = None,
    ) -> str:
        if letter is None:
            body = "".join(
                [
                    f'<h1 class="name">{escape(cv.name)}</h1>',
                    self._contact(header_items(cv.contact), rule=True),
                    *(self._section(section) for section in cv.sections),
                ]
            )
        else:
            body = "".join(
                [
                    f'<h1 class="name">{escape(letter.name)}</h1>',
                    *(self._contact([item]) for item in header_items(letter.contact)),
                    *self._letter(letter),
                ]
            )
        frame = "preview" if preview else "print"
        page = "page letter" if letter else "page"
        return (
            "<!DOCTYPE html>"
            '<html lang="en"><head><meta charset="utf-8">'
            f"<title>{escape((letter.name if letter else cv.name))}</title>"
            f"<style>{self._css(preview=preview)}</style></head>"
            f'<body class="{frame}"><main class="{page}">{body}</main>'
            '<pre id="overflow-report" hidden></pre>'
            f"<script>{_MEASURE_SCRIPT}"
            + (_FIT_SCRIPT if preview else "")
            + "</script></body></html>"
        )

    # -- sections -------------------------------------------------------

    @staticmethod
    def _contact_item(item: ContactItem) -> str:
        text = escape(item.text)
        if item.href:
            return f'<a href="{escape(item.href, quote=True)}">{text}</a>'
        return text

    def _contact(self, items: Iterable[ContactItem], *, rule: bool = False) -> str:
        css = "contact rule" if rule else "contact"
        return f'<p class="{css}">' + " | ".join(map(self._contact_item, items)) + "</p>"

    def _letter(self, letter: CoverLetter) -> list[str]:
        parts = [
            f'<p class="letter-line greeting">{escape(letter.fill(letter.greeting))}</p>',
            *(
                f'<p class="letter-line">{escape(letter.fill(paragraph))}</p>'
                for paragraph in letter.paragraphs
                if paragraph
            ),
            f'<p class="letter-line">{escape(letter.fill(letter.closing))}</p>',
            f'<p class="letter-line signoff">{escape(letter.fill(letter.signoff))}</p>',
        ]
        name, details = letter.signature()
        parts.append(f'<p class="letter-line signature"><strong>{escape(name)}</strong></p>')
        parts.extend(
            f'<p class="letter-line signature">{self._contact_item(item)}</p>' for item in details
        )
        return parts

    def _section(self, section: Section) -> str:
        heading = f'<h2 class="heading">{escape(section.heading)}</h2>'
        return heading + self._section_body(section)

    def _section_body(self, section: Section) -> str:
        if isinstance(section, ParagraphSection):
            return f'<p class="summary">{escape(section.text)}</p>'
        if isinstance(section, EntrySection):
            return "".join(self._entry(entry) for entry in shown(section.entries))
        if isinstance(section, LabeledSection):
            return "".join(
                f'<p class="labeled"><strong>{escape(item.label)}:</strong> '
                f"{escape(item.text)}</p>"
                for item in section.items
            )
        raise TypeError(f"unsupported section type: {type(section).__name__}")

    def _entry(self, entry: Entry) -> str:
        parts = [self._row(entry.left, entry.right)]
        if entry.sub_left or entry.sub_right:
            parts.append(
                self._row(
                    entry.sub_left or "",
                    entry.sub_right or "",
                    italic_left=entry.sub_italic,
                )
            )
        if entry.note:
            parts.append(f'<p class="note">{escape(entry.note)}</p>')
        if entry.bullets:
            parts.append(self._bullets(entry.bullets, entry.budget))
        label = escape(entry.left, quote=True)
        return f'<section class="entry" data-label="{label}">{"".join(parts)}</section>'

    def _row(self, left: str, right: str, *, italic_left: bool = False) -> str:
        left_class = "left italic" if italic_left else "left"
        right_html = f'<span class="right">{escape(right)}</span>' if right else ""
        return (
            f'<div class="row" data-label="{escape(left, quote=True)}">'
            f'<span class="{left_class}">{escape(left)}</span>{right_html}</div>'
        )

    def _bullets(self, bullets: Iterable[str], budget: Budget | None = None) -> str:
        budget = budget or Budget()
        items = "".join(
            f'<li data-allowed="{budget.allowance(n)}">{escape(text)}</li>'
            for n, text in enumerate(bullets)
        )
        return f"<ul>{items}</ul>"

    # -- styling --------------------------------------------------------

    def _css(self, *, preview: bool) -> str:
        theme = self._theme
        underline = "underline" if theme.link_underline else "none"
        # Word lineRule=auto / 240 = "single", which is ~1.15× the font size.
        # CSS `line-height: 1` is tighter and leaves a blank band at the
        # bottom of a letter sheet. Scale so the preview/PDF fill like DOCX.
        css_line_height = theme.line_height * 1.15
        css = f"""
*{{box-sizing:border-box;}}
@page{{size:{theme.page_width_in}in {theme.page_height_in}in;margin:{theme.margin_in}in;}}
html,body{{margin:0;padding:0;background:#fff;color:#000;}}
body{{font-family:{theme.font_stack};
  font-size:{theme.size_body_pt}pt;line-height:{css_line_height};
  -webkit-font-smoothing:antialiased;}}
.page{{width:{theme.text_width_in}in;}}
h1,h2,p,ul{{margin:0;padding:0;font-size:inherit;font-weight:normal;}}
.name{{font-size:{theme.size_name_pt}pt;font-weight:bold;text-align:center;
  letter-spacing:normal;font-kerning:normal;text-rendering:geometricPrecision;}}
.contact{{font-size:{theme.size_contact_pt}pt;font-weight:bold;text-align:center;}}
.letter .name,.letter .contact{{text-align:right;}}
.contact.rule{{border-bottom:{RULE_WEIGHT_PT}pt solid #{RULE_COLOR};padding-bottom:{RULE_GAP_PT}pt;}}
.contact a{{color:#{theme.link_color};text-decoration:{underline};}}
.heading{{font-size:{theme.size_heading_pt}pt;font-weight:bold;text-transform:uppercase;
  margin-top:{theme.space_before_heading_pt}pt;margin-bottom:{theme.space_after_heading_pt}pt;}}
.heading,.entry > .row{{break-after:avoid;}}
.row,li{{break-inside:avoid;}}
.summary{{text-align:justify;}}
.letter-line{{text-align:justify;margin-top:10pt;}}
.letter-line.greeting{{margin-top:16pt;text-align:left;}}
.letter-line.signoff,.letter-line.signature{{text-align:left;}}
.letter-line.signoff{{margin-top:16pt;}}
.letter-line.signature{{margin-top:0;}}
.labeled{{text-align:left;}}
.entry + .entry{{margin-top:{theme.space_between_entries_pt}pt;}}
.row{{display:flex;justify-content:space-between;align-items:baseline;
  width:100%;overflow:hidden;}}
.row .left,.row .right{{font-weight:bold;white-space:nowrap;}}
.row .left.italic{{font-style:italic;}}
.row .right{{padding-left:0.15in;}}
ul{{list-style:none;}}
li{{padding-left:{theme.bullet_indent_in}in;}}
li::before{{content:"{escape(theme.bullet_char)}";display:inline-block;
  width:{theme.bullet_hang_in}in;margin-left:-{theme.bullet_hang_in}in;}}
.entry section,.note{{text-align:left;}}
"""
        if preview:
            css += f"""
body.preview{{background:#e9e9ec;margin:0;padding:0;overflow:hidden;height:100%;
  display:flex;flex-direction:column;justify-content:center;align-items:center;}}
html{{height:100%;overflow:hidden;}}
body.preview .page{{width:{theme.page_width_in + 2 * _SHEET_BLEED_IN}in;
  height:{theme.page_height_in + 2 * _SHEET_BLEED_IN}in;
  border:{_SHEET_BLEED_IN}in solid #fff;
  padding:{theme.margin_in}in;margin:0 auto;background:#fff;overflow:hidden;
  box-shadow:0 2px 12px rgba(0,0,0,.25);}}
.row.overflow{{outline:1.5px solid #c1121f;outline-offset:2px;}}
body.preview .page{{position:relative;text-rendering:geometricPrecision;}}
{self._preview_faces_css()}
body.preview .page::before{{{self._margin_marks_css()}}}
body.preview .page::after{{content:"";position:absolute;inset:0;pointer-events:none;
  border:1px dashed #b4b4b4;}}
"""
        return css

    def _preview_faces_css(self) -> str:
        """Point the theme font at bitmap-free copies served by the editor.

        See :mod:`resume_maker.fonts` for why. Each face falls back to the
        installed font if the server cannot supply one.
        """
        font = self._theme.font
        name = font.replace("'", "\\'")  # theme allows apostrophes
        rules = []
        for weight, style, suffix in (
            ("normal", "normal", ""), ("bold", "normal", " Bold"),
            ("normal", "italic", " Italic"), ("bold", "italic", " Bold Italic"),
        ):
            query = urlencode({"family": font, "bold": int(weight == "bold"),
                               "italic": int(style == "italic")})
            rules.append(
                f"@font-face{{font-family:'{name}';font-weight:{weight};font-style:{style};"
                f"src:url('/api/font-face?{query}') format('truetype'),local('{name}{suffix}');}}"
            )
        return "\n".join(rules)

    def _margin_marks_css(self) -> str:
        """LibreOffice-style text-boundary corners, preview only.

        An L at each corner of the text area, arms reaching out into the
        margin. Drawn as eight 1px gradients on one overlay so nothing is
        added to the page content, the PDF or the DOCX.
        """
        margin = self._theme.margin_in
        arm = min(0.15, margin)  # ponytail: arm clamps to margin; tiny margins get tiny marks
        line = "linear-gradient(#b4b4b4,#b4b4b4)"
        segments = [
            (f"{arm}in 1px", f"left 0 top {arm}in"),
            (f"1px {arm}in", f"left {arm}in top 0"),
            (f"{arm}in 1px", f"right 0 top {arm}in"),
            (f"1px {arm}in", f"right {arm}in top 0"),
            (f"{arm}in 1px", f"left 0 bottom {arm}in"),
            (f"1px {arm}in", f"left {arm}in bottom 0"),
            (f"{arm}in 1px", f"right 0 bottom {arm}in"),
            (f"1px {arm}in", f"right {arm}in bottom 0"),
        ]
        return (
            'content:"";position:absolute;pointer-events:none;'
            f"inset:{margin - arm}in;"
            f"background-image:{','.join([line] * len(segments))};"
            f"background-size:{','.join(size for size, _ in segments)};"
            f"background-position:{','.join(pos for _, pos in segments)};"
            "background-repeat:no-repeat;"
        )

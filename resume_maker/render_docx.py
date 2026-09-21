"""DOCX renderer.

Emits Office Open XML directly. The two things this package exists to get
right -- right-aligned tab stops at the right margin, and a font that can be
swapped without moving anything -- are both handled here:

* every two-column line is one paragraph containing ``left <tab> right`` with a
  single right-aligned tab stop at ``theme.right_tab_twips``;
* the font is declared once, in ``docDefaults``, and inherited everywhere.

All text is XML-escaped on the way in.
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from xml.sax.saxutils import escape, quoteattr

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
)
from .theme import RULE_COLOR, RULE_GAP_PT, RULE_WEIGHT_PT, Theme

_FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_HYPERLINK_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
)


class DocxRenderer:
    """Turns a :class:`CV` into a ``.docx`` byte string."""

    def __init__(self, theme: Theme) -> None:
        self._theme = theme

    # -- public ---------------------------------------------------------

    def render(self, cv: CV, letter: CoverLetter | None = None) -> bytes:
        links: list[str] = []
        body = self._body(cv, links) if letter is None else self._letter_body(cv, letter, links)
        document = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f"<w:document {_NS}><w:body>{body}{self._sect_pr()}</w:body></w:document>"
        )

        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, payload in (
                ("[Content_Types].xml", _CONTENT_TYPES),
                ("_rels/.rels", _ROOT_RELS),
                ("word/_rels/document.xml.rels", self._document_rels(links)),
                ("word/document.xml", document),
                ("word/styles.xml", self._styles()),
                ("word/numbering.xml", self._numbering()),
            ):
                info = zipfile.ZipInfo(name, date_time=_FIXED_TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, payload.encode("utf-8"))
        return buffer.getvalue()

    # -- document body --------------------------------------------------

    def _body(self, cv: CV, links: list[str]) -> str:
        theme = self._theme
        parts = [
            self._paragraph(
                self._run(cv.name, bold=True, size_pt=theme.size_name_pt),
                align="right",
                keep_next=True,
            ),
            self._contact_paragraph(header_items(cv.contact), links, rule=True),
        ]
        for section in cv.sections:
            parts.append(self._heading(section.heading))
            parts.append(self._section_body(section))
        return "".join(parts)

    def _letter_body(self, cv: CV, letter: CoverLetter, links: list[str]) -> str:
        theme = self._theme
        gap = theme.space_between_entries_pt
        parts = [
            self._paragraph(
                self._run(letter.name, bold=True, size_pt=theme.size_name_pt),
                align="right",
                keep_next=True,
            ),
            *(
                self._contact_paragraph((item,), links)
                for item in header_items(letter.contact)
            ),
            self._paragraph(
                self._run(letter.fill(letter.greeting)),
                space_before=16,
            ),
        ]
        for paragraph in letter.paragraphs:
            if paragraph:
                parts.append(
                    self._paragraph(
                        self._run(letter.fill(paragraph)),
                        align="both",
                        space_before=gap,
                    )
                )
        parts.append(
            self._paragraph(
                self._run(letter.fill(letter.closing)),
                align="both",
                space_before=gap,
            )
        )
        parts.append(
            self._paragraph(self._run(letter.fill(letter.signoff)), space_before=16)
        )
        name, details = letter.signature()
        parts.append(self._paragraph(self._run(name, bold=True)))
        parts.extend(
            self._paragraph(self._contact_run(item, links, bold=False, size_pt=None))
            for item in details
        )
        return "".join(parts)

    def _section_body(self, section: Section) -> str:
        if isinstance(section, ParagraphSection):
            return self._paragraph(self._run(section.text), align="both")
        if isinstance(section, EntrySection):
            return "".join(
                self._entry(entry, first=(index == 0))
                for index, entry in enumerate(section.entries)
            )
        if isinstance(section, LabeledSection):
            return "".join(
                self._paragraph(
                    self._run(f"{item.label}:", bold=True) + self._run(f" {item.text}")
                )
                for item in section.items
            )
        raise TypeError(f"unsupported section type: {type(section).__name__}")

    def _entry(self, entry: Entry, *, first: bool) -> str:
        theme = self._theme
        gap = 0 if first else theme.space_between_entries_pt
        parts = [
            self._column_paragraph(
                entry.left, entry.right, space_before=gap, keep_next=True
            )
        ]
        if entry.sub_left or entry.sub_right:
            parts.append(
                self._column_paragraph(
                    entry.sub_left or "",
                    entry.sub_right or "",
                    italic_left=entry.sub_italic,
                    keep_next=True,
                )
            )
        if entry.note:
            parts.append(self._paragraph(self._run(entry.note)))
        parts.extend(self._bullet(text) for text in entry.bullets)
        return "".join(parts)

    def _heading(self, text: str) -> str:
        theme = self._theme
        return self._paragraph(
            self._run(text, bold=True, size_pt=theme.size_heading_pt),
            space_before=theme.space_before_heading_pt,
            space_after=theme.space_after_heading_pt,
            keep_next=True,
        )

    def _contact_paragraph(
        self, contact: tuple[ContactItem, ...], links: list[str], *, rule: bool = False
    ) -> str:
        theme = self._theme
        size = theme.size_contact_pt
        runs: list[str] = []
        for index, item in enumerate(contact):
            if index:
                runs.append(self._run(" | ", bold=True, size_pt=size))
            runs.append(self._contact_run(item, links, bold=True, size_pt=size))
        return self._paragraph("".join(runs), align="right", border_bottom=rule)

    def _contact_run(
        self, item: ContactItem, links: list[str], *, bold: bool, size_pt: float | None
    ) -> str:
        if not item.href:
            return self._run(item.text, bold=bold, size_pt=size_pt)
        links.append(item.href)
        inner = self._run(item.text, bold=bold, size_pt=size_pt, hyperlink=True)
        return f'<w:hyperlink r:id="rIdL{len(links)}">{inner}</w:hyperlink>'


    # -- two-column line ------------------------------------------------

    def _column_paragraph(
        self,
        left: str,
        right: str,
        *,
        italic_left: bool = False,
        space_before: float = 0,
        keep_next: bool = False,
    ) -> str:
        runs = self._run(left, bold=True, italic=italic_left)
        if right:
            runs += "<w:r><w:tab/></w:r>" + self._run(right, bold=True)
        return self._paragraph(
            runs, tab_stop=True, space_before=space_before, keep_next=keep_next
        )

    def _bullet(self, text: str) -> str:
        return self._paragraph(self._run(text), num_id=1)

    # -- primitives -----------------------------------------------------

    def _paragraph(
        self,
        runs: str,
        *,
        align: str | None = None,
        space_before: float = 0,
        space_after: float = 0,
        tab_stop: bool = False,
        num_id: int | None = None,
        keep_next: bool = False,
        border_bottom: bool = False,
    ) -> str:
        theme = self._theme
        # Element order below follows the CT_PPr schema sequence; Word rejects
        # the file if these are reordered.
        props = ['<w:pStyle w:val="ListParagraph"/>' if num_id else '<w:pStyle w:val="Normal"/>']
        if keep_next:
            props.append("<w:keepNext/>")
        if num_id is not None:
            props.append(
                f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{num_id}"/></w:numPr>'
            )
        if border_bottom:
            # w:sz is in eighths of a point; w:space is the gap in points.
            props.append(
                '<w:pBdr><w:bottom w:val="single" '
                f'w:sz="{round(RULE_WEIGHT_PT * 8)}" w:space="{RULE_GAP_PT}" w:color="{RULE_COLOR}"/></w:pBdr>'
            )
        if tab_stop:
            props.append(
                f'<w:tabs><w:tab w:val="right" w:pos="{theme.right_tab_twips}"/></w:tabs>'
            )
        props.append(
            '<w:spacing w:before="{before}" w:after="{after}" '
            'w:line="{line}" w:lineRule="auto"/>'.format(
                before=theme.twips(space_before),
                after=theme.twips(space_after),
                line=round(240 * theme.line_height),
            )
        )
        if num_id is not None:
            props.append(
                f'<w:ind w:left="{theme.bullet_indent_twips}" '
                f'w:hanging="{theme.bullet_hang_twips}"/>'
            )
            props.append("<w:contextualSpacing/>")
        if align:
            props.append(f'<w:jc w:val="{align}"/>')
        return f"<w:p><w:pPr>{''.join(props)}</w:pPr>{runs}</w:p>"

    def _run(
        self,
        text: str,
        *,
        bold: bool = False,
        italic: bool = False,
        size_pt: float | None = None,
        hyperlink: bool = False,
    ) -> str:
        theme = self._theme
        props: list[str] = []
        if hyperlink:
            props.append('<w:rStyle w:val="Hyperlink"/>')
        if bold:
            props.append("<w:b/><w:bCs/>")
        if italic:
            props.append("<w:i/><w:iCs/>")
        if hyperlink:
            props.append(f'<w:color w:val="{theme.link_color}"/>')
        if size_pt is not None and size_pt != theme.size_body_pt:
            half = theme.half_points(size_pt)
            props.append(f'<w:sz w:val="{half}"/><w:szCs w:val="{half}"/>')
        if hyperlink and theme.link_underline:
            props.append('<w:u w:val="single"/>')
        rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
        body = f'<w:t xml:space="preserve">{escape(text)}</w:t>'
        return f"<w:r>{rpr}{body}</w:r>"

    def _sect_pr(self) -> str:
        theme = self._theme
        return (
            "<w:sectPr>"
            f'<w:pgSz w:w="{theme.page_width_twips}" w:h="{theme.page_height_twips}"/>'
            f'<w:pgMar w:top="{theme.margin_twips}" w:right="{theme.margin_twips}" '
            f'w:bottom="{theme.margin_twips}" w:left="{theme.margin_twips}" '
            'w:header="0" w:footer="0" w:gutter="0"/>'
            "</w:sectPr>"
        )

    # -- supporting parts -----------------------------------------------

    def _document_rels(self, links: list[str]) -> str:
        entries = [
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/styles" Target="styles.xml"/>',
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/numbering" Target="numbering.xml"/>',
        ]
        for index, href in enumerate(links, start=1):
            entries.append(
                f'<Relationship Id="rIdL{index}" Type="{_HYPERLINK_REL}" '
                f"Target={quoteattr(href)} TargetMode=\"External\"/>"
            )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
            f'2006/relationships">{"".join(entries)}</Relationships>'
        )

    def _styles(self) -> str:
        theme = self._theme
        font = escape(theme.font)
        body = theme.half_points(theme.size_body_pt)
        # The font is named here and nowhere else. Changing theme.font changes
        # every glyph in the document without touching a single measurement.
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f"<w:styles {_NS}>"
            "<w:docDefaults><w:rPrDefault><w:rPr>"
            f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:eastAsia="{font}" w:cs="{font}"/>'
            f'<w:sz w:val="{body}"/><w:szCs w:val="{body}"/>'
            "</w:rPr></w:rPrDefault>"
            "<w:pPrDefault><w:pPr>"
            f'<w:spacing w:before="0" w:after="0" w:line="{round(240 * theme.line_height)}" '
            'w:lineRule="auto"/>'
            "</w:pPr></w:pPrDefault></w:docDefaults>"
            '<w:style w:type="paragraph" w:styleId="Normal" w:default="1">'
            '<w:name w:val="Normal"/></w:style>'
            '<w:style w:type="paragraph" w:styleId="ListParagraph">'
            '<w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/>'
            "<w:pPr><w:contextualSpacing/></w:pPr></w:style>"
            '<w:style w:type="character" w:styleId="Hyperlink">'
            '<w:name w:val="Hyperlink"/><w:rPr>'
            f'<w:color w:val="{theme.link_color}"/>'
            + ('<w:u w:val="single"/>' if theme.link_underline else "")
            + "</w:rPr></w:style></w:styles>"
        )

    def _numbering(self) -> str:
        theme = self._theme
        font = escape(theme.font)
        bullet = escape(theme.bullet_char)
        # The bullet glyph inherits the document font, so a font swap carries
        # the dash with it instead of stranding it in a symbol face.
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f"<w:numbering {_NS}>"
            '<w:abstractNum w:abstractNumId="0">'
            '<w:multiLevelType w:val="singleLevel"/>'
            '<w:lvl w:ilvl="0">'
            '<w:start w:val="1"/><w:numFmt w:val="bullet"/>'
            f'<w:lvlText w:val="{bullet}"/><w:lvlJc w:val="left"/>'
            f'<w:pPr><w:ind w:left="{theme.bullet_indent_twips}" '
            f'w:hanging="{theme.bullet_hang_twips}"/></w:pPr>'
            f'<w:rPr><w:rFonts w:ascii="{font}" w:hAnsi="{font}" '
            f'w:eastAsia="{font}" w:cs="{font}" w:hint="default"/></w:rPr>'
            "</w:lvl></w:abstractNum>"
            '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
            "</w:numbering>"
        )

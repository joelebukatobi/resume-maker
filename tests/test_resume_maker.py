"""Self-checks. Run with: python3 -m unittest discover -s tests -v"""

from __future__ import annotations

import sys
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resume_maker.build import export_name, safe_stem  # noqa: E402
from resume_maker.model import CV, ContentError, CoverLetter, normalise_dates, shown  # noqa: E402
from resume_maker.render_docx import DocxRenderer  # noqa: E402
from resume_maker.render_html import HtmlRenderer  # noqa: E402
from resume_maker.theme import Theme, ThemeError  # noqa: E402
from resume_maker import match
from resume_maker.validate import letter_issues, Level, validate  # noqa: E402

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def load(font: str | None = None) -> tuple[CV, Theme]:
    theme = Theme.load(ROOT / "theme.json")
    if font:
        theme = Theme.from_mapping({**{k: getattr(theme, k) for k in
                                       Theme.__dataclass_fields__}, "font": font})
    cv = CV.load(ROOT / "cv.example.json", date_separator=theme.date_separator)
    return cv, theme


def docx_part(data: bytes, name: str) -> str:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        return archive.read(name).decode("utf-8")


class TabStopTests(unittest.TestCase):
    """The whole point of the project: both edges lap the margins."""

    def test_stop_sits_exactly_on_the_right_margin(self):
        _, theme = load()
        expected_in = theme.page_width_in - 2 * theme.margin_in
        self.assertAlmostEqual(theme.text_width_in, expected_in)
        self.assertEqual(theme.right_tab_twips, round(expected_in * 1440))

    def test_every_two_column_line_gets_a_right_tab_stop(self):
        cv, theme = load()
        xml = docx_part(DocxRenderer(theme).render(cv), "word/document.xml")
        stops = xml.count(f'<w:tab w:val="right" w:pos="{theme.right_tab_twips}"/>')
        self.assertEqual(stops, len(cv.column_pairs()))
        self.assertGreater(stops, 0)

    def test_tab_stop_does_not_move_when_the_font_changes(self):
        cv, cambria = load("Cambria")
        _, arial = load("Arial")
        positions = set()
        for theme in (cambria, arial):
            xml = docx_part(DocxRenderer(theme).render(cv), "word/document.xml")
            root = ElementTree.fromstring(xml)
            for tab in root.iter(f"{W}tab"):
                if tab.get(f"{W}val") == "right":
                    positions.add(tab.get(f"{W}pos"))
        self.assertEqual(positions, {str(cambria.right_tab_twips)})
        self.assertEqual(cambria.right_tab_twips, arial.right_tab_twips)

    def test_margins_drive_the_stop(self):
        _, theme = load()
        wider = Theme.from_mapping(
            {**{k: getattr(theme, k) for k in Theme.__dataclass_fields__}, "margin_in": 1.0}
        )
        self.assertEqual(wider.right_tab_twips, 9360)  # 6.5in


class FontSwapTests(unittest.TestCase):
    def test_font_is_declared_once_in_doc_defaults(self):
        cv, theme = load("Georgia")
        styles = docx_part(DocxRenderer(theme).render(cv), "word/styles.xml")
        self.assertIn('w:ascii="Georgia"', styles)
        document = docx_part(DocxRenderer(theme).render(cv), "word/document.xml")
        self.assertNotIn("w:rFonts", document)

    def test_bullet_inherits_the_document_font(self):
        cv, theme = load("Verdana")
        numbering = docx_part(DocxRenderer(theme).render(cv), "word/numbering.xml")
        self.assertIn('w:ascii="Verdana"', numbering)
        self.assertIn('<w:lvlText w:val="-"/>', numbering)
        self.assertNotIn("OpenSymbol", numbering)

    def test_html_and_docx_agree_on_geometry(self):
        cv, theme = load()
        css = HtmlRenderer(theme).render(cv)
        self.assertIn(f"margin:{theme.margin_in}in", css)
        self.assertIn(f"font-size:{theme.size_body_pt}pt", css)
        self.assertIn(f"width:{theme.text_width_in}in", css)


class DocxStructureTests(unittest.TestCase):
    def test_package_is_complete_and_well_formed(self):
        cv, theme = load()
        data = DocxRenderer(theme).render(cv)
        with zipfile.ZipFile(BytesIO(data)) as archive:
            self.assertIsNone(archive.testzip())
            names = set(archive.namelist())
            for required in (
                "[Content_Types].xml",
                "_rels/.rels",
                "word/document.xml",
                "word/styles.xml",
                "word/numbering.xml",
                "word/_rels/document.xml.rels",
            ):
                self.assertIn(required, names)
            for name in names:
                ElementTree.fromstring(archive.read(name))

    def test_the_header_is_centred(self):
        """The name and the contact line sit in the middle, in both outputs."""
        cv, theme = load()
        xml = docx_part(DocxRenderer(theme).render(cv), "word/document.xml")
        html = HtmlRenderer(theme).render(cv, preview=False)
        self.assertIn('<w:jc w:val="center"/>', xml)
        self.assertNotIn('<w:jc w:val="right"/>', xml)   # the header was the only one
        self.assertIn("text-align:center", html.split(".heading")[0])

    def test_the_letter_header_stays_on_the_right(self):
        """Only the CV moved. The letter's name and contact sit where they
        were, above prose that is itself left aligned."""
        cv, theme = load()
        letter = CoverLetter.from_mapping({
            "name": "Joel Onwuanaku",
            "contact": [{"text": "you@example.com"}],
            "role": "Rotational Development Program",
            "company": "Flexport",
            "greeting": "Dear [Company] Hiring Team,",
            "paragraphs": ["I am applying for the [Role] at [Company]."],
            "closing": "Thank you for your consideration.",
            "signoff": "Sincerely,",
        })
        html = HtmlRenderer(theme).render(cv, preview=False, letter=letter)
        self.assertIn('<main class="page letter">', html)
        self.assertIn(".letter .name,.letter .contact{text-align:right;}", html)
        xml = docx_part(DocxRenderer(theme).render(cv, letter), "word/document.xml")
        self.assertIn('<w:jc w:val="right"/>', xml)
        self.assertNotIn('<w:jc w:val="center"/>', xml)

    def test_bullets_use_the_list_numbering(self):
        cv, theme = load()
        xml = docx_part(DocxRenderer(theme).render(cv), "word/document.xml")
        # What the page holds, not what the document holds: an entry that is
        # off travels with the CV and prints nothing.
        bullets = sum(
            len(entry.bullets)
            for section in cv.sections
            for entry in shown(getattr(section, "entries", ()))
        )
        self.assertEqual(xml.count('<w:numId w:val="1"/>'), bullets)

    def test_hyperlinks_get_external_relationships(self):
        cv, theme = load()
        rels = docx_part(DocxRenderer(theme).render(cv), "word/_rels/document.xml.rels")
        for href in cv.links():
            self.assertIn(href, rels)
        self.assertEqual(rels.count('TargetMode="External"'), len(cv.links()))


class InjectionTests(unittest.TestCase):
    """Content is data, never markup."""

    HOSTILE = '</w:t></w:r><w:r><w:t>INJECTED & "quoted" <tag>'

    def _cv_with(self, text: str) -> CV:
        return CV.from_mapping(
            {
                "name": text,
                "contact": [{"text": text}],
                "sections": [{"heading": "SKILLS", "type": "paragraph", "text": text}],
            }
        )

    def test_docx_escapes_content(self):
        _, theme = load()
        xml = docx_part(DocxRenderer(theme).render(self._cv_with(self.HOSTILE)),
                        "word/document.xml")
        ElementTree.fromstring(xml)
        self.assertNotIn("<tag>", xml)
        self.assertIn("&lt;tag&gt;", xml)

    def test_html_escapes_content(self):
        _, theme = load()
        page = HtmlRenderer(theme).render(self._cv_with("<script>alert(1)</script>"))
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_javascript_urls_are_refused(self):
        for href in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,x"):
            with self.subTest(href=href), self.assertRaises(ContentError):
                CV.from_mapping(
                    {"name": "x", "contact": [{"text": "x", "href": href}], "sections": []}
                )

    def test_unknown_keys_are_refused(self):
        with self.assertRaises(ContentError):
            CV.from_mapping({"name": "x", "contact": [], "sections": [], "extra": 1})

    def test_export_names_read_like_documents(self):
        cases = {
            ("cv", "JOEL ONWUANAKU", "Meta", "Product Manager"):
                "Joel Onwuanaku - CV - Meta - Product Manager",
            ("cover", "JOEL ONWUANAKU", "Meta", "Product Manager"):
                "Joel Onwuanaku - Cover Letter - Meta - Product Manager",
            ("cv", "JOEL ONWUANAKU", "Meta", ""): "Joel Onwuanaku - CV - Meta",
            ("cv", "JOEL ONWUANAKU", "", ""): "Joel Onwuanaku - CV",
        }
        for args, expected in cases.items():
            with self.subTest(args=args):
                self.assertEqual(export_name(*args), expected)

    def test_export_name_cannot_carry_a_path(self):
        self.assertEqual(export_name("cv", "Joel Onwuanaku", "../Evil Co", "a/b"),
                         "Joel Onwuanaku - CV - Evil Co - ab")

    def test_theme_font_cannot_break_out_of_css(self):
        _, theme = load()
        base = {k: getattr(theme, k) for k in Theme.__dataclass_fields__}
        with self.assertRaises(ThemeError):
            Theme.from_mapping({**base, "font": "X; } body { display:none } .y{"})

    def test_file_stem_cannot_traverse(self):
        self.assertEqual(safe_stem("../../etc/passwd"), "etcpasswd")
        self.assertEqual(safe_stem(""), "cv")
        self.assertEqual(safe_stem("  ..  "), "cv")
        # spaces survive so export names read like titles; separators do not
        self.assertEqual(safe_stem("my cv v2"), "my cv v2")
        self.assertEqual(safe_stem("Meta/../.. Corp"), "Meta Corp")


class ContentRuleTests(unittest.TestCase):
    def test_date_separator_is_normalised(self):
        self.assertEqual(normalise_dates("March 2022 – December 2023", "-"),
                         "March 2022 - December 2023")
        self.assertEqual(normalise_dates("2019—2021", "-"), "2019 - 2021")
        self.assertEqual(normalise_dates("July 2024 – Present", "-"),
                         "July 2024 - Present")

    def test_hyphenated_words_survive(self):
        self.assertEqual(normalise_dates("end-to-end delivery", "-"), "end-to-end delivery")

    def test_short_phone_is_an_error(self):
        cv = CV.from_mapping(
            {"name": "x", "contact": [{"text": "+1 929361065"}], "sections": []}
        )
        issues = validate(cv)
        self.assertTrue(any(i.level is Level.ERROR and "10" in i.message for i in issues))

    def test_correct_phone_passes(self):
        cv = CV.from_mapping(
            {"name": "x", "contact": [{"text": "+1 929-361-0650"}], "sections": []}
        )
        self.assertFalse([i for i in validate(cv) if i.level is Level.ERROR])

    def test_bare_link_label_is_flagged(self):
        cv = CV.from_mapping(
            {
                "name": "x",
                "contact": [{"text": "LinkedIn", "href": "https://example.com"}],
                "sections": [],
            }
        )
        self.assertTrue(any("hides the address" in i.message for i in validate(cv)))

    def test_banned_punctuation_in_prose_is_flagged(self):
        """Proof-reading for a character is a job for a regex, not for eyes."""
        cv = CV.from_mapping({
            "name": "x", "contact": [],
            "sections": [{
                "heading": "EXPERIENCE", "type": "entries",
                "entries": [{"left": "MKEL", "bullets": [
                    "Took on whatever was needed: account management and rates.",
                    "Ran discovery — then built the thing.",
                    "Shipped it; nobody complained.",
                    "Built an end-to-end pipeline for 600 teams.",
                ]}],
            }],
        })
        found = {i.message for i in validate(cv) if i.where.startswith("MKEL")}
        self.assertIn("contains a colon", found)
        self.assertIn("contains a em dash", found)
        self.assertIn("contains a semicolon", found)
        # A hyphen inside a compound word is not punctuation.
        self.assertEqual(len([i for i in validate(cv) if i.where.startswith("MKEL")]), 3)

    def test_a_labelled_line_may_keep_its_colon(self):
        """Skills print as "Label: text"; only prose is policed."""
        cv = CV.from_mapping({
            "name": "x", "contact": [],
            "sections": [{"heading": "SKILLS", "type": "labeled",
                          "items": [{"label": "Product", "text": "discovery, roadmaps"}]}],
        })
        self.assertFalse([i for i in validate(cv) if "colon" in i.message])

    def test_shipped_cv_has_no_errors(self):
        cv, _ = load()
        errors = [i for i in validate(cv) if i.level is Level.ERROR]
        self.assertEqual([str(i) for i in errors], [])


class CoverLetterTests(unittest.TestCase):
    def _cv(self) -> CV:
        return CV.from_mapping(
            {
                "name": "JOEL ONWUANAKU",
                "contact": [{"text": "New York, NY"}],
                "sections": [],
            }
        )

    def _letter(self, **fields: object) -> CoverLetter:
        data = {
            "name": "JOEL ONWUANAKU",
            "contact": [{"text": "New York, NY"}],
            "role": "Product Manager",
            "company": "Acme",
            "greeting": "Dear Hiring Team,",
            "paragraphs": ["I'm applying for the [Role] role at [Company]."],
            "closing": "Thank you for your consideration.",
            "signoff": "Sincerely,",
        }
        data.update(fields)
        return CoverLetter.from_mapping(data)

    def test_placeholders_fill_in_preview_html(self):
        cv, theme = load()
        letter = self._letter()
        html = HtmlRenderer(theme).render(cv, preview=True, letter=letter)
        self.assertIn("Product Manager", html)
        self.assertIn("Acme", html)
        self.assertNotIn("[Role]", html)
        self.assertNotIn("[Company]", html)

    def test_cover_letter_omits_cv_sections(self):
        cv, theme = load()
        letter = self._letter()
        html = HtmlRenderer(theme).render(cv, preview=False, letter=letter)
        self.assertNotIn("PROFESSIONAL SUMMARY", html)
        self.assertIn("Dear Hiring Team,", html)
        self.assertIn("Sincerely,", html)
        self.assertIn(letter.name, html)

    def test_letter_contact_does_not_use_the_cv_line(self):
        cv, theme = load()
        letter = self._letter(contact=[{"text": "you@example.com"}])
        html = HtmlRenderer(theme).render(cv, preview=False, letter=letter)
        self.assertIn("you@example.com", html)
        self.assertNotIn("your-site.dev", html)

    def test_unknown_cover_key_is_rejected(self):
        with self.assertRaises(ContentError):
            CoverLetter.from_mapping({"role": "x", "extra": 1})


class PdfMeasurementTest(unittest.TestCase):
    """The page's report must not travel in the title: Chrome prints the title
    into the PDF, where a recruiter's viewer shows it."""

    def test_the_report_is_read_back_and_never_set_as_the_title(self):
        from resume_maker.export_pdf import Browser
        from resume_maker.render_html import _MEASURE_SCRIPT

        self.assertNotIn("document.title", _MEASURE_SCRIPT)
        dumped = ('<title>Joel</title><pre id="overflow-report" hidden="">'
                  '{"offenders":[],"over":[{"label":"A &amp; B","bullet":2,"lines":3,"allowed":2}]}'
                  "</pre>")

        class Dump(Browser):
            def _run(self, args, *, timeout):
                return type("Result", (), {"stdout": dumped.encode()})

        found = Dump("chrome").measure("<html></html>").over_budget[0]
        self.assertEqual((found.label, found.bullet), ("A & B", 2))


if __name__ == "__main__":
    unittest.main()


class SignatureTests(unittest.TestCase):
    def _letter(self, contact):
        from resume_maker.model import CoverLetter
        return CoverLetter.from_mapping({
            "name": "JOEL ONWUANAKU", "contact": contact, "role": "PM", "company": "X",
            "greeting": "Hi,", "paragraphs": ["Body."], "closing": "Thanks.", "signoff": "Sincerely,",
        })

    def test_footer_holds_only_ticked_bio_items(self):
        letter = self._letter([
            {"text": "linkedin.com/in/x", "href": "https://linkedin.com/in/x"},
            {"text": "a@b.com", "href": "mailto:a@b.com", "footer": True},
            {"text": "+1 929-361-0654", "footer": True},
            {"text": "New York, NY"},
        ])
        name, details = letter.signature()
        self.assertEqual(name, "Joel Onwuanaku")
        self.assertEqual([d.text for d in details], ["a@b.com", "+1 929-361-0654"])

    def test_header_holds_only_ticked_bio_items(self):
        from resume_maker.model import header_items
        letter = self._letter([
            {"text": "a@b.com", "href": "mailto:a@b.com"},
            {"text": "+1 929-361-0654", "header": False, "footer": True},
        ])
        self.assertEqual([c.text for c in header_items(letter.contact)], ["a@b.com"])
        _, theme = load()
        page = HtmlRenderer(theme).render(load()[0], letter=letter)
        head = page.split('class="contact"', 1)[1].split("</p>", 1)[0]
        self.assertNotIn("929-361-0654", head)

    def test_placement_flags_must_be_boolean(self):
        with self.assertRaises(ContentError):
            self._letter([{"text": "x", "footer": "yes"}])

    def test_title_case_keeps_mixed_case_words(self):
        from resume_maker.model import title_case
        self.assertEqual(title_case("MARY O'NEIL-SMITH"), "Mary O'Neil-Smith")
        self.assertEqual(title_case("Ronald McDonald"), "Ronald McDonald")


class LetterHeaderTests(unittest.TestCase):
    def test_letter_header_stacks_one_detail_per_line(self):
        from resume_maker.model import CoverLetter
        letter = CoverLetter.from_mapping({
            "name": "X", "role": "PM", "company": "Y", "greeting": "Hi,",
            "paragraphs": ["Body."], "closing": "Thanks.", "signoff": "Sincerely,",
            "contact": [{"text": "linkedin.com/in/x", "href": "https://linkedin.com/in/x"},
                        {"text": "New York, NY"}],
        })
        cv, theme = load()
        page = HtmlRenderer(theme).render(cv, letter=letter)
        self.assertEqual(page.count('class="contact"'), 2)
        self.assertNotIn(" | ", page.split("Hi,", 1)[0])
        xml = docx_part(DocxRenderer(theme).render(cv, letter), "word/document.xml")
        self.assertNotIn(" | ", xml)


class HeaderRuleTests(unittest.TestCase):
    def test_cv_has_one_rule_under_the_header(self):
        cv, theme = load()
        xml = docx_part(DocxRenderer(theme).render(cv), "word/document.xml")
        self.assertEqual(xml.count("<w:pBdr>"), 1)
        root = ElementTree.fromstring(xml)
        paragraphs = list(root.iter(f"{W}p"))
        self.assertIsNotNone(paragraphs[1].find(f"{W}pPr/{W}pBdr"))  # name, then contact
        self.assertEqual(HtmlRenderer(theme).render(cv).count('class="contact rule"'), 1)

    def test_letter_has_no_rule(self):
        from resume_maker.model import CoverLetter
        cv, theme = load()
        letter = CoverLetter.load(ROOT / "cover.example.json")
        xml = docx_part(DocxRenderer(theme).render(cv, letter), "word/document.xml")
        self.assertNotIn("<w:pBdr>", xml)
        self.assertNotIn("contact rule", HtmlRenderer(theme).render(cv, letter=letter))


class PreviewFontTests(unittest.TestCase):
    def test_strip_bitmaps_removes_only_bitmap_tables(self):
        import struct
        from resume_maker.fonts import strip_bitmaps
        tables = [(b"EBDT", b"x" * 5), (b"glyf", b"g" * 8), (b"EBLC", b"y" * 3)]
        head = b"\x00\x01\x00\x00" + struct.pack(">HHHH", len(tables), 32, 1, 16)
        offset, body, directory = 12 + 16 * len(tables), b"", b""
        for tag, data in tables:
            directory += struct.pack(">4sIII", tag, 0, offset + len(body), len(data))
            body += data + b"\0" * (-len(data) % 4)
        out = strip_bitmaps(head + directory + body)
        self.assertEqual(struct.unpack(">H", out[4:6])[0], 1)
        tag, _, start, length = struct.unpack(">4sIII", out[12:28])
        self.assertEqual((tag, out[start:start + length]), (b"glyf", b"g" * 8))

    def test_preview_loads_theme_font_from_server_but_print_does_not(self):
        cv, theme = load()
        self.assertIn("/api/font-face?family=Cambria&bold=1", HtmlRenderer(theme).render(cv, preview=True))
        self.assertNotIn("/api/font-face", HtmlRenderer(theme).render(cv))


class PaginationTests(unittest.TestCase):
    def test_headings_and_entry_lines_keep_with_next_like_docx(self):
        cv, theme = load()
        page = HtmlRenderer(theme).render(cv)
        self.assertIn(".heading,.entry > .row{break-after:avoid;}", page)
        xml = docx_part(DocxRenderer(theme).render(cv), "word/document.xml")
        self.assertGreater(xml.count("<w:keepNext/>"), len(cv.sections))

    def test_preview_paginates_but_print_does_not(self):
        cv, theme = load()
        self.assertIn("column-fill:auto", HtmlRenderer(theme).render(cv, preview=True))
        self.assertNotIn("column-fill", HtmlRenderer(theme).render(cv))


    def test_the_preview_reports_the_room_left_on_the_page(self):
        """Spare lines at the foot of a one-page CV, for the chat to use."""
        cv, theme = load()
        html = HtmlRenderer(theme).render(cv, preview=True)
        self.assertIn("type: 'resume-pages', count: pageCount, room: room", html)


class MatchTests(unittest.TestCase):
    """Coverage of a posting's vocabulary, counted rather than judged."""

    JD = (
        "We are looking for a product manager to own the data pipeline roadmap. "
        "You will run discovery with engineering, define the data pipeline, and "
        "measure adoption. Strong product manager candidates have shipped a data "
        "pipeline before and can run discovery independently."
    )

    def test_a_document_that_uses_the_posting_language_scores_higher(self):
        weak = match.score(["I wrote software and fixed bugs."], self.JD)
        strong = match.score(
            ["Owned the data pipeline roadmap and ran discovery with engineering.",
             "Measured adoption of the pipeline across teams."], self.JD)
        self.assertGreater(strong.score, weak.score)
        self.assertIn("data pipeline", strong.matched)

    def test_what_is_missing_is_named(self):
        result = match.score(["Ran discovery with engineering."], self.JD)
        self.assertIn("data pipeline", result.missing)
        self.assertNotIn("discovery", result.missing)

    def test_the_company_name_is_not_a_requirement(self):
        result = match.score(["A product manager."], "Acme is hiring. Acme builds "
                             "pipelines. Acme is a great place to work at Acme.",
                             company="Acme")
        self.assertNotIn("acme", " ".join(result.missing))

    def test_a_different_ending_is_not_a_miss(self):
        """A posting asking to "track" is answered by a CV that says "tracked".

        Suffixes only: an irregular verb ("grow" against "grew") is still a
        miss, which is the price of not shipping a dictionary.
        """
        jd = "You will track adoption and own the roadmap. Track adoption daily."
        result = match.score(["Tracked adoption daily and owned the roadmap."], jd)
        self.assertNotIn("track", result.missing)
        self.assertNotIn("roadmap", result.missing)
        self.assertEqual(result.score, 100)

    def test_no_posting_scores_zero_rather_than_everything(self):
        self.assertEqual(match.score(["anything at all"], "").score, 0)

    def test_boilerplate_is_not_counted_as_a_requirement(self):
        result = match.score(["x"], "You will be responsible for the role. "
                                    "Requirements: experience. We offer benefits.")
        self.assertNotIn("responsibilities", result.missing)
        self.assertNotIn("benefits", result.missing)


class RequirementTests(unittest.TestCase):
    """Counting against a list of requirements rather than against repetition.

    The posting's own repetitions cannot tell a requirement from the team's
    org chart. On one engineering posting "marketplace makers" and "star
    seller" weighed 6 each, because the posting names its team and its product
    three times apiece, while "ruby" weighed 2 and was the only miss the
    candidate could have done anything about.
    """

    CV = ["Built full-stack features in PHP and React with TypeScript.",
          "Ran A/B experiments and shipped behind feature flags."]

    def test_each_requirement_counts_once(self):
        result = match.score_against(self.CV, ["php", "react", "typescript", "ruby"])
        self.assertEqual(result.score, 75)
        self.assertEqual(result.missing, ("ruby",))

    def test_a_requirement_is_matched_on_its_stem(self):
        result = match.score_against(["Ran experiments on the pipeline."],
                                     ["a/b experiment", "pipelines"])
        self.assertIn("pipelines", result.matched)

    def test_an_empty_list_scores_zero_rather_than_everything(self):
        self.assertEqual(match.score_against(self.CV, []).score, 0)

    def test_the_weighting_that_made_this_necessary(self):
        """Side by side, on the shape of the posting that prompted it."""
        posting = ("Software Engineer, Marketplace Makers. Join the Marketplace "
                   "Makers team. Marketplace Makers build the Star Seller program. "
                   "The Star Seller dashboard and Star Seller status matter. "
                   "You will write PHP. Ruby is a plus.\n")
        counted = match.score(self.CV, posting)
        self.assertTrue(any("marketplace makers" in term for term in counted.missing))
        listed = match.score_against(self.CV, ["php", "ruby"])
        self.assertEqual(listed.missing, ("ruby",))     # the only one worth knowing


class JobTextTests(unittest.TestCase):
    """The posting cut down to the part that describes the job.

    Counted whole, a benefits table and an equal-opportunity paragraph beat the
    requirements: one consulting posting's forty terms included "employees
    spouses", "usd", "cost" and "qualified applicants", and the heaviest term
    of all was the company's own acronym. None of it can honestly appear on a
    CV, so the missing list was asking for words that could not help.
    """

    POSTING = (
        "What You'll Do\n"
        "You will own the product roadmap and run discovery with engineering.\n"
        "Experience with data pipelines is required.\n"
        "You will write the requirements, define the metrics and measure adoption.\n"
        "You will work with data science on the pipeline design and the rollout.\n"
        "You will run the launch, the handoff and the release notes.\n"
        "Strong candidates have owned a roadmap and shipped a data pipeline.\n"
        "\n"
        "Additional info\n"
        "The first year base compensation for this role is $120,000 USD.\n"
        "Zero dollar health insurance premiums for employees, spouses and children.\n"
        "Dental coverage, including up to $5,000 in orthodontia benefits.\n"
        "Generous paid time off including vacation and holidays.\n"
        "All qualified applicants will be considered for employment without "
        "regard to sex, gender identity, age, or any other characteristic.\n"
    )

    def terms(self, text):
        return [term for term, _ in match._terms(match.job_text(text))]

    def test_the_benefits_and_the_law_are_not_requirements(self):
        kept = " ".join(self.terms(self.POSTING))
        for gone in ("spouses", "orthodontia", "usd", "qualified", "vacation",
                     "characteristic", "premiums"):
            self.assertNotIn(gone, kept)
        for stays in ("roadmap", "pipeline", "discovery", "adoption"):
            self.assertIn(stays, kept)

    def test_a_perk_word_that_is_the_industry_survives(self):
        """One benefits word on a line is the industry; several together are
        the benefits table. A pensions company's postings are about retirement.

        Note what this cannot rescue: "insurance" and "benefits" have been in
        STOPWORDS since before any of this, so a reinsurance posting never
        counted them in the first place.
        """
        kept = self.terms("We are a leading retirement platform.\n"
                          "You will own the retirement pricing product.\n"
                          "You will run discovery and own the roadmap.\n")
        self.assertIn("retirement", " ".join(kept))

    def test_a_marker_inside_a_longer_word_is_not_a_marker(self):
        """"vested" matched "invested" and deleted the job description."""
        line = "We want a developer who is invested in the craft of the web."
        self.assertEqual(match.job_text(line), line)

    def test_a_posting_pasted_as_one_wall_is_filtered_by_sentence(self):
        wall = ("You will own the product roadmap and run discovery. "
                "Salary starts at $90,000. "
                "You will define the data pipeline with engineering. ") * 3
        kept = match.job_text(wall)
        self.assertNotIn("90,000", kept)
        self.assertIn("product roadmap", kept)   # the sentence either side survives

    def test_a_filter_that_eats_the_posting_is_abandoned(self):
        """It has misfired rather than found an unusually legal posting, and a
        posting counted against nothing scores zero, which is worse."""
        mostly_pay = "Base salary $100,000 USD. 401(k). Dental. Paid time off."
        self.assertEqual(match.job_text(mostly_pay), mostly_pay)

    POSTING_WITH_SECTIONS = (
        "Software Engineer I, Marketplace Makers\n"
        "Continue Application\n"
        "remote type\n"
        "Partially Remote\n"
        "locations\n"
        "Brooklyn, New York\n"
        "posted on\n"
        "Posted Yesterday\n"
        "job requisition id\n"
        "JR6037\n"
        "\n"
        "Company Description\n"
        "We are the global marketplace for unique and creative goods, and we "
        "believe exceptional companies are built by exceptional teams who "
        "believe in what they do.\n"
        "\n"
        "What You'll Do\n"
        "You will own the product roadmap and run discovery with engineering, "
        "and you will ship features behind feature flags.\n"
        "Some experience building web applications, in languages such as PHP, "
        "Python, Ruby, Java, or JavaScript/TypeScript.\n"
        "\n"
        "Compensation & Benefits\n"
        "Etsy uses two geographic pay zones covering the New York City "
        "metropolitan area and the San Francisco Bay Area, and salary ranges "
        "for this role vary by zone.\n"
        "We prioritize candidates within commutable distance of the office.\n"
    )

    def test_a_section_that_is_not_the_job_goes_whole(self):
        kept = " ".join(self.terms(self.POSTING_WITH_SECTIONS))
        for gone in ("unique", "exceptional", "believe",          # the blurb
                     "geo", "metropolitan", "commutable",         # the pay zones
                     "requisition", "posted", "brooklyn"):        # the job board
            self.assertNotIn(gone, kept)
        for stays in ("roadmap", "discovery", "php", "python"):
            self.assertIn(stays, kept)

    def test_a_slash_joined_pair_counts_as_both_sides(self):
        """"React/TypeScript" was one term no CV could answer, because a CV
        lists React and TypeScript in a skills line rather than side by side
        in that order. Short pairs stay whole: "a/b" and "ci/cd" are words."""
        terms = self.terms("You will work in React/TypeScript on the frontend. "
                           "React/TypeScript every day, React/TypeScript always.")
        self.assertIn("react", terms)
        self.assertIn("typescript", terms)
        self.assertNotIn("react/typescript", terms)
        self.assertEqual(match._words("we run a/b tests with ci/cd"),
                         ["we", "run", "a/b", "tests", "with", "ci/cd"])

    def test_a_skill_named_once_in_a_list_still_counts(self):
        """Ranked by frequency alone, the six languages a posting asks for
        came 150th and never reached the count, while a culture paragraph's
        third "believe" did."""
        terms = self.terms(self.POSTING_WITH_SECTIONS)
        for language in ("php", "python", "ruby", "java", "javascript", "typescript"):
            self.assertIn(language, terms)

    def test_a_list_needs_several_items_before_it_counts(self):
        """The line has to look like a list. A sentence with a comma or two in
        it is not one, and the limit is deliberately low: requiring most items
        to be single words would have been tighter, and it threw away
        "Proficiency in React, TypeScript, Node.js, and modern frontend and
        backend frameworks", which is exactly the line worth reading.

        The cost is that a long enough sentence still yields a fragment or
        two. They are weighted the same as a real skill and show up in the
        missing list, which is the known ceiling here.
        """
        self.assertEqual(match._listed("We work with product, design, and research.\n"), {})
        listed = match._listed("Proficiency in React, TypeScript, Node.js, and "
                               "modern frontend and backend frameworks.\n")
        self.assertIn("react", listed)
        self.assertIn("typescript", listed)
        self.assertIn("node.js", listed)

    def test_the_acronym_goes_with_the_company_name(self):
        """It was the heaviest term on the posting, because the name it was
        stripped by was spelled out in full."""
        posting = ("BCG partners with leaders in business. BCG was the pioneer. "
                   "At BCG you will own a strategy project. BCG hires associates.")
        result = match.score(["A strategy project owner."], posting,
                             company="Boston Consulting Group")
        self.assertNotIn("bcg", " ".join(result.missing))


class LetterCheckTests(unittest.TestCase):
    """A letter's failure modes are its own, and a CV check does not see them."""

    BASE = {
        "name": "Joel Onwuanaku", "contact": [], "company": "IBM",
        "role": "Product Manager", "greeting": "Dear [Company] Hiring Team",
        "paragraphs": ["I hope this finds you well. My name is Joel, and I am applying "
                       "for the [Role] at [Company]."],
        "closing": "Thank you for your consideration.", "signoff": "Sincerely,",
    }

    def issues(self, **changes):
        letter = CoverLetter.from_mapping({**self.BASE, **changes})
        return {i.where: i.message for i in letter_issues(letter)}

    def test_a_clean_letter_raises_nothing(self):
        self.assertEqual(self.issues(), {})

    def test_a_missing_greeting_is_caught(self):
        self.assertIn("no greeting", self.issues(greeting="")["greeting"])

    def test_something_that_is_not_a_greeting_is_caught(self):
        self.assertIn("greeting", self.issues(greeting="Regarding your posting")["greeting"])

    def test_an_opening_with_no_introduction_is_caught(self):
        found = self.issues(paragraphs=["I spent last summer at Amazon expanding a dataset."])
        self.assertIn("introduce you by name", found["paragraphs[0]"])

    def test_a_real_name_written_out_costs_the_letter_its_reuse(self):
        found = self.issues(paragraphs=["My name is Joel and I am applying to IBM today."])
        self.assertIn("[Company]", found["company"])

    def test_banned_punctuation_in_a_paragraph_is_caught(self):
        found = self.issues(paragraphs=["My name is Joel; I want this role — truly: yes."])
        self.assertIn("paragraphs[0]", found)

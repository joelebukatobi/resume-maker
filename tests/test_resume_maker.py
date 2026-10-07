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
from resume_maker.model import CV, ContentError, CoverLetter, normalise_dates  # noqa: E402
from resume_maker.render_docx import DocxRenderer  # noqa: E402
from resume_maker.render_html import HtmlRenderer  # noqa: E402
from resume_maker.theme import Theme, ThemeError  # noqa: E402
from resume_maker.validate import Level, validate  # noqa: E402

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def load(font: str | None = None) -> tuple[CV, Theme]:
    theme = Theme.load(ROOT / "theme.json")
    if font:
        theme = Theme.from_mapping({**{k: getattr(theme, k) for k in
                                       Theme.__dataclass_fields__}, "font": font})
    cv = CV.load(ROOT / "cv.json", date_separator=theme.date_separator)
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

    def test_bullets_use_the_list_numbering(self):
        cv, theme = load()
        xml = docx_part(DocxRenderer(theme).render(cv), "word/document.xml")
        bullets = sum(
            len(entry.bullets)
            for section in cv.sections
            for entry in getattr(section, "entries", ())
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
        letter = CoverLetter.load(ROOT / "cover.json")
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

"""A one page CV that the posting chooses.

The history is longer than one page, so the CV carries every role and prints
the ones that are on. Which ones answer a posting changes with the posting, so
the choice is per application, the page is measured rather than estimated, and
every change is offered rather than taken.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resume_maker import assistant  # noqa: E402
from resume_maker.export_pdf import Measurement, OverBudget  # noqa: E402
from resume_maker.model import CV, Budget, Entry, shown  # noqa: E402
from resume_maker.render_docx import DocxRenderer  # noqa: E402
from resume_maker.render_html import HtmlRenderer  # noqa: E402
from resume_maker.theme import Theme  # noqa: E402
from resume_maker.validate import validate  # noqa: E402

DOC = {
    "name": "Joel Onwuanaku",
    "contact": [{"text": "you@example.com"}],
    "sections": [
        {"heading": "EXPERIENCE", "type": "entries", "entries": [
            {"left": "AMAZON", "sub_left": "Product Manager",
             "sub_right": "May 2025 - August 2025",
             "budget": {"bullets": 3, "lines": 2, "long": 2},
             "bullets": ["Owned the roadmap.", "Ran discovery."]},
            {"left": "FORDHAM UNIVERSITY", "sub_left": "Graduate Assistant",
             "sub_right": "October 2024 - May 2026", "off": True,
             "bullets": ["Ran student engagement events and orientation programmes."]},
        ]},
    ],
}


def theme() -> Theme:
    return Theme.from_mapping(json.loads((ROOT / "theme.json").read_text()))


def cv(data: dict | None = None) -> CV:
    return CV.from_mapping(data or DOC)


class OffEntryTests(unittest.TestCase):
    """History the document holds without printing it."""

    def test_an_off_entry_is_absent_from_both_renderers(self):
        document = cv()
        html = HtmlRenderer(theme()).render(document, preview=False)
        docx = DocxRenderer(theme()).render(document, None)
        with zipfile.ZipFile(io.BytesIO(docx)) as archive:
            word = archive.read("word/document.xml").decode("utf-8")
        for where, blob in (("html", html), ("docx", word)):
            with self.subTest(where=where):
                self.assertIn("AMAZON", blob)
                self.assertNotIn("FORDHAM", blob)
                self.assertNotIn("orientation programmes", blob)

    def test_an_off_entry_is_still_part_of_the_document(self):
        """It is carried, not deleted: a later posting can ask for it."""
        entries = cv().sections[0].entries
        self.assertEqual([e.left for e in entries], ["AMAZON", "FORDHAM UNIVERSITY"])
        self.assertEqual([e.left for e in shown(entries)], ["AMAZON"])

    def test_a_section_whose_every_entry_is_off_reads_as_empty(self):
        """It would otherwise print a heading with nothing under it."""
        data = json.loads(json.dumps(DOC))
        for entry in data["sections"][0]["entries"]:
            entry["off"] = True
        issues = [i for i in validate(cv(data)) if "EXPERIENCE is empty" in i.message]
        self.assertTrue(issues)

    def test_off_must_be_a_flag(self):
        with self.assertRaises(Exception) as caught:
            Entry.parse({"left": "A", "off": "yes"}, where="e", separator="-")
        self.assertIn("off", str(caught.exception))


class BudgetTests(unittest.TestCase):
    """How much of the page one entry may take."""

    def test_the_default_is_two_lines_a_bullet(self):
        plain = Entry.parse({"left": "A"}, where="e", separator="-").budget
        self.assertEqual((plain.lines, plain.long, plain.bullets), (2, 0, None))
        self.assertEqual([plain.allowance(i) for i in range(3)], [2, 2, 2])

    def test_the_headline_entry_gets_two_long_bullets_of_three(self):
        """The user's own rule, and it belongs to the entry rather than to a
        company: the day the headline role is somewhere else, no code has to
        learn a new employer's name."""
        head = Budget(lines=2, long=2, bullets=3)
        self.assertEqual([head.allowance(i) for i in range(3)], [3, 3, 2])

    def test_a_bullet_carries_its_allowance_into_the_page(self):
        """The allowance is in the markup, so the page can measure against it
        without anything telling it the rules a second time."""
        data = json.loads(json.dumps(DOC))
        data["sections"][0]["entries"][0]["bullets"].append("Shipped the first cut.")
        html = HtmlRenderer(theme()).render(cv(data), preview=False)
        self.assertEqual(html.count('data-allowed="3"'), 2)   # the two long ones
        self.assertEqual(html.count('data-allowed="2"'), 1)   # the third

    def test_a_budget_must_be_whole_numbers(self):
        for bad in ({"lines": "two"}, {"long": -1}, {"bullets": 1.5}):
            with self.subTest(bad=bad):
                with self.assertRaises(Exception):
                    Entry.parse({"left": "A", "budget": bad}, where="e", separator="-")


class MeasurementTests(unittest.TestCase):
    """What the rendered page reports about itself."""

    def test_the_page_counts_its_own_lines(self):
        """The script runs in the export browser, so it is exercised the same
        way: give it the markup and read what it reports."""
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("node is not installed")
        from resume_maker.render_html import _LINE_COUNT
        script = _LINE_COUNT + """
        // Three rects on two tops is two lines; a zero sized rect is ignored.
        const rects = [
          { top: 10, width: 100, height: 12 },
          { top: 10.1, width: 50, height: 12 },
          { top: 24, width: 80, height: 12 },
          { top: 99, width: 0, height: 0 },
        ];
        const item = { };
        global.document = { createRange: () => ({
          selectNodeContents() {},
          getClientRects: () => rects,
        }) };
        console.log(JSON.stringify(lineCount(item)));
        """
        done = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
        if done.returncode:
            raise AssertionError(done.stderr.strip())
        self.assertEqual(json.loads(done.stdout), 2)

    def test_an_overrun_reads_as_a_sentence(self):
        self.assertEqual(
            str(OverBudget("MKEL NETWORKS", 2, 3, 2)),
            "MKEL NETWORKS bullet 2 runs 3 lines, allowed 2")

    def test_nothing_measured_is_nothing_reported(self):
        self.assertEqual(Measurement(), Measurement((), ()))


class PromptTests(unittest.TestCase):
    """What the model is told, and what it is told to do about it."""

    def test_the_field_list_says_which_roles_are_not_printed(self):
        marked, plain = [], []
        for _, label, _ in assistant.field_paths("cv", DOC):
            (marked if "[not on the CV]" in label else plain).append(label)
        # Every field of the entry that is off says so, and no field of the one
        # that prints does.
        self.assertTrue(marked)
        self.assertTrue(all("FORDHAM" in label for label in marked))
        self.assertTrue(all("FORDHAM" not in label for label in plain))

    def test_the_show_verb_is_explained(self):
        prompt = assistant.system_prompt("cv", DOC)
        self.assertIn('"show": true', prompt)
        self.assertIn("Never assume", prompt)

    def test_the_bench_is_ranked_against_the_requirements(self):
        note = assistant.bench_note(
            DOC, ["student services", "roadmap", "orientation", "discovery"])
        self.assertIn("Roles this CV holds but does not print", note)
        self.assertIn("FORDHAM UNIVERSITY answers 1 of 4", note)   # orientation
        self.assertIn("AMAZON answers 2 of 4", note)               # roadmap, discovery

    def test_a_bench_role_that_wins_is_offered_as_a_swap(self):
        note = assistant.bench_note(DOC, ["student services", "orientation"])
        self.assertIn("offer the swap", note)
        self.assertIn("The user decides both", note)

    def test_nothing_off_is_nothing_to_say(self):
        data = json.loads(json.dumps(DOC))
        del data["sections"][0]["entries"][1]["off"]
        self.assertEqual(assistant.bench_note(data, ["roadmap"]), "")

    def test_the_overruns_reach_the_model(self):
        note = assistant.fit_note(1, (), ["AMAZON bullet 3 runs 3 lines, allowed 2"])
        self.assertIn("more lines than the CV allows", note)
        self.assertIn("AMAZON bullet 3", note)


# The browser half: the verb that puts a role on the page or takes it off.
START, END = "function pathParts(", "async function fields("


def run(script: str) -> object:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node is not installed")
    source = (ROOT / "static" / "chat.js").read_text(encoding="utf-8")
    helpers = source[source.index(START):source.index(END)]
    done = subprocess.run([node, "-e", helpers + script],
                          capture_output=True, text=True, timeout=30)
    if done.returncode:
        raise AssertionError(done.stderr.strip())
    return json.loads(done.stdout)


CV_JSON = json.dumps(DOC)


class ShowVerbTests(unittest.TestCase):
    def test_only_an_entry_can_be_shown_or_hidden(self):
        checks = run(f"""
          const cv = {CV_JSON};
          console.log(JSON.stringify({{
            entry: canShow(cv, "sections.0.entries.1"),
            bullet: canShow(cv, "sections.0.entries.0.bullets.0"),
            field: canShow(cv, "sections.0.entries.0.left"),
            missing: canShow(cv, "sections.0.entries.9"),
            section: canShow(cv, "sections.0"),
          }}));
        """)
        self.assertEqual(checks, {"entry": True, "bullet": False, "field": False,
                                  "missing": False, "section": False})

    def test_putting_one_on_and_taking_one_off(self):
        after = run(f"""
          const cv = {CV_JSON};
          setShown(cv, "sections.0.entries.1", true);
          setShown(cv, "sections.0.entries.0", false);
          console.log(JSON.stringify(cv.sections[0].entries.map((e) => [e.left, !!e.off])));
        """)
        self.assertEqual(after, [["AMAZON", True], ["FORDHAM UNIVERSITY", False]])

    def test_a_document_the_browser_wrote_still_validates(self):
        """The round trip that matters: the editor turns one on, the server
        parses what comes back."""
        after = run(f"""
          const cv = {CV_JSON};
          setShown(cv, "sections.0.entries.1", true);
          console.log(JSON.stringify(cv));
        """)
        document = CV.from_mapping(after)
        self.assertEqual([e.left for e in shown(document.sections[0].entries)],
                         ["AMAZON", "FORDHAM UNIVERSITY"])


if __name__ == "__main__":
    unittest.main()

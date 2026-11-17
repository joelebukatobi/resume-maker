"""Every line he has ever used, by the slot it belongs to, searchable.

A new application used to start as a copy of the last one, so a bullet written
for one posting (Zapier, for a posting that asked for low-code tooling) rode
along into postings that never asked for it. Each CV is now built from the
whole history instead, and this is that history.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resume_maker import library  # noqa: E402
from resume_maker.applications import Workspace  # noqa: E402


def make_cv(*, summary="Product manager.", skills=None, entries=(), additional=()):
    """A CV in the shape the editor saves, with only what a test needs."""
    return {
        "name": "Joel", "contact": [],
        "sections": [
            {"heading": "PROFESSIONAL SUMMARY", "type": "paragraph", "text": summary},
            {"heading": "EDUCATION", "type": "entries",
             "entries": [{"left": "FORDHAM UNIVERSITY", "bullets": ["MBA coursework."]}]},
            {"heading": "SKILLS", "type": "labeled",
             "items": [{"label": k, "text": v} for k, v in (skills or {"AI": "Claude, MCP"}).items()]},
            {"heading": "EXPERIENCE", "type": "entries",
             "entries": [{"left": left, "sub_left": "Developer", "bullets": list(bullets)}
                         for left, bullets in entries]},
            {"heading": "ADDITIONAL INFORMATION", "type": "labeled",
             "items": [{"label": "Interests", "text": t} for t in additional]},
        ],
    }


INTERESTS = ("Deep Linux user exploring agentic AI workflows via MCPs and custom "
             "SKILL.md tooling. Currently picking up Rust.")
SPONSOR = "US permanent resident, no sponsorship required."
USUAL = "Coordinated with client and internal teams to deliver 15+ web applications."
ZAPIER = "Automated client onboarding using Zapier, n8n, Webflow and Airtable."

BASE = make_cv(entries=[("xPathEdge LLC, IT Services and Consulting", [USUAL])],
               additional=[INTERESTS])


def bullets_slot(company="xpathedge"):
    return ("bullets", company)


class CollectTests(unittest.TestCase):
    def test_a_line_on_three_cvs_is_one_line_counted_three_times(self):
        cvs = {f"app{i}": make_cv(entries=[("xPathEdge LLC", [USUAL])]) for i in range(3)}
        lines = library.collect(BASE, cvs, {})
        usual = [l for l in lines if l.text == USUAL]
        self.assertEqual(len(usual), 1)
        self.assertEqual(usual[0].count, 4)          # three applications and the base
        self.assertTrue(usual[0].base)

    def test_entries_with_the_same_company_share_a_slot(self):
        cvs = {"a": make_cv(entries=[("XPATHEDGE LLC", [ZAPIER])])}
        lines = library.collect(BASE, cvs, {})
        slots = {l.slot for l in lines if l.text in (USUAL, ZAPIER)}
        self.assertEqual(slots, {bullets_slot()})

    def test_education_is_not_in_the_library(self):
        lines = library.collect(BASE, {}, {})
        self.assertFalse([l for l in lines if l.text == "MBA coursework."])

    def test_an_unreadable_cv_is_skipped(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cv.json").write_text(json.dumps(BASE))
            (root / "cover.json").write_text("{}")
            workspace = Workspace(root / "applications", {"cv": root / "cv.json",
                                                          "cover": root / "cover.json"})
            good = workspace.create("Good")
            bad = workspace.create("Bad")
            (workspace.folder(bad.slug) / "cv.json").write_text("{ not json")
            found = library.load_cvs(workspace)
        self.assertIn(good.slug, found)
        self.assertNotIn(bad.slug, found)


class RankTests(unittest.TestCase):
    def setUp(self):
        cvs = {
            "ramp": make_cv(entries=[("xPathEdge LLC", [ZAPIER])], additional=[SPONSOR]),
            "lseg": make_cv(entries=[("xPathEdge LLC", [ZAPIER, USUAL])], additional=[SPONSOR]),
            "meta": make_cv(entries=[("xPathEdge LLC", [USUAL])], additional=[INTERESTS]),
        }
        self.index = library.Index(library.collect(BASE, cvs, {}))

    def test_the_posting_ranks_a_slot(self):
        ranked = self.index.rank(("additional", ""), ["agentic workflows", "mcp"])
        self.assertEqual(ranked[0][0].text, INTERESTS)
        self.assertGreater(ranked[0][1], 0)
        self.assertEqual(ranked[-1][1], 0)

    def test_with_no_hit_the_most_used_line_comes_first(self):
        ranked = self.index.rank(bullets_slot(), ["go-to-market"])
        self.assertEqual(ranked[0][0].text, USUAL)
        self.assertEqual(ranked[0][1], 0)

    def test_punctuation_in_terms_and_lines_is_safe(self):
        odd = 'Built C# and Node.js AND "quoted" low-code a:b x* tooling.'
        index = library.Index(library.collect(
            make_cv(entries=[("Acme", [odd])]), {}, {}))
        terms = ['C#', 'Node.js', 'AND', '"quoted"', 'low-code', 'a:b', 'x*', '"', '']
        ranked = index.rank(("bullets", "acme"), terms)
        self.assertEqual(ranked[0][0].text, odd)


class DecisionTests(unittest.TestCase):
    def test_a_struck_line_and_its_rewordings_are_gone(self):
        reworded = "Automated client onboarding with low-code tools."
        cvs = {"a": make_cv(entries=[("xPathEdge LLC", [ZAPIER, reworded])])}
        lines = library.collect(BASE, cvs, {reworded: ZAPIER})
        decisions = library.Decisions()
        decisions.struck.add(ZAPIER)
        kept = {l.text for l in library.usable(lines, decisions)}
        self.assertNotIn(ZAPIER, kept)
        self.assertNotIn(reworded, kept)
        self.assertIn(USUAL, kept)

    def test_an_excluded_cv_contributes_nothing_it_alone_had(self):
        cvs = {"draft": make_cv(entries=[("xPathEdge LLC", [ZAPIER, USUAL])])}
        decisions = library.Decisions()
        decisions.excluded.add("draft")
        kept = {l.text for l in library.usable(library.collect(BASE, cvs, {}), decisions)}
        self.assertNotIn(ZAPIER, kept)
        self.assertIn(USUAL, kept)                  # the base still has it

    def test_decisions_survive_a_save_and_a_bad_file(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "library.json"
            decisions = library.Decisions()
            decisions.struck.add(ZAPIER)
            decisions.labels[SPONSOR] = "authorisation"
            decisions.save(path)
            again = library.Decisions.load(path)
            self.assertEqual(again.struck, {ZAPIER})
            self.assertEqual(again.labels[SPONSOR], "authorisation")
            path.write_text("[not a mapping")
            self.assertEqual(library.Decisions.load(path).struck, set())


NOTES = """# My history

## xPathEdge

At xPathEdge I coordinated with client and internal teams and delivered 15+ web
applications.

## From the chat

### 2026-10-23

At xPathEdge I also worked with low-code and no-code tools, Zapier, n8n, Webflow
and Airtable, automating client onboarding forms.

### 2026-10-24 · his answer

I wrote SQL against live impression data at Amazon every week.
"""

SQL_LINE = "Wrote SQL against live impression data every week at Amazon."


class FakeSupport:
    """Stands in for jev.support; records every pair it is asked about."""

    def __init__(self, verdict="supports", confidence=0.95, error=None):
        self.asked = []
        self.verdict, self.confidence, self.error = verdict, confidence, error

    def __call__(self, pairs):
        self.asked.extend(pairs)
        if self.error:
            raise self.error
        return [(self.verdict, self.confidence) for _ in pairs]


def one(text, slot=("bullets", "xpathedge"), base=False):
    return library.Line(text=text, slot=slot, cvs=("a",), base=base)


class SupportTests(unittest.TestCase):
    def setUp(self):
        self.decisions = library.Decisions()

    def test_passages_carry_who_wrote_them(self):
        found = {p.trust for p in library.passages(NOTES)}
        self.assertEqual(found, {"notes", "legacy", "his"})

    def test_a_base_line_passes_without_a_check(self):
        support = FakeSupport(error=AssertionError("asked"))
        out = library.check([one(ZAPIER, base=True)], NOTES, self.decisions, support=support)
        self.assertEqual(out[ZAPIER], "pass")

    def test_a_line_the_notes_state_passes_in_code(self):
        support = FakeSupport(error=AssertionError("asked"))
        out = library.check([one(USUAL)], NOTES, self.decisions, support=support)
        self.assertEqual(out[USUAL], "pass")

    def test_his_own_answer_counts(self):
        support = FakeSupport(error=AssertionError("asked"))
        out = library.check([one(SQL_LINE, ("bullets", "amazon"))], NOTES, self.decisions,
                            support=support)
        self.assertEqual(out[SQL_LINE], "pass")

    def test_the_zapier_case_is_unconfirmed_not_passed(self):
        """The note that put Zapier in his notes was the assistant's; checking
        the line against it would only check that the two agree."""
        support = FakeSupport()
        out = library.check([one(ZAPIER)], NOTES, self.decisions, support=support)
        self.assertEqual(out[ZAPIER], "unconfirmed")
        self.assertEqual(support.asked, [])

    def test_a_confirmed_note_counts(self):
        legacy = [p for p in library.passages(NOTES) if p.trust == "legacy"][0]
        self.decisions.confirmed[library.passage_key(legacy.text)] = True
        out = library.check([one(ZAPIER)], NOTES, self.decisions,
                            support=FakeSupport(error=AssertionError("asked")))
        self.assertEqual(out[ZAPIER], "pass")

    def test_a_note_he_said_no_to_backs_nothing(self):
        legacy = [p for p in library.passages(NOTES) if p.trust == "legacy"][0]
        self.decisions.confirmed[library.passage_key(legacy.text)] = False
        out = library.check([one(ZAPIER)], NOTES, self.decisions, support=FakeSupport("says_nothing"))
        self.assertEqual(out[ZAPIER], "held")

    def test_a_named_tool_the_notes_do_not_name_goes_to_jev(self):
        line = "Coordinated with client and internal teams on Salesforce rollouts."
        support = FakeSupport("says_nothing")
        out = library.check([one(line)], NOTES, self.decisions, support=support)
        self.assertEqual(out[line], "held")
        self.assertEqual(len(support.asked), 1)

    def test_jev_support_below_the_threshold_holds_the_line(self):
        line = "Partnered with client teams on delivery of web apps."
        out = library.check([one(line)], NOTES, self.decisions,
                            support=FakeSupport("supports", 0.6))
        self.assertEqual(out[line], "held")

    SPLIT = ("## Amazon\n\nBuilt a Python CLI tool to keep stakeholder interviews in one place.\n\n"
             "## Results\n\nThe CLI tool cut interview prep time by 40%.\n")

    def test_a_line_backed_across_two_notes_passes(self):
        """The proof can sit on two cards: the tool in one, the number in another."""
        line = "Built a Python CLI tool that cut interview prep time by 40%."
        out = library.check([one(line)], self.SPLIT, self.decisions,
                            support=FakeSupport(error=AssertionError("asked")))
        self.assertEqual(out[line], "pass")

    def test_jev_reads_the_closest_notes_together(self):
        line = "Built a Python CLI tool in Rust that cut interview prep time by 40%."
        support = FakeSupport("says_nothing")
        library.check([one(line)], self.SPLIT, self.decisions, support=support)
        (asked_line, evidence), = support.asked
        self.assertIn("Python CLI tool", evidence)
        self.assertIn("40%", evidence)

    def test_a_tool_on_no_card_is_still_held(self):
        line = "Built a Python CLI tool on Zapier that cut interview prep time by 40%."
        out = library.check([one(line)], self.SPLIT, self.decisions,
                            support=FakeSupport("says_nothing"))
        self.assertEqual(out[line], "held")

    def test_a_result_is_reused_until_the_passage_changes(self):
        line = "Partnered with client teams on delivery of web apps."
        support = FakeSupport()
        library.check([one(line)], NOTES, self.decisions, support=support)
        library.check([one(line)], NOTES, self.decisions, support=support)
        self.assertEqual(len(support.asked), 1)
        changed = NOTES.replace("delivered 15+ web", "delivered 16+ web")
        library.check([one(line)], changed, self.decisions, support=support)
        self.assertEqual(len(support.asked), 2)

    def test_no_key_holds_back_without_failing(self):
        line = "Partnered with client teams on delivery of web apps."
        out = library.check([one(line)], NOTES, self.decisions,
                            support=FakeSupport(error=library.jev.JevError("no key")))
        self.assertEqual(out[line], "unchecked")

    def test_a_no_never_backs_the_line_it_denies(self):
        """His "I haven't" is filed as a no, and words that overlap a no are
        not support for the claim it denies."""
        line = "Automated client onboarding with Zapier and Airtable."
        for heading in ("### 2026-10-24 · his no", "### 2026-10-24 · his answer"):
            notes = ("## From the chat\n\n" + heading + "\n\n"
                     "I have not automated client onboarding with Zapier and Airtable.\n")
            with self.subTest(heading=heading):
                out = library.check([one(line)], notes, library.Decisions(),
                                    support=FakeSupport("contradicts"))
                self.assertNotEqual(out[line], "pass")
                self.assertNotEqual(out[line], "unconfirmed")

    def test_unconfirmed_lines_report_the_note_behind_them(self):
        evidence = {}
        library.check([one(ZAPIER)], NOTES, self.decisions, support=FakeSupport(),
                      evidence=evidence)
        self.assertIn("Zapier, n8n, Webflow", evidence[ZAPIER])

    def test_a_kept_line_passes(self):
        self.decisions.kept.add(ZAPIER)
        out = library.check([one(ZAPIER)], NOTES, self.decisions, support=FakeSupport())
        self.assertEqual(out[ZAPIER], "pass")


if __name__ == "__main__":
    unittest.main()


class GapTests(unittest.TestCase):
    """A gap is asked only when neither his notes nor a past CV answer it."""

    NOTES = ("## MKEL\n\nThe app we launched in rural Nigeria loaded slowly on 3G, so we "
             "added lazy loading and Redis caching.\n")
    MENTOR = "Mentored 3 software engineering interns at MKEL."

    def judge(self, yes):
        asked = []

        def answers(pairs):
            asked.extend(pairs)
            return [("yes", 0.95) if any(w in evidence for w in yes) else ("says_nothing", 0.9)
                    for _, evidence in pairs]
        answers.asked = asked
        return answers

    def test_a_gap_his_notes_answer_is_covered(self):
        out = library.answer_gaps(["Have you improved a slow app after launch, e.g. with lazy loading?"],
                                  self.NOTES, library.Decisions(), [],
                                  judge=self.judge(["Redis"]))
        self.assertEqual(out[0]["status"], "covered")

    def test_a_gap_a_past_cv_answers_quotes_the_line(self):
        lines = [one(self.MENTOR, ("bullets", "mkel"))]
        out = library.answer_gaps(["Have you mentored software engineering interns?"],
                                  self.NOTES, library.Decisions(), lines,
                                  judge=self.judge(["Mentored"]))
        self.assertEqual(out[0], {"question": "Have you mentored software engineering interns?",
                                  "status": "past", "line": self.MENTOR})

    def test_a_gap_nothing_answers_stays_open(self):
        out = library.answer_gaps(["Have you used React Native?"], self.NOTES,
                                  library.Decisions(), [one(self.MENTOR)], judge=self.judge([]))
        self.assertEqual(out[0]["status"], "open")

    def test_when_the_check_cannot_run_every_gap_is_asked(self):
        def down(pairs):
            raise library.jev.JevError("no key")
        out = library.answer_gaps(["Have you used Redis?"], self.NOTES, library.Decisions(),
                                  [], judge=down)
        self.assertEqual(out[0]["status"], "open")

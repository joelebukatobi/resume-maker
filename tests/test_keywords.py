"""One keyword list per posting, read once and counted literally.

The ring used to switch rulers without saying so, and the requirement list was
asked for again on every press, so it came back fifteen long, then twelve, then
ten. A score that moves when the document has not is not a measurement. The
list is now read once, kept beside the posting it came from, and counted by
plain code, because the applicant tracking system it imitates is literal.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resume_maker import applications, assistant, keywords, match  # noqa: E402


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_a_new_application_does_not_inherit_the_list(self):
        """A list is read from one posting. A copied one would count the next
        application against the last job."""
        self.assertIn("keywords", applications.FILES)
        self.assertNotIn("keywords", applications.COPIED)

    def test_the_list_belongs_to_the_posting_it_was_read_from(self):
        path = Path(self.tmp.name) / "keywords.json"
        keywords.save(path, posting="A  posting.", terms=["sql"], entries={})
        stored = keywords.load(path)
        self.assertEqual(stored["keywords"], ["sql"])
        self.assertTrue(keywords.is_current(stored, "A posting."))       # whitespace only
        self.assertFalse(keywords.is_current(stored, "A fuller posting."))

    def test_skips_are_kept_with_the_list(self):
        path = Path(self.tmp.name) / "keywords.json"
        keywords.save(path, posting="A posting.", terms=["sql"], entries={},
                      skipped=["sections.3.entries.0.bullets.1"])
        self.assertEqual(keywords.load(path)["skipped"], ["sections.3.entries.0.bullets.1"])
        keywords.save(path, posting="A posting.", terms=["sql"], entries={})
        self.assertEqual(keywords.load(path)["skipped"], [])

    def test_a_missing_file_is_an_empty_list(self):
        stored = keywords.load(Path(self.tmp.name) / "nope.json")
        self.assertEqual(stored["keywords"], [])
        self.assertFalse(keywords.is_current(stored, "A posting."))


class RuleTests(unittest.TestCase):
    def test_phrases_are_kept_as_the_posting_wrote_them(self):
        """"compliance-first mindset" cut down to "compliance" throws away what
        they asked for, and a phrase that does not suit a bullet may still be
        answered in the letter."""
        rules = assistant.keywords_prompt()
        self.assertIn("exactly as the posting writes it", rules)
        self.assertNotIn("Two or three words at most", rules)
        self.assertIn("stakeholder management", rules)     # the craft terms of a role

    def test_the_company_and_generic_words_never_reach_the_list(self):
        reply = ('{"keywords": ["Flexport tooling", "communication",'
                 ' "compliance-first mindset", "sql"]}')
        self.assertEqual(assistant.parse_keywords(reply, company="Flexport"),
                         ["compliance-first mindset", "sql"])

    def test_a_generic_word_inside_a_phrase_is_kept(self):
        """"organization" alone says nothing; "organization design" is a skill."""
        reply = '{"keywords": ["organization design", "organization"]}'
        self.assertEqual(assistant.parse_keywords(reply), ["organization design"])


class CountTests(unittest.TestCase):
    """Literal, because the ATS is. But a phrase is its words, not their order."""

    def test_a_phrase_counts_when_its_words_share_a_line(self):
        """The CV said "low code" and the list asked for "low-code tooling",
        and the exact-phrase count could not see it."""
        m = match.score_against(["Built onboarding tooling on low code platforms."],
                                ["low-code tooling"])
        self.assertEqual(m.matched, ("low-code tooling",))

    def test_words_scattered_across_lines_do_not_count(self):
        m = match.score_against(["Used low code platforms.", "Wrote internal tooling."],
                                ["low-code tooling"])
        self.assertEqual(m.missing, ("low-code tooling",))

    def test_slashes_and_hyphens_split_as_the_cv_does(self):
        m = match.score_against(["Ran A/B testing and CI/CD."], ["a/b testing", "ci/cd"])
        self.assertEqual(m.score, 100)

    def test_a_slash_pair_is_its_two_words(self):
        """The split leaves a "/" token between longer pairs. Counted as a word,
        it made "React and TypeScript" miss "react/typescript", and any other
        slash in the same line complete it."""
        self.assertEqual(match.score_against(["Built React and TypeScript apps."],
                                             ["react/typescript"]).score, 100)
        self.assertEqual(match.score_against(["Built Node/Express services."],
                                             ["react/typescript"]).score, 0)

    def test_a_term_that_counts_as_nothing_does_not_shift_the_rest(self):
        """A term that splits to nothing used to shift every keyword after it
        onto the wrong one, so "sql" vanished from the count."""
        m = match.score_against(["Wrote SQL."], ["24/7", "sql"])
        self.assertIn("sql", m.matched)


class MapTests(unittest.TestCase):
    """Where in the notes each keyword is answered, and how to say it."""

    def test_every_line_traces_to_the_notes(self):
        rules = assistant.map_prompt()
        self.assertIn("quote", rules)
        self.assertIn("never invent", rules.lower())
        self.assertIn("declined", rules)            # an explicit no in the notes
        self.assertIn("even if they never used the word", rules)

    def test_an_answer_without_a_source_is_not_an_answer(self):
        """A "say" line with no "from" is a sentence nobody can defend."""
        reply = '{"sql": {"state": "answered", "from": "", "say": "Wrote SQL."}}'
        self.assertEqual(assistant.parse_map(reply, ["sql", "go"]),
                         {"sql": {"state": "unanswered", "from": "", "say": ""},
                          "go": {"state": "unanswered", "from": "", "say": ""}})

    def test_an_unknown_state_is_unanswered_and_a_no_is_kept(self):
        reply = ('{"sql": {"state": "maybe", "from": "x", "say": "y"},'
                 ' "customs regulations": {"state": "declined", "from": "", "say": ""}}')
        got = assistant.parse_map(reply, ["sql", "customs regulations"])
        self.assertEqual(got["sql"]["state"], "unanswered")
        self.assertEqual(got["customs regulations"]["state"], "declined")

    def test_the_request_carries_the_list_and_the_notes(self):
        asked = assistant.map_request(["sql", "account management"], "# Notes\nRan rate sheets.")
        self.assertIn("account management", asked)
        self.assertIn("Ran rate sheets.", asked)

    def test_the_prompt_names_each_keyword_with_its_answer(self):
        note = assistant.map_note({"keywords": ["sql", "go"], "map": {
            "sql": {"state": "answered", "from": "MKEL rate sheets",
                    "say": "Queried rate data in SQL."},
            "go": {"state": "declined", "from": "", "say": ""}}})
        self.assertIn("Queried rate data in SQL.", note)
        self.assertIn("go", note)
        self.assertIn("declined", note)
        self.assertEqual(assistant.map_note({"keywords": [], "map": {}}), "")

    def test_a_keyword_already_on_the_page_is_not_worked_on(self):
        """What the user hit: SQL was in his skills line, but his notes do not
        mention it, so the map said unanswered and the round read that as
        missing and started there. On the page is one fact, in the notes is
        another, and the round works only on what is missing from the page."""
        stored = {"keywords": ["sql", "go"], "map": {
            "sql": {"state": "unanswered", "from": "", "say": ""},
            "go": {"state": "unanswered", "from": "", "say": ""}}}
        note = assistant.map_note(stored, on_page=["sql"])
        self.assertIn("- sql already on the CV", note)
        self.assertNotIn("sql unanswered", note)
        self.assertIn("- go unanswered", note)
        wording = assistant.tailor_rules("wording")
        self.assertIn("already on the CV", wording)


if __name__ == "__main__":
    unittest.main()

"""A chat card's text goes into the section it belongs in, adding only."""

import json
import unittest
from unittest import mock

from resume_maker import place

NOTES = """# HOW I GOT HERE

## Amazon

At Amazon I wrote epics and user stories. The BRD went through review.

## Skills

- Product: Jira

## From the chat

### 2026-11-05 · suggested by the assistant

Old entry.
"""


class SectionTests(unittest.TestCase):
    def test_skills_and_the_old_log_are_not_offered(self):
        self.assertEqual(list(place.sections(NOTES)), ["# HOW I GOT HERE", "## Amazon"])

    def test_adding_a_sentence_or_continuing_one_is_add_only(self):
        before = "At Amazon I wrote epics and user stories. The BRD went through review."
        self.assertTrue(place.add_only(before, before + " Directors read it."))
        self.assertTrue(place.add_only(before, before[:-1] + " three or four times."))

    def test_rewording_or_dropping_is_not(self):
        before = "At Amazon I wrote epics and user stories. The BRD went through review."
        self.assertFalse(place.add_only(before, "At Amazon I wrote user stories. The BRD went through review."))
        self.assertFalse(place.add_only(before, "The BRD went through review."))


class SnippetTests(unittest.TestCase):
    def test_a_long_unchanged_stretch_is_cut_between_words(self):
        before = "start " + "lorem ipsum " * 40 + "end."
        segs = place.segments(before, before + " New bit.")
        self.assertTrue(segs[0]["text"].startswith("… "))
        self.assertIn(segs[0]["text"].split()[1], {"lorem", "ipsum"})


class ProposeTests(unittest.TestCase):
    def propose(self, *replies):
        with mock.patch.object(place.llm, "complete", side_effect=list(replies)):
            return place.propose("notes", NOTES, "Directors read it.", "deepseek", "m")

    AFTER = "At Amazon I wrote epics and user stories. The BRD went through review. Directors read it."

    def test_a_good_placement_comes_back_with_the_addition_marked(self):
        out = self.propose(json.dumps({"heading": "## Amazon", "section": self.AFTER}))
        self.assertEqual(out["heading"], "## Amazon")
        self.assertEqual([p["text"] for p in out["segments"] if p["added"]], [" Directors read it."])

    def test_a_rewrite_is_retried_then_refused(self):
        bad = json.dumps({"heading": "## Amazon", "section": "I wrote stories. Directors read it."})
        with self.assertRaises(place.PlaceError):
            self.propose(bad, bad)

    def test_saving_writes_the_section_in_place_and_leaves_the_rest(self):
        out = self.propose(json.dumps({"heading": "## Amazon", "section": self.AFTER}))
        text = place.checked(NOTES, out["heading"], out["before"], out["after"])
        self.assertIn("## Amazon\n\n" + self.AFTER + "\n\n## Skills", text)
        self.assertIn("Old entry.", text)
        self.assertNotIn("2026-11-10", text)

    def test_saving_refuses_if_the_section_changed_meanwhile(self):
        with self.assertRaises(place.PlaceError):
            place.checked(NOTES.replace("review.", "review twice."), "## Amazon",
                          "At Amazon I wrote epics and user stories. The BRD went through review.",
                          self.AFTER)


if __name__ == "__main__":
    unittest.main()

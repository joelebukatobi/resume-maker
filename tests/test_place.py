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
    GOOD = json.dumps({"heading": "## Amazon", "after": "The BRD went through review.",
                       "text": "Directors read it."})

    def test_a_good_placement_comes_back_with_the_addition_marked(self):
        out = self.propose(self.GOOD)
        self.assertEqual(out["heading"], "## Amazon")
        self.assertEqual([p["text"] for p in out["segments"] if p["added"]], [" Directors read it."])

    def test_an_anchor_not_in_the_section_is_retried_then_refused(self):
        bad = json.dumps({"heading": "## Amazon", "after": "I led the team.", "text": "Directors read it."})
        with self.assertRaises(place.PlaceError):
            self.propose(bad, bad)

    def test_saving_writes_the_section_in_place_and_leaves_the_rest(self):
        out = self.propose(self.GOOD)
        text = place.checked(NOTES, out["heading"], out["before"], out["after"])
        self.assertIn("## Amazon\n\n" + self.AFTER + "\n\n## Skills", text)
        self.assertIn("Old entry.", text)
        self.assertNotIn("2026-11-10", text)

    def test_saving_refuses_if_the_section_changed_meanwhile(self):
        with self.assertRaises(place.PlaceError):
            place.checked(NOTES.replace("review.", "review twice."), "## Amazon",
                          "At Amazon I wrote epics and user stories. The BRD went through review.",
                          self.AFTER)


class QuickModelTests(unittest.TestCase):
    REPLY = json.dumps({"heading": "## Amazon", "after": "", "text": "Directors read it."})

    def models_used(self, env):
        with mock.patch.dict(place.os.environ, env, clear=False), \
             mock.patch.object(place.llm, "complete", return_value=self.REPLY) as call:
            place.propose("notes", NOTES, "x", "deepseek", "deepseek-v4-pro")
            place.propose("notes", NOTES, "x", "openai", "gpt-big")
        return [c.args[1] for c in call.call_args_list]

    def test_by_default_placement_uses_the_page_model(self):
        with mock.patch.dict(place.os.environ, {}, clear=True):
            self.assertEqual(self.models_used({}), ["deepseek-v4-pro", "gpt-big"])

    def test_a_local_setting_swaps_one_providers_model(self):
        used = self.models_used({"RESUME_MAKER_PLACE_MODELS": "deepseek=deepseek-flash"})
        self.assertEqual(used, ["deepseek-flash", "gpt-big"])


class InsertTests(unittest.TestCase):
    LIST = "Intro line.\n\n- First item that wraps\n  onto a second line with **Cypress**.\n- Second item."

    def test_a_list_item_goes_after_the_whole_item_it_follows(self):
        out = place.insert(self.LIST, "First item that wraps onto a second line with Cypress.", "- New item.")
        self.assertEqual(out, "Intro line.\n\n- First item that wraps\n  onto a second line with"
                              " **Cypress**.\n- New item.\n- Second item.")

    def test_prose_continues_the_paragraph_and_keeps_every_word(self):
        out = place.insert(self.LIST, "Intro line.", "More intro.")
        self.assertTrue(out.startswith("Intro line. More intro.\n\n- First"))
        self.assertTrue(place.add_only(self.LIST, out))

    def test_no_anchor_adds_at_the_end(self):
        self.assertTrue(place.insert(self.LIST, "", "Closing.").endswith("Second item.\n\nClosing."))


if __name__ == "__main__":
    unittest.main()

"""Evidence by meaning, judged by his model: a yes must quote the evidence."""

import unittest
from unittest import mock

from resume_maker import evidence, library

AMAZON = "At Amazon I wrote **epics and user stories** to break the work down."
REACT = "I have started picking up React Native but have not built anything with it yet."


class QuoteTests(unittest.TestCase):
    def test_a_quote_from_the_evidence_counts(self):
        self.assertTrue(evidence.quoted("I wrote epics and user stories", AMAZON))
        self.assertTrue(evidence.quoted("At Amazon ... user stories", AMAZON))

    def test_a_quote_not_in_the_evidence_does_not(self):
        self.assertFalse(evidence.quoted("I built React Native apps", REACT))
        self.assertFalse(evidence.quoted("", AMAZON))


class JudgeTests(unittest.TestCase):
    def run_judge(self, reply, pairs):
        with mock.patch.object(evidence.llm, "complete", return_value=reply):
            return evidence.judge("deepseek", "deepseek-v4-pro")(pairs)

    def test_yes_with_a_real_quote_is_yes(self):
        out = self.run_judge('[{"i": 0, "verdict": "yes", "quote": "wrote epics and user stories"}]',
                             [("Do you have experience with story writing?", AMAZON)])
        self.assertEqual(out, [("yes", 1.0)])

    def test_yes_with_an_invented_quote_is_not(self):
        out = self.run_judge('[{"i": 0, "verdict": "yes", "quote": "built two React Native apps"}]',
                             [("Have you used React Native?", REACT)])
        self.assertEqual(out[0][0], "says_nothing")

    def test_a_reply_that_is_not_a_list_cannot_judge(self):
        with self.assertRaises(evidence.JudgeError):
            self.run_judge("I think so.", [("q", AMAZON)])

    def test_a_judge_that_fails_leaves_every_gap_open(self):
        def down(pairs):
            raise evidence.JudgeError("model unavailable")
        out = library.answer_gaps(["Have you written user stories?"], "## Amazon\n\n" + AMAZON + "\n",
                                  library.Decisions(), [], judge=down)
        self.assertEqual(out[0]["status"], "open")


class ShortlistTests(unittest.TestCase):
    FOUND = [library.Passage("Operations, support and servers", "notes"),
             library.Passage(AMAZON, "notes"),
             library.Passage(REACT, "notes")]

    def test_without_the_embedding_model_word_search_is_used(self):
        with mock.patch.object(evidence, "nearest", return_value=None):
            out = library.shortlist(self.FOUND, "Have you written user stories?", 2)
        self.assertIn(self.FOUND[1], out)

    def test_with_it_meaning_comes_first_and_word_search_still_adds(self):
        with mock.patch.object(evidence, "nearest", return_value=[2, 0]):
            out = library.shortlist(self.FOUND, "Have you written user stories?", 2)
        self.assertEqual(out, [self.FOUND[2], self.FOUND[0], self.FOUND[1]])

    def test_the_question_words_are_not_searched(self):
        self.assertEqual(evidence.topic("Have you done market research?"), "market research")
        self.assertEqual(evidence.topic("Do you have troubleshooting experience?"), "troubleshooting")


if __name__ == "__main__":
    unittest.main()


class InferredBulletTests(unittest.TestCase):
    """Work History may infer a bullet within the user's boundary; nothing else may."""

    def test_only_the_bullet_turns_allow_inference(self):
        from resume_maker import assistant
        for phase in ("bullets", "bullets_all"):
            rules = assistant.tailor_rules(phase)
            self.assertIn('"inferred"', rules)
            self.assertIn("Never infer a number", rules)
        for phase in ("summary", "skills", "additional"):
            self.assertNotIn("you\nmay infer a bullet", assistant.tailor_rules(phase))
            self.assertNotIn("may infer a bullet", assistant.tailor_rules(phase))

    def test_review_asks_about_each_inferred_bullet(self):
        from pathlib import Path
        ui = Path(__file__).resolve().parent.parent / "static" / "ui"
        self.assertIn("addInferred", (ui / "step-bullets.js").read_text())
        review = (ui / "step-review.js").read_text()
        self.assertIn("inferredRow", review)
        self.assertIn("I inferred this", review)

"""Reading the notes back against what the user actually said."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resume_maker import audit  # noqa: E402

NOTES = """\
# How I got here

## Before there was a career

I pieced together one working computer out of the damaged ones when I was eleven.

## The US, Fordham, Amazon

The brief was one line, "expand the dataset", and nothing about what was failing.

I ran discovery across teams in the UK, Mexico and India.

## Still to confirm

- Whether Cedric is spelled that way.
"""


class ClaimTests(unittest.TestCase):
    def test_claims_carry_their_section(self):
        found = audit.claims(NOTES)
        sections = {c.section for c in found}
        self.assertIn("Before there was a career", sections)
        self.assertIn("The US, Fordham, Amazon", sections)

    def test_open_questions_are_not_claims(self):
        """"Still to confirm" is a list of things known to be unconfirmed."""
        self.assertFalse([c for c in audit.claims(NOTES) if "Cedric" in c.text])

    def test_headings_and_short_lines_are_not_claims(self):
        for claim in audit.claims(NOTES):
            self.assertFalse(claim.text.startswith("#"))
            self.assertGreaterEqual(len(claim.text), audit.MIN_CLAIM_CHARS)

    def test_markdown_is_stripped_back_to_the_sentence(self):
        claim = audit.claims("## X\n\nI **owned** the `dataset` for over 600 teams here.")[0]
        self.assertEqual(claim.text, "I owned the dataset for over 600 teams here.")


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="audit-test-"))
        app = self.tmp / "applications" / "acme-pm"
        app.mkdir(parents=True)
        (app / "chat.json").write_text(json.dumps({"messages": [
            {"role": "user", "content": "Not sure where you got the idea it was a "
                                        "one line brief. It was a whole project plan, "
                                        "neck deep in ambiguity, with no next steps."},
            {"role": "assistant", "content": "Understood, I will not say one line brief."},
        ]}))

    def test_only_the_user_is_evidence(self):
        """The assistant's words are not the user's, and cannot support a claim."""
        found = audit.passages(self.tmp)
        self.assertTrue(any("neck deep in ambiguity" in p for p in found))
        self.assertFalse(any("Understood" in p for p in found))

    def test_a_claim_finds_the_thing_that_contradicts_it(self):
        """Retrieval is about relevance, not agreement.

        A correction usually quotes what it is correcting, so the wrong claim
        and the passage that disproves it share vocabulary and end up side by
        side in front of the model.
        """
        index = [(audit._terms(p), p) for p in audit.passages(self.tmp)]
        claim = audit.Claim("Amazon", 'The brief was one line, "expand the dataset".')
        near = audit.nearest(claim, index)
        self.assertTrue(near)
        self.assertIn("whole project plan", near[0])


    def test_a_claim_sharing_no_words_with_anything_gets_no_evidence(self):
        """The limit of matching on words.

        Retrieval pairs a claim with passages that share its vocabulary. Say
        the same thing in entirely different words and nothing is found, so
        the claim reaches the model bare. That is visible in the output rather
        than silent, and it is why the model, not the matching, gives the
        verdict.
        """
        index = [(audit._terms(p), p) for p in audit.passages(self.tmp)]
        bare = audit.Claim("Elsewhere", "I taught myself to solder circuit boards at school.")
        self.assertEqual(audit.nearest(bare, index), ())


class PassageShapeTests(unittest.TestCase):
    def test_a_long_message_is_windowed_not_truncated(self):
        """A pasted transcript is one message with few blank lines in it.

        Truncating it to a paragraph threw away most of what the user had
        ever said, and the audit then reported their own history as
        unsupported.
        """
        story = ("I fixed the computers. " * 60) + "I ran Sabayon on an Atom netbook."
        windows = list(audit._windows(story))
        self.assertGreater(len(windows), 1)
        self.assertTrue(any("Sabayon" in w for w in windows),
                        "the end of a long message must stay reachable")

    def test_pasted_reference_material_is_not_testimony(self):
        docs = ("| Language | Client | |---|---| | Python | `from anthropic import "
                "Anthropic` | const x = 1; https://docs.example.com/api")
        self.assertFalse(audit._is_prose(docs))
        self.assertTrue(audit._is_prose(
            "I ran discovery across teams in the UK, Mexico and India for that dataset."))


class VerdictTests(unittest.TestCase):
    FINDINGS = (
        audit.Finding(audit.Claim("Amazon", "The brief was one line."), ("It was a plan.",)),
        audit.Finding(audit.Claim("Amazon", "I ran discovery in three regions."), ("UK, Mexico, India.",)),
    )

    def test_a_verdict_maps_back_to_the_claim_it_judged(self):
        flagged = audit.parse_answer(
            '{"unsupported": [{"n": 1, "why": "they said a whole project plan"}]}',
            self.FINDINGS)
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0][0].claim.text, "The brief was one line.")
        self.assertEqual(flagged[0][1], "they said a whole project plan")

    def test_a_number_that_is_not_a_claim_is_ignored(self):
        """The reply is untrusted: an index out of range names nothing."""
        self.assertEqual(
            audit.parse_answer('{"unsupported": [{"n": 99}, {"n": 0}, "junk"]}', self.FINDINGS),
            [])

    def test_a_clean_run_is_an_empty_list_not_a_failure(self):
        self.assertEqual(audit.parse_answer('{"unsupported": []}', self.FINDINGS), [])

    def test_a_reply_that_is_not_a_verdict_is_refused(self):
        with self.assertRaises(ValueError):
            audit.parse_answer("I had a look and it all seems fine", self.FINDINGS)

    def test_every_claim_reaches_the_model_numbered(self):
        asked = audit.question(self.FINDINGS)
        self.assertIn("[1] Claim", asked)
        self.assertIn("[2] Claim", asked)
        self.assertIn("It was a plan.", asked)


if __name__ == "__main__":
    unittest.main()

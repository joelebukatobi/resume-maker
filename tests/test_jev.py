"""Jev, asked over plain HTTP whether his notes back a line.

Jev answers with a choice and a probability, not prose, which is all the
support check needs and costs far less than asking Claude to read his notes.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resume_maker import jev  # noqa: E402


class FakeOpener:
    """Records each request and answers every question with ``verdict``."""

    def __init__(self, verdict="supports", confidence=0.95):
        self.requests = []
        self.verdict, self.confidence = verdict, confidence

    def __call__(self, request, timeout=None):
        body = json.loads(request.data)
        self.requests.append((request, body))
        answers = {name: {"type": "choice", "choice": self.verdict,
                          "confidence": self.confidence,
                          "probabilities": {self.verdict: self.confidence}}
                   for name in body["questions"]}
        return io.BytesIO(json.dumps({"model": "jev", "answers": answers}).encode())


class AskTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "k-secret-123"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_key_goes_in_the_header_and_nowhere_else(self):
        opener = FakeOpener()
        jev.support([("line", "passage")], opener=opener)
        request, body = opener.requests[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer k-secret-123")
        self.assertNotIn("k-secret-123", json.dumps(body))
        self.assertEqual(request.full_url, "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(body["model"], "jev-latest")

    def test_twenty_five_pairs_are_two_requests(self):
        opener = FakeOpener()
        out = jev.support([(f"line {i}", "p") for i in range(25)], opener=opener)
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(len(out), 25)

    def test_answers_come_back_in_the_order_asked(self):
        def opener(request, timeout=None):
            body = json.loads(request.data)
            answers = {}
            for name in body["questions"]:
                i = int(name[1:])
                line = body["state"]["checks"][i]["line"]
                verdict = "supports" if line == "yes" else "says_nothing"
                answers[name] = {"type": "choice", "choice": verdict, "confidence": 0.9}
            return io.BytesIO(json.dumps({"answers": answers}).encode())
        out = jev.support([("yes", "p"), ("no", "p"), ("yes", "p")], opener=opener)
        self.assertEqual([v for v, _ in out], ["supports", "says_nothing", "supports"])

    def test_labels_come_back_one_per_line(self):
        out = jev.label(["a", "b"], opener=FakeOpener("interests"))
        self.assertEqual(out, ["interests", "interests"])

    def test_a_gap_question_is_judged_against_its_evidence(self):
        opener = FakeOpener("yes", 0.9)
        out = jev.answers([("Have you mentored?", "Mentored 3 interns.")], opener=opener)
        self.assertEqual(out, [("yes", 0.9)])
        check = opener.requests[0][1]["state"]["checks"][0]
        self.assertEqual(check, {"question": "Have you mentored?", "evidence": "Mentored 3 interns."})

    def test_an_http_error_is_a_jev_error(self):
        def opener(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 422, "bad",
                                         {}, io.BytesIO(b'{"detail": "bad field"}'))
        with self.assertRaises(jev.JevError):
            jev.support([("l", "p")], opener=opener)

    def test_a_network_failure_is_a_jev_error(self):
        def opener(request, timeout=None):
            raise urllib.error.URLError("down")
        with self.assertRaises(jev.JevError):
            jev.support([("l", "p")], opener=opener)


class NoKeyTests(unittest.TestCase):
    def setUp(self):
        # Never the real ~/.config: a key saved there would answer instead.
        self.config = tempfile.TemporaryDirectory()
        self.addCleanup(self.config.cleanup)

    def test_no_key_is_a_plain_error(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.config.name}, clear=True):
            with self.assertRaises(jev.JevError) as caught:
                jev.support([("l", "p")], opener=FakeOpener())
        self.assertIn("TYPESAFE_API_KEY", str(caught.exception))

    def test_nothing_to_ask_asks_nothing(self):
        opener = FakeOpener()
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.config.name}, clear=True):
            self.assertEqual(jev.support([], opener=opener), [])
        self.assertEqual(opener.requests, [])

    def test_a_key_saved_in_the_dialog_wins_over_the_environment(self):
        env = {"XDG_CONFIG_HOME": self.config.name, "TYPESAFE_API_KEY": "env-key-12345678"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(jev.key(), ("env-key-12345678", "env"))
            jev.connect("saved-key-12345678", opener=FakeOpener())
            self.assertEqual(jev.key(), ("saved-key-12345678", "saved"))


if __name__ == "__main__":
    unittest.main()

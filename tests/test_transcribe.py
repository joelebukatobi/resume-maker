"""Local dictation: finding a Whisper engine and running it on a WAV."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resume_maker import transcribe  # noqa: E402


class TranscribeTests(unittest.TestCase):
    def test_no_engine_is_reported_rather_than_guessed(self):
        with mock.patch.dict(os.environ, {"RESUME_WHISPER_CMD": "", "RESUME_WHISPER_MODEL": ""}), \
                mock.patch.object(transcribe.shutil, "which", return_value=None), \
                mock.patch.object(transcribe, "MODEL_DIRS", ()):
            self.assertFalse(transcribe.available())
            with self.assertRaises(transcribe.TranscriptionFailed):
                transcribe.transcribe(b"RIFF")

    def test_a_local_engine_is_run_and_its_text_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = Path(tmp) / "whisper-cli"
            runner.write_text("#!/bin/sh\necho 'hello from whisper'\n")
            runner.chmod(runner.stat().st_mode | stat.S_IEXEC)
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"x")
            with mock.patch.dict(os.environ, {"RESUME_WHISPER_CMD": str(runner),
                                              "RESUME_WHISPER_MODEL": str(model)}):
                self.assertTrue(transcribe.available())
                self.assertEqual(transcribe.transcribe(b"RIFF...."), "hello from whisper")

    def test_no_audio_is_refused_before_running_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = Path(tmp) / "whisper-cli"
            runner.write_text("#!/bin/sh\necho nope\n")
            runner.chmod(runner.stat().st_mode | stat.S_IEXEC)
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"x")
            with mock.patch.dict(os.environ, {"RESUME_WHISPER_CMD": str(runner),
                                              "RESUME_WHISPER_MODEL": str(model)}):
                with self.assertRaises(transcribe.TranscriptionFailed):
                    transcribe.transcribe(b"")


if __name__ == "__main__":
    unittest.main()

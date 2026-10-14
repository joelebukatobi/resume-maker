"""Local speech-to-text, when a Whisper engine is on the machine.

The chat's dictation button records audio in the browser and posts a 16 kHz
WAV here. Nothing goes to the cloud: a locally installed whisper.cpp binary
does the transcription, so the same button works in every browser — including
ones with no Google speech service, like Vivaldi or Brave.

If no engine is installed, :func:`available` is false, the endpoint says so
plainly, and the editor keeps using the browser's own speech recognition.
Neither a Python package nor ffmpeg is needed: the browser hands over a WAV the
engine can read directly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# Overrides, so a user can point at a build we cannot guess.
CMD_ENV = "RESUME_WHISPER_CMD"
MODEL_ENV = "RESUME_WHISPER_MODEL"
# The names whisper.cpp ships its runner under, newest first.
BINARIES = ("whisper-cli", "whisper-cpp", "main")
# Models we look for, best-for-dictation first, by their usual file names.
MODELS = ("ggml-base.en.bin", "ggml-base.bin", "ggml-small.en.bin", "ggml-small.bin")
# The places a whisper.cpp checkout or package keeps its models.
MODEL_DIRS = (
    "~/.cache/whisper.cpp",
    "~/.local/share/whisper.cpp",
    "~/.local/share/whisper.cpp/models",
    "~/whisper.cpp/models",
    "/opt/homebrew/share/whisper.cpp/models",
    "/usr/local/share/whisper.cpp/models",
)
TIMEOUT = 120


class TranscriptionFailed(ValueError):
    """No local engine is installed, or it could not read the audio."""


def binary() -> str:
    """The whisper.cpp runner, from the override or the PATH, or ""."""
    override = os.environ.get(CMD_ENV, "").strip()
    if override:
        return override
    for name in BINARIES:
        found = shutil.which(name)
        if found:
            return found
    return ""


def model() -> str:
    """A model file, from the override or a known directory, or ""."""
    override = os.environ.get(MODEL_ENV, "").strip()
    if override:
        return override
    for directory in MODEL_DIRS:
        base = Path(directory).expanduser()
        for name in MODELS:
            candidate = base / name
            if candidate.is_file():
                return str(candidate)
    return ""


def available() -> bool:
    """Whether dictation can run locally, so the editor can choose the path."""
    return bool(binary() and model())


def transcribe(wav: bytes) -> str:
    """The words in ``wav`` (16 kHz mono PCM), transcribed on this machine.

    Raises :class:`TranscriptionFailed` with a readable message — no engine, no
    audio, or the engine refusing — so the endpoint can answer plainly.
    """
    cmd, mdl = binary(), model()
    if not cmd or not mdl:
        raise TranscriptionFailed("no local speech engine found")
    if not wav:
        raise TranscriptionFailed("no audio to transcribe")
    with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
        handle.write(wav)
        handle.flush()
        try:
            result = subprocess.run(
                [cmd, "-m", mdl, "-f", handle.name, "-nt"],
                capture_output=True, timeout=TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise TranscriptionFailed("the speech engine timed out") from exc
        except OSError as exc:
            raise TranscriptionFailed(f"could not run the speech engine: {exc}") from exc
    if result.returncode != 0:
        lines = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise TranscriptionFailed(lines[-1] if lines else "the speech engine failed")
    return " ".join(result.stdout.decode("utf-8", "replace").split())

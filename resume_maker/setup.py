"""Readiness of the setup pillars, and validation of what the user saves.

The hub shows three tiles — Voice (``voice.md``), Notes (``notes.md``) and
Documents (``cv.json`` / ``cover.json``) — each reporting ``empty``,
``started`` or ``ready``. That judgement lives here rather than in the request
handler so it can be tested directly and reused by any page.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

# A file past this is substantial enough to call "ready"; below it the user
# has begun but the model still has little to work with.
READY_CHARS = 400
MAX_VOICE_SAVE = 40_000
MAX_NOTES_SAVE = 400_000

_LIMITS = {"voice": MAX_VOICE_SAVE, "notes": MAX_NOTES_SAVE}

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
# Offered in this order on the Style page; "other" last as the fallback.
ROLES: tuple[tuple[str, str], ...] = (
    ("pm", "Product Manager"),
    ("swe", "Software Engineer"),
    ("consultant", "Consultant"),
    ("other", "Other"),
)

State = str  # "empty" | "started" | "ready"


def text_state(text: str) -> State:
    """How far along a markdown pillar is."""
    stripped = (text or "").strip()
    if not stripped:
        return "empty"
    return "ready" if len(stripped) >= READY_CHARS else "started"


def cv_state(cv: Mapping[str, Any] | None) -> State:
    """Empty until there are sections; ready once a section carries content."""
    sections = (cv or {}).get("sections") or []
    if not sections:
        return "empty"
    for section in sections:
        if not isinstance(section, Mapping):
            continue
        if (section.get("text") or "").strip() or section.get("entries") or section.get("items"):
            return "ready"
    return "started"


def cover_state(cover: Mapping[str, Any] | None) -> State:
    """Empty with no body; ready once the letter has real paragraphs."""
    paragraphs = [p for p in ((cover or {}).get("paragraphs") or []) if isinstance(p, str) and p.strip()]
    if not paragraphs:
        return "empty"
    return "ready" if sum(len(p) for p in paragraphs) >= READY_CHARS else "started"


def readiness(
    voice: str,
    notes: str,
    cv: Mapping[str, Any] | None,
    cover: Mapping[str, Any] | None,
) -> dict[str, State]:
    """One state per hub tile."""
    return {
        "voice": text_state(voice),
        "notes": text_state(notes),
        "cv": cv_state(cv),
        "cover": cover_state(cover),
    }


def templates() -> list[dict[str, str]]:
    """Role starter text for the Voice page, skipping any missing file."""
    found = []
    for role_id, label in ROLES:
        path = TEMPLATE_DIR / f"voice-{role_id}.md"
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        found.append({"id": role_id, "label": label, "text": text})
    return found


APPENDED_HEADING = "## From the chat"


def append_text(current: str, addition: Any, *, today: str, kind: str = "notes") -> str:
    """``current`` with ``addition`` filed under a dated note at the end.

    Everything the chat contributes lands in one place rather than being
    guessed into the middle of a story the candidate wrote themselves. Moving
    it to the right section afterwards is a two-second edit; a wrong guess in
    the middle of someone's own words is not. ``kind`` picks which size limit
    the result is checked against — Notes or the Voice file.
    """
    if not isinstance(addition, str) or not addition.strip():
        raise ValueError("nothing to add")
    body = addition.strip()
    parts = [current.rstrip()] if current.strip() else []
    if APPENDED_HEADING not in current:
        parts.append(APPENDED_HEADING)
    parts.append(f"### {today}\n\n{body}")
    return check_text(kind, "\n\n".join(parts))


def check_text(kind: str, text: Any) -> str:
    """Validate a save; returns the text to write or raises ``ValueError``."""
    limit = _LIMITS.get(kind)
    if limit is None:
        raise ValueError(f"unknown setup file: {kind}")
    if not isinstance(text, str):
        raise ValueError(f"{kind} must be text")
    stripped = text.strip()
    if len(stripped) > limit:
        raise ValueError(f"{kind} is longer than {limit:,} characters")
    return stripped

"""Readiness of the setup pillars, and validation of what the user saves.

The hub shows three tiles — Style (``assistant.md``), Notes (``notes.md``) and
Documents (``cv.json`` / ``cover.json``) — each reporting ``empty``,
``started`` or ``ready``. That judgement lives here rather than in the request
handler so it can be tested directly and reused by any page.
"""

from __future__ import annotations

from typing import Any, Mapping

# A file past this is substantial enough to call "ready"; below it the user
# has begun but the model still has little to work with.
READY_CHARS = 400
MAX_ASSISTANT_SAVE = 40_000
MAX_NOTES_SAVE = 400_000

_LIMITS = {"assistant": MAX_ASSISTANT_SAVE, "notes": MAX_NOTES_SAVE}

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
    assistant: str,
    notes: str,
    cv: Mapping[str, Any] | None,
    cover: Mapping[str, Any] | None,
) -> dict[str, State]:
    """One state per hub tile."""
    return {
        "style": text_state(assistant),
        "notes": text_state(notes),
        "cv": cv_state(cv),
        "cover": cover_state(cover),
    }


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

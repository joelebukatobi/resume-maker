"""Readiness of the setup pillars, and validation of what the user saves.

The hub shows three tiles — Voice (``voice.md``), Notes (``notes.md``) and
Documents (``cv.json`` / ``cover.json``) — each reporting ``empty``,
``started`` or ``ready``. That judgement lives here rather than in the request
handler so it can be tested directly and reused by any page.
"""

from __future__ import annotations

import re
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

# Who put a dated entry in the log, written after the date. A Keep this on a
# note the assistant offered files the assistant's words, and a sentence typed
# into I have… files his; the support check trusts only the second until he
# confirms the first. A no he pressed is his too, but it backs nothing. An
# entry with no suffix predates the record.
LOG_SOURCES = {"his answer": "his", "his no": "no", "suggested by the assistant": "assistant"}
_SUFFIX = {source: label for label, source in LOG_SOURCES.items()}


def append_text(current: str, addition: Any, *, today: str, kind: str = "notes",
                source: str = "") -> str:
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
    suffix = f" · {_SUFFIX[source]}" if source in _SUFFIX else ""
    parts.append(f"### {today}{suffix}\n\n{body}")
    return check_text(kind, "\n\n".join(parts))


SKILLS_HEADING = "## Skills"
# A comma between tools, not one inside "JavaScript (React, Next.js)".
_TOOL_SPLIT = re.compile(r",\s*(?![^()]*\))")
_SMALL_WORDS = {"a", "an", "and", "as", "at", "for", "in", "of", "on", "or", "the", "to", "with"}


def _capitalised(tool: str) -> str:
    """A list entry starts each word with a capital ("Large Language Models").
    A word already carrying a capital is his spelling and stays: iOS, LLMs."""
    words = tool.split()
    return " ".join(
        w if any(c.isupper() for c in w) or (i and w in _SMALL_WORDS) else w[:1].upper() + w[1:]
        for i, w in enumerate(words))


def skills(notes: str) -> dict[str, list[str]]:
    """His skills list: the ``## Skills`` section, one ``- Label: a, b`` line
    per label. The only place the CV's skill lines are written from."""
    out: dict[str, list[str]] = {}
    inside = False
    for raw in notes.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            inside = line == SKILLS_HEADING
        elif inside and line.startswith("- ") and ":" in line:
            label, _, tools = line[2:].partition(":")
            out[label.strip()] = [t.strip() for t in _TOOL_SPLIT.split(tools) if t.strip()]
    return out


def add_skill(notes: str, label: str, tool: str) -> str:
    """``notes`` with ``tool`` under ``label`` in his skills list, once.

    The list sits above the chat log, where his own words are, so a tool he
    said yes to reads as his. A label not there yet gets its own line.
    """
    label, tool = " ".join(str(label).split()), _capitalised(" ".join(str(tool).split()))
    if not label or not tool or ":" in label or len(_TOOL_SPLIT.split(tool)) > 1:
        raise ValueError("a skill needs a label and one tool")
    listed = skills(notes)
    name = next((k for k in listed if k.casefold() == label.casefold()), label)
    tools = listed.get(name, [])
    if any(t.casefold() == tool.casefold() for t in tools):
        return notes
    listed[name] = tools + [tool]
    section = SKILLS_HEADING + "\n\n" + "\n".join(
        f"- {k}: {', '.join(v)}" for k, v in listed.items())
    # Take the old section out, then put the new one back above the log.
    kept, inside = [], False
    for raw in notes.splitlines():
        if raw.strip().startswith("#"):
            inside = raw.strip() == SKILLS_HEADING
        if not inside:
            kept.append(raw)
    rest = "\n".join(kept).strip()
    if APPENDED_HEADING in rest:
        head, _, tail = rest.partition(APPENDED_HEADING)
        return check_text("notes", f"{head.rstrip()}\n\n{section}\n\n{APPENDED_HEADING}{tail}".lstrip())
    return check_text("notes", f"{rest}\n\n{section}".strip())


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

"""Readiness of the setup pillars, and validation of what the user saves.

The hub shows three tiles — Voice (``voice.md``), Notes (``notes.md``) and
Documents (``cv.json`` / ``cover.json``) — each reporting ``empty``,
``started`` or ``ready``. That judgement lives here rather than in the request
handler so it can be tested directly and reused by any page.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence

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

# The old chat log: notes kept from the chat used to pile up under this heading
# as dated entries, labelled with who wrote them. Nothing writes there now
# (2026-11-17); notes are placed in their section and a no joins the limits
# section. The labels are still read, so a notes file with the log works.
LOG_SOURCES = {"his answer": "his", "his no": "no", "suggested by the assistant": "assistant"}


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


TECHNICAL_HEADING = "## Technical skills"
# Which CV label a Technical skills group lands on, by a word in its name;
# anything else is Programming.
# ponytail: keyword match on group names; name the label per group in the
# notes if a group ever needs to land somewhere these words don't reach.
_FOLD = (("cloud", "Cloud Infrastructure"), ("infrastructure", "Cloud Infrastructure"),
         ("linux", "Cloud Infrastructure"), ("server", "Cloud Infrastructure"),
         ("ai", "AI"), ("business", "Product"), ("design", "Product"))


_JOINED = re.compile(r"\s+&\s+")  # "Testing and Evaluation" is one skill
_WORD = re.compile(r"[A-Za-z0-9][\w'.+#-]*")


def atomic(tool: str) -> list[str]:
    """One tool per entry. "SQL & Python" is SQL and Python; "PHP (Laravel)"
    is PHP and Laravel; "Model Context Protocol (MCPs)" is just MCP, because a
    bracket that only spells the name's initials is the same tool twice."""
    tool = " ".join(str(tool or "").split()).strip().rstrip(".")
    found = re.fullmatch(r"(.+?)\s*\(([^()]*)\)", tool)
    if found:
        head, inner = found[1].strip(), found[2].strip()
        short = inner[:-1] if inner.endswith("s") and inner[:-1].isupper() else inner
        if short.isupper() and short == _initials(head):
            return [short]
        return [*atomic(head), *(t for part in inner.split(",") for t in atomic(part))]
    return [t.strip() for t in _JOINED.split(tool) if t.strip()]


def _initials(name: str) -> str:
    words = _WORD.findall(name)
    return "".join(w[0] for w in words).upper() if len(words) > 1 else ""


def _name(tool: str) -> str:
    """Node and Node.js are one name."""
    return tool.casefold().removesuffix(".js").replace(" ", "")


def _acronym(tool: str) -> str:
    """LLMs is LLM; a tool not written in capitals has none."""
    word = tool[:-1] if tool.endswith("s") and tool[:-1].isupper() else tool
    return word if word.isupper() and " " not in word else ""


def _merge(have: list[str], tools: Sequence[str]) -> None:
    """Add each tool to ``have`` unless the same tool is already there, by
    name or as an acronym and the name it stands for (LLMs, Large Language
    Models). Two spelled-out names never match on their initials alone."""
    names = {_name(t) for t in have}
    acronyms = {_acronym(t) for t in have} - {""}
    spelled = {_initials(t) for t in have} - {""}
    for tool in tools:
        for one in atomic(tool):
            short, initials = _acronym(one), _initials(one)
            if _name(one) in names or short in spelled or initials in acronyms:
                continue
            # "Jira and Confluence" is two tools already listed; "Testing and
            # Evaluation" stays one skill.
            parts = re.split(r"\s+and\s+", one)
            if len(parts) > 1 and all(_name(p) in names for p in parts):
                continue
            have.append(one)
            names.add(_name(one))
            if short:
                acronyms.add(short)
            if initials:
                spelled.add(initials)


def _listed(notes: str, heading: str) -> dict[str, list[str]]:
    """One section's ``- Label: a, b`` lines, label to tools, one tool each."""
    out: dict[str, list[str]] = {}
    inside = False
    for raw in notes.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            inside = line == heading
        elif inside and line.startswith("- ") and ":" in line:
            label, _, tools = line[2:].partition(":")
            have: list[str] = []
            _merge(have, [t for t in _TOOL_SPLIT.split(tools) if t.strip()])
            out[label.strip()] = have
    return out


def _fold_label(group: str) -> str:
    words = re.findall(r"[a-z]+", group.casefold())
    for word, label in _FOLD:
        if any(w == word or (len(word) > 2 and w.startswith(word)) for w in words):
            return label
    return "Programming"


def skills(notes: str) -> dict[str, list[str]]:
    """His skills list: the ``## Skills`` section, one ``- Label: a, b`` line
    per label, with the ``## Technical skills`` groups folded onto the CV's
    labels (Programming, Cloud Infrastructure, AI, Product). The only place
    the CV's skill lines are written from."""
    out = _listed(notes, SKILLS_HEADING)
    for group, tools in _listed(notes, TECHNICAL_HEADING).items():
        label = _fold_label(group)
        name = next((k for k in out if k.casefold() == label.casefold()), label)
        _merge(out.setdefault(name, []), tools)
    return out


def tools_from_line(text: str) -> list[str]:
    """Split one skills line into tools the way the notes list is written."""
    out: list[str] = []
    for part in _TOOL_SPLIT.split(str(text or "")):
        tool = re.sub(r"^(?:and|&)\s+", "", part.strip().rstrip("."), flags=re.I).strip()
        if tool:
            out.extend(atomic(tool))
    return out


def enrich_skills(
    listed: Mapping[str, list[str]],
    past_lines: Mapping[str, Sequence[str]],
) -> dict[str, list[str]]:
    """Union the notes list with every tool that appeared on a past CV.

    ``past_lines`` is label (casefolded) → skill-line texts from history.
    Labels that only exist on past CVs are ignored: the page is driven by
    the labels on this CV. New tools keep the spelling they had on a CV.
    """
    out = {label: list(tools) for label, tools in listed.items()}
    canon = {label.casefold(): label for label in out}
    for key, lines in past_lines.items():
        label = canon.get(str(key).casefold())
        if label is None:
            continue
        for line in lines:
            _merge(out[label], tools_from_line(line))
    return out


def add_skill(notes: str, label: str, tool: str) -> str:
    """``notes`` with ``tool`` under ``label`` in his skills list, once.

    The list sits above the chat log, where his own words are, so a tool he
    said yes to reads as his. A label not there yet gets its own line.
    """
    label, tool = " ".join(str(label).split()), _capitalised(" ".join(str(tool).split()))
    if not label or not tool or ":" in label or len(_TOOL_SPLIT.split(tool)) > 1:
        raise ValueError("a skill needs a label and one tool")
    listed = _listed(notes, SKILLS_HEADING)  # his own lines, not the folded groups
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


LIMITS_HEADING = "## Limits and what I have not done"


def add_limit(notes: str, line: Any) -> str:
    """``notes`` with a no he pressed filed as a bullet in his limits section.

    Every "I have not done X" belongs there, so no model places it. The
    section is made, above the old chat log, the first time one arrives.
    """
    line = " ".join(str(line or "").split())
    if not line:
        raise ValueError("nothing to add")
    if line.casefold() in notes.casefold():
        return notes
    bullet = f"- {line}"
    lines = notes.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == LIMITS_HEADING)
    except StopIteration:
        section = f"{LIMITS_HEADING}\n\n{bullet}"
        head, sep, tail = notes.partition(APPENDED_HEADING)
        joined = f"{head.rstrip()}\n\n{section}\n\n{sep}{tail}" if sep else f"{notes.rstrip()}\n\n{section}"
        return check_text("notes", joined.lstrip())
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("#")), len(lines))
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return check_text("notes", "\n".join(lines[:end] + [bullet] + lines[end:]))


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

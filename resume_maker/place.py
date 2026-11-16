"""Filing what the chat offers into the section of Notes or the Playbook it
belongs to, instead of a dated pile at the bottom (decided 2026-11-10).

The model reads the whole file, picks a section and returns that section with
the new words worked in. It may only add: every sentence already there must
survive word for word, or the placement is refused. He sees the result as a
before/after and approves it before anything is written.
"""

from __future__ import annotations

import difflib
import json
import re

from . import llm
from .setup import APPENDED_HEADING, SKILLS_HEADING, TECHNICAL_HEADING

# The skills lists have their own add path; the old chat log is frozen.
_NOT_HERE = {SKILLS_HEADING, TECHNICAL_HEADING}

RULES = """You file a new piece of text into the candidate's own file: {what}.
Read the whole file, pick the one section the new text belongs in, and return
that section's body with the new text worked in where it fits in context: next
to the paragraph it relates to, or added onto the sentence it continues.

Hard rules:
- Every sentence already in the section stays, word for word. You may add new
  sentences, or add words after the end of an existing sentence. Never reword,
  reorder, shorten or drop anything that is there.
- Add only what the new text says. Do not repeat what the section already says.
- Write the added words to match the surrounding text: same person, same tense.
  Drop what the section already makes plain: inside an employer's section, do
  not name that employer again ("At Netvox I also..." becomes "I also...").
- The heading must be copied exactly from this list:
{headings}

Reply with JSON only: {{"heading": "<heading line>", "section": "<the full new body, without the heading line>"}}"""

WHAT = {"notes": "notes about their work history, in their own words",
        "voice": "their playbook of rules for how their CV and letters are written"}


class PlaceError(ValueError):
    pass


def sections(text: str) -> dict[str, tuple[int, int]]:
    """Heading line -> (first body line, end line), for every section a note
    may go in. A heading that appears twice is left out: it can't be named."""
    lines = text.splitlines()
    found: dict[str, tuple[int, int]] = {}
    seen: set[str] = set()
    starts = [i for i, line in enumerate(lines) if line.startswith("#")]
    for n, i in enumerate(starts):
        heading = lines[i].strip()
        if heading == APPENDED_HEADING:
            break
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        if heading in seen:
            found.pop(heading, None)
        elif heading not in _NOT_HERE:
            found[heading] = (i + 1, end)
        seen.add(heading)
    return found


def body(text: str, heading: str) -> str:
    start, end = sections(text)[heading]
    return "\n".join(text.splitlines()[start:end]).strip()


def _flat(text: str) -> str:
    return " ".join(text.replace("*", "").split())


def add_only(before: str, after: str) -> bool:
    """Every sentence of ``before`` is still in ``after``, word for word. Its
    closing punctuation may move, so a sentence can be continued."""
    kept = _flat(after)
    for line in before.splitlines():
        for sentence in re.split(r"(?<=[.!?])\s+", line.strip()):
            sentence = _flat(sentence).rstrip(".!?,;:")
            if sentence and sentence not in kept:
                return False
    return True


def segments(before: str, after: str) -> list[dict]:
    """``after`` in pieces, the added ones marked, for the review card."""
    a, b = re.findall(r"\S+|\s+", before), re.findall(r"\S+|\s+", after)
    out: list[dict] = []
    for op, _, _, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if j1 == j2:
            continue
        piece, added = "".join(b[j1:j2]), op != "equal"
        if out and out[-1]["added"] == added:
            out[-1]["text"] += piece
        else:
            out.append({"text": piece, "added": added})
    # A section can run to a hundred lines; show the words around each addition.
    for n, part in enumerate(out):
        if not part["added"] and len(part["text"]) > 300:
            t = part["text"]
            # Cut on spaces, so the snippet never starts or ends mid-word.
            head = "" if n == 0 else t[:120].rsplit(" ", 1)[0].rstrip() + " …"
            tail = "" if n == len(out) - 1 else "… " + t[-120:].split(" ", 1)[-1].lstrip()
            part["text"] = "\n".join(p for p in (head, tail) if p) + " "
    return out


def checked(text: str, heading: str, before: str, after: str) -> str:
    """``text`` with the section replaced, if the placement is still valid."""
    if heading not in sections(text):
        raise PlaceError("that section is not in the file any more")
    if body(text, heading) != before.strip():
        raise PlaceError("that section changed since this was placed; keep it again")
    after = after.strip()
    if after == before.strip() or not add_only(before, after):
        raise PlaceError("the placement changed words already in the section")
    lines = text.splitlines()
    start, end = sections(text)[heading]
    return "\n".join(lines[:start] + ["", after, ""] + lines[end:]).strip() + "\n"


def propose(kind: str, text: str, addition: str, provider: str, model: str) -> dict:
    """Ask the model where ``addition`` goes. Two tries, then a PlaceError."""
    addition = addition.strip()
    if not addition:
        raise PlaceError("nothing to add")
    heads = sections(text)
    if not heads:
        raise PlaceError("the file has no sections to add to")
    system = RULES.format(what=WHAT[kind], headings="\n".join(heads))
    prompt = f"## The file\n\n{text}\n\n## The new text\n\n{addition}"
    why = "the model did not reply"
    for _ in range(2):
        raw = llm.complete(provider, model, system, prompt, temperature=0)
        found = re.search(r"\{.*\}", raw, re.S)
        try:
            reply = json.loads(found.group(0)) if found else {}
        except json.JSONDecodeError:
            reply = {}
        heading = str(reply.get("heading") or "").strip()
        after = str(reply.get("section") or "").strip()
        if heading not in heads:
            why = f"the model named a section that isn't there ({heading or 'none'})"
            continue
        before = body(text, heading)
        if after == before or not add_only(before, after):
            why = "the model changed words already in the section"
            continue
        return {"heading": heading, "before": before, "after": after,
                "segments": segments(before, after)}
    raise PlaceError(f"Couldn't place this: {why}. Try Keep again, or copy it in yourself.")

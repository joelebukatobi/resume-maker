"""Filing what the chat offers into the section of Notes or the Playbook it
belongs to, instead of a dated pile at the bottom (decided 2026-11-10).

The model reads the whole file and names a section, the sentence or list item
the new words should follow, and the new words. Code puts them there, so
nothing already written can change. It used to send the whole section back:
measured 2026-11-16, that cost 2-10s of writing per save and let it reword
text; a shortlist of sections by embedding did not cut its thinking time and
once left the right section out. He sees the result and approves it before
anything is written.
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
Pick the one section it belongs in, and the exact sentence or list item in that
section it should come right after. Then write the new words to sit there:
- Add only what the new text says, nothing the section already says.
- Same person and tense as the text around it. Drop what the section already
  makes plain: inside an employer's section, do not name that employer again
  ("At Netvox I also..." becomes "I also...").
- If the spot is a list, write the new words as one list item starting "- ".
- "after" is copied exactly from the section; leave it empty to add at its end.
The heading must be copied exactly from this list:
{headings}

Reply with JSON only: {{"heading": "<heading line>", "after": "<exact words from that section>", "text": "<the new words>"}}"""

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


def insert(before: str, anchor: str, words: str) -> str | None:
    """``before`` with ``words`` put right after ``anchor``; None when the
    anchor is not in it. A list item goes on its own line after the item the
    anchor ends in; anything else continues the anchor's paragraph."""
    words = words.strip()
    if not anchor.strip():
        return f"{before}\n\n{words}" if before else words
    # Copied text may lose bold markers and line wraps; match it word by word.
    tokens = re.findall(r"\w+|[^\w\s*]", anchor)
    pattern = r"[\s*]*".join(re.escape(t) for t in tokens)
    found = re.search(pattern, before)
    if not found:
        return None
    end = found.end()
    while end < len(before) and before[end] in ".!?;:*)\"'":
        end += 1
    if not words.startswith("- "):
        return f"{before[:end]} {words}{before[end:]}"
    # The end of the list item: its line, and any indented lines that continue it.
    line_end = before.find("\n", end)
    while line_end != -1 and before[line_end + 1:line_end + 3] == "  ":
        line_end = before.find("\n", line_end + 1)
    if line_end == -1:
        line_end = len(before)
    return f"{before[:line_end]}\n{words}{before[line_end:]}"


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
        words = str(reply.get("text") or "").strip()
        if heading not in heads or not words:
            why = f"the model named a section that isn't there ({heading or 'none'})"
            continue
        before = body(text, heading)
        after = insert(before, str(reply.get("after") or ""), words)
        if after is None:
            why = "the model pointed at words that aren't in the section"
            continue
        return {"heading": heading, "before": before, "after": after,
                "segments": segments(before, after)}
    raise PlaceError(f"Couldn't place this: {why}. Try Keep again, or copy it in yourself.")

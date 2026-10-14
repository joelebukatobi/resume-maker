"""Check the notes against what the user actually said.

Twice now a claim has reached ``notes.md`` that the user never made. Both
times it was a paraphrase hardening into a fact, and both times it was caught
weeks later in a finished cover letter. This reads the notes back against the
user's own words and reports what it cannot trace.

The work splits in two, because the two halves have different costs.

Finding the evidence is local and free. Every claim is matched against the
user's own messages, in the app's chats and in the Claude Code transcripts for
this project, and the closest passages are attached to it.

Judging the evidence needs a model, because the failures are not invented
vocabulary. "The brief was one line" uses only words the user wrote; what was
invented was the relation between them. No amount of string matching sees
that. A model reading the claim beside the passage does.

Nothing here edits anything. It prints, and the user decides.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from .match import STOPWORDS, _words, stem

# Sections that are not claims about the user's work.
SKIP_HEADINGS = ("still to confirm",)
MIN_CLAIM_CHARS = 40
PASSAGES_PER_CLAIM = 3
MAX_PASSAGE_CHARS = 700
CODE_TRANSCRIPTS = Path.home() / ".claude" / "projects"


@dataclass(frozen=True, slots=True)
class Claim:
    section: str
    text: str


@dataclass(frozen=True, slots=True)
class Finding:
    claim: Claim
    passages: tuple[str, ...]

    def as_prompt(self, number: int) -> str:
        said = "\n".join(f"  > {p}" for p in self.passages) or "  > (nothing close found)"
        return f"[{number}] Claim ({self.claim.section}): {self.claim.text}\nNearest things they wrote:\n{said}"


# ----------------------------------------------------------------- claims


def _plain(text: str) -> str:
    """Markdown stripped back to the sentence underneath it."""
    text = re.sub(r"\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`", r"\1\2\3", text)
    return " ".join(text.replace("- ", " ").split())


def claims(notes_md: str) -> list[Claim]:
    """Every paragraph of the notes that asserts something, with its section."""
    section = ""
    found: list[Claim] = []
    for block in re.split(r"\n\s*\n", notes_md):
        block = block.strip()
        if not block:
            continue
        if block.startswith("#"):
            section = _plain(block.lstrip("# ")).strip()
            continue
        if any(skip in section.lower() for skip in SKIP_HEADINGS):
            continue
        for part in block.split("\n- ") if block.startswith("- ") else [block]:
            line = _plain(part)
            if len(line) >= MIN_CLAIM_CHARS:
                found.append(Claim(section or "top", line))
    return found


# --------------------------------------------------------------- evidence


def _app_messages(root: Path) -> Iterator[str]:
    for chat in sorted(root.glob("applications/*/chat.json")):
        try:
            data = json.loads(chat.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for message in data.get("messages") or []:
            if isinstance(message, dict) and message.get("role") == "user":
                yield str(message.get("content", ""))


def _code_messages(project: Path) -> Iterator[str]:
    """The user's turns in this project's Claude Code sessions.

    The notes are written here as often as in the app, so a check that read
    only the app's chats would miss the case that prompted this.
    """
    slug = "-" + str(project.resolve()).strip("/").replace("/", "-")
    for transcript in sorted((CODE_TRANSCRIPTS / slug).glob("*.jsonl")):
        with transcript.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("type") != "user":
                    continue
                content = (row.get("message") or {}).get("content")
                if isinstance(content, str):
                    yield content
                elif isinstance(content, list):
                    yield " ".join(part.get("text", "") for part in content
                                   if isinstance(part, dict) and part.get("type") == "text")


def passages(project: Path) -> list[str]:
    """What the user wrote, split into paragraph-sized pieces."""
    out: list[str] = []
    for message in list(_app_messages(project)) + list(_code_messages(project)):
        for block in re.split(r"\n\s*\n", message):
            block = " ".join(block.split())
            if len(block) >= MIN_CLAIM_CHARS:
                out.append(block[:MAX_PASSAGE_CHARS])
    return out


# ---------------------------------------------------------------- pairing


def _terms(text: str) -> set[str]:
    return {stem(w) for w in _words(text) if w not in STOPWORDS and len(w) > 3}


def nearest(claim: Claim, index: Sequence[tuple[set[str], str]],
            limit: int = PASSAGES_PER_CLAIM) -> tuple[str, ...]:
    """The passages sharing most of this claim's distinctive words.

    Rare words count for more: a passage that also mentions "iDRAC" is better
    evidence than one that also mentions "data", and weighting by how rarely a
    word appears across everything the user wrote says so without a model.
    """
    wanted = _terms(claim.text)
    if not wanted:
        return ()
    spread = Counter(term for terms, _ in index for term in terms if term in wanted)
    scored = []
    for terms, text in index:
        shared = wanted & terms
        if shared:
            scored.append((sum(1 / (1 + spread[t]) for t in shared), text))
    scored.sort(key=lambda pair: -pair[0])
    return tuple(text for _, text in scored[:limit])


def gather(notes_md: str, project: Path) -> list[Finding]:
    """Every claim paired with the closest things the user wrote."""
    index = [(_terms(p), p) for p in passages(project)]
    index = [(terms, text) for terms, text in index if terms]
    return [Finding(claim, nearest(claim, index)) for claim in claims(notes_md)]


# ----------------------------------------------------------------- asking

RULES = """\
You are checking a candidate's notes against their own words.

For each numbered claim you are given the nearest things the person actually
wrote. Decide whether the claim is supported by them.

Reply with JSON only:
{"unsupported": [{"n": <number>, "why": "<a few words>"}]}

- List a claim only when the passages do not support it, or contradict it.
- A claim written in different words from the passage is still supported.
  Judge the substance.
- A claim with no nearby passage is not automatically wrong. Say so only when
  it asserts something specific that nothing they wrote backs up.
- Watch for a claim that sharpens what was said into something neater: a
  summary turned into a quote, a vague brief turned into a single line, an
  approximate number turned into an exact one. Those are the ones that matter.
- Empty list when everything checks out.
"""


def question(findings: Sequence[Finding]) -> str:
    return "\n\n".join(f.as_prompt(i + 1) for i, f in enumerate(findings))


def parse_answer(reply: str, findings: Sequence[Finding]) -> list[tuple[Finding, str]]:
    """The model's verdict, mapped back onto the claims it was given."""
    match = re.search(r"\{.*\}", reply, re.S)
    if not match:
        raise ValueError("the model did not return a verdict")
    data = json.loads(match.group(0))
    out = []
    for item in data.get("unsupported") or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("n", 0)) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(findings):
            out.append((findings[index], " ".join(str(item.get("why", "")).split())[:160]))
    return out


# -------------------------------------------------------------------- cli


def _report(flagged: Sequence[tuple[Finding, str]], total: int) -> None:
    if not flagged:
        print(f"{total} claims checked. Nothing unsupported.")
        return
    print(f"{total} claims checked. {len(flagged)} to look at.\n")
    for finding, why in flagged:
        print(f"[{finding.claim.section}] {why}")
        print(f"  claim: {finding.claim.text}")
        for passage in finding.passages[:2]:
            print(f"  said:  {passage[:300]}")
        print()
    print("Nothing was changed. Edit notes.md yourself where this is right.")


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    from . import llm

    parser = argparse.ArgumentParser(
        description="Check notes.md against what you actually said.")
    parser.add_argument("--notes", type=Path, default=Path("notes.md"))
    parser.add_argument("--project", type=Path, default=Path("."))
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--dry", action="store_true",
                        help="pair the claims with the evidence and stop, no model call")
    args = parser.parse_args(argv)

    try:
        notes = args.notes.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"could not read {args.notes}: {exc}")
        return 1
    findings = gather(notes, args.project)
    if not findings:
        print("no claims found")
        return 0

    if args.dry:
        for n, finding in enumerate(findings, 1):
            print(finding.as_prompt(n), "\n")
        print(f"{len(findings)} claims, {len(question(findings)) // 4} tokens if asked")
        return 0

    provider = args.provider or next(
        (p.id for p in llm.PROVIDERS.values() if p.available), "")
    if not provider:
        print("no model connected; run with --dry, or connect one in the editor")
        return 1
    model = args.model or llm.list_models(provider)[0]["id"]
    print(f"asking {provider} ({model}) about {len(findings)} claims…")
    reply = llm.complete(provider, model, RULES, question(findings))
    try:
        flagged = parse_answer(reply, findings)
    except ValueError as exc:
        print(f"{exc}: {reply[:200]}")
        return 1
    _report(flagged, len(findings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

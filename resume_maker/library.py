"""Every line he has ever put on a CV, by the slot it belongs to.

A new application used to start as a copy of the newest one in its track, so
whatever the last posting asked for came along. A bullet naming Zapier, n8n,
Webflow and Airtable, written for a posting that wanted low-code tooling,
reached three later CVs that never asked for it, while his usual interests
line, which carried the words the next posting did want, had dropped out and
could not come back: the assistant never sees his other CVs.

So the history is gathered here instead. Every distinct line from every CV,
tagged with where on the page it lives and which CVs it was on, searched with
SQLite's full-text index against the posting's frozen keyword list. Plain
code, no model: the same posting and the same history give the same answer.
The index is built in memory per call; a few hundred lines take milliseconds,
and a cache would be one more thing to go stale.

What he has decided about the lines (struck, kept, confirmed, checked,
labelled) is the only part that persists, in one file beside his notes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from . import jev
from .match import _words, stem
from .setup import APPENDED_HEADING, LOG_SOURCES

Slot = tuple[str, str]
BASE_NAME = "(base)"

# A company is matched across CVs by name, not position: one CV says
# "xPathEdge LLC, IT Services and Consulting", another "XPATHEDGE LLC".
_COMPANY_TAIL = re.compile(r"\s+(?:llc|ltd|limited|inc)\.?$")


def company(left: str) -> str:
    name = " ".join(str(left or "").split(",")[0].split()).casefold()
    while True:
        trimmed = _COMPANY_TAIL.sub("", name)
        if trimmed == name:
            return name
        name = trimmed


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


@dataclass(frozen=True)
class Line:
    text: str
    slot: Slot
    cvs: tuple[str, ...]
    base: bool
    reworded_from: str | None = None

    @property
    def count(self) -> int:
        return len(self.cvs)


def slots_of(cv: Mapping[str, Any]) -> Iterable[tuple[Slot, str]]:
    """Each (slot, line) on one CV. Education and the header are fixed facts,
    not choices, so they are not in the library."""
    for section in cv.get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        heading = str(section.get("heading") or "").upper()
        kind = section.get("type")
        if kind == "paragraph" and "SUMMARY" in heading:
            yield ("summary", ""), _clean(section.get("text"))
        elif kind == "labeled" and "SKILL" in heading:
            for item in section.get("items") or []:
                if isinstance(item, Mapping):
                    yield ("skills", _clean(item.get("label")).casefold()), _clean(item.get("text"))
        elif kind == "labeled" and "ADDITIONAL" in heading:
            for item in section.get("items") or []:
                if isinstance(item, Mapping):
                    yield ("additional", ""), _clean(item.get("text"))
        elif kind == "entries" and "EXPERIENCE" in heading:
            for entry in section.get("entries") or []:
                if not isinstance(entry, Mapping):
                    continue
                slot = ("bullets", company(entry.get("left")))
                for bullet in entry.get("bullets") or []:
                    yield slot, _clean(bullet)


def collect(base_cv: Mapping[str, Any], cvs: Mapping[str, Mapping[str, Any]],
            reworded: Mapping[str, str]) -> list[Line]:
    """One line per distinct (slot, text), with every CV it appeared on.

    ``reworded`` maps a line to the line it was reworded from, for lines the
    assistant wrote and he accepted; the empty string means the original is
    not known.
    """
    seen: dict[tuple[Slot, str], list[str]] = {}
    for name, cv in [(BASE_NAME, base_cv), *cvs.items()]:
        for slot, text in slots_of(cv):
            if text:
                where = seen.setdefault((slot, text), [])
                if name not in where:
                    where.append(name)
    return [Line(text=text, slot=slot, cvs=tuple(where), base=BASE_NAME in where,
                 reworded_from=reworded.get(text))
            for (slot, text), where in seen.items()]


_BLOCK = re.compile(r"```(proposal|answer|notes|voice|gaps|ask)\s*([\s\S]*?)```")


def accepted_proposals(chat: Mapping[str, Any]) -> list[str]:
    """The lines the assistant proposed in this chat and he accepted.

    Keyed the way the editor keys its cards: every block but an answer counts,
    in order, and ``resolved`` records what he did with each.
    """
    out: list[str] = []
    for message in chat.get("messages") or []:
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            continue
        resolved = message.get("resolved") if isinstance(message.get("resolved"), Mapping) else {}
        n = 0
        for kind, body in _BLOCK.findall(str(message.get("content") or "")):
            if kind == "answer":
                continue
            key, n = str(n), n + 1
            if kind != "proposal" or resolved.get(key) != "accepted":
                continue
            try:
                text = json.loads(body).get("text")
            except (ValueError, AttributeError):
                continue
            if isinstance(text, str) and text.strip():
                out.append(_clean(text))
    return out


def load_cvs(workspace: Any) -> dict[str, dict[str, Any]]:
    """Every application's CV, live and archived. One that does not parse is
    left out rather than taking the whole history down with it."""
    found: dict[str, dict[str, Any]] = {}
    for app in workspace.list():
        path = workspace.folder(app.slug, archived=app.archived) / "cv.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            found[app.slug] = data
    return found


@dataclass
class Decisions:
    """What he has decided about lines and notes. Everything else is derived."""

    struck: set[str] = field(default_factory=set)
    excluded: set[str] = field(default_factory=set)
    kept: set[str] = field(default_factory=set)
    confirmed: dict[str, bool] = field(default_factory=dict)
    checks: dict[str, dict] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    # A line the assistant wrote and he accepted, and what it was reworded
    # from ("" when it was new). Evidence only through its original.
    reworded: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Decisions":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}

        def strings(key: str) -> set[str]:
            value = data.get(key)
            return {str(v) for v in value} if isinstance(value, list) else set()

        def mapping(key: str) -> dict:
            value = data.get(key)
            return dict(value) if isinstance(value, dict) else {}

        return cls(struck=strings("struck"), excluded=strings("excluded"),
                   kept=strings("kept"),
                   confirmed={k: bool(v) for k, v in mapping("confirmed").items()},
                   checks={k: v for k, v in mapping("checks").items() if isinstance(v, dict)},
                   labels={k: str(v) for k, v in mapping("labels").items()},
                   reworded={k: str(v) for k, v in mapping("reworded").items()})

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps({"struck": sorted(self.struck), "excluded": sorted(self.excluded),
                           "kept": sorted(self.kept), "confirmed": self.confirmed,
                           "checks": self.checks, "labels": self.labels,
                           "reworded": self.reworded},
                          indent=2, ensure_ascii=False)
        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(text + "\n")
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise


def usable(lines: Iterable[Line], decisions: Decisions) -> list[Line]:
    """The lines that may still be picked. Striking an original strikes what
    was reworded from it; excluding a CV removes only what that CV alone had."""
    out = []
    for line in lines:
        if line.text in decisions.struck or line.reworded_from in decisions.struck:
            continue
        if not line.base and set(line.cvs) <= decisions.excluded:
            continue
        out.append(line)
    return out


def _phrase(term: str) -> str:
    """One word as an FTS5 string. Inside double quotes FTS5 treats every
    character as text, so "C#", "AND" or "a:b" cannot become query syntax."""
    return '"' + " ".join(term.replace('"', " ").split()) + '"'


def _term_query(term: str) -> str:
    """A keyword as all of its words, in any order. The ring counts "agentic
    workflows" as answered by "agentic AI workflows" in one line; a phrase
    query would not, and the library would rank against a different ruler."""
    return " ".join(_phrase(word) for word in term.replace('"', " ").split())


class Index:
    """The lines in an in-memory full-text table, ranked by BM25 with stemming."""

    def __init__(self, lines: Iterable[Line]):
        self.lines = list(lines)
        self._db = sqlite3.connect(":memory:")
        self._db.execute("CREATE VIRTUAL TABLE lines USING fts5(text, tokenize='porter')")
        self._db.executemany("INSERT INTO lines(rowid, text) VALUES (?, ?)",
                             [(i, line.text) for i, line in enumerate(self.lines)])

    def _scores(self, terms: Iterable[str]) -> dict[int, float]:
        phrases = [_term_query(t) for t in terms if str(t).replace('"', " ").strip()]
        scores: dict[int, float] = {}
        for phrase in phrases:
            try:
                rows = self._db.execute(
                    "SELECT rowid, bm25(lines) FROM lines WHERE lines MATCH ?", (phrase,))
            except sqlite3.OperationalError:
                continue  # a term with no searchable characters ("#", "*")
            for rowid, rank in rows:
                # bm25 is lower-is-better and negative; summed per phrase so a
                # line that answers two keywords outranks one that answers one.
                scores[rowid] = scores.get(rowid, 0.0) - rank
        return scores

    def search(self, terms: Iterable[str], limit: int = 3) -> list[Line]:
        """The lines, from any slot, that best answer ``terms``."""
        scores = self._scores(terms)
        return [self.lines[i] for i in sorted(scores, key=lambda i: -scores[i])[:limit]]

    def rank(self, slot: Slot, terms: Iterable[str]) -> list[tuple[Line, float]]:
        """Every line of ``slot``, best for ``terms`` first. A line no term
        reaches scores 0, and those follow in order of how often he used them."""
        scores = self._scores(terms)
        ranked = [(line, scores.get(i, 0.0)) for i, line in enumerate(self.lines)
                  if line.slot == slot]
        ranked.sort(key=lambda pair: (-pair[1], -pair[0].count))
        return ranked


# -- the support check ------------------------------------------------------

SUPPORT_THRESHOLD = 0.8
# Of a line's longer words, the share a notes passage must also hold for code
# to pass it without asking Jev. High on purpose: a near-copy of his own
# sentence passes; anything reworded is a judgement, and judgements go to Jev.
_CODE_SHARE = 0.6
_TRUSTED = {"notes", "his"}


@dataclass(frozen=True)
class Passage:
    text: str
    trust: str  # "notes" | "his" | "no" | "assistant" | "legacy"


def passage_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def passages(notes: str) -> list[Passage]:
    """The notes as paragraphs, each with who wrote it.

    Above the chat log, he did. In the log each dated entry says whose words
    it holds; an entry written before that was recorded says nothing, and is
    treated like the assistant's until he confirms it.
    """
    out: list[Passage] = []
    trust, buffer, in_log = "notes", [], False

    def flush() -> None:
        text = _clean(" ".join(buffer))
        if text:
            out.append(Passage(text, trust))
        buffer.clear()

    for raw in notes.splitlines():
        line = raw.strip()
        if line == APPENDED_HEADING:
            flush()
            in_log = True
        elif in_log and line.startswith("### "):
            flush()
            _, _, suffix = line[4:].partition(" · ")
            trust = LOG_SOURCES.get(suffix.strip(), "legacy")
        elif not line or line.startswith("#"):
            flush()
        else:
            buffer.append(line)
    flush()
    return out


def trusted(passage: Passage, decisions: Decisions) -> bool:
    if passage.trust == "no":
        return False  # a no he pressed backs nothing, confirmed or not
    if passage.trust in _TRUSTED:
        return True
    return decisions.confirmed.get(passage_key(passage.text)) is True


def _tokens(text: str) -> list[str]:
    return [t.strip(".,;:()[]'\"!?") for t in text.split()]


def named_tools(line: str) -> set[str]:
    """What a line names: capitalised words after the first, and anything with
    a digit, a dot, a plus or a hash. These are the claims a recruiter can ask
    about, so code passes a line only when the notes name every one."""
    names = set()
    for i, token in enumerate(_tokens(line)):
        if not token:
            continue
        if (i > 0 and token[0].isupper()) or any(c.isdigit() or c in ".+#" for c in token):
            names.add(token.casefold())
    return names


def _stems(text: str) -> set[str]:
    return {stem(w) for w in _words(text) if len(w) >= 4}


# A passage that says he did not do something shares every word with the line
# claiming he did. Overlap cannot read a "not", so such a passage goes to Jev.
# "no-code" is a kind of tool, not a no.
_NEGATION = re.compile(
    r"\b(?:not|never|no|cannot|can't|didn't|haven't|hasn't|wasn't|don't|doesn't|won't)\b(?!-)",
    re.I)


def _code_pass(line: str, passage: str) -> bool:
    if _NEGATION.search(passage):
        return False
    words = {t.casefold() for t in _tokens(passage)}
    if not named_tools(line) <= words:
        return False
    wanted = _stems(line)
    return not wanted or len(wanted & _stems(passage)) / len(wanted) >= _CODE_SHARE


def closest(found: list[Passage], line: str, limit: int = 3) -> list[Passage]:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE VIRTUAL TABLE p USING fts5(text, tokenize='porter')")
    db.executemany("INSERT INTO p(rowid, text) VALUES (?, ?)",
                   [(i, x.text) for i, x in enumerate(found)])
    query = " OR ".join(_phrase(w) for w in set(_words(line)) if len(w) >= 3 and w != "/")
    if not query:
        return []
    try:
        rows = db.execute("SELECT rowid FROM p WHERE p MATCH ? ORDER BY bm25(p) LIMIT ?",
                          (query, limit)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [found[i] for (i,) in rows]


def _check_key(line: str, passage: str) -> str:
    return passage_key(line + "\n" + passage)


def check(lines: Iterable[Line], notes: str, decisions: Decisions, *,
          support: Callable[[list[tuple[str, str]]], list[tuple[str, float]]] = jev.support,
          evidence: dict[str, str] | None = None) -> dict[str, str]:
    """Whether his notes back each line: pass, held, unconfirmed or unchecked.

    His base CV is his, and a line he kept is his decision. Otherwise code
    passes a line his notes state outright. A line only an unconfirmed note
    states is "unconfirmed", never sent to Jev: Jev checks that a line and a
    passage agree, not that the passage is true, and the Zapier note was the
    assistant's. The rest go to Jev with the closest passages he wrote, read
    together, once per (line, passages); anything short of a confident
    "supports" is held.
    ``evidence``, if given, is filled with the note behind each unconfirmed
    line, so the card can ask about the note itself.
    """
    found = passages(notes)
    verdicts: dict[str, str] = {}
    pending: list[tuple[str, str]] = []
    for line in lines:
        text = line.text
        if text in verdicts:
            continue
        if line.base or text in decisions.kept:
            verdicts[text] = "pass"
            continue
        close = closest(found, text, limit=4)
        mine = [p for p in close if trusted(p, decisions)]
        # The proof can be spread over his notes: the tool in one paragraph,
        # the number in another. Read the closest of his together.
        together = "\n\n".join(p.text for p in mine)
        undecided = [p for p in close if not trusted(p, decisions) and p.trust != "no"
                     and passage_key(p.text) not in decisions.confirmed]
        if any(_code_pass(text, p.text) for p in mine) or (mine and _code_pass(text, together)):
            verdicts[text] = "pass"
        elif any(_code_pass(text, p.text) for p in undecided):
            verdicts[text] = "unconfirmed"
            if evidence is not None:
                evidence[text] = next(p.text for p in undecided if _code_pass(text, p.text))
        elif not mine:
            verdicts[text] = "held"
        else:
            cached = decisions.checks.get(_check_key(text, together))
            if cached:
                verdicts[text] = _judged(cached)
            else:
                pending.append((text, together))
    if pending:
        try:
            answers = support(pending)
        except jev.JevError as exc:
            print(f"[library] support check did not run: {exc}", flush=True)
            for text, _ in pending:
                verdicts[text] = "unchecked"
            return verdicts
        for (text, passage), (verdict, confidence) in zip(pending, answers):
            result = {"verdict": verdict, "confidence": confidence}
            decisions.checks[_check_key(text, passage)] = result
            verdicts[text] = _judged(result)
    return verdicts


def answer_gaps(questions: Iterable[str], notes: str, decisions: Decisions,
                lines: Iterable[Line], *,
                judge: Callable[[list[tuple[str, str]]], list[tuple[str, float]]] = jev.answers,
                ) -> list[dict[str, str]]:
    """Each gap question: covered by his notes, answered by a past CV line, or
    open. The model reads the notes and can still miss what is in them, and it
    never sees his old CVs; code looks before he is asked.

    His notes are read the way the support check reads them: the closest of
    his passages, together. A past line is quoted back for him to confirm,
    never filed for him. If the check cannot run, every question is asked.
    """
    found = passages(notes)
    past = [Passage(line.text, "notes") for line in lines]
    questions = [q for q in questions if q.strip()]
    pairs: list[tuple[str, str]] = []
    plan: list[tuple[str, str]] = []   # (question, "notes" or the past line)
    for q in questions:
        mine = [p for p in closest(found, q, limit=4) if trusted(p, decisions)]
        if mine:
            pairs.append((q, "\n\n".join(p.text for p in mine)))
            plan.append((q, "notes"))
        for p in closest(past, q, limit=3):
            pairs.append((q, p.text))
            plan.append((q, p.text))
    try:
        verdicts = judge(pairs) if pairs else []
    except jev.JevError as exc:
        print(f"[library] gap check did not run: {exc}", flush=True)
        verdicts = [("says_nothing", 0.0)] * len(pairs)
    yes = {i for i, (v, c) in enumerate(verdicts) if v == "yes" and c >= SUPPORT_THRESHOLD}
    out = []
    for q in questions:
        mine = [(i, where) for i, (asked, where) in enumerate(plan) if asked == q]
        if any(i in yes and where == "notes" for i, where in mine):
            out.append({"question": q, "status": "covered"})
        elif line := next((where for i, where in mine if i in yes and where != "notes"), None):
            out.append({"question": q, "status": "past", "line": line})
        else:
            out.append({"question": q, "status": "open"})
    return out


def _judged(result: Mapping[str, Any]) -> str:
    good = result.get("verdict") == "supports" and float(result.get("confidence") or 0) >= SUPPORT_THRESHOLD
    return "pass" if good else "held"

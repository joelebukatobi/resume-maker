"""A new CV, filled from everything he has ever written, for one posting.

The base CV fixes what is fact: titles, dates, education, which roles exist
and how much room each may take. Every bullet, the summary, each skills line
and Additional Information is then chosen fresh from the library, by the
posting's frozen keyword list. When a posting says nothing that touches a
slot, the slot gets the line he has used most, which is how a role the
posting does not care about keeps its usual bullets instead of whatever the
last application put there.

The page is filled as a whole, not slot by slot. Once a keyword is on the page
it stops counting for the slots after it, so the next one goes after what is
still missing; five bullets saying "roadmap" score once with the ATS and read
badly to a person.

What comes back beside the CV is a short list of cards: only the places code
made a call he might not, so he decides three things, not fifteen.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from .library import Index, Line, Slot, company
from .match import score_against


@dataclass
class Card:
    kind: str  # changed | close | held | unconfirmed | unanswered | cast
    slot: Slot
    path: str
    text: str
    other: str = ""
    keywords: tuple[str, ...] = ()
    passage: str = ""  # the unconfirmed note behind the line, for "Is it true?"

    def as_json(self) -> dict[str, Any]:
        out = asdict(self)
        out["slot"] = list(self.slot)
        out["keywords"] = list(self.keywords)
        return out


def _answers(text: str, terms: list[str]) -> set[str]:
    return set(score_against([text], terms).matched) if terms else set()


class _Page:
    """The fill in progress: what is on the page, and what it already says."""

    def __init__(self, index: Index, terms: list[str], verdicts: Mapping[str, str],
                 labels: Mapping[str, str], evidence: Mapping[str, str]):
        self.index, self.terms = index, list(terms)
        self.verdicts, self.labels, self.evidence = verdicts, labels, evidence
        # What may stand in when no line passes: only a line still in the
        # library. A line he struck is not in it, and must not come back.
        self.known = {line.text for line in index.lines}
        self.covered: set[str] = set()
        self.used: set[str] = set()
        self.flagged: set[str] = set()
        self.cards: list[Card] = []

    def verdict(self, line: Line) -> str:
        return self.verdicts.get(line.text) or ("pass" if line.base else "held")

    def allowed(self, line: Line, slot: Slot) -> bool:
        if slot[0] != "additional":
            return True
        kind = self.labels.get(line.text, "")
        if kind == "self_description":
            return False
        # A certificate earns its place only when the posting asks about it.
        return kind != "certification" or bool(_answers(line.text, self.terms))

    def pick(self, slot: Slot, path: str, fallback: str) -> str | None:
        """The line for this position, or None to leave it out."""
        remaining = [t for t in self.terms if t not in self.covered]
        ranked = [(line, score) for line, score in self.index.rank(slot, remaining)
                  if line.text not in self.used and self.allowed(line, slot)]
        passing = [(line, score) for line, score in ranked if self.verdict(line) == "pass"]
        if not passing:
            if fallback in self.known and fallback not in self.used:
                self.used.add(fallback)
                return fallback
            return None
        top, score = passing[0]
        if score <= 0:
            # Nothing on the posting touches this slot: his most-used line.
            top = max((line for line, _ in passing), key=lambda l: (l.count, l.base))
        usual = max((line for line, _ in passing), key=lambda l: (l.count, l.base))
        gained = _answers(top.text, remaining)
        if top is not usual and gained:
            self.cards.append(Card("changed", slot, path, top.text, usual.text,
                                   tuple(sorted(gained))))
        runner = next((line for line, _ in passing if line is not top), None)
        if runner is not None and gained and len(_answers(runner.text, remaining)) == len(gained):
            self.cards.append(Card("close", slot, path, top.text, runner.text,
                                   tuple(sorted(gained))))
        # The best line his notes did not back, if it would have beaten this one.
        for line, line_score in ranked:
            if line is top:
                break
            kind = self.verdict(line)
            if kind != "pass" and line_score > 0 and line.text not in self.flagged:
                self.flagged.add(line.text)
                self.cards.append(Card("unconfirmed" if kind == "unconfirmed" else "held",
                                       slot, path, line.text, top.text,
                                       passage=self.evidence.get(line.text, "")))
                break
        self.used.add(top.text)
        self.covered |= _answers(top.text, self.terms)
        return top.text


def _sections(cv: Mapping[str, Any], word: str, kind: str) -> Iterable[tuple[int, dict]]:
    for i, section in enumerate(cv.get("sections") or []):
        if (isinstance(section, dict) and section.get("type") == kind
                and word in str(section.get("heading") or "").upper()):
            yield i, section


def _role_hits(page: _Page, entry: dict) -> int:
    """How many frozen terms this role's best past lines can answer."""
    slot = ("bullets", company(entry.get("left")))
    room = len(entry.get("bullets") or []) or 1
    best = [line.text for line, score in page.index.rank(slot, page.terms)
            if page.verdict(line) == "pass"][:room]
    return len(set().union(*(_answers(t, page.terms) for t in best))) if best else 0


def _cast(page: _Page, entries: list[dict], at: int) -> None:
    """Which roles print. An off role comes on when it answers more of the
    posting than the weakest role showing, and that role goes off."""
    if not page.terms:
        return
    scored = [(_role_hits(page, e), i) for i, e in enumerate(entries)]
    while True:
        shown = [(h, i) for h, i in scored if not entries[i].get("off")]
        hidden = [(h, i) for h, i in scored if entries[i].get("off")]
        if not shown or not hidden:
            return
        weakest, best = min(shown), max(hidden)
        if best[0] <= weakest[0]:
            return
        entries[best[1]]["off"] = False
        entries[weakest[1]]["off"] = True
        for h, i, verb in ((best[0], best[1], "on"), (weakest[0], weakest[1], "off")):
            page.cards.append(Card("cast", ("bullets", company(entries[i].get("left"))),
                                   f"sections.{at}.entries.{i}.off",
                                   f"{entries[i].get('left', '')} turned {verb}: "
                                   f"answers {h} of {len(page.terms)}"))


def suggest_cast(entries: list[dict], index: Index, terms: list[str],
                 verdicts: Mapping[str, str]) -> list[dict[str, Any]]:
    """Score each experience entry for this posting and recommend on/off.

    Does not mutate ``entries``. Same swap rule as ``_cast``: a hidden role
    that answers more than the weakest shown role is recommended on, and that
    weakest role is recommended off. Hits count how many frozen terms the
    role's best past (notes-backed) lines cover.
    """
    page = _Page(index, list(terms), verdicts, {}, {})
    trial = [{**entry, "off": bool(entry.get("off")),
              "bullets": list(entry.get("bullets") or [])} for entry in entries]
    hits = [_role_hits(page, entry) for entry in trial]
    if page.terms:
        _cast(page, trial, at=0)
    total = len(page.terms)
    return [
        {
            "index": i,
            "hits": hits[i],
            "total": total,
            "off": bool(entries[i].get("off")),
            "recommend_off": bool(trial[i].get("off")),
        }
        for i in range(len(entries))
    ]


def fill(base_cv: dict, index: Index, terms: list[str], verdicts: Mapping[str, str],
         labels: Mapping[str, str], evidence: Mapping[str, str] | None = None,
         ) -> tuple[dict, list[Card]]:
    """``base_cv`` with every chosen slot filled for ``terms``, and the cards.

    Edits ``base_cv`` in place and returns it; callers pass a copy.
    """
    page = _Page(index, terms, verdicts, labels, evidence or {})
    cv = base_cv
    # A summary or a skills line with nothing that passes keeps the base text:
    # an empty one would break the page, and the base is his.
    for i, section in _sections(cv, "SUMMARY", "paragraph"):
        old = section.get("text") or ""
        section["text"] = page.pick(("summary", ""), f"sections.{i}.text", old) or old
    for i, section in _sections(cv, "SKILL", "labeled"):
        for j, item in enumerate(section.get("items") or []):
            slot = ("skills", " ".join(str(item.get("label") or "").split()).casefold())
            old = item.get("text") or ""
            item["text"] = page.pick(slot, f"sections.{i}.items.{j}.text", old) or old
    for i, section in _sections(cv, "EXPERIENCE", "entries"):
        entries = section.get("entries") or []
        _cast(page, entries, i)
        for j, entry in enumerate(entries):
            if entry.get("off"):
                continue
            slot = ("bullets", company(entry.get("left")))
            room = len(entry.get("bullets") or [])
            cap = (entry.get("budget") or {}).get("bullets")
            if isinstance(cap, int):
                room = min(room, cap)
            old = list(entry.get("bullets") or [])
            # A position nothing can fill is left out: a struck line does not
            # come back, and no line prints twice.
            picked = [page.pick(slot, f"sections.{i}.entries.{j}.bullets.{k}",
                                old[k] if k < len(old) else "")
                      for k in range(room)]
            entry["bullets"] = [text for text in picked if text]
    for i, section in _sections(cv, "ADDITIONAL", "labeled"):
        items = section.get("items") or []
        for j, item in enumerate(items):
            item["text"] = page.pick(("additional", ""), f"sections.{i}.items.{j}.text",
                                     item.get("text") or "")
        section["items"] = [item for item in items if item.get("text")]
    if terms:
        missing = score_against(_printed(cv), terms).missing
        if missing:
            page.cards.append(Card("unanswered", ("", ""), "", ", ".join(missing),
                                   keywords=tuple(missing)))
    return cv, page.cards


def blank(cv: dict) -> dict:
    """``cv`` as a frame for the chat to write into: headings, education,
    skill labels and each role's name, place, title and dates stay; the
    summary, skill text, bullets and Additional Information are emptied.

    Edits ``cv`` in place and returns it; callers pass a copy.
    """
    for section in cv.get("sections") or []:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading") or "").upper()
        if "EDUCATION" in heading:
            continue
        if section.get("type") == "paragraph":
            section["text"] = ""
        for item in section.get("items") or []:
            if isinstance(item, dict):
                item["text"] = ""
        for entry in section.get("entries") or []:
            if isinstance(entry, dict):
                entry["bullets"] = []
    return cv


def shorten(cv: dict, index: Index, terms: list[str], verdicts: Mapping[str, str],
            over: Iterable[tuple[str, int]]) -> list[str]:
    """Swap each over-long bullet for the best shorter line of its slot.

    ``over`` is (entry label, 1-based bullet) as the page measured it. A
    bullet with no shorter line that passed the check stays as it is; the
    budget warning he already sees covers it.
    """
    swapped: list[str] = []
    printed = set(_printed(cv))
    for i, section in _sections(cv, "EXPERIENCE", "entries"):
        for j, entry in enumerate(section.get("entries") or []):
            for label, bullet in over:
                bullets = entry.get("bullets") or []
                if label != entry.get("left") or not 0 < bullet <= len(bullets):
                    continue
                current = bullets[bullet - 1]
                slot = ("bullets", company(entry.get("left")))
                for line, _ in index.rank(slot, terms):
                    good = verdicts.get(line.text) or ("pass" if line.base else "held")
                    if good == "pass" and len(line.text) < len(current) and line.text not in printed:
                        bullets[bullet - 1] = line.text
                        printed.add(line.text)
                        swapped.append(f"sections.{i}.entries.{j}.bullets.{bullet - 1}")
                        break
    return swapped


def _printed(cv: Mapping[str, Any]) -> list[str]:
    """Every line that prints, for the final count of what is still missing."""
    out: list[str] = []
    for section in cv.get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        out.append(str(section.get("text") or ""))
        for entry in section.get("entries") or []:
            if isinstance(entry, Mapping) and not entry.get("off"):
                out.extend(str(entry.get(k) or "") for k in ("left", "sub_left", "note"))
                out.extend(str(b) for b in entry.get("bullets") or [])
        for item in section.get("items") or []:
            if isinstance(item, Mapping):
                out.append(f"{item.get('label') or ''}: {item.get('text') or ''}")
    return [t for t in out if t.strip()]

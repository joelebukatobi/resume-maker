"""Content checks that protect the things an ATS cares about.

These are advisory: they never rewrite the CV and never block an export. They
report, and the caller decides. Each check is independent, so adding one means
appending a function to :data:`CHECKS` rather than editing the others.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable

from .model import PHONE_SHAPE, CV, CoverLetter, EntrySection, LabeledSection, ParagraphSection

# Headings most applicant tracking systems map to a known section. Anything
# else still exports; it simply lands in the parser's unclassified bucket.
KNOWN_HEADINGS = frozenset(
    {
        "PROFESSIONAL SUMMARY",
        "SUMMARY",
        "PROFESSIONAL EXPERIENCE",
        "EXPERIENCE",
        "WORK EXPERIENCE",
        "EMPLOYMENT HISTORY",
        "EDUCATION",
        "SKILLS",
        "TECHNICAL SKILLS",
        "CERTIFICATIONS",
        "PROJECTS",
        "PUBLICATIONS",
        "ADDITIONAL INFORMATION",
    }
)

_DIGITS = re.compile(r"\d")
_BARE_LABEL = re.compile(r"\A(linkedin|my website|website|portfolio|github|email)\Z", re.I)


class Level(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Issue:
    level: Level
    where: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"[{self.level.value}] {self.where}: {self.message}"


Check = Callable[[CV], Iterable[Issue]]


def check_phone(cv: CV) -> Iterable[Issue]:
    """A US number needs ten digits after the country code, or parsers drop it."""
    for index, item in enumerate(cv.contact):
        if item.href or not PHONE_SHAPE.match(item.text):
            continue
        digits = _DIGITS.findall(item.text)
        national = digits[1:] if item.text.lstrip().startswith("+1") else digits
        if len(national) != 10:
            yield Issue(
                Level.ERROR,
                f"contact[{index}]",
                f"phone {item.text!r} has {len(national)} national digits, "
                "expected 10 for a US number",
            )


def check_link_text(cv: CV) -> Iterable[Issue]:
    """Many parsers keep anchor text and discard the target, losing the URL."""
    for index, item in enumerate(cv.contact):
        if item.href and _BARE_LABEL.match(item.text):
            yield Issue(
                Level.WARNING,
                f"contact[{index}]",
                f"link text {item.text!r} hides the address; show the URL itself "
                "so it survives a parser that strips hyperlinks",
            )


def check_headings(cv: CV) -> Iterable[Issue]:
    for index, section in enumerate(cv.sections):
        if section.heading.upper() not in KNOWN_HEADINGS:
            yield Issue(
                Level.WARNING,
                f"sections[{index}]",
                f"heading {section.heading!r} is not one most parsers recognise",
            )


def check_not_empty(cv: CV) -> Iterable[Issue]:
    for index, section in enumerate(cv.sections):
        empty = (
            (isinstance(section, ParagraphSection) and not section.text)
            or (isinstance(section, EntrySection) and not section.entries)
            or (isinstance(section, LabeledSection) and not section.items)
        )
        if empty:
            yield Issue(
                Level.WARNING, f"sections[{index}]", f"{section.heading} is empty"
            )


def check_dates_normalised(cv: CV) -> Iterable[Issue]:
    """Catches a stray dash the normaliser could not recognise as a date range."""
    for section in cv.sections:
        if not isinstance(section, EntrySection):
            continue
        for entry in section.entries:
            for value in (entry.right, entry.sub_right):
                if value and any(dash in value for dash in "–—−"):
                    yield Issue(
                        Level.WARNING,
                        entry.left,
                        f"{value!r} still contains a non-standard dash",
                    )


# Punctuation the user has banned in their own writing. Proof-reading for a
# character is work a regex does instantly and a person does badly, and the
# model forgets the rule often enough to be worth catching here as well.
BANNED_PUNCTUATION = {
    "—": "em dash",
    "–": "en dash",
    ";": "semicolon",
}
# A colon is fine where a labelled line prints one, so it is checked only in
# prose: a summary, a bullet, a cover letter paragraph.
_PROSE_COLON = re.compile(r"\S\s*:\s")


def _prose_of(cv: CV) -> Iterable[tuple[str, str]]:
    """Every stretch of the CV that is written as sentences, with its label."""
    for section in cv.sections:
        if isinstance(section, ParagraphSection):
            yield section.heading, section.text
        elif isinstance(section, EntrySection):
            for entry in section.entries:
                for n, bullet in enumerate(entry.bullets):
                    yield f"{entry.left} bullet {n + 1}", bullet
                if entry.note:
                    yield f"{entry.left} note", entry.note
        elif isinstance(section, LabeledSection):
            for item in section.items:
                yield f"{section.heading} / {item.label}", item.text


def check_punctuation(cv: CV) -> Iterable[Issue]:
    for where, text in _prose_of(cv):
        for mark, name in BANNED_PUNCTUATION.items():
            if mark in text:
                yield Issue(Level.WARNING, where, f"contains a {name}")
        if _PROSE_COLON.search(text):
            yield Issue(Level.WARNING, where, "contains a colon")


# A letter's failure modes are not a CV's. It is prose, so nobody searches it
# for keywords, and the things that go wrong are the rules the user has had to
# repeat: a missing greeting, the company's real name where a placeholder
# belongs, punctuation they have banned.
_GREETS = ("dear", "hello", "hi ")


def letter_issues(letter: CoverLetter) -> tuple[Issue, ...]:
    """Checks for a cover letter, which ``validate`` does not cover."""
    found: list[Issue] = []
    greeting = (letter.greeting or "").strip()
    if not greeting:
        found.append(Issue(Level.WARNING, "greeting", "the letter opens with no greeting"))
    elif not greeting.lower().startswith(_GREETS):
        found.append(Issue(Level.WARNING, "greeting",
                           "does not read as a greeting; a reader meets this first"))

    opening = (letter.paragraphs[0] if letter.paragraphs else "")
    if opening and "my name is" not in opening.lower():
        found.append(Issue(Level.WARNING, "paragraphs[0]",
                           "does not introduce you by name before the first fact"))

    # The placeholders are what let a letter be reused for the next
    # application. A real name written in is a letter that only works once.
    written = " ".join([greeting, *letter.paragraphs, letter.closing or ""]).lower()
    for value, label in ((letter.company, "company"), (letter.role, "role")):
        name = (value or "").strip()
        if len(name) > 2 and name.lower() in written:
            found.append(Issue(Level.WARNING, label,
                               f"{name!r} is written out; use [{label.capitalize()}]"
                               " so the letter can be reused"))

    for index, paragraph in enumerate(letter.paragraphs):
        for mark, name in BANNED_PUNCTUATION.items():
            if mark in paragraph:
                found.append(Issue(Level.WARNING, f"paragraphs[{index}]",
                                   f"contains a {name}"))
        if _PROSE_COLON.search(paragraph):
            found.append(Issue(Level.WARNING, f"paragraphs[{index}]", "contains a colon"))
    return tuple(found)


CHECKS: tuple[Check, ...] = (
    check_phone,
    check_link_text,
    check_headings,
    check_not_empty,
    check_dates_normalised,
    check_punctuation,
)


def validate(cv: CV) -> tuple[Issue, ...]:
    """Run every check and return the issues found, errors first."""
    issues = [issue for check in CHECKS for issue in check(cv)]
    issues.sort(key=lambda issue: 0 if issue.level is Level.ERROR else 1)
    return tuple(issues)

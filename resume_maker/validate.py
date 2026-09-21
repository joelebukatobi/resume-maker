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

from .model import PHONE_SHAPE, CV, EntrySection, LabeledSection, ParagraphSection

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


CHECKS: tuple[Check, ...] = (
    check_phone,
    check_link_text,
    check_headings,
    check_not_empty,
    check_dates_normalised,
)


def validate(cv: CV) -> tuple[Issue, ...]:
    """Run every check and return the issues found, errors first."""
    issues = [issue for check in CHECKS for issue in check(cv)]
    issues.sort(key=lambda issue: 0 if issue.level is Level.ERROR else 1)
    return tuple(issues)

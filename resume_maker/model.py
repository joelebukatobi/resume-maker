"""CV content model.

Plain immutable data. Renderers depend on these types; these types depend on
no renderer. Adding a new section kind means adding a dataclass here and one
handler in each renderer -- nothing existing gets edited.

All input is treated as untrusted: parsing rejects unknown keys, wrong types,
over-long strings and unsafe URL schemes rather than passing them through to a
document.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

MAX_TEXT = 8_000
MAX_ITEMS = 200
SAFE_SCHEMES = ("http://", "https://", "mailto:")
PHONE_SHAPE = re.compile(r"\A\+?[\d][\d\s().\-]{5,}\Z")
_WORD = re.compile(r"[^\W\d_]+")

_DATE_SEPARATORS = re.compile(r"\s*[‐-―−-]\s*")
_DATE_RANGE = re.compile(
    r"\A(?P<start>[A-Za-z]+\.?\s+\d{4}|\d{4})"
    r"(?P<sep>\s*[‐-―−-]\s*)"
    r"(?P<end>[A-Za-z]+\.?\s+\d{4}|\d{4}|Present|present|Current|current)\Z"
)


class ContentError(ValueError):
    """Raised when CV content is malformed or unsafe."""


# ---------------------------------------------------------------- helpers


def _text(value: Any, *, where: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ContentError(f"{where}: expected a string, got {type(value).__name__}")
    cleaned = value.strip()
    if not cleaned and not allow_empty:
        raise ContentError(f"{where}: must not be empty")
    if len(cleaned) > MAX_TEXT:
        raise ContentError(f"{where}: longer than {MAX_TEXT} characters")
    if "\x00" in cleaned:
        raise ContentError(f"{where}: contains a null byte")
    return cleaned


def _optional_text(value: Any, *, where: str) -> str | None:
    if value is None:
        return None
    cleaned = _text(value, where=where, allow_empty=True)
    return cleaned or None


def _mapping(value: Any, *, where: str, allowed: Iterable[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContentError(f"{where}: expected an object")
    allowed = set(allowed)
    unknown = set(value) - allowed
    if unknown:
        raise ContentError(f"{where}: unknown keys: {', '.join(sorted(unknown))}")
    return dict(value)


def _sequence(value: Any, *, where: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ContentError(f"{where}: expected a list")
    items = list(value)
    if len(items) > MAX_ITEMS:
        raise ContentError(f"{where}: more than {MAX_ITEMS} items")
    return items


def _safe_url(value: Any, *, where: str) -> str | None:
    if value is None:
        return None
    url = _text(value, where=where)
    lowered = url.lower()
    if not lowered.startswith(SAFE_SCHEMES):
        raise ContentError(
            f"{where}: only http, https and mailto links are allowed (got {url!r})"
        )
    if any(ch in url for ch in "<>\"'\\\n\r\t"):
        raise ContentError(f"{where}: link contains illegal characters")
    return url


def normalise_dates(value: str, separator: str) -> str:
    """Rewrite any dash used between two dates to the configured separator.

    Leaves ordinary hyphenated words (``end-to-end``) untouched by matching the
    whole string against a date-range shape first.
    """
    match = _DATE_RANGE.match(value.strip())
    if not match:
        return value
    return f"{match['start']} {separator} {match['end']}"


# ----------------------------------------------------------------- model


@dataclass(frozen=True, slots=True)
class ContactItem:
    """One bio detail. ``header``/``footer`` choose where it is printed."""

    text: str
    href: str | None = None
    header: bool = True
    footer: bool = False

    @classmethod
    def parse(cls, data: Any, *, where: str) -> "ContactItem":
        raw = _mapping(data, where=where, allowed={"text", "href", "header", "footer"})
        flags = {}
        for key, default in (("header", True), ("footer", False)):
            value = raw.get(key, default)
            if not isinstance(value, bool):
                raise ContentError(f"{where}.{key}: must be true or false")
            flags[key] = value
        return cls(
            text=_text(raw.get("text"), where=f"{where}.text"),
            href=_safe_url(raw.get("href"), where=f"{where}.href"),
            **flags,
        )


def header_items(contact: Iterable[ContactItem]) -> tuple[ContactItem, ...]:
    return tuple(item for item in contact if item.header)


@dataclass(frozen=True, slots=True)
class Entry:
    """One job or degree.

    ``left``/``right`` is the organisation line, ``sub_left``/``sub_right`` the
    role or degree line. Each pair renders as a single line whose left text sits
    on the left margin and whose right text ends on the right margin.
    """

    left: str
    right: str = ""
    sub_left: str | None = None
    sub_right: str | None = None
    sub_italic: bool = False
    note: str | None = None
    bullets: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def parse(cls, data: Any, *, where: str, separator: str) -> "Entry":
        raw = _mapping(
            data,
            where=where,
            allowed={
                "left",
                "right",
                "sub_left",
                "sub_right",
                "sub_italic",
                "note",
                "bullets",
            },
        )
        sub_italic = raw.get("sub_italic", False)
        if not isinstance(sub_italic, bool):
            raise ContentError(f"{where}.sub_italic: must be true or false")

        right = _optional_text(raw.get("right"), where=f"{where}.right") or ""
        sub_right = _optional_text(raw.get("sub_right"), where=f"{where}.sub_right")

        bullets = tuple(
            _text(item, where=f"{where}.bullets[{i}]")
            for i, item in enumerate(_sequence(raw.get("bullets"), where=f"{where}.bullets"))
        )

        return cls(
            left=_text(raw.get("left"), where=f"{where}.left"),
            right=normalise_dates(right, separator),
            sub_left=_optional_text(raw.get("sub_left"), where=f"{where}.sub_left"),
            sub_right=normalise_dates(sub_right, separator) if sub_right else None,
            sub_italic=sub_italic,
            note=_optional_text(raw.get("note"), where=f"{where}.note"),
            bullets=bullets,
        )

    def column_pairs(self) -> tuple[tuple[str, str], ...]:
        """Every line in this entry that must lap both margins."""
        pairs = [(self.left, self.right)]
        if self.sub_left or self.sub_right:
            pairs.append((self.sub_left or "", self.sub_right or ""))
        return tuple(pairs)


@dataclass(frozen=True, slots=True)
class LabeledItem:
    label: str
    text: str

    @classmethod
    def parse(cls, data: Any, *, where: str) -> "LabeledItem":
        raw = _mapping(data, where=where, allowed={"label", "text"})
        return cls(
            label=_text(raw.get("label"), where=f"{where}.label"),
            text=_text(raw.get("text"), where=f"{where}.text"),
        )


@dataclass(frozen=True, slots=True)
class Section:
    heading: str


@dataclass(frozen=True, slots=True)
class ParagraphSection(Section):
    """A single justified block of prose, e.g. the professional summary."""

    text: str = ""


@dataclass(frozen=True, slots=True)
class EntrySection(Section):
    """Education or experience: organisation lines, role lines, bullets."""

    entries: tuple[Entry, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class LabeledSection(Section):
    """Skills or additional information: a bold label, then regular text."""

    items: tuple[LabeledItem, ...] = field(default_factory=tuple)


_SECTION_KEYS = {
    "paragraph": {"heading", "type", "text"},
    "entries": {"heading", "type", "entries"},
    "labeled": {"heading", "type", "items"},
}


def _parse_section(data: Any, *, where: str, separator: str) -> Section:
    if not isinstance(data, Mapping):
        raise ContentError(f"{where}: expected an object")
    kind = data.get("type")
    if kind not in _SECTION_KEYS:
        raise ContentError(
            f"{where}.type: must be one of {', '.join(sorted(_SECTION_KEYS))}"
        )
    raw = _mapping(data, where=where, allowed=_SECTION_KEYS[kind])
    heading = _text(raw.get("heading"), where=f"{where}.heading")

    if kind == "paragraph":
        return ParagraphSection(
            heading=heading, text=_text(raw.get("text"), where=f"{where}.text")
        )
    if kind == "entries":
        items = _sequence(raw.get("entries"), where=f"{where}.entries")
        return EntrySection(
            heading=heading,
            entries=tuple(
                Entry.parse(item, where=f"{where}.entries[{i}]", separator=separator)
                for i, item in enumerate(items)
            ),
        )
    items = _sequence(raw.get("items"), where=f"{where}.items")
    return LabeledSection(
        heading=heading,
        items=tuple(
            LabeledItem.parse(item, where=f"{where}.items[{i}]")
            for i, item in enumerate(items)
        ),
    )


@dataclass(frozen=True, slots=True)
class CV:
    name: str
    contact: tuple[ContactItem, ...]
    sections: tuple[Section, ...]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, date_separator: str = "-") -> "CV":
        raw = _mapping(data, where="cv", allowed={"name", "contact", "sections"})
        contact = _sequence(raw.get("contact"), where="cv.contact")
        sections = _sequence(raw.get("sections"), where="cv.sections")
        return cls(
            name=_text(raw.get("name"), where="cv.name"),
            contact=tuple(
                ContactItem.parse(item, where=f"cv.contact[{i}]")
                for i, item in enumerate(contact)
            ),
            sections=tuple(
                _parse_section(item, where=f"cv.sections[{i}]", separator=date_separator)
                for i, item in enumerate(sections)
            ),
        )

    @classmethod
    def load(cls, path: str | Path, *, date_separator: str = "-") -> "CV":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ContentError("cv file must contain a JSON object")
        return cls.from_mapping(data, date_separator=date_separator)

    def column_pairs(self) -> tuple[tuple[str, str], ...]:
        """Every two-column line in the document, for the width check."""
        pairs: list[tuple[str, str]] = []
        for section in self.sections:
            if isinstance(section, EntrySection):
                for entry in section.entries:
                    pairs.extend(entry.column_pairs())
        return tuple(pairs)

    def links(self) -> tuple[str, ...]:
        return tuple(item.href for item in self.contact if item.href)


@dataclass(frozen=True, slots=True)
class CoverLetter:
    name: str
    contact: tuple[ContactItem, ...]
    role: str
    company: str
    greeting: str
    paragraphs: tuple[str, ...]
    closing: str
    signoff: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CoverLetter":
        raw = _mapping(
            data,
            where="cover",
            allowed={
                "name",
                "contact",
                "role",
                "company",
                "greeting",
                "paragraphs",
                "closing",
                "signoff",
            },
        )
        contact = _sequence(raw.get("contact"), where="cover.contact")
        paragraphs = _sequence(raw.get("paragraphs"), where="cover.paragraphs")
        return cls(
            name=_text(raw.get("name"), where="cover.name"),
            contact=tuple(
                ContactItem.parse(item, where=f"cover.contact[{i}]")
                for i, item in enumerate(contact)
            ),
            role=_text(raw.get("role"), where="cover.role", allow_empty=True),
            company=_text(raw.get("company"), where="cover.company", allow_empty=True),
            greeting=_text(raw.get("greeting"), where="cover.greeting", allow_empty=True),
            paragraphs=tuple(
                _text(item, where=f"cover.paragraphs[{i}]", allow_empty=True)
                for i, item in enumerate(paragraphs)
            ),
            closing=_text(raw.get("closing"), where="cover.closing", allow_empty=True),
            signoff=_text(raw.get("signoff"), where="cover.signoff", allow_empty=True),
        )

    @classmethod
    def load(cls, path: str | Path) -> "CoverLetter":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ContentError("cover file must contain a JSON object")
        return cls.from_mapping(data)

    def fill(self, text: str) -> str:
        return text.replace("[Role]", self.role).replace("[Company]", self.company)

    def signature(self) -> tuple[str, tuple[ContactItem, ...]]:
        """Sign-off block, drawn from the bio so nothing is typed twice.

        Returns the name in title case plus every bio item ticked for the
        footer, in bio order.
        """
        return title_case(self.name), tuple(c for c in self.contact if c.footer)


def title_case(name: str) -> str:
    """``JOEL ONWUANAKU`` -> ``Joel Onwuanaku``; leaves mixed-case words alone.

    Only all-caps words are rewritten, so ``McDonald`` typed as such survives.
    """
    return " ".join(
        _WORD.sub(lambda m: m.group().capitalize(), word) if word.isupper() else word
        for word in name.split()
    )

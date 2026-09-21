"""Font choices offered by the editor.

The curated list is limited to faces that ship with Microsoft Office or are
installed as standard on Windows and macOS. That matters for DOCX: a font the
recipient does not have is silently substituted, and the document reflows on
their screen. PDF embeds its font, so it is not affected.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

# (name, css stack, ships with Word on Windows and macOS, note)
_CATALOGUE: tuple[tuple[str, str, bool, str], ...] = (
    ("Cambria", "Cambria, Caladea, Georgia, serif", True, "Serif. Recommended default."),
    ("Georgia", "Georgia, 'Noto Serif', serif", True, "Serif, wider, uses more space."),
    ("Garamond", "Garamond, 'EB Garamond', Georgia, serif", True, "Serif, runs small."),
    ("Calibri", "Calibri, Carlito, 'Segoe UI', sans-serif", True, "Sans, common default."),
    ("Arial", "Arial, Liberation Sans, Helvetica, sans-serif", True, "Sans, universal."),
    ("Helvetica", "Helvetica, Arial, sans-serif", True, "Sans."),
    ("Times New Roman", "'Times New Roman', Liberation Serif, serif", True, "Serif, traditional."),
    ("Verdana", "Verdana, DejaVu Sans, sans-serif", True, "Sans, wide."),
    ("Tahoma", "Tahoma, DejaVu Sans, sans-serif", True, "Sans, compact."),
    (
        "EB Garamond 12",
        "'EB Garamond 12', 'EB Garamond', Garamond, serif",
        False,
        "Not installed on most machines; safe for PDF, reflows in DOCX.",
    ),
)


@dataclass(frozen=True, slots=True)
class FontOption:
    name: str
    stack: str
    ships_with_office: bool
    note: str
    installed: bool


def _installed_families() -> frozenset[str]:
    """Families fontconfig can resolve locally; empty if fontconfig is absent."""
    binary = shutil.which("fc-list")
    if not binary:
        return frozenset()
    try:
        result = subprocess.run(  # noqa: S603 - fixed binary, argument list, no shell
            [binary, ":", "family"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    families: set[str] = set()
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        for alias in line.split(","):
            families.add(alias.strip().casefold())
    return frozenset(families)


def catalogue() -> tuple[FontOption, ...]:
    """The font list shown in the editor, annotated with local availability."""
    installed = _installed_families()
    return tuple(
        FontOption(
            name=name,
            stack=stack,
            ships_with_office=ships,
            note=note,
            installed=not installed or name.casefold() in installed,
        )
        for name, stack, ships, note in _CATALOGUE
    )


# -- bitmap-free faces for the preview ---------------------------------------
#
# Some Office fonts (Cambria Bold, the whole Calibri family, Courier New) carry
# embedded bitmap strikes (EBDT/EBLC) for a handful of small pixel sizes. Chrome
# on Linux uses them, and their advance widths disagree with the outlines, so
# at those sizes glyphs widen or vanish and text breaks apart. Which sizes you
# hit depends on display scaling and window width. The preview therefore loads
# the theme font from a copy with the bitmap tables removed; the PDF is printed
# from outlines and never had the problem.

import re as _re
import struct as _struct
from functools import lru_cache as _lru_cache

_BITMAP_TABLES = frozenset({b"EBDT", b"EBLC", b"EBSC", b"DSIG"})
_FACE_NAME = _re.compile(r"\A[A-Za-z0-9 .\-']{1,100}\Z")


def strip_bitmaps(data: bytes) -> bytes:
    """Return a TrueType/OpenType font without its embedded bitmap tables."""
    if data[:4] == b"ttcf":
        raise ValueError("font collections are not supported")
    count = _struct.unpack(">H", data[4:6])[0]
    records = [_struct.unpack(">4sIII", data[12 + 16 * i : 28 + 16 * i]) for i in range(count)]
    keep = [r for r in records if r[0] not in _BITMAP_TABLES]
    if len(keep) == len(records):
        return data
    n = len(keep)
    selector = max(i for i in range(16) if 2**i <= n)
    search = 16 * 2**selector
    header = data[:4] + _struct.pack(">HHHH", n, search, selector, n * 16 - search)
    offset = 12 + 16 * n
    directory, body = b"", b""
    for tag, checksum, start, length in keep:
        table = data[start : start + length]
        directory += _struct.pack(">4sIII", tag, checksum, offset + len(body), length)
        body += table + b"\0" * (-len(table) % 4)
    return header + directory + body


@_lru_cache(maxsize=32)
def preview_face(family: str, bold: bool, italic: bool) -> bytes | None:
    """The installed face for ``family`` with bitmaps removed, or None.

    None means "let the browser use the system font": the family is not
    installed (fontconfig would substitute another), or the file is a
    collection we do not rewrite.
    """
    if not _FACE_NAME.match(family):
        return None
    binary = shutil.which("fc-match")
    if not binary:
        return None
    style = ":".join(s for s in ("bold" if bold else "", "italic" if italic else "") if s)
    pattern = f"{family}:{style}" if style else family
    try:
        result = subprocess.run(  # noqa: S603 - fixed binary, argument list, no shell
            [binary, "-f", "%{family}\n%{file}", pattern],
            check=False, capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = result.stdout.decode("utf-8", "replace").splitlines()
    if len(lines) < 2:
        return None
    families = {name.strip().casefold() for name in lines[0].split(",")}
    if family.casefold() not in families:
        return None
    try:
        with open(lines[1], "rb") as handle:
            return strip_bitmaps(handle.read())
    except (OSError, ValueError, _struct.error):
        return None

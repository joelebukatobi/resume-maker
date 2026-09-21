"""Typography and page geometry.

Single source of truth for every measurement used by both renderers. If a
number describing the page appears anywhere else in this package, it is a bug:
the DOCX and the PDF must agree, and they only agree because they read here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

TWIPS_PER_INCH = 1440
TWIPS_PER_POINT = 20
# Below this, text is unreadable and ATS-hostile; almost always a typo.
MIN_FONT_PT = 6
# Divider under the CV header: stroke weight and gap above it, in points.
RULE_WEIGHT_PT = 0.5
RULE_COLOR = "404040"  # dark grey
RULE_GAP_PT = 6

_HEX_COLOR = re.compile(r"\A[0-9A-Fa-f]{6}\Z")
# Font names reach both an XML attribute and a CSS declaration, so keep
# them to characters that cannot terminate either context.
_FONT_NAME = re.compile(r"\A[A-Za-z0-9 ,.\-']{1,200}\Z")


class ThemeError(ValueError):
    """Raised when a theme file cannot produce a well formed document."""


@dataclass(frozen=True, slots=True)
class Theme:
    font: str
    font_stack: str

    size_name_pt: float
    size_contact_pt: float
    size_heading_pt: float
    size_body_pt: float

    page_width_in: float
    page_height_in: float
    margin_in: float

    space_before_heading_pt: float
    space_after_heading_pt: float
    space_between_entries_pt: float

    bullet_char: str
    bullet_indent_in: float
    bullet_hang_in: float

    line_height: float
    date_separator: str
    link_color: str
    link_underline: bool

    # -- geometry -------------------------------------------------------
    # The right tab stop lives at the right margin. It is a coordinate on
    # the page, derived from page width and margins alone -- never from font
    # metrics, text length, or character count. That is precisely why
    # changing the font cannot move it.

    @property
    def text_width_in(self) -> float:
        return self.page_width_in - 2 * self.margin_in

    @property
    def right_tab_twips(self) -> int:
        return round(self.text_width_in * TWIPS_PER_INCH)

    @property
    def margin_twips(self) -> int:
        return round(self.margin_in * TWIPS_PER_INCH)

    @property
    def page_width_twips(self) -> int:
        return round(self.page_width_in * TWIPS_PER_INCH)

    @property
    def page_height_twips(self) -> int:
        return round(self.page_height_in * TWIPS_PER_INCH)

    @property
    def bullet_indent_twips(self) -> int:
        return round(self.bullet_indent_in * TWIPS_PER_INCH)

    @property
    def bullet_hang_twips(self) -> int:
        return round(self.bullet_hang_in * TWIPS_PER_INCH)

    @staticmethod
    def half_points(pt: float) -> int:
        """Word expresses font size in half-points."""
        return round(pt * 2)

    @staticmethod
    def twips(pt: float) -> int:
        """Word expresses vertical spacing in twentieths of a point."""
        return round(pt * TWIPS_PER_POINT)

    # -- construction ---------------------------------------------------

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Theme":
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ThemeError(f"unknown theme keys: {', '.join(sorted(unknown))}")
        missing = known - set(data)
        if missing:
            raise ThemeError(f"missing theme keys: {', '.join(sorted(missing))}")
        try:
            theme = cls(**dict(data))
        except TypeError as exc:  # pragma: no cover - guarded by checks above
            raise ThemeError(str(exc)) from exc
        theme.validate()
        return theme

    @classmethod
    def load(cls, path: str | Path) -> "Theme":
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ThemeError("theme file must contain a JSON object")
        return cls.from_mapping(data)

    def validate(self) -> None:
        if not isinstance(self.font, str) or not _FONT_NAME.match(self.font):
            raise ThemeError("font must be a plain font name")
        if not isinstance(self.font_stack, str) or not _FONT_NAME.match(self.font_stack):
            raise ThemeError("font_stack must be a comma separated list of font names")

        for field in (
            "size_name_pt",
            "size_contact_pt",
            "size_heading_pt",
            "size_body_pt",
            "page_width_in",
            "page_height_in",
            "line_height",
        ):
            value = getattr(self, field)
            if not isinstance(value, (int, float)) or value <= 0:
                raise ThemeError(f"{field} must be a positive number")
            if field.startswith("size_") and value < MIN_FONT_PT:
                raise ThemeError(f"{field} must be at least {MIN_FONT_PT}pt")

        for field in (
            "margin_in",
            "space_before_heading_pt",
            "space_after_heading_pt",
            "space_between_entries_pt",
            "bullet_indent_in",
            "bullet_hang_in",
        ):
            value = getattr(self, field)
            if not isinstance(value, (int, float)) or value < 0:
                raise ThemeError(f"{field} must be a non-negative number")

        if self.text_width_in <= 0:
            raise ThemeError("margins leave no usable text width")
        if self.bullet_hang_in > self.bullet_indent_in:
            raise ThemeError("bullet_hang_in must not exceed bullet_indent_in")
        if len(self.bullet_char) != 1 or self.bullet_char.isspace():
            raise ThemeError("bullet_char must be a single visible character")
        if not self.date_separator.strip():
            raise ThemeError("date_separator must not be blank")
        if not _HEX_COLOR.match(self.link_color):
            raise ThemeError("link_color must be a 6 digit hex colour, e.g. 0563C1")
        if not isinstance(self.link_underline, bool):
            raise ThemeError("link_underline must be true or false")

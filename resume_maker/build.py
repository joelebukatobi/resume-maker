"""Orchestration: content + theme in, files out.

The single place that knows how a build is sequenced. The CLI and the local
server both call :class:`Builder`, so neither can drift from the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from .export_pdf import Browser, page_count
from .model import CV, title_case, CoverLetter
from .render_docx import DocxRenderer
from .render_html import HtmlRenderer
from .theme import Theme
from .validate import Issue, validate

FORMAT_ALIASES: Mapping[str, tuple[str, ...]] = {
    "docx": ("docx",),
    "pdf": ("pdf",),
    "html": ("html",),
    "both": ("docx", "pdf"),
    "all": ("docx", "pdf", "html"),
}

_SAFE_STEM = "-_. "


def safe_stem(stem: str) -> str:
    """Reduce a user-supplied file stem to something that cannot escape a directory.

    Spaces survive (the export names read like a document title); separators,
    dot runs and anything exotic do not.
    """
    cleaned = "".join(ch for ch in stem if ch.isalnum() or ch in _SAFE_STEM)
    cleaned = " ".join(cleaned.replace("..", " ").split()).strip(". ")
    return cleaned or "cv"


def export_name(kind: str, name: str, company: str = "", role: str = "") -> str:
    """``Joel Onwuanaku - CV - Meta - Product Manager``.

    The company and role come from the cover letter, which is where the
    application being worked on is recorded; either may be missing, and the
    name simply gets shorter.
    """
    label = "Cover Letter" if kind == "cover" else "CV"
    parts = [title_case(name.strip()) or "Document", label]
    parts += [p.strip() for p in (company, role) if p and p.strip()]
    return safe_stem(" - ".join(parts))


@dataclass(frozen=True, slots=True)
class BuildResult:
    issues: tuple[Issue, ...] = ()
    overflowing: tuple[str, ...] = ()
    written: tuple[Path, ...] = ()
    pages: int | None = None
    artifacts: dict[str, bytes] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.overflowing and not any(
            issue.level.value == "error" for issue in self.issues
        )


class Builder:
    """Renders a CV in every requested format."""

    def __init__(
        self,
        cv: CV,
        theme: Theme,
        *,
        letter: CoverLetter | None = None,
        browser: str | None = None,
    ) -> None:
        self._cv = cv
        self._theme = theme
        self._letter = letter
        self._browser_hint = browser
        self._docx = DocxRenderer(theme)
        self._html = HtmlRenderer(theme)

    @classmethod
    def from_files(
        cls,
        cv_path: str | Path,
        theme_path: str | Path,
        *,
        browser: str | None = None,
    ) -> "Builder":
        theme = Theme.load(theme_path)
        cv = CV.load(cv_path, date_separator=theme.date_separator)
        return cls(cv, theme, browser=browser)

    # -- rendering ------------------------------------------------------

    def preview_html(self) -> str:
        return self._html.render(self._cv, preview=True, letter=self._letter)

    def print_html(self) -> str:
        return self._html.render(self._cv, preview=False, letter=self._letter)

    def docx_bytes(self) -> bytes:
        return self._docx.render(self._cv, self._letter)

    def build(
        self,
        out_dir: str | Path,
        *,
        stem: str = "cv",
        formats: str | Sequence[str] = "both",
        dry_run: bool = False,
    ) -> BuildResult:
        wanted = (
            FORMAT_ALIASES[formats] if isinstance(formats, str) else tuple(formats)
        )
        unknown = set(wanted) - {"docx", "pdf", "html"}
        if unknown:
            raise ValueError(f"unknown format(s): {', '.join(sorted(unknown))}")

        issues = validate(self._cv)
        print_html = self.print_html()

        overflowing: tuple[str, ...] = ()
        pages: int | None = None
        artifacts: dict[str, bytes] = {}
        written: list[Path] = []

        needs_browser = "pdf" in wanted or not dry_run
        browser: Browser | None = None
        if needs_browser:
            browser = Browser.locate(self._browser_hint)
            overflowing = browser.measure_overflow(print_html)

        out = Path(out_dir)
        stem = safe_stem(stem)

        if "docx" in wanted:
            artifacts["docx"] = self.docx_bytes()
        if "html" in wanted:
            artifacts["html"] = print_html.encode("utf-8")

        if "pdf" in wanted and browser is not None:
            out.mkdir(parents=True, exist_ok=True)
            pdf_path = out / f"{stem}.pdf"
            browser.to_pdf(print_html, pdf_path)
            data = pdf_path.read_bytes()
            artifacts["pdf"] = data
            pages = page_count(data)
            if dry_run:
                pdf_path.unlink(missing_ok=True)
            else:
                written.append(pdf_path)

        if not dry_run:
            out.mkdir(parents=True, exist_ok=True)
            for suffix in ("docx", "html"):
                if suffix in artifacts:
                    path = out / f"{stem}.{suffix}"
                    path.write_bytes(artifacts[suffix])
                    written.append(path)

        return BuildResult(
            issues=issues,
            overflowing=overflowing,
            written=tuple(written),
            pages=pages,
            artifacts=artifacts,
        )

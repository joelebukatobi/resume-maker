"""PDF export and line-width measurement, both driven by headless Chrome.

Chrome is invoked with an argument list (never a shell string) inside a private
profile directory, so it cannot attach to the user's running browser session or
touch their profile. Temporary files live in a 0700 directory that is removed
afterwards.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT = 60

_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
)

_TITLE = re.compile(r"<title>OVERFLOW:(?P<payload>\[.*?\])</title>", re.S)


class BrowserNotFound(RuntimeError):
    """Raised when no Chromium-family browser is installed."""


class ExportError(RuntimeError):
    """Raised when the browser fails to produce output."""


@dataclass(frozen=True, slots=True)
class Browser:
    """A located Chromium-family binary."""

    path: str

    @classmethod
    def locate(cls, explicit: str | None = None) -> "Browser":
        if explicit:
            found = shutil.which(explicit)
            if not found:
                raise BrowserNotFound(f"browser not found: {explicit}")
            return cls(found)
        for name in _CANDIDATES:
            found = shutil.which(name)
            if found:
                return cls(found)
        raise BrowserNotFound(
            "no Chromium-family browser found; install Chrome or Chromium, "
            f"or pass one of: {', '.join(_CANDIDATES)}"
        )

    def _run(self, args: list[str], *, timeout: int) -> subprocess.CompletedProcess:
        return subprocess.run(  # noqa: S603 - fixed binary, argument list, no shell
            [self.path, *args],
            check=False,
            capture_output=True,
            timeout=timeout,
        )

    def to_pdf(self, html: str, destination: Path, *, timeout: int = DEFAULT_TIMEOUT) -> Path:
        """Render ``html`` to a PDF at ``destination``."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with _workspace(html) as (profile, source):
            result = self._run(
                [
                    "--headless=new",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-extensions",
                    "--disable-background-networking",
                    f"--user-data-dir={profile}",
                    "--run-all-compositor-stages-before-draw",
                    "--virtual-time-budget=3000",
                    "--no-pdf-header-footer",
                    f"--print-to-pdf={destination}",
                    source.as_uri(),
                ],
                timeout=timeout,
            )
        if not destination.exists() or destination.stat().st_size == 0:
            raise ExportError(
                "browser produced no PDF: "
                + (result.stderr.decode("utf-8", "replace").strip() or "no output")
            )
        return destination

    def measure_overflow(
        self, html: str, *, timeout: int = DEFAULT_TIMEOUT
    ) -> tuple[str, ...]:
        """Return the left-hand label of every two-column line that overflows."""
        with _workspace(html) as (profile, source):
            result = self._run(
                [
                    "--headless=new",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-extensions",
                    "--disable-background-networking",
                    f"--user-data-dir={profile}",
                    "--virtual-time-budget=3000",
                    "--dump-dom",
                    source.as_uri(),
                ],
                timeout=timeout,
            )
        match = _TITLE.search(result.stdout.decode("utf-8", "replace"))
        if not match:
            raise ExportError("could not measure the page; browser returned no report")
        try:
            payload = json.loads(match.group("payload"))
        except json.JSONDecodeError as exc:
            raise ExportError("overflow report was not valid JSON") from exc
        return tuple(str(item) for item in payload)


class _workspace:
    """A private 0700 directory holding the browser profile and source page."""

    def __init__(self, html: str) -> None:
        self._html = html
        self._root: Path | None = None

    def __enter__(self) -> tuple[Path, Path]:
        self._root = Path(tempfile.mkdtemp(prefix="resume-maker-"))
        self._root.chmod(0o700)
        source = self._root / "cv.html"
        source.write_text(self._html, encoding="utf-8")
        source.chmod(0o600)
        profile = self._root / "profile"
        profile.mkdir(mode=0o700)
        return profile, source

    def __exit__(self, *exc_info: object) -> None:
        if self._root is not None:
            shutil.rmtree(self._root, ignore_errors=True)


_PAGE_COUNT = re.compile(rb"/Type\s*/Page[^s]")


def page_count(pdf: Path | bytes) -> int:
    """Number of pages in a PDF, for the one-page fit check."""
    data = pdf if isinstance(pdf, bytes) else Path(pdf).read_bytes()
    return len(_PAGE_COUNT.findall(data))

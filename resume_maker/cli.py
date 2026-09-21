"""Command line entry point: ``python -m resume_maker``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .build import BuildResult, Builder
from .export_pdf import BrowserNotFound, ExportError
from .model import ContentError
from .theme import ThemeError
from .validate import Level


def _report(result: BuildResult, *, stream) -> None:
    for issue in result.issues:
        print(f"  {issue}", file=stream)
    for label in result.overflowing:
        print(
            f"  [error] {label}: left and right text do not fit on one line; "
            "shorten one side",
            file=stream,
        )
    if result.pages and result.pages > 1:
        print(
            f"  [warning] output is {result.pages} pages; reduce "
            "space_between_entries_pt, line_height or size_body_pt in theme.json "
            "to fit one page",
            file=stream,
        )
    for path in result.written:
        print(f"  wrote {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="resume_maker", description="Render a CV to DOCX and PDF."
    )
    parser.add_argument("--cv", default="cv.json", type=Path, help="content file")
    parser.add_argument("--theme", default="theme.json", type=Path, help="theme file")
    parser.add_argument("--out", default="out", type=Path, help="output directory")
    parser.add_argument(
        "--format",
        default="both",
        choices=("docx", "pdf", "html", "both", "all"),
        help="what to write (default: both)",
    )
    parser.add_argument("--name", default="cv", help="output file stem")
    parser.add_argument("--browser", default=None, help="browser binary for PDF export")
    parser.add_argument(
        "--check-only", action="store_true", help="validate without writing files"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        builder = Builder.from_files(args.cv, args.theme, browser=args.browser)
        result = builder.build(
            args.out, stem=args.name, formats=args.format, dry_run=args.check_only
        )
    except (ThemeError, ContentError) as exc:
        print(f"invalid input: {exc}", file=sys.stderr)
        return 2
    except BrowserNotFound as exc:
        print(f"{exc}", file=sys.stderr)
        return 3
    except ExportError as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 4

    _report(result, stream=sys.stderr)
    blocking = [i for i in result.issues if i.level is Level.ERROR] or result.overflowing
    if blocking:
        print("completed with errors", file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

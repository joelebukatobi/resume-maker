#!/usr/bin/env python3
"""Local editor for resume-maker.

Binds to the loopback interface only. There is no authentication because there
is no network exposure: the socket is not reachable from another machine. The
handler still refuses cross-origin requests, caps request bodies, serves a
fixed set of static files, and writes only to the two JSON paths it was started
with.

    python3 server.py [--port 8000] [--cv cv.json] [--theme theme.json]
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import webbrowser
from dataclasses import asdict
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from resume_maker.build import FORMAT_ALIASES, Builder, safe_stem
from resume_maker.export_pdf import BrowserNotFound, ExportError
from resume_maker.fonts import catalogue, preview_face
from resume_maker.model import CV, ContentError, CoverLetter
from resume_maker.theme import Theme, ThemeError

MAX_BODY = 1 << 20  # 1 MiB is far more than any CV needs.
HOST = "127.0.0.1"
STATIC_ROOT = Path(__file__).resolve().parent / "static"

_MEDIA_TYPES = {
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    "pdf": ("application/pdf", "pdf"),
    "html": ("text/html; charset=utf-8", "html"),
}


class EditorHandler(BaseHTTPRequestHandler):
    server_version = "resume-maker"
    sys_version = ""

    def __init__(
        self,
        *args: Any,
        cv_path: Path,
        theme_path: Path,
        cover_path: Path,
        cover_theme_path: Path,
        out_dir: Path,
        **kwargs: Any,
    ) -> None:
        self._cv_path = cv_path
        self._theme_path = theme_path
        self._cover_path = cover_path
        self._cover_theme_path = cover_theme_path
        self._out_dir = out_dir
        super().__init__(*args, **kwargs)

    # -- plumbing -------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} {fmt % args}")

    def _send(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        extra: dict[str, str] | None = None,
        cache: str = "no-store",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", cache)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: HTTPStatus, payload: Any) -> None:
        self._send(
            status,
            json.dumps(payload).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json(status, {"error": message})

    def _same_origin(self) -> bool:
        """Reject requests initiated by another origin (a page in another tab)."""
        origin = self.headers.get("Origin")
        if origin is None:
            return True  # curl and the like send no Origin; they are not a browser.
        allowed = {
            f"http://{HOST}:{self.server.server_address[1]}",
            f"http://localhost:{self.server.server_address[1]}",
        }
        return origin in allowed

    def _read_json(self) -> dict[str, Any] | None:
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "expected application/json")
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "bad Content-Length")
            return None
        if length <= 0 or length > MAX_BODY:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body out of range")
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, f"invalid JSON: {exc}")
            return None
        if not isinstance(payload, dict):
            self._error(HTTPStatus.BAD_REQUEST, "expected a JSON object")
            return None
        return payload

    def _builder(self, payload: dict[str, Any]) -> Builder:
        kind = payload.get("document", "cv")
        theme_data = payload["cover_theme"] if kind == "cover" else payload["theme"]
        theme = Theme.from_mapping(theme_data)
        cv = CV.from_mapping(payload["cv"], date_separator=theme.date_separator)
        letter = CoverLetter.from_mapping(payload["cover"]) if kind == "cover" else None
        return Builder(cv, theme, letter=letter)

    # -- routes ---------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html", "/resume", "/cover-letter"):
            return self._static("index.html", "text/html; charset=utf-8")
        if route == "/api/document":
            return self._document()
        if route == "/api/fonts":
            return self._json(HTTPStatus.OK, [asdict(f) for f in catalogue()])
        if route == "/api/font-face":
            return self._font_face()
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def _font_face(self) -> None:
        """Serve an installed font face without embedded bitmaps (preview only).

        Read-only, and only ever a file fontconfig resolves for the requested
        family. The preview iframe is sandboxed (opaque origin), so the font
        needs a CORS header to load.
        """
        query = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
        family = (query.get("family") or [""])[0]
        bold = (query.get("bold") or ["0"])[0] == "1"
        italic = (query.get("italic") or ["0"])[0] == "1"
        data = preview_face(family, bold, italic)
        if data is None:
            return self._error(HTTPStatus.NOT_FOUND, "font not available")
        self._send(
            HTTPStatus.OK,
            data,
            "font/ttf",
            extra={"Access-Control-Allow-Origin": "*"},
            cache="max-age=3600",
        )

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        if not self._same_origin():
            return self._error(HTTPStatus.FORBIDDEN, "cross-origin request refused")
        route = self.path.split("?", 1)[0]
        handlers = {
            "/api/preview": self._preview,
            "/api/export": self._export,
            "/api/save": self._save,
        }
        handler = handlers.get(route)
        if handler is None:
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        payload = self._read_json()
        if payload is None:
            return
        try:
            handler(payload)
        except KeyError as exc:
            self._error(HTTPStatus.BAD_REQUEST, f"missing field: {exc}")
        except (ThemeError, ContentError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except BrowserNotFound as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
        except ExportError as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def _static(self, name: str, content_type: str) -> None:
        path = STATIC_ROOT / name
        # Fixed filenames only; nothing here is derived from the request path.
        if not path.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        self._send(HTTPStatus.OK, path.read_bytes(), content_type)

    def _document(self) -> None:
        try:
            theme = Theme.load(self._theme_path)
            cover_theme = (
                Theme.load(self._cover_theme_path)
                if self._cover_theme_path.is_file()
                else theme
            )
            cv = CV.load(self._cv_path, date_separator=theme.date_separator)
            cover = CoverLetter.load(self._cover_path)
        except (ThemeError, ContentError, OSError, json.JSONDecodeError) as exc:
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
        self._json(
            HTTPStatus.OK,
            {
                "cv": json.loads(self._cv_path.read_text(encoding="utf-8")),
                "theme": asdict(theme),
                "cover": json.loads(self._cover_path.read_text(encoding="utf-8")),
                "cover_theme": asdict(cover_theme),
            },
        )

    def _preview(self, payload: dict[str, Any]) -> None:
        builder = self._builder(payload)
        result = builder.build(".", formats=(), dry_run=True)
        self._json(
            HTTPStatus.OK,
            {
                "html": builder.preview_html(),
                "issues": [
                    {"level": i.level.value, "where": i.where, "message": i.message}
                    for i in result.issues
                ],
            },
        )

    def _export(self, payload: dict[str, Any]) -> None:
        fmt = str(payload.get("format", "pdf"))
        if fmt not in _MEDIA_TYPES:
            return self._error(HTTPStatus.BAD_REQUEST, f"unknown format: {fmt}")
        builder = self._builder(payload)
        stem = safe_stem(str(payload.get("stem", "cv")))
        with tempfile.TemporaryDirectory(prefix="resume-maker-export-") as tmp:
            result = builder.build(tmp, stem=stem, formats=FORMAT_ALIASES[fmt])
            data = result.artifacts[fmt]
        media_type, extension = _MEDIA_TYPES[fmt]
        self._send(
            HTTPStatus.OK,
            data,
            media_type,
            extra={
                "Content-Disposition": f'attachment; filename="{stem}.{extension}"',
                "X-Overflowing": json.dumps(list(result.overflowing)),
                "X-Pages": str(result.pages or ""),
            },
        )

    def _save(self, payload: dict[str, Any]) -> None:
        # Parse before writing: a malformed document never reaches disk.
        cv_theme = Theme.from_mapping(payload["theme"])
        cover_theme = Theme.from_mapping(payload["cover_theme"])
        cv = CV.from_mapping(payload["cv"], date_separator=cv_theme.date_separator)
        cover = CoverLetter.from_mapping(payload["cover"])
        _atomic_write(self._cv_path, json.dumps(payload["cv"], indent=2, ensure_ascii=False))
        _atomic_write(self._theme_path, json.dumps(payload["theme"], indent=2))
        _atomic_write(self._cover_path, json.dumps(payload["cover"], indent=2, ensure_ascii=False))
        _atomic_write(self._cover_theme_path, json.dumps(payload["cover_theme"], indent=2))

        kind = payload.get("document", "cv")
        response: dict[str, Any] = {"saved": True, "written": [], "pages": None}
        try:
            if kind == "cover":
                result = Builder(cv, cover_theme, letter=cover).build(
                    self._out_dir, stem="cover", formats="both"
                )
            else:
                result = Builder(cv, cv_theme).build(self._out_dir, stem="cv", formats="both")
        except (BrowserNotFound, ExportError) as exc:
            # The JSON is already safely on disk; say what could not be rebuilt.
            response["build_error"] = str(exc)
        else:
            response["written"] = [str(path) for path in result.written]
            response["pages"] = result.pages
            response["overflowing"] = list(result.overflowing)
        self._json(HTTPStatus.OK, response)


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temporary file in the same directory, then rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text + "\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local resume editor.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--cv", type=Path, default=Path("cv.json"))
    parser.add_argument("--theme", type=Path, default=Path("theme.json"))
    parser.add_argument("--cover", type=Path, default=Path("cover.json"))
    parser.add_argument("--cover-theme", type=Path, default=Path("cover-theme.json"))
    parser.add_argument("--out", type=Path, default=Path("out"),
                        help="directory rebuilt on every save")
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    args = parser.parse_args(argv)

    handler = partial(
        EditorHandler,
        cv_path=args.cv.resolve(),
        theme_path=args.theme.resolve(),
        cover_path=args.cover.resolve(),
        cover_theme_path=args.cover_theme.resolve(),
        out_dir=args.out.resolve(),
    )
    with ThreadingHTTPServer((HOST, args.port), handler) as httpd:
        url = f"http://{HOST}:{httpd.server_address[1]}/resume"
        print(f"resume-maker editor on {url}  (ctrl-c to stop)")
        if not args.no_open:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
import re
import tempfile
import threading
import webbrowser
from dataclasses import asdict
from datetime import date
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from html import escape
from urllib.parse import parse_qs, quote

from resume_maker.applications import UNNAMED, ApplicationError, Workspace, as_json
from resume_maker.build import FORMAT_ALIASES, Builder, export_name, safe_stem
from resume_maker.export_pdf import BrowserNotFound, ExportError
from resume_maker import assistant, fill, jev, keywords, library, llm, match, setup, transcribe
from resume_maker.fonts import catalogue, preview_face
from resume_maker.model import CV, ContentError, CoverLetter
from resume_maker.theme import Theme, ThemeError

# route -> (file, page id). The page id selects the active nav chip.
# library.json is read, changed and written back by several handlers, on a
# threaded server. The check spends seconds in Jev between its read and its
# write, so it never holds this: it merges what it learned into a fresh read.
_LIBRARY_LOCK = threading.Lock()

PAGES = {
    "/": ("home.html", "hub"),
    "/voice": ("voice.html", "voice"),
    "/notes": ("notes.html", "notes"),
    "/review": ("review.html", "review"),
}
# The documents hang off the application they belong to:
# /meta-rotational-product-manager/resume. The slug on its own opens the CV.
DOCUMENT_PAGES = {"": "resume", "resume": "resume", "cover-letter": "cover"}

MAX_BODY = 1 << 20  # 1 MiB is far more than any CV needs.
MAX_IMPORT_BODY = 8 << 20  # a 5 MB file, base64 encoded
MAX_HISTORY = 2 << 20
MAX_TURNS = 200
MAX_CHAT_CHARS = 400_000  # ceiling on the condensed transcript sent to a model
DOCUMENTS = ("cv", "cover")
HOST = "127.0.0.1"
STATIC_ROOT = Path(__file__).resolve().parent / "static"
# The redesigned frontend's own files: one folder, CSS and JS, one name shape.
UI_ROOT = STATIC_ROOT / "ui"
_UI_NAME = re.compile(r"[a-z0-9][a-z0-9-]*\.(css|js)")
_UI_TYPES = {"css": "text/css; charset=utf-8", "js": "text/javascript; charset=utf-8"}

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
        workspace: Workspace,
        out_dir: Path,
        voice_path: Path,
        notes_path: Path,
        **kwargs: Any,
    ) -> None:
        self._workspace = workspace
        self._voice_path = voice_path
        self._notes_path = notes_path
        self._out_dir = out_dir
        super().__init__(*args, **kwargs)

    # -- the open application -------------------------------------------
    #
    # Every document path is read through here, so switching application is
    # one write to one file: the next request already reads the new folder.

    def _document_page(self, route: str) -> tuple[str, str] | None:
        """``("meta-rotational-pm", "resume")`` for an application's page.

        Returns None for anything that is not one — ``/api/...``, a static
        file, or a slug with no application behind it — so the caller falls
        through to its own 404.
        """
        parts = route.strip("/").split("/")
        if not parts or len(parts) > 2 or parts[0] in ("api", "static"):
            return None
        tail = parts[1] if len(parts) == 2 else ""
        page = DOCUMENT_PAGES.get(tail)
        if page is None:
            return None
        try:
            self._workspace.find(parts[0])
        except ApplicationError:
            return None
        return parts[0], page

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _app(self) -> str:
        """Which application this request is about.

        The slug travels with the request — in the query for a GET, in the
        body for a POST — so two tabs on two applications cannot write over
        each other. Without one, the open application is used.
        """
        query = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
        slug = (query.get("app") or [""])[0]
        return slug or self._workspace.current

    def _paths(self, slug: str = "") -> dict[str, Path]:
        return self._workspace.paths(slug or self._app())

    @property
    def _cv_path(self) -> Path:
        return self._paths()["cv"]

    @property
    def _theme_path(self) -> Path:
        return self._paths()["theme"]

    @property
    def _cover_path(self) -> Path:
        return self._paths()["cover"]

    @property
    def _cover_theme_path(self) -> Path:
        return self._paths()["cover_theme"]

    @property
    def _chat_path(self) -> Path:
        return self._paths()["chat"]

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

    def _read_json(self, limit: int = MAX_BODY) -> dict[str, Any] | None:
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "expected application/json")
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "bad Content-Length")
            return None
        if length <= 0 or length > limit:
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
        if route in PAGES:
            return self._page(*PAGES[route])
        # /resume and /cover-letter from before applications existed, and any
        # bookmark of them, land in whichever application is open.
        # The Voice page was called Style until the file was renamed.
        if route == "/style":
            return self._redirect("/voice")
        if route in ("/resume", "/cover-letter", "/index.html"):
            tail = "resume" if route != "/cover-letter" else "cover-letter"
            try:
                return self._redirect(f"/{self._workspace.current}/{tail}")
            except ApplicationError as exc:
                return self._error(HTTPStatus.NOT_FOUND, str(exc))
        document_page = self._document_page(route)
        if document_page is not None:
            return self._page("index.html", document_page[1], app=document_page[0])
        if route == "/api/setup":
            return self._setup_get()
        if route == "/api/setup/templates":
            return self._json(HTTPStatus.OK, setup.templates())
        if route == "/api/transcribe":
            # Lets the editor choose local dictation when an engine is present.
            return self._json(HTTPStatus.OK, {"available": transcribe.available()})
        if route == "/static/chat.js":
            return self._static("chat.js", "text/javascript; charset=utf-8")
        if route == "/static/apps.js":
            return self._static("apps.js", "text/javascript; charset=utf-8")
        if route == "/static/setup.js":
            return self._static("setup.js", "text/javascript; charset=utf-8")
        if route == "/static/setup-chat.js":
            return self._static("setup-chat.js", "text/javascript; charset=utf-8")
        if route == "/static/base.css":
            return self._static("base.css", "text/css; charset=utf-8")
        if route == "/static/chat.css":
            return self._static("chat.css", "text/css; charset=utf-8")
        if route == "/static/setup.css":
            return self._static("setup.css", "text/css; charset=utf-8")
        if route == "/api/document":
            return self._document()
        if route == "/api/fonts":
            return self._json(HTTPStatus.OK, [asdict(f) for f in catalogue()])
        if route == "/api/font-face":
            return self._font_face()
        if route == "/api/llm/providers":
            return self._json(HTTPStatus.OK, [
                {"id": p.id, "label": p.label, "available": p.available,
                 "key_env": p.key_env, "source": p.key_source}
                for p in llm.PROVIDERS.values()
            ])
        if route == "/api/llm/models":
            return self._models()
        if route == "/api/chat/history":
            return self._history_get()
        if route == "/api/apps":
            return self._apps_get()
        if route.startswith("/static/ui/"):
            return self._ui_static(route[len("/static/ui/"):])
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
            "/api/chat": self._chat,
            "/api/import": self._import,
            "/api/chat/history": self._history_put,
            "/api/chat/fields": self._fields,
            "/api/setup/voice": self._setup_voice,
            "/api/setup/notes": self._setup_notes,
            "/api/setup/extract": self._setup_extract,
            "/api/llm/key": self._key,
            "/api/match": self._match,
            "/api/keywords": self._keywords,
            "/api/fit": self._fit,
            "/api/transcribe": self._transcribe,
            "/api/apps": self._apps_post,
            "/api/library": self._library_post,
            "/api/gaps": self._gaps,
        }
        handler = handlers.get(route)
        if handler is None:
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        big_body = route in ("/api/import", "/api/setup/extract", "/api/setup/notes",
                             "/api/transcribe")
        payload = self._read_json(MAX_IMPORT_BODY if big_body else MAX_BODY)
        if payload is None:
            return
        try:
            handler(payload)
        except KeyError as exc:
            self._error(HTTPStatus.BAD_REQUEST, f"missing field: {exc}")
        # ImportFailed subclasses ValueError: it must be caught first, or a
        # bad upload reports as a plain bad request.
        except assistant.ImportFailed as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc))
        # TranscriptionFailed is a ValueError too: no local engine is a 501, not
        # a bad request, and it must be caught before the plain ValueError.
        except transcribe.TranscriptionFailed as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
        except (ThemeError, ContentError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except BrowserNotFound as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
        except ExportError as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
        except llm.LLMError as exc:
            self._error(HTTPStatus.BAD_GATEWAY, str(exc))

    def _page(self, name: str, page: str, app: str = "") -> None:
        """An HTML page with the shared top-bar nav inserted and tagged.

        Every page carries the same nav from ``_nav.html``; ``data-page``
        on <body> tells the CSS which chip is active, so the first paint is
        already right.
        """
        path = STATIC_ROOT / name
        if not path.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        html = path.read_text(encoding="utf-8")
        try:
            slug = app or self._workspace.current
            found = next(a for a in self._workspace.list() if a.slug == slug)
            name, app_id = found.name, found.id
        except (ApplicationError, StopIteration):
            slug, name, app_id = "", "No application", ""
        # The nav's document links point into this application, and its name
        # sits between the nav and the page's own buttons.
        nav = (STATIC_ROOT / "_nav.html").read_text(encoding="utf-8").strip()
        nav = nav.replace("{app_name}", escape(name))
        html = html.replace("<!-- site-nav -->", nav, 1).replace(
            '<body data-page="">',
            f'<body data-page="{page}" data-app="{escape(slug)}"'
            f' data-app-id="{escape(app_id)}"'
            f'{" data-unnamed=\"1\"" if name == UNNAMED else ""}>', 1
        )
        # Any link in any page can point into the open application.
        html = html.replace("{app}", quote(slug))
        if "<!-- site-chat -->" in html:
            chat = (STATIC_ROOT / "_chat.html").read_text(encoding="utf-8").strip()
            html = html.replace("<!-- site-chat -->", chat, 1)
        self._send(HTTPStatus.OK, html.encode("utf-8"), "text/html; charset=utf-8")

    def _static(self, name: str, content_type: str) -> None:
        path = STATIC_ROOT / name
        # Fixed filenames only; nothing here is derived from the request path.
        if not path.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        self._send(HTTPStatus.OK, path.read_bytes(), content_type)

    def _ui_static(self, name: str) -> None:
        """A file from static/ui/, the redesigned frontend's folder.

        The name is matched whole against one shape before it touches the
        disk, so no slash, dot segment, escape or other extension gets
        through. The resolved path is checked again, so a link inside the
        folder cannot point anywhere else.
        """
        match = _UI_NAME.fullmatch(name)
        if match is None:
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        root = UI_ROOT.resolve()
        path = (root / name).resolve()
        if path.parent != root or not path.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        self._send(HTTPStatus.OK, path.read_bytes(), _UI_TYPES[match.group(1)])

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

    @staticmethod
    def _export_name(payload: dict[str, Any]) -> str:
        """``Joel Onwuanaku - CV - Meta - Product Manager``.

        The role and company live on the cover letter — that is where the
        application being worked on is recorded — and name both documents.
        """
        kind = payload.get("document", "cv")
        doc = payload.get(kind if kind == "cover" else "cv") or {}
        cover = payload.get("cover") or {}
        return export_name(kind, str(doc.get("name", "")),
                           str(cover.get("company", "")), str(cover.get("role", "")))

    def _export(self, payload: dict[str, Any]) -> None:
        fmt = str(payload.get("format", "pdf"))
        if fmt not in _MEDIA_TYPES:
            return self._error(HTTPStatus.BAD_REQUEST, f"unknown format: {fmt}")
        builder = self._builder(payload)
        stem = self._export_name(payload)
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
        paths = self._paths(str(payload.get("app") or ""))
        cv_theme = Theme.from_mapping(payload["theme"])
        cover_theme = Theme.from_mapping(payload["cover_theme"])
        cv = CV.from_mapping(payload["cv"], date_separator=cv_theme.date_separator)
        cover = CoverLetter.from_mapping(payload["cover"])
        _atomic_write(paths["cv"], json.dumps(payload["cv"], indent=2, ensure_ascii=False))
        _atomic_write(paths["theme"], json.dumps(payload["theme"], indent=2))
        _atomic_write(paths["cover"], json.dumps(payload["cover"], indent=2, ensure_ascii=False))
        _atomic_write(paths["cover_theme"], json.dumps(payload["cover_theme"], indent=2))

        kind = payload.get("document", "cv")
        response: dict[str, Any] = {"saved": True, "written": [], "pages": None}
        # Naming an application saves the documents purely so they are no
        # longer the untouched copies a re-seed may replace. The files in out/
        # are named after the company on the cover letter, which the posting
        # has not been read for yet, so building here would write a second set
        # under the previous application's name.
        if payload.get("build") is False:
            return self._json(HTTPStatus.OK, response)
        # out/ keeps the same names as an export, so each application's files
        # stand on their own instead of overwriting the last one.
        stem = self._export_name(payload)
        try:
            if kind == "cover":
                result = Builder(cv, cover_theme, letter=cover).build(
                    self._out_dir, stem=stem, formats="both"
                )
            else:
                result = Builder(cv, cv_theme).build(self._out_dir, stem=stem, formats="both")
        except (BrowserNotFound, ExportError) as exc:
            # The JSON is already safely on disk; say what could not be rebuilt.
            response["build_error"] = str(exc)
        else:
            response["written"] = [str(path) for path in result.written]
            response["pages"] = result.pages
            response["overflowing"] = list(result.overflowing)
        self._json(HTTPStatus.OK, response)


    # -- chat --------------------------------------------------------

    @staticmethod
    def _document_kind(value: Any) -> str:
        if value not in DOCUMENTS:
            raise ValueError("document must be 'cv' or 'cover'")
        return value

    @staticmethod
    def _checked_document(kind: str, data: Any) -> dict[str, Any]:
        """The editor's unsaved document, validated before it reaches a model."""
        if not isinstance(data, dict):
            raise ValueError("doc must be an object")
        if kind == "cover":
            CoverLetter.from_mapping(data)
        else:
            CV.from_mapping(data)
        return data

    @staticmethod
    def _checked_turns(messages: Any) -> list[dict[str, str]]:
        if not isinstance(messages, list) or not messages or len(messages) > MAX_TURNS:
            raise ValueError("messages must be a non-empty list")
        turns = []
        for m in messages:
            if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
                raise ValueError("each message needs role user or assistant")
            content = m.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("each message needs text content")
            turn = {"role": m["role"], "content": content}
            # Which proposals the user already accepted or dismissed, so the
            # condensed transcript can drop their JSON on the way to the model.
            resolved = m.get("resolved")
            if isinstance(resolved, dict):
                keep = {str(k): str(v) for k, v in resolved.items()
                        if v in ("accepted", "dismissed")}
                if keep:
                    turn["resolved"] = keep
            turns.append(turn)
        if turns[-1]["role"] != "user":
            raise ValueError("the last message must be from the user")
        return turns

    def _key(self, payload: dict[str, Any]) -> None:
        """Connect (test + save) or disconnect a provider. The key is never echoed."""
        provider_id = str(payload.get("provider", ""))
        key = payload.get("key") or ""
        if not isinstance(key, str):
            raise ValueError("key must be text")
        if key.strip():
            models = llm.connect(provider_id, key.strip())
            return self._json(HTTPStatus.OK, {"connected": True, "models": models})
        llm.save_key(provider_id, "")
        self._json(HTTPStatus.OK, {"connected": False})

    # -- setup pillars ---------------------------------------------------

    def _voice_and_notes(self) -> tuple[str, str]:
        """The user's Voice and Notes files, empty when not written yet."""
        return assistant.load_text(self._voice_path), assistant.load_text(self._notes_path)

    def _setup_get(self) -> None:
        voice, notes = self._voice_and_notes()
        self._json(HTTPStatus.OK, {
            "voice": voice,
            "notes": notes,
            "status": setup.readiness(voice, notes, _read_json(self._cv_path), _read_json(self._cover_path)),
        })

    def _save_setup_file(self, kind: str, path: Path, payload: dict[str, Any]) -> None:
        text = setup.check_text(kind, payload.get("text", ""))
        _atomic_write(path, text)
        voice, notes = self._voice_and_notes()
        self._json(HTTPStatus.OK, {
            "saved": True,
            "status": setup.readiness(voice, notes, _read_json(self._cv_path), _read_json(self._cover_path)),
        })

    def _setup_extract(self, payload: dict[str, Any]) -> None:
        """Plain text from an uploaded file, for pasting into Notes.

        No model involved: this is the same extractor the import flow uses.
        """
        text = assistant.extract_text(
            str(payload.get("filename", "")), assistant.decode_upload(str(payload.get("file", "")))
        )
        self._json(HTTPStatus.OK, {"text": text})

    def _transcribe(self, payload: dict[str, Any]) -> None:
        """A 16 kHz WAV recorded in the browser, transcribed on this machine.

        The audio never leaves the loopback server: a local whisper.cpp binary
        does the work, so dictation is not tied to a cloud speech service.
        """
        audio = payload.get("audio")
        if not isinstance(audio, str) or not audio.strip():
            raise ValueError("no audio sent")
        self._json(HTTPStatus.OK, {"text": transcribe.transcribe(assistant.decode_upload(audio))})

    def _setup_voice(self, payload: dict[str, Any]) -> None:
        """Save the Style page, or add a rule the chat offered to the end of it.

        Like Notes, an "append" is the chat's only way into this file, and only
        because the user accepted the card, so the rule arrives reviewed.
        """
        if "append" in payload:
            current = assistant.load_text(self._voice_path)
            text = setup.append_text(current, payload.get("append"),
                                     today=date.today().isoformat(), kind="voice")
            payload = {"text": text}
        self._save_setup_file("voice", self._voice_path, payload)

    def _setup_notes(self, payload: dict[str, Any]) -> None:
        """Save the Notes page, or add what the chat offered to the end of it.

        An "append" is the chat's only way into this file, and it only happens
        because the user accepted the card, so the text arrives reviewed. It
        arrives in the assistant's words all the same, and is filed as such:
        his own sentences come in through the keyword answer, never here, so
        nothing a page sends can claim them.
        """
        if "append" in payload:
            current = assistant.load_text(self._notes_path)
            text = setup.append_text(current, payload.get("append"),
                                     today=date.today().isoformat(), source="assistant")
            payload = {"text": text}
        elif isinstance(payload.get("skill"), dict):
            # A tool he said yes to, filed in his skills list under its label.
            skill = payload["skill"]
            text = setup.add_skill(assistant.load_text(self._notes_path),
                                   str(skill.get("label") or ""), str(skill.get("tool") or ""))
            payload = {"text": text}
        self._save_setup_file("notes", self._notes_path, payload)

    def _fields(self, payload: dict[str, Any]) -> None:
        kind = self._document_kind(payload.get("document"))
        doc = self._checked_document(kind, payload.get("doc"))
        self._json(HTTPStatus.OK, [
            {"path": path, "label": label, "text": text}
            for path, label, text in assistant.field_paths(kind, doc)
        ])

    def _models(self) -> None:
        query = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
        try:
            models = llm.list_models((query.get("provider") or [""])[0])
        except llm.LLMError as exc:
            return self._error(HTTPStatus.BAD_GATEWAY, str(exc))
        self._json(HTTPStatus.OK, models)  # [{"id", "label"}]

    def _match(self, payload: dict[str, Any]) -> None:
        """How much of the job description this document already answers.

        Counted here rather than asked of a model: it costs nothing, it gives
        the same answer twice, and the missing terms can be pointed at.
        """
        kind = self._document_kind(payload.get("document"))
        doc = self._checked_document(kind, payload.get("doc"))
        jd = str(payload.get("job_description") or "")[:40_000]
        parts = [text for _, _, text in assistant.field_paths(kind, doc, printed_only=True)]
        # One ruler. The ring used to count the posting's repeated words until a
        # list was read and then switch to the list without saying so, which
        # showed 52 and then 25 for the same CV. A list the browser sends is
        # ignored: the one counted is the one kept beside this posting.
        stored = keywords.load(self._paths(str(payload.get("app") or ""))["keywords"])
        if not keywords.is_current(stored, jd):
            return self._json(HTTPStatus.OK, {"score": None, "matched": [], "missing": [],
                                              "needs_list": True})
        self._json(HTTPStatus.OK,
                   match.score_against(parts, stored["keywords"]).as_json())

    def _keywords(self, payload: dict[str, Any]) -> None:
        """The posting's keyword list and its map, kept beside the application.

        ``get`` reports what is kept and whether it was read from the posting
        now in the chat. ``read`` asks for a fresh list and map, replacing any
        earlier one. ``answer`` and ``decline`` record what the user said about
        one keyword, in the notes first, then in that keyword's map entry only:
        rewriting the whole map would re-roll lines he has already used.
        """
        slug = str(payload.get("app") or "")
        path = self._paths(slug)["keywords"]
        action = str(payload.get("action") or "read")
        if action == "get":
            return self._json(HTTPStatus.OK, self._keyword_state(slug))
        if action == "read":
            jd = str(payload.get("job_description") or "")[: assistant.MAX_IMPORT_TEXT]
            if not jd.strip():
                raise ValueError("paste a job description first")
            self._read_keywords(path, jd, payload)
            return self._json(HTTPStatus.OK, self._keyword_state(slug))
        stored = keywords.load(path)
        jd = str(_read_json(self._paths(slug)["chat"]).get("job_description") or "")
        # Saving stamps the list with the posting in the chat. A list read from
        # another posting would come out of that save looking current, and the
        # ring would count the CV against the wrong job.
        if not keywords.is_current(stored, jd):
            raise ValueError("this posting's keyword list is out of date; read a fresh one")
        if action == "remap":
            provider = str(payload.get("provider", ""))
            model = llm.check_model(payload.get("model", ""))
            _, notes = self._voice_and_notes()
            stored["map"] = assistant.parse_map(
                llm.complete(provider, model, assistant.map_prompt(),
                             assistant.map_request(stored["keywords"], notes), temperature=0),
                stored["keywords"])
            keywords.save(path, posting=jd, terms=stored["keywords"], entries=stored["map"],
                          skipped=stored["skipped"])
            return self._json(HTTPStatus.OK, self._keyword_state(slug))
        if action == "skip":
            what = str(payload.get("path") or "").strip()[:120]
            if not what:
                raise ValueError("nothing to skip")
            if what not in stored["skipped"]:
                stored["skipped"].append(what)
            keywords.save(path, posting=jd, terms=stored["keywords"], entries=stored["map"],
                          skipped=stored["skipped"][-100:])
            return self._json(HTTPStatus.OK, self._keyword_state(slug))
        term = str(payload.get("keyword") or "")
        if term not in stored["keywords"]:
            raise ValueError("that keyword is not on this posting's list")
        if action == "decline":
            self._append_note(str(payload.get("line") or "").strip()[:400], source="no")
            stored["map"][term] = {"state": "declined", "from": "", "say": ""}
        elif action == "answer":
            sentence = str(payload.get("sentence") or "").strip()[:1000]
            if not sentence:
                raise ValueError("say what you did, in a sentence")
            provider = str(payload.get("provider", ""))
            if not provider or not payload.get("model"):
                raise ValueError("connect an agent first, so the answer can be matched")
            model = llm.check_model(payload.get("model", ""))
            # The note is written before the model is asked anything, so a
            # failed call cannot lose what he typed. Once: a retry after a
            # failed call would otherwise add the same sentence again.
            _, notes = self._voice_and_notes()
            if sentence not in notes:
                self._append_note(sentence)
                _, notes = self._voice_and_notes()
            reply = llm.complete(provider, model, assistant.map_prompt(),
                                 assistant.map_request([term], notes), temperature=0)
            stored["map"][term] = assistant.parse_map(reply, [term])[term]
        else:
            raise ValueError(f"unknown keyword action: {action}")
        keywords.save(path, posting=jd, terms=stored["keywords"], entries=stored["map"],
                      skipped=stored["skipped"])
        self._json(HTTPStatus.OK, self._keyword_state(slug))

    def _keyword_state(self, slug: str) -> dict[str, Any]:
        paths = self._paths(slug)
        stored = keywords.load(paths["keywords"])
        jd = str(_read_json(paths["chat"]).get("job_description") or "")
        return {"keywords": stored["keywords"], "map": stored["map"],
                 "skipped": stored["skipped"], "current": keywords.is_current(stored, jd)}

    def _read_keywords(self, path: Path, jd: str, payload: dict[str, Any]) -> None:
        """Read a list and its map for ``jd`` and keep them, replacing any earlier."""
        provider = str(payload.get("provider", ""))
        model = llm.check_model(payload.get("model", ""))
        # The application being read, which during naming is not yet the one
        # the request was addressed to.
        company = str(_read_json(path.with_name("cover.json")).get("company") or "")
        terms = assistant.parse_keywords(
            llm.complete(provider, model, assistant.keywords_prompt(),
                         assistant.keywords_request(jd), temperature=0),
            company=company)
        _, notes = self._voice_and_notes()
        entries = assistant.parse_map(
            llm.complete(provider, model, assistant.map_prompt(),
                         assistant.map_request(terms, notes), temperature=0),
            terms)
        keywords.save(path, posting=jd, terms=terms, entries=entries)

    def _append_note(self, text: str, source: str = "his") -> None:
        """His answer, or his no, filed as his: he typed it or pressed it. A no
        is filed as one, so it can never be read as backing what it denies."""
        if not text:
            raise ValueError("nothing to add to the notes")
        current = assistant.load_text(self._notes_path)
        updated = setup.append_text(current, text, today=date.today().isoformat(),
                                    source=source)
        _atomic_write(self._notes_path, updated.rstrip("\n"))

    def _fit(self, payload: dict[str, Any]) -> None:
        """One screening pass: how strong does this candidate look for this role.

        Deliberately a separate, explicit request rather than part of the
        preview. It costs a model call, so it runs when the user asks for it,
        not on every keystroke the way the coverage count does.
        """
        kind = self._document_kind(payload.get("document"))
        doc = self._checked_document(kind, payload.get("doc"))
        jd = str(payload.get("job_description") or "").strip()[:40_000]
        if not jd:
            raise ValueError("paste a job description first")
        reply = llm.complete(
            str(payload.get("provider", "")), llm.check_model(payload.get("model", "")),
            assistant.fit_prompt(), assistant.fit_request(kind, doc, jd),
            temperature=0,
        )
        self._json(HTTPStatus.OK, assistant.parse_fit(reply))

    def _chat(self, payload: dict[str, Any]) -> None:
        """Stream a reply as plain text. A mid-stream failure is sent as a
        line starting with NUL so the editor can tell it from reply text."""
        kind = self._document_kind(payload.get("document"))
        doc = self._checked_document(kind, payload.get("doc"))
        jd = payload.get("job_description") or ""
        if not isinstance(jd, str) or len(jd) > 40_000:
            raise ValueError("job description must be text under 40,000 characters")
        voice, notes = self._voice_and_notes()
        # The editor measured the rendered page; pass that on rather than
        # letting the model guess whether the CV still fits.
        overflowing = payload.get("overflowing")
        over = payload.get("over_budget")
        fit = assistant.fit_note(
            _as_pages(payload.get("pages")),
            [str(x)[:120] for x in overflowing][:8] if isinstance(overflowing, list) else (),
            [str(x)[:160] for x in over][:6] if isinstance(over, list) else (),
        )
        # The same count the ring shows, so the model knows which of the
        # posting's words are missing instead of guessing at them.
        if jd.strip():
            parts = [text for _, _, text in assistant.field_paths(kind, doc, printed_only=True)]
            # The kept keyword list, so the model is told the same thing the
            # ring shows, and the map, so a missing keyword is answered from the
            # user's own work rather than pasted in.
            stored = keywords.load(self._paths(str(payload.get("app") or ""))["keywords"])
            if keywords.is_current(stored, jd):
                covered = match.score_against(parts, stored["keywords"])
                note = assistant.coverage_note(covered.missing, covered.matched)
                fit = f"{fit}\n{note}".strip() if note else fit
                mapped = assistant.map_note(stored, on_page=covered.matched)
                fit = f"{fit}\n{mapped}".strip() if mapped else fit
            # Which jobs answer this posting. Always from the CV, including
            # while the letter is open: the letter argues the CV's case, and
            # the letter inheriting the last posting's argument is what made
            # this necessary.
            cv_data = doc if kind == "cv" else _read_json(self._paths()["cv"])
            lead = assistant.experience_note(cv_data, jd)
            fit = f"{fit}\n{lead}".strip() if lead else fit
        # A tailoring round is an ordinary turn with one extra instruction: the
        # user pasted a posting and typed nothing, so the work they would have
        # had to ask for is described here instead. Sharing /api/chat keeps the
        # streaming, the transcript window and the error handling identical.
        task = ""
        if payload.get("task") == "tailor":
            if kind != "cv":
                raise ValueError("tailoring runs on the CV")
            if not jd.strip():
                raise ValueError("paste a job description first")
            raw = payload.get("settled")
            settled = [str(x)[:120] for x in raw][:20] if isinstance(raw, list) else []
            # Which step of the round this is. The browser runs them in order
            # and asks for one suggestion at a time, so the step decides what
            # the model is being asked for.
            phase = str(payload.get("phase") or "wording")
            if phase not in assistant.PHASES and phase not in assistant.SECTIONS:
                raise ValueError(f"unknown tailoring step: {phase}")
            raw_no = payload.get("declined")
            declined = [str(x)[:120] for x in raw_no][:20] if isinstance(raw_no, list) else []
            task = assistant.tailor_rules(phase, settled=settled, declined=declined)
            if phase in assistant.SECTIONS:
                task = f"{task}\n\n{self._section_note(phase, payload)}"
        # The requirement list the editor read off this posting, if it has one.
        # It is the only thing in the prompt that says what the posting asks
        # for rather than what the notes happen to hold.
        raw_wanted = payload.get("wanted")
        wanted = assistant.wanted_note(
            [str(w)[:60] for w in raw_wanted][: assistant.MAX_KEYWORDS]
            if isinstance(raw_wanted, list) else [])
        if kind == "cv" or jd.strip():
            cv_for_bench = doc if kind == "cv" else _read_json(self._paths()["cv"])
            bench = assistant.bench_note(
                cv_for_bench,
                [str(w)[:60] for w in raw_wanted][: assistant.MAX_KEYWORDS]
                if isinstance(raw_wanted, list) else [],
                jd,
            )
            if bench:
                fit = f"{fit}\n{bench}".strip()
        stable, volatile = assistant.prompt_parts(
            kind, doc, jd, voice_md=voice, notes_md=notes, fit=fit, wanted=wanted,
            task=task)
        turns = assistant.condense(self._checked_turns(payload.get("messages")))
        # Guard what is actually sent, not the stored conversation: the window
        # above has already dropped everything but the last few turns.
        if sum(len(t["content"]) for t in turns) > MAX_CHAT_CHARS:
            raise ValueError("conversation is too long; clear the chat")
        chunks = llm.stream_chat(
            str(payload.get("provider", "")), llm.check_model(payload.get("model", "")),
            f"{stable}\n\n{volatile}", turns,
            cacheable=stable, tail=volatile,
        )
        # Pull the first chunk before answering, so a bad key or model comes
        # back as a normal error response rather than a broken stream.
        try:
            first = next(chunks)
        except StopIteration:
            first = ""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            for chunk in [first] if first else []:
                self.wfile.write(chunk.encode("utf-8")); self.wfile.flush()
            for chunk in chunks:
                self.wfile.write(chunk.encode("utf-8")); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the editor pressed Stop
        except llm.LLMError as exc:
            try:
                self.wfile.write(f"\0{exc}".encode("utf-8"))
            except OSError:
                pass
        finally:
            chunks.close()

    def _import(self, payload: dict[str, Any]) -> None:
        kind = self._document_kind(payload.get("document"))
        example = self._checked_document(kind, payload.get("doc"))
        if payload.get("file"):
            text = assistant.extract_text(
                str(payload.get("filename", "")), assistant.decode_upload(str(payload["file"]))
            )
        else:
            text = str(payload.get("text", "")).strip()[: assistant.MAX_IMPORT_TEXT]
            if not text:
                raise assistant.ImportFailed("paste some text or choose a file to import")
        reply = llm.complete(
            str(payload.get("provider", "")), llm.check_model(payload.get("model", "")),
            assistant.import_prompt(kind, example), text,
        )
        self._json(HTTPStatus.OK, {"doc": assistant.parse_import(kind, reply)})

    # -- applications ----------------------------------------------------

    def _last_opened(self) -> str:
        try:
            return self._workspace.current
        except ApplicationError:
            return ""

    def _apps_get(self) -> None:
        apps = [as_json(a) for a in self._workspace.list()]
        # "" rather than a raise when every application is gone: the hub still
        # has to render so a new one can be created from it.
        self._json(HTTPStatus.OK, {"applications": apps, "current": self._last_opened()})

    def _apps_post(self, payload: dict[str, Any]) -> None:
        """Create, open, rename, archive or delete an application.

        ApplicationError subclasses ValueError, so a bad slug or a name that
        is only punctuation comes back as a plain bad request.
        """
        action = payload.get("action")
        slug = str(payload.get("slug") or "")
        if action == "create":
            # A plain New starts from the base CV and letter. It used to follow
            # on from the application last open, and a bullet written for one
            # posting rode into every application after it. Duplicate names its source and copies it, letter
            # and all: that is a choice he made.
            chosen = str(payload.get("source") or "")
            app = self._workspace.create(
                str(payload.get("name") or "").strip() or UNNAMED,
                job_url=str(payload.get("job_url") or ""),
                source=chosen or None,
                keep_letter=bool(chosen),
            )
            # A plain New opens on the empty frame; the chat writes the rest.
            # A failure costs the frame, never the application.
            if not chosen:
                try:
                    self._build_page(app.slug)
                except Exception as exc:  # noqa: BLE001
                    print(f"[apps] frame not written for {app.slug}: {exc}", flush=True)
        elif action == "open":
            app = self._workspace.set_current(slug)
        elif action == "rename":
            app = self._workspace.rename(slug, str(payload.get("name") or ""))
        elif action == "identify":
            return self._identify(payload)
        elif action in ("archive", "restore"):
            app = self._workspace.archive(slug, archived=action == "archive")
        elif action == "track":
            app = self._workspace.set_track(slug, str(payload.get("track") or ""))
        elif action == "delete":
            self._workspace.delete(slug)
            self._json(HTTPStatus.OK, {"deleted": slug, "current": self._last_opened()})
            return
        else:
            raise ValueError(f"unknown action: {action!r}")
        self._json(HTTPStatus.OK, {"application": as_json(app), "current": self._workspace.current})

    def _identify(self, payload: dict[str, Any]) -> None:
        """Name an unnamed application from the job description just pasted.

        The posting names the company and the role; those are what the
        application is called, what the cover letter is addressed to, and what
        the exported files are named after. Asking the model once here saves
        typing the same two things three times. It also reads the kind of role
        (the track), and the posting's keyword list.
        """
        slug = str(payload.get("slug") or "") or self._workspace.current
        text = str(payload.get("job_description") or "").strip()[: assistant.MAX_IMPORT_TEXT]
        if not text:
            raise ValueError("paste a job description first")
        reply = llm.complete(
            str(payload.get("provider", "")), llm.check_model(payload.get("model", "")),
            assistant.job_prompt(), text,
        )
        found = assistant.parse_job(reply)
        name = " — ".join(p for p in (found["company"], found["role"]) if p)
        if not name:
            raise ValueError("that posting does not name a company or a role")
        app = self._workspace.rename(slug, name, reslug=True)

        app = self._workspace.set_track(app.slug, found["track"])

        # The cover letter is addressed to the same pair, and the export
        # filenames are built from it.
        cover_path = self._paths(app.slug)["cover"]
        cover = _read_json(cover_path)
        if cover:
            cover["company"] = found["company"] or cover.get("company", "")
            cover["role"] = found["role"] or cover.get("role", "")
            _atomic_write(cover_path, json.dumps(cover, indent=2, ensure_ascii=False))
        # The posting has just been read once; this is when its keyword list
        # is read and frozen. A failure here costs the list, never the name:
        # the editor reads it again when the application next opens.
        try:
            self._read_keywords(self._paths(app.slug)["keywords"], text, payload)
        except (llm.LLMError, assistant.ImportFailed, ValueError) as exc:
            print(f"[keywords] not read for {app.slug}: {exc}")
        self._json(HTTPStatus.OK, {"application": as_json(app), **found,
                                   "current": self._workspace.current})

    # -- the line library -------------------------------------------------

    @property
    def _library_path(self) -> Path:
        return self._notes_path.with_name("library.json")

    def _history(self) -> tuple[dict[str, Any], list[library.Line], library.Decisions]:
        """The base CV, every usable line he has used, and his decisions."""
        base = _read_json(Path(self._workspace.base["cv"]))
        cvs = library.load_cvs(self._workspace)
        reworded: dict[str, str] = {}
        for app in self._workspace.list():
            chat = _read_json(self._workspace.folder(app.slug, archived=app.archived)
                              / "chat.json")
            for text in library.accepted_proposals(chat):
                reworded.setdefault(text, "")
        decisions = library.Decisions.load(self._library_path)
        reworded.update(decisions.reworded)
        lines = library.usable(library.collect(base, cvs, reworded), decisions)
        return base, lines, decisions

    def _build_page(self, slug: str) -> bool:
        """Empty ``slug``'s CV down to its frame for the chat to write into.

        Only a CV still exactly as it was created is emptied; anything he
        saved first is his. Returns whether the CV was written.
        """
        if not self._workspace.untouched(slug, "cv"):
            return False
        paths = self._paths(slug)
        base = _read_json(Path(self._workspace.base["cv"]))
        cv = fill.blank(json.loads(json.dumps(base)))
        _atomic_write(paths["cv"], json.dumps(cv, indent=2, ensure_ascii=False))
        return True

    _ENTRY_PATH = re.compile(r"sections\.(\d+)\.entries\.(\d+)")
    # How many of his lines a section turn is shown, best first.
    _SECTION_LINES = {"summary": 4, "bullets": 8, "additional": 6}

    def _section_note(self, phase: str, payload: dict[str, Any]) -> str:
        """His past lines for the section being written, only those his notes
        back, ranked against the posting's frozen keyword list."""
        _, notes = self._voice_and_notes()
        if phase == "skills":
            # His skills list, not old lines: a line is a list of tools, and
            # no one paragraph of his notes ever backed a whole one.
            posting = str(payload.get("job_description") or "").casefold()
            listed = {label: sorted(tools, key=lambda t: t.casefold() not in posting)
                      for label, tools in setup.skills(notes).items()}
            return assistant.skills_note(listed)
        base, lines, decisions = self._history()
        terms = keywords.load(self._paths(str(payload.get("app") or ""))["keywords"])["keywords"]
        limit = self._SECTION_LINES[phase]
        sections = base.get("sections") or []
        room, where = 0, ""
        if phase == "bullets":
            found = self._ENTRY_PATH.fullmatch(str(payload.get("entry") or ""))
            try:
                entry = sections[int(found[1])]["entries"][int(found[2])] if found else None
            except (IndexError, KeyError, TypeError):
                entry = None
            if not isinstance(entry, dict):
                raise ValueError("which job?")
            where = found[0]
            # The frame has no bullets left to count; the base is the record
            # of the room each role gets.
            room = len(entry.get("bullets") or []) or 3
            cap = (entry.get("budget") or {}).get("bullets")
            room = min(room, cap) if isinstance(cap, int) else room
            slots = [(("bullets", library.company(entry.get("left"))), "")]
        else:
            slots = [((phase, ""), "")]
        wanted = {slot for slot, _ in slots}
        mine = [line for line in lines if line.slot in wanted]
        try:
            verdicts = library.check(mine, notes, decisions, support=jev.support)
            self._label_additional(mine, decisions)
            self._keep_learned(decisions)
        except jev.JevError:
            def no_jev(pairs):
                raise jev.JevError("not reachable")
            verdicts = library.check(mine, notes, decisions, support=no_jev)
        index = library.Index(mine)
        shown: list[str] = []
        for slot, prefix in slots:
            ranked = [line for line, _ in index.rank(slot, terms)
                      if verdicts.get(line.text) == "pass"
                      and decisions.labels.get(line.text) != "self_description"]
            shown.extend(prefix + line.text for line in ranked[:limit])
        return assistant.section_note(shown, room=room, where=where)

    @staticmethod
    def _label_additional(lines: list[library.Line], decisions: library.Decisions) -> None:
        """Label each Additional Information line once; a label he set stays."""
        unlabelled = [l.text for l in lines
                      if l.slot == ("additional", "") and l.text not in decisions.labels]
        if not unlabelled:
            return
        try:
            for text, kind in zip(unlabelled, jev.label(unlabelled)):
                decisions.labels[text] = kind
        except jev.JevError as exc:
            print(f"[library] lines not labelled: {exc}", flush=True)

    def _gaps(self, payload: dict[str, Any]) -> None:
        """Before a gap is asked: is it in his notes, or on a past CV? A past
        line he confirms is filed in his notes as his own answer."""
        if payload.get("action") == "confirm":
            self._append_note(str(payload.get("line") or "").strip()[:400])
            return self._json(HTTPStatus.OK, {"saved": True})
        questions = payload.get("questions")
        if not isinstance(questions, list) or len(questions) > 20:
            raise ValueError("questions must be a short list")
        _, lines, decisions = self._history()
        _, notes = self._voice_and_notes()
        self._json(HTTPStatus.OK, library.answer_gaps(
            [str(q)[:400] for q in questions], notes, decisions, lines, judge=jev.answers))

    def _library_post(self, payload: dict[str, Any]) -> None:
        """What he decided about a line, a note, or a CV."""
        action = payload.get("action")
        text = " ".join(str(payload.get("text") or "").split())
        if action in ("check", "review"):
            return self._review(action)
        with _LIBRARY_LOCK:
            decisions = library.Decisions.load(self._library_path)
            self._decide(action, payload, text, decisions)
            decisions.save(self._library_path)
        self._json(HTTPStatus.OK, {"ok": True})

    def _decide(self, action: Any, payload: dict[str, Any], text: str,
                decisions: library.Decisions) -> None:
        if action in ("keep", "strike"):
            if not text:
                raise ValueError("which line?")
            (decisions.kept if action == "keep" else decisions.struck).add(text)
            (decisions.struck if action == "keep" else decisions.kept).discard(text)
        elif action == "confirm":
            passage = " ".join(str(payload.get("passage") or "").split())
            if not passage:
                raise ValueError("which note?")
            decisions.confirmed[library.passage_key(passage)] = bool(payload.get("value"))
        elif action == "exclude":
            slug = str(payload.get("slug") or "")
            self._workspace.find(slug)
            (decisions.excluded.add if payload.get("value", True) else decisions.excluded.discard)(slug)
        else:
            raise ValueError(f"unknown library action: {action!r}")

    def _keep_learned(self, worked: library.Decisions) -> None:
        """Save what a check learned (Jev results, labels) into a fresh read,
        so a Keep or Strike he made while it ran is not written over."""
        with _LIBRARY_LOCK:
            fresh = library.Decisions.load(self._library_path)
            fresh.checks.update(worked.checks)
            for text, kind in worked.labels.items():
                fresh.labels.setdefault(text, kind)
            fresh.save(self._library_path)

    def _review(self, action: str) -> None:
        """Check the whole library against the notes, or list what needs him.

        "check" is the one sitting: every line not yet judged goes to Jev in
        batches, and the results are kept. "review" never calls Jev; a line
        the check has not reached shows as unchecked.
        """
        _, lines, decisions = self._history()
        _, notes = self._voice_and_notes()
        if action == "check":
            support = jev.support
        else:
            def support(pairs):
                raise jev.JevError("review does not call Jev")
        verdicts = library.check(lines, notes, decisions, support=support)
        if action == "check":
            self._label_additional(lines, decisions)
            self._keep_learned(decisions)
            counts = {kind: sum(1 for v in verdicts.values() if v == kind)
                      for kind in ("held", "unconfirmed", "unchecked")}
            return self._json(HTTPStatus.OK, counts)
        flagged = [{"text": l.text, "slot": list(l.slot), "cvs": list(l.cvs),
                    "verdict": verdicts.get(l.text)}
                   for l in lines if verdicts.get(l.text, "pass") != "pass"]
        unsure = [{"passage": p.text, "trust": p.trust} for p in library.passages(notes)
                  if not library.trusted(p, decisions) and p.trust != "no"
                  and library.passage_key(p.text) not in decisions.confirmed]
        apps = [{"slug": a.slug, "name": a.name, "excluded": a.slug in decisions.excluded}
                for a in self._workspace.list()]
        self._json(HTTPStatus.OK, {"lines": flagged, "notes": unsure, "applications": apps})

    def _history_get(self) -> None:
        """The application's one conversation, spanning both documents."""
        try:
            data = json.loads(self._chat_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"messages": [], "job_description": ""}
        self._json(HTTPStatus.OK, data)

    def _history_put(self, payload: dict[str, Any]) -> None:
        path = self._paths(str(payload.get("app") or ""))["chat"]
        messages = payload.get("messages") or []
        jd = payload.get("job_description") or ""
        if not isinstance(messages, list) or len(messages) > MAX_TURNS or not isinstance(jd, str):
            raise ValueError("invalid chat history")
        clean = []
        for m in messages:
            if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") \
                    or not isinstance(m.get("content"), str):
                raise ValueError("invalid chat message")
            item = {"role": m["role"], "content": m["content"]}
            # One chat covers the CV and the cover letter, so a message says
            # which it was about: a proposal has to keep pointing at the
            # document whose field it names, whichever one is open now.
            if m.get("document") in DOCUMENTS:
                item["document"] = m["document"]
            # A tailoring round is a turn nobody typed. The flag is what keeps
            # it from reading as the user's own words after a reload.
            if m.get("pipeline") is True:
                item["pipeline"] = True
            if isinstance(m.get("resolved"), dict):  # which proposals were accepted/dismissed
                item["resolved"] = {str(k): str(v) for k, v in m["resolved"].items()
                                    if v in ("accepted", "dismissed")}
            clean.append(item)
        text = json.dumps({"messages": clean, "job_description": jd}, ensure_ascii=False)
        if len(text) > MAX_HISTORY:
            raise ValueError("chat history is too large; clear the chat")
        path.parent.mkdir(mode=0o700, exist_ok=True)
        _atomic_write(path, text)
        self._json(HTTPStatus.OK, {"saved": True})

def _as_pages(value: Any) -> float:
    """A page count from the browser, ignored unless it is a sane number."""
    try:
        pages = float(value)
    except (TypeError, ValueError):
        return 0.0
    return pages if 0 < pages < 50 else 0.0


def _read_json(path: Path) -> dict[str, Any]:
    """A JSON document from disk, or {} when it is missing or unreadable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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
    parser.add_argument("--voice", type=Path, default=Path("voice.md"),
                        help="Voice file fed into every chat turn")
    parser.add_argument("--notes", type=Path, default=Path("notes.md"),
                        help="shared experience bank fed into every chat turn")
    parser.add_argument("--out", type=Path, default=Path("out"),
                        help="directory rebuilt on every save")
    parser.add_argument("--applications", type=Path, default=Path("applications"),
                        help="one folder per job application")
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    args = parser.parse_args(argv)

    root = args.cv.resolve().parent
    # The root documents are the base every application is copied from; the
    # copies, and the chat about each one, live under applications/.
    workspace = Workspace(args.applications.resolve(), {
        "cv": args.cv.resolve(),
        "cover": args.cover.resolve(),
        "theme": args.theme.resolve(),
        "cover_theme": args.cover_theme.resolve(),
    })
    opened = workspace.ensure(chat_dir=root / ".chat")
    print(f"application: {opened.name}")

    handler = partial(
        EditorHandler,
        workspace=workspace,
        out_dir=args.out.resolve(),
        voice_path=args.voice.resolve(),
        notes_path=args.notes.resolve(),
    )
    with ThreadingHTTPServer((HOST, args.port), handler) as httpd:
        url = f"http://{HOST}:{httpd.server_address[1]}/"
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

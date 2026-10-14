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
from resume_maker import assistant, llm, match, setup, transcribe
from resume_maker.fonts import catalogue, preview_face
from resume_maker.model import CV, ContentError, CoverLetter
from resume_maker.theme import Theme, ThemeError

# route -> (file, page id). The page id selects the active nav chip.
PAGES = {
    "/": ("home.html", "hub"),
    "/voice": ("voice.html", "voice"),
    "/notes": ("notes.html", "notes"),
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
            "/api/fit": self._fit,
            "/api/transcribe": self._transcribe,
            "/api/apps": self._apps_post,
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
            name = next(a.name for a in self._workspace.list() if a.slug == slug)
        except (ApplicationError, StopIteration):
            slug, name = "", "No application"
        # The nav's document links point into this application, and its name
        # sits between the nav and the page's own buttons.
        nav = (STATIC_ROOT / "_nav.html").read_text(encoding="utf-8").strip()
        nav = nav.replace("{app_name}", escape(name))
        html = html.replace("<!-- site-nav -->", nav, 1).replace(
            '<body data-page="">',
            f'<body data-page="{page}" data-app="{escape(slug)}"'
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
        # out/ keeps the same names as an export, so each application's files
        # stand on their own instead of overwriting the last one.
        stem = self._export_name(payload)
        response: dict[str, Any] = {"saved": True, "written": [], "pages": None}
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
        because the user accepted the card, so the text arrives reviewed.
        """
        if "append" in payload:
            current = assistant.load_text(self._notes_path)
            text = setup.append_text(current, payload.get("append"),
                                     today=date.today().isoformat())
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
        parts = [text for _, _, text in assistant.field_paths(kind, doc)]
        self._json(HTTPStatus.OK,
                   match.score(parts, jd, str(payload.get("company") or "")).as_json())

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
        fit = assistant.fit_note(
            _as_pages(payload.get("pages")),
            [str(x)[:120] for x in overflowing][:8] if isinstance(overflowing, list) else (),
        )
        # The same count the ring shows, so the model knows which of the
        # posting's words are missing instead of guessing at them.
        if jd.strip():
            parts = [text for _, _, text in assistant.field_paths(kind, doc)]
            company = str(_read_json(self._cover_path).get("company") or "")
            covered = match.score(parts, jd, company)
            note = assistant.coverage_note(covered.missing)
            fit = f"{fit}\n{note}".strip() if note else fit
        stable, volatile = assistant.prompt_parts(
            kind, doc, jd, voice_md=voice, notes_md=notes, fit=fit)
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
            # A new application starts from the one you were last on, so the
            # work already done carries over; the base files seed the first.
            source = str(payload.get("source") or "") or self._last_opened()
            app = self._workspace.create(
                str(payload.get("name") or "").strip() or UNNAMED,
                job_url=str(payload.get("job_url") or ""),
                source=source or None,
            )
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
        (the track), which decides whether the untouched documents are re-seeded
        from the newest application of that same kind.
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

        # The track goes on the application, then the untouched documents are
        # re-seeded from the newest application of that same track — the latest
        # version of this kind of CV, not whatever happened to be open last.
        app = self._workspace.set_track(app.slug, found["track"])
        reseeded = self._workspace.reseed(app.slug, found["track"])

        # The cover letter is addressed to the same pair, and the export
        # filenames are built from it. Runs after the reseed so the freshly
        # copied letter is the one addressed.
        cover_path = self._paths(app.slug)["cover"]
        cover = _read_json(cover_path)
        if cover:
            cover["company"] = found["company"] or cover.get("company", "")
            cover["role"] = found["role"] or cover.get("role", "")
            _atomic_write(cover_path, json.dumps(cover, indent=2, ensure_ascii=False))
        self._json(HTTPStatus.OK, {"application": as_json(app), **found,
                                   "reseeded": reseeded,
                                   "current": self._workspace.current})

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

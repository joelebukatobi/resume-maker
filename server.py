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
from resume_maker import assistant, llm, setup
from resume_maker.fonts import catalogue, preview_face
from resume_maker.model import CV, ContentError, CoverLetter
from resume_maker.theme import Theme, ThemeError

MAX_BODY = 1 << 20  # 1 MiB is far more than any CV needs.
MAX_IMPORT_BODY = 8 << 20  # a 5 MB file, base64 encoded
MAX_HISTORY = 2 << 20
MAX_TURNS = 200
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
        cv_path: Path,
        theme_path: Path,
        cover_path: Path,
        cover_theme_path: Path,
        out_dir: Path,
        chat_dir: Path,
        assistant_path: Path,
        notes_path: Path,
        **kwargs: Any,
    ) -> None:
        self._chat_dir = chat_dir
        self._assistant_path = assistant_path
        self._notes_path = notes_path
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
        if route == "/":
            return self._static("home.html", "text/html; charset=utf-8")
        if route in ("/style", "/notes"):
            return self._static(f"{route.lstrip('/')}.html", "text/html; charset=utf-8")
        if route == "/api/setup":
            return self._setup_get()
        if route == "/api/setup/templates":
            return self._json(HTTPStatus.OK, setup.templates())
        if route in ("/index.html", "/resume", "/cover-letter"):
            return self._static("index.html", "text/html; charset=utf-8")
        if route == "/static/chat.js":
            return self._static("chat.js", "text/javascript; charset=utf-8")
        if route == "/static/setup.js":
            return self._static("setup.js", "text/javascript; charset=utf-8")
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
            "/api/setup/assistant": self._setup_assistant,
            "/api/setup/notes": self._setup_notes,
            "/api/setup/extract": self._setup_extract,
            "/api/llm/key": self._key,
        }
        handler = handlers.get(route)
        if handler is None:
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        big_body = route in ("/api/import", "/api/setup/extract", "/api/setup/notes")
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
        except (ThemeError, ContentError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except BrowserNotFound as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
        except ExportError as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
        except llm.LLMError as exc:
            self._error(HTTPStatus.BAD_GATEWAY, str(exc))

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


    # -- assistant -------------------------------------------------------

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
        turns, total = [], 0
        for m in messages:
            if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
                raise ValueError("each message needs role user or assistant")
            content = m.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("each message needs text content")
            total += len(content)
            turns.append({"role": m["role"], "content": content})
        if total > 400_000:
            raise ValueError("conversation is too long; clear the chat")
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

    def _style_and_notes(self) -> tuple[str, str]:
        """The user's Style and Notes files, empty when not written yet."""
        return assistant.load_text(self._assistant_path), assistant.load_text(self._notes_path)

    def _setup_get(self) -> None:
        style, notes = self._style_and_notes()
        self._json(HTTPStatus.OK, {
            "assistant": style,
            "notes": notes,
            "status": setup.readiness(style, notes, _read_json(self._cv_path), _read_json(self._cover_path)),
        })

    def _save_setup_file(self, kind: str, path: Path, payload: dict[str, Any]) -> None:
        text = setup.check_text(kind, payload.get("text", ""))
        _atomic_write(path, text)
        style, notes = self._style_and_notes()
        self._json(HTTPStatus.OK, {
            "saved": True,
            "status": setup.readiness(style, notes, _read_json(self._cv_path), _read_json(self._cover_path)),
        })

    def _setup_extract(self, payload: dict[str, Any]) -> None:
        """Plain text from an uploaded file, for pasting into Notes.

        No model involved: this is the same extractor the import flow uses.
        """
        text = assistant.extract_text(
            str(payload.get("filename", "")), assistant.decode_upload(str(payload.get("file", "")))
        )
        self._json(HTTPStatus.OK, {"text": text})

    def _setup_assistant(self, payload: dict[str, Any]) -> None:
        self._save_setup_file("assistant", self._assistant_path, payload)

    def _setup_notes(self, payload: dict[str, Any]) -> None:
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

    def _chat(self, payload: dict[str, Any]) -> None:
        """Stream a reply as plain text. A mid-stream failure is sent as a
        line starting with NUL so the editor can tell it from reply text."""
        kind = self._document_kind(payload.get("document"))
        doc = self._checked_document(kind, payload.get("doc"))
        jd = payload.get("job_description") or ""
        if not isinstance(jd, str) or len(jd) > 40_000:
            raise ValueError("job description must be text under 40,000 characters")
        style, notes = self._style_and_notes()
        system = assistant.system_prompt(kind, doc, jd, assistant_md=style, notes_md=notes)
        chunks = llm.stream_chat(
            str(payload.get("provider", "")), llm.check_model(payload.get("model", "")),
            system, self._checked_turns(payload.get("messages")),
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

    def _history_path(self, kind: str) -> Path:
        return self._chat_dir / f"{self._document_kind(kind)}.json"

    def _history_get(self) -> None:
        query = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
        try:
            path = self._history_path((query.get("document") or [""])[0])
        except ValueError as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"messages": [], "job_description": ""}
        self._json(HTTPStatus.OK, data)

    def _history_put(self, payload: dict[str, Any]) -> None:
        path = self._history_path(payload.get("document"))
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
    parser.add_argument("--assistant", type=Path, default=Path("assistant.md"),
                        help="Style file fed into every chat turn")
    parser.add_argument("--notes", type=Path, default=Path("notes.md"),
                        help="shared experience bank fed into every chat turn")
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
        # Chat history holds CV text: kept beside the data, git-ignored.
        chat_dir=args.cv.resolve().parent / ".chat",
        assistant_path=args.assistant.resolve(),
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

"""What the chat model sees, and how its output is trusted.

The model gets the open document as a list of addressable fields plus an
optional job description. When it wants to change a field it emits a
``proposal`` block naming the field's path; the editor shows it as a diff and
the user accepts or dismisses it. Nothing the model writes is applied
unreviewed, and paths are checked against the document before use.

Import turns a pasted or uploaded CV/cover letter into the document JSON. The
result is parsed with the same strict model as everything else, so a malformed
or hostile reply is rejected rather than loaded.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Iterator, Mapping
from xml.etree import ElementTree

from .model import CV, ContentError, CoverLetter

MAX_IMPORT_BYTES = 5 << 20
MAX_IMPORT_TEXT = 60_000
# The experience bank goes into the prompt whole (no retrieval in v1), so it
# needs a ceiling; past this the tail is dropped and the prompt says so.
MAX_NOTES_CHARS = 60_000
_TRUNCATED = "\n\n[Notes truncated here: the file is longer than this prompt allows.]"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.S)


class ImportFailed(ValueError):
    """The file could not be read, or the model's reply was not usable."""


# --------------------------------------------------------------- field paths


def field_paths(document: str, data: Mapping[str, Any]) -> Iterator[tuple[str, str, str]]:
    """Every editable text field as ``(path, label, current text)``.

    Paths are dot separated keys and list indices, e.g.
    ``sections.3.entries.0.bullets.1``; the editor resolves the same syntax.
    """
    yield "name", "Name", data.get("name", "")
    for i, item in enumerate(data.get("contact") or []):
        yield f"contact.{i}.text", f"Contact item {i + 1}", item.get("text", "")
    if document == "cover":
        for key, label in (("role", "Role"), ("company", "Company"), ("greeting", "Greeting")):
            yield key, label, data.get(key, "")
        for i, text in enumerate(data.get("paragraphs") or []):
            yield f"paragraphs.{i}", f"Body paragraph {i + 1}", text
        for key, label in (("closing", "Closing"), ("signoff", "Sign-off")):
            yield key, label, data.get(key, "")
        return
    for s, section in enumerate(data.get("sections") or []):
        head = section.get("heading", f"Section {s + 1}")
        yield f"sections.{s}.heading", f"{head} heading", head
        if section.get("type") == "paragraph":
            yield f"sections.{s}.text", head, section.get("text", "")
        for e, entry in enumerate(section.get("entries") or []):
            where = f"{head} / {entry.get('left') or f'entry {e + 1}'}"
            base = f"sections.{s}.entries.{e}"
            for key, label in (("left", "organisation"), ("right", "location"),
                               ("sub_left", "role or degree"), ("sub_right", "dates"),
                               ("note", "note")):
                if entry.get(key) is not None:
                    yield f"{base}.{key}", f"{where} {label}", entry.get(key) or ""
            for b, bullet in enumerate(entry.get("bullets") or []):
                yield f"{base}.bullets.{b}", f"{where} bullet {b + 1}", bullet
        for i, item in enumerate(section.get("items") or []):
            yield f"sections.{s}.items.{i}.text", f"{head} / {item.get('label', '')}", item.get("text", "")


# ------------------------------------------------------------------ chat


_CHAT_RULES = """\
You are a careful resume and cover-letter editor working inside a resume tool.
The user's {kind} is below as numbered fields, each with a path.

How to answer:
- Talk normally. Short, direct, no filler.
- When you write text meant for a specific field, output it as a proposal block
  so the user can review and apply it:
```proposal
{{"path": "<field path from the list>", "text": "<the full new text>"}}
```
- One block per field. "text" is the complete replacement, plain text only:
  no markdown, no bullet characters, no surrounding quotes.
- To add a bullet to an experience entry, use the next unused index, e.g. if
  bullets 0 and 1 exist, propose "...bullets.2".
- Only use paths from the list. Never invent a path.
- Never invent facts, employers, dates, numbers or credentials. Rephrase and
  prioritise what is there; if something is missing, ask the user.
- Keep it ATS friendly: plain wording, standard terms, keywords from the job
  description where they are true of the candidate.
"""


def load_text(path: str | Path) -> str:
    """A user markdown file's contents, or "" when it does not exist yet."""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def clip_notes(text: str) -> str:
    """Notes trimmed to the prompt ceiling, with a visible marker when cut."""
    notes = text.strip()
    if len(notes) <= MAX_NOTES_CHARS:
        return notes
    return notes[:MAX_NOTES_CHARS].rstrip() + _TRUNCATED


def system_prompt(
    document: str,
    data: Mapping[str, Any],
    job_description: str = "",
    *,
    assistant_md: str = "",
    notes_md: str = "",
) -> str:
    """The protocol, then the user's style, the document, and their notes.

    ``assistant_md`` is taste and method (how to write); ``notes_md`` is the
    shared experience bank (what actually happened). The protocol rules stay
    first so they are never buried by user text.
    """
    kind = "cover letter" if document == "cover" else "CV"
    lines = [_CHAT_RULES.format(kind=kind)]
    if assistant_md.strip():
        lines += ["## Editor style", assistant_md.strip(), ""]
    lines.append(f"## Current {kind} fields")
    for path, label, text in field_paths(document, data):
        lines.append(f"- `{path}` ({label}): {text}")
    notes = clip_notes(notes_md)
    if notes:
        lines += ["", "## Experience notes",
                  "The candidate's own record of their work. Draw on it; never invent beyond it.",
                  notes]
    if job_description.strip():
        lines += ["", "## Target job description", job_description.strip()]
    return "\n".join(lines)


# ------------------------------------------------------------------ import


def extract_text(filename: str, data: bytes) -> str:
    """Plain text from a .docx, .pdf or .txt upload."""
    if len(data) > MAX_IMPORT_BYTES:
        raise ImportFailed("file is larger than 5 MB")
    suffix = Path(filename).suffix.lower()
    if suffix == ".docx":
        text = _docx_text(data)
    elif suffix == ".pdf":
        text = _pdf_text(data)
    elif suffix in (".txt", ".md", ""):
        text = data.decode("utf-8", "replace")
    else:
        raise ImportFailed("import a .docx, .pdf or .txt file")
    text = text.strip()
    if not text:
        raise ImportFailed("no text found in the file")
    return text[:MAX_IMPORT_TEXT]


_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
            try:
                rels = ElementTree.fromstring(archive.read("word/_rels/document.xml.rels"))
            except KeyError:
                rels = None
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise ImportFailed("not a readable .docx file") from exc
    # Link text like "LinkedIn" hides the address; keep it for the import.
    targets = {r.get("Id"): r.get("Target") for r in rels.iter(f"{_REL}Relationship")} if rels is not None else {}

    def text_of(node) -> str:
        parts = []
        for child in node:
            if child.tag == f"{_W}t" and child.text:
                parts.append(child.text)
            elif child.tag == f"{_W}tab":
                parts.append("\t")
            elif child.tag == f"{_W}br":
                parts.append("\n")
            elif child.tag == f"{_W}hyperlink":
                label = text_of(child)
                target = targets.get(child.get(f"{_R}id"))
                parts.append(f"{label} ({target})" if target and target not in label else label)
            else:
                parts.append(text_of(child))
        return "".join(parts)

    return "\n".join(text_of(p) for p in root.iter(f"{_W}p"))


def _pdf_text(data: bytes) -> str:
    binary = shutil.which("pdftotext")
    if not binary:
        raise ImportFailed("PDF import needs pdftotext (poppler-utils); import the .docx instead")
    with tempfile.TemporaryDirectory(prefix="resume-import-") as tmp:
        source = Path(tmp) / "in.pdf"
        source.write_bytes(data)
        result = subprocess.run(  # noqa: S603 - fixed binary, argument list, no shell
            [binary, "-layout", "-enc", "UTF-8", str(source), "-"],
            check=False, capture_output=True, timeout=30,
        )
    if result.returncode != 0:
        raise ImportFailed("could not read the PDF")
    return result.stdout.decode("utf-8", "replace")


def decode_upload(encoded: str) -> bytes:
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImportFailed("upload was not valid base64") from exc


_IMPORT_RULES = """\
Convert the {kind} text the user provides into JSON with exactly the shape of
the example below (same keys, same nesting, same section "type" values).
Rules:
- Output only the JSON object, nothing else.
- Copy wording faithfully. Do not rewrite, summarise, or invent anything.
- Contact: one item per detail; email gets "href": "mailto:<email>", web
  addresses get "href": "https://<address>"; phone and location have no href.
- Dates on the right as "Month YYYY - Month YYYY".
- Omit optional keys that have no value.
Example of the shape (content is someone else's; do not copy it):
{example}
"""


def import_prompt(document: str, example: Mapping[str, Any]) -> str:
    kind = "cover letter" if document == "cover" else "CV"
    return _IMPORT_RULES.format(kind=kind, example=json.dumps(example, indent=1))


def parse_import(document: str, reply: str, *, date_separator: str = "-") -> dict[str, Any]:
    """Validate the model's JSON; returns the raw mapping the editor stores."""
    match = _FENCE.search(reply)
    raw = match.group(1) if match else reply[reply.find("{") : reply.rfind("}") + 1]
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ImportFailed("the model did not return valid JSON; try again") from exc
    if not isinstance(data, dict):
        raise ImportFailed("the model did not return a document")
    if document == "cover":
        # A letter may not name the role or company; the editor fills those.
        for key in ("role", "company", "greeting", "closing", "signoff"):
            data.setdefault(key, "")
        data.setdefault("paragraphs", [])
    try:
        if document == "cover":
            CoverLetter.from_mapping(data)
        else:
            CV.from_mapping(data, date_separator=date_separator)
    except ContentError as exc:
        raise ImportFailed(f"imported document failed validation: {exc}") from exc
    return data

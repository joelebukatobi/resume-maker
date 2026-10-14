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
from typing import Any, Iterator, Mapping, Sequence
from xml.etree import ElementTree

from .applications import TRACKS
from .model import CV, ContentError, CoverLetter

MAX_IMPORT_BYTES = 5 << 20
MAX_IMPORT_TEXT = 60_000
# The experience bank goes into the prompt whole (no retrieval in v1), so it
# needs a ceiling; past this the tail is dropped and the prompt says so.
MAX_NOTES_CHARS = 60_000
# Only the last this many turns of the conversation are sent; the file keeps
# the rest, so the transcript stops growing on the wire without losing history.
CONTEXT_TURNS = 40
_TRUNCATED = "\n\n[Notes truncated here: the file is longer than this prompt allows.]"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.S)
# A card block in a reply, in the order the editor renders and numbers them.
_CARD = re.compile(r"```(proposal|answer|notes|voice)\s*([\s\S]*?)```")
# What a resolved proposal collapses to on the wire. The edit is already in the
# live fields, and the marker keeps the fact that it happened without the JSON.
_ELIDED = {"accepted": "(proposal already applied)", "dismissed": "(proposal declined)"}


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

Non-negotiable, whatever else the style file says:
- No dashes as punctuation, no colons, no semicolons. Quotation marks are fine.
- In a cover letter, open by greeting the reader and introducing the user by
  name before any fact about their work.
- In a cover letter, write "[Company]" and "[Role]" rather than the real
  company name or job title. They are filled in on export.
- Never name an internal team, org or project. Describe it by what it did.

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
- To delete something, propose it without text:
```proposal
{{"path": "<field path>", "remove": true}}
```
  The editor takes the bullet out of the list rather than leaving it blank.
  Propose one removal at a time and wait: removing a bullet renumbers the ones
  after it, so a second path from the old numbering would hit the wrong line.
- Some questions are not about the {kind} at all: the user is answering an
  application form, a screening question or an interview prompt, and will paste
  your text into that form. Never propose those; put them in an answer block,
  which the tool offers to copy rather than to apply to a field:
```answer
<the answer, plain text, no markdown>
```
- When the user tells you something about their own history that is not in
  their notes yet, or asks you to remember or save something, offer it as a
  notes block. Write it in their voice, as a fact about them, not as chat:
```notes
<what they said about their work, in plain sentences>
```
  The editor shows it with an "Add to Notes" button; it is only written when
  the user accepts. Offer one when it is worth keeping, not for every message.
- When the user states a rule about how to write — a tone, a banned word, a
  habit you should drop — offer it as a voice block. It is a rule for the
  editor, not a fact about their work, so write it as an instruction:
```voice
<the rule, one or two plain sentences>
```
  The editor shows it with an "Add to Voice" button; it is only written when
  the user accepts. Use notes for what they have done, voice for how they want
  to sound.
- Only use paths from the list. Never invent a path.
- The field list at the end of this prompt is the live document, including
  edits the user has not saved yet. It is sent again with every message, so it
  is never out of date. Never say you cannot see the document and never ask the
  user to paste it back to you.
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


def order_notes(text: str, job_description: str) -> str:
    """The notes whole, with the parts this posting cares about first.

    Nothing is dropped. The size of this file was once a cost problem, and
    caching made that mostly go away; what is left is an attention problem,
    where the part that matters is buried behind the part that does not. A
    posting about data products should meet the Amazon work before the story
    about fixing PCs at eleven, and the reverse when it is not.

    Sections are the file's own ``##`` headings, ranked by how much of the
    posting's vocabulary each one uses. Ranked here rather than by a model:
    it is the same count the score ring uses, so it costs nothing and gives
    the same answer twice.
    """
    from .match import score  # local: match imports nothing from here

    body = text.strip()
    if not body or not job_description.strip():
        return body
    parts = re.split(r"\n(?=## )", body)
    if len(parts) < 3:                       # nothing worth reordering
        return body
    head = parts[0] if not parts[0].startswith("## ") else ""
    sections = parts[1:] if head else parts
    ranked = sorted(sections, key=lambda s: -score([s], job_description).score)
    best = score([ranked[0]], job_description).score
    if not best:                             # no section beats any other
        return body
    lead = ["### Most relevant to this posting", *ranked[: max(1, len(ranked) // 2)]]
    rest = ["### The rest, for background", *ranked[max(1, len(ranked) // 2):]]
    return "\n\n".join(x for x in ([head] if head else []) + lead + rest if x.strip())


def clip_notes(text: str) -> str:
    """Notes trimmed to the prompt ceiling, with a visible marker when cut."""
    notes = text.strip()
    if len(notes) <= MAX_NOTES_CHARS:
        return notes
    return notes[:MAX_NOTES_CHARS].rstrip() + _TRUNCATED


def condense(turns: Sequence[Mapping[str, Any]], *,
             window: int = CONTEXT_TURNS) -> list[dict[str, str]]:
    """The turns actually sent to the model.

    Two mechanical savings that cost nothing to the model's view of the live
    document: only the last ``window`` turns travel (the file keeps the whole
    conversation), and a proposal the user has already accepted or dismissed is
    replaced by a one-line marker, since its effect is in the live fields and
    resending its JSON every turn buys nothing. A still-open proposal stays
    whole so the model can revise it.
    """
    kept = list(turns[-window:])
    if len(kept) > 1 and kept[0].get("role") == "assistant":
        kept = kept[1:]
    sent: list[dict[str, str]] = []
    for turn in kept:
        content = str(turn.get("content", ""))
        if turn.get("role") == "assistant":
            content = _strip_resolved(content, turn.get("resolved") or {})
        sent.append({"role": turn["role"], "content": content})
    return sent


def _strip_resolved(content: str, resolved: Mapping[str, Any]) -> str:
    """``content`` with resolved proposal cards replaced by a short marker.

    The key is the card's ordinal in the message, counted as the editor numbers
    them: answer blocks are not cards, so they do not consume a number.
    """
    if not resolved:
        return content
    pieces, last, n = [], 0, 0
    for card in _CARD.finditer(content):
        kind = card.group(1)
        if kind == "answer":
            continue
        key = str(n)
        n += 1
        if kind != "proposal":
            continue
        verdict = resolved.get(key)
        if verdict in _ELIDED:
            pieces.append(content[last:card.start()])
            pieces.append(_ELIDED[verdict])
            last = card.end()
    if not pieces:
        return content
    pieces.append(content[last:])
    return "".join(pieces)


def system_prompt(
    document: str,
    data: Mapping[str, Any],
    job_description: str = "",
    *,
    assistant_md: str = "",
    notes_md: str = "",
) -> str:
    """The whole prompt as one string (the stable part followed by the fields)."""
    stable, volatile = prompt_parts(document, data, job_description,
                                    assistant_md=assistant_md, notes_md=notes_md)
    return f"{stable}\n\n{volatile}"


def fit_note(pages: float = 0, overflowing: Sequence[str] = ()) -> str:
    """What the preview measured, in a line the model can act on.

    The editor already knows the page count and which lines run past the right
    margin. Without this the model finds out when the user types "we are over
    a page", which costs a turn and leaves it guessing how much to cut.
    """
    lines = []
    if pages and pages > 1:
        over = "just over one page" if pages <= 1.25 else f"about {pages:g} pages"
        lines.append(f"This CV currently runs to {over}. It must fit on one page,"
                     " so look for bullets to merge or trim before anything else.")
    elif pages:
        lines.append("This CV fits on one page. Keep it that way.")
    if overflowing:
        lines.append("These lines are too long for their row, so the left and right"
                     " text collide: " + ", ".join(overflowing[:8]) + ".")
    return "\n".join(lines)


def coverage_note(missing: Sequence[str]) -> str:
    """The posting's words that appear nowhere in the document.

    Counted, not guessed. A recruiter's search over the applicant database is
    a literal string match, so a term that is true of the user but absent from
    the page costs them the search. The instruction is deliberately narrow:
    use the ones that are honestly true, never the rest.
    """
    if not missing:
        return ""
    return ("Words this posting leans on that appear nowhere in the document: "
            + ", ".join(missing[:10]) + ". A recruiter searching the applicant"
            " database matches these literally, so work in the ones that are"
            " honestly true of the user's experience, in their own sentences."
            " Leave the rest out, and never add a word to score a match.")


def prompt_parts(
    document: str,
    data: Mapping[str, Any],
    job_description: str = "",
    *,
    assistant_md: str = "",
    notes_md: str = "",
    fit: str = "",
) -> tuple[str, str]:
    """The prompt split into its cacheable prefix and the part that changes.

    Providers cache by prefix: everything up to the first difference can be
    reused, everything after is charged in full. The rules, the user's style,
    their notes and the job description are identical turn after turn, so they
    go first; the document's fields change with every edit, so they go last.
    The protocol rules stay at the very top so user text cannot bury them.
    """
    kind = "cover letter" if document == "cover" else "CV"
    stable = [_CHAT_RULES.format(kind=kind)]
    if assistant_md.strip():
        stable += ["## Editor style", assistant_md.strip(), ""]
    notes = clip_notes(order_notes(notes_md, job_description))
    if notes:
        stable += ["## Experience notes",
                   "The candidate's own record of their work. Draw on it; never invent beyond it.",
                   notes, ""]
    if job_description.strip():
        stable += ["## Target job description", job_description.strip(), ""]

    volatile = [
        f"## The {kind} as it stands right now",
        "This is the live document, including unsaved edits, resent every"
        " message. Work from it; do not ask for it.",
        "",
    ]
    if fit.strip():
        volatile += [fit.strip(), ""]
    for path, label, text in field_paths(document, data):
        volatile.append(f"- `{path}` ({label}): {text}")
    volatile += ["", _tail(kind)]
    return "\n".join(stable).rstrip(), "\n".join(volatile)


_TAIL = """\
Before you answer, check your own text against these. They are the rules the
user has had to repeat, and whatever sits nearest here has the most pull.
- No dashes as punctuation, no colons, no semicolons.
- Nothing invented. Every fact comes from the notes or from the user.
- No internal team, org or project names. Countries and regions stay.
"""
_LETTER_TAIL = """\
- Greet the reader and introduce the user by name before any fact about work.
- "[Company]" and "[Role]", never the real company name or job title.
"""


def _tail(kind: str) -> str:
    """A short restatement after the document, where the model is about to write.

    The same rules are already at the top of the prompt. They are here again
    because position beats repetition: a model that can recite a rule from the
    opening will still break it several hundred lines later, and the text
    nearest the generation point is the text that governs.
    """
    tail = _TAIL if kind == "CV" else _TAIL + _LETTER_TAIL
    return tail.rstrip()


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


_JOB_RULES = """\
Read the job posting below and name it.
Reply with JSON only, nothing else:
{{"company": "<the hiring company>", "role": "<the job title>", "track": "<one of: {tracks}>"}}
Copy the company and role exactly as the posting writes them — no additions.
If the posting does not state one, use an empty string for it.
"track" is the kind of role it is, chosen from the list above. Use "Other" only
when none of the named tracks fits.
"""


def job_prompt() -> str:
    return _JOB_RULES.format(tracks=", ".join(TRACKS))


def parse_job(reply: str) -> dict[str, str]:
    """``{"company", "role", "track"}`` from the model's answer.

    A job description is pasted text that reached the model, so the reply is
    treated as untrusted: only strings are taken from it, trimmed to a length
    that fits a document field, and the track is forced onto the known set.
    """
    match = _FENCE.search(reply)
    raw = match.group(1) if match else reply[reply.find("{") : reply.rfind("}") + 1]
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ImportFailed("could not read the company and role from that posting") from exc
    if not isinstance(data, dict):
        raise ImportFailed("could not read the company and role from that posting")
    found = {key: " ".join(str(data.get(key) or "").split())[:120] for key in ("company", "role")}
    wanted = " ".join(str(data.get("track") or "").split()).lower()
    found["track"] = next((t for t in TRACKS if t.lower() == wanted), "Other")
    return found


_FIT_RULES = """\
You are screening one candidate against one job posting, the way a hiring
manager reads a CV in twenty seconds.
Reply with JSON only, nothing else:
{"score": <0-100>, "strengths": ["<short phrase>", ...], "gaps": ["<short phrase>", ...]}
- score is how strong this candidate looks for this specific role. 50 is a
  plausible applicant, 70 is a likely interview, 85 is a strong yes.
- Three strengths at most, three gaps at most, a few words each.
- Judge the experience, not the wording. Do not reward keyword stuffing.
- Judge only what the CV says. Never assume experience that is not written.
"""


def fit_prompt() -> str:
    return _FIT_RULES


def fit_request(document: str, data: Mapping[str, Any], job_description: str) -> str:
    """The posting and the document, as one block for a single-shot call."""
    lines = ["## Job posting", job_description.strip(), "", f"## The candidate's {document}"]
    lines += [f"- {label}: {text}" for _, label, text in field_paths(document, data) if text]
    return "\n".join(lines)


def parse_fit(reply: str) -> dict[str, Any]:
    """Validate the screening reply. Untrusted: three fields, nothing else."""
    found = _FENCE.search(reply)
    raw = found.group(1) if found else reply[reply.find("{") : reply.rfind("}") + 1]
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ImportFailed("the model did not return a score; try again") from exc
    if not isinstance(data, dict):
        raise ImportFailed("the model did not return a score")
    try:
        score = int(float(data.get("score", -1)))
    except (TypeError, ValueError) as exc:
        raise ImportFailed("the model did not return a score") from exc
    if not 0 <= score <= 100:
        raise ImportFailed("the model's score was out of range")

    def phrases(key: str) -> list[str]:
        value = data.get(key)
        if not isinstance(value, list):
            return []
        return [" ".join(str(x).split())[:120] for x in value[:3] if str(x).strip()]

    return {"score": score, "strengths": phrases("strengths"), "gaps": phrases("gaps")}


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

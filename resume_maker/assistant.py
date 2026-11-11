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
import math
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import date
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
_CARD = re.compile(r"```(proposal|answer|notes|voice|gaps|ask|skill)\s*([\s\S]*?)```")
# What a resolved proposal collapses to on the wire. The edit is already in the
# live fields, and the marker keeps the fact that it happened without the JSON.
_ELIDED = {"accepted": "(proposal already applied)", "dismissed": "(proposal declined)"}


class ImportFailed(ValueError):
    """The file could not be read, or the model's reply was not usable."""


# --------------------------------------------------------------- field paths


# The CV's structure: the name, contacts, section headings, and each entry's
# organisation (team included), location, role and dates. These are facts the
# user sets in Settings; the notes are evidence for what the lines say, never
# for these. The chat marks them fixed and Review refuses a change to one.
FIXED_CV_FIELD = re.compile(
    r"^(?:name|contact\.\d+\.text|sections\.\d+\.heading"
    r"|sections\.\d+\.entries\.\d+\.(?:left|right|sub_left|sub_right))$")


def field_paths(document: str, data: Mapping[str, Any], printed_only: bool = False) -> Iterator[tuple[str, str, str]]:
    """Every editable text field as ``(path, label, current text)``.

    Paths are dot separated keys and list indices, e.g.
    ``sections.3.entries.0.bullets.1``; the editor resolves the same syntax.

    ``printed_only`` leaves out the entries that are switched off. Editing
    needs them, because a role can only be offered back onto the page if the
    model can see it; measuring must not have them. Counting the posting's
    words against work that is not on the page, and asking for a rating of a
    document longer than the one that prints, both answer about a CV nobody
    will read.
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
            if printed_only and entry.get("off"):
                continue
            where = f"{head} / {entry.get('left') or f'entry {e + 1}'}"
            if entry.get("off"):
                where += " [not on the CV]"
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
  A dates field is the exception. The dash between two dates is a setting, not
  punctuation, and the tool rewrites it on export. Never propose changing it,
  and never replace it with the word "to".
- Never name an internal team, org or project in a bullet or the summary.
  Describe it by what it did.
{letter_rules}

How to answer:
- Talk normally. Short, direct, no filler.
- When you write text meant for a specific field, output it as a proposal block
  so the user can review and apply it:
```proposal
{{"path": "<field path from the list>", "text": "<the full new text>"}}
```
- One block per field. "text" is the complete replacement, plain text only:
  no markdown, no bullet characters, no surrounding quotes.
- A bullet that goes beyond what his notes state, where a turn below allows
  one, carries "inferred": one short line naming what it was inferred from,
  e.g. {{"path": "...", "text": "...", "inferred": "SQL in his skills list, the Numinix stores"}}.
  The user is shown every inferred bullet to confirm. Never leave the tag off
  one, and never put it on a line his notes state.
- To add a bullet to an experience entry, or a paragraph to a letter, use the
  next unused index, e.g. if bullets 0 and 1 exist, propose "...bullets.2", and
  if the letter has one paragraph, propose "paragraphs.1" for the next.
- The CV carries roles it is not printing. A field marked "[not on the CV]"
  belongs to an entry that is in the document but switched off. To offer putting
  one on the page, or taking one off it, propose the entry's own path with
  "show" instead of "text":
```proposal
{{"path": "sections.<n>.entries.<n>", "show": true}}
```
  "show": false takes an entry off. The CV is one page, so putting a role on
  usually means taking another off. Propose each as its own block and say which
  one you would take off and why, so the user decides both. Never assume: an
  entry is on or off because the user chose it.
- To delete something, propose it without text:
```proposal
{{"path": "<field path>", "remove": true}}
```
  The editor takes the bullet out of the list rather than leaving it blank.
  Propose one removal at a time and wait: removing a bullet renumbers the ones
  after it, so a second path from the old numbering would hit the wrong line.
  Removal is for something that sits in a list, which means a bullet. Every
  other field is part of the document's shape and cannot be taken out, so to
  shorten one, propose new text for it.
- Some questions are not about the {kind} at all: the user is answering an
  application form, a screening question or an interview prompt, and will paste
  your text into that form. Never propose those; put them in an answer block,
  which the tool offers to copy rather than to apply to a field:
```answer
<the answer, plain text, no markdown>
```
  An answer block is for text that belongs on someone else's form. It is not a
  place to put the user's other document.
- When the posting asks for something the notes cannot support, do not write
  around it and do not invent it. Say so in a gaps block, one question per
  line, each asking whether the user has that experience:
```gaps
<one short question per line>
```
  The editor shows each line with a way to answer it. A gap is something the
  posting wants that nothing in the notes backs up, not something that is
  merely worded differently.
- For one keyword from the posting's list that the notes do not answer, ask
  about it in an ask block instead, one keyword per block:
```ask
{{"keyword": "<the keyword exactly as listed>", "question": "<the question>"}}
```
  Ask about the experience underneath the word, not the word. Not "Do you
  have a compliance-first mindset?" but "Compliance-first mindset. Have you
  worked somewhere rules or data access limited what you could do?" The user
  answers with a sentence, which goes into the notes, or says they have not.
- When the user tells you something about their own history that is not in
  their notes yet, or asks you to remember or save something, offer it as a
  notes block. Write it in their voice, as a fact about them, not as chat:
```notes
<what they said about their work, in plain sentences>
```
  The editor shows it with an "Add to Notes" button; it is only written when
  the user accepts. Offer one when it is worth keeping, not for every message.
  You cannot write to the notes yourself, so never say you have saved,
  recorded, remembered or added anything. Say what the block is for and that
  it needs the button. A user who is told it is saved does not press it, and
  the fact is lost.
- When the user says they have used a tool, platform or language, offer it
  for their skills list too, in a skill block, under the label in their
  skills list it belongs with, alongside a notes block saying what they used
  it for:
```skill
{{"label": "<a label from their skills list>", "tool": "<the tool's name>"}}
```
- When the user states a rule about how to write — a tone, a banned word, a
  habit you should drop — offer it as a voice block. It is a rule for the
  editor, not a fact about their work, so write it as an instruction:
```voice
<the rule, one or two plain sentences>
```
  The editor shows it with a button that adds it to the Playbook; it is only written when
  the user accepts, and the same goes here: never say you have saved it.
  Use notes for what they have done, voice for how they want to sound.
- Only use paths from the list. Never invent a path.
- The field list at the end of this prompt is the live document, including
  edits the user has not saved yet. It is sent again with every message, so it
  is never out of date. Never say you cannot see the document and never ask the
  user to paste it back to you.
- Never invent facts, employers, dates, numbers or credentials. Rephrase and
  prioritise what is there; if something is missing, ask the user. The one
  exception is an inferred Work History bullet, only in a turn that allows it.
- Keep it ATS friendly: plain wording, standard terms, keywords from the job
  description where they are true of the candidate.
- Every claim has to answer something this posting asks for. The notes hold
  work done for other postings, and evidence that answered one of those does
  not belong here. A true sentence about the wrong subject costs the user a
  line they needed for the right one.
"""


# Which document is open decides which rules are even sent. Both documents
# shared one prompt, so a CV session carried the letter's rules and the voice
# file's whole letter section, template opening and all. The model then wrote a
# full cover letter into an answer block in the middle of a CV session, which
# is what the user saw: nothing in the prompt said the letter was not open.
_LETTER_RULES = """\
- In a cover letter, open by greeting the reader and introducing the user by
  name before any fact about their work.
- A new letter starts as a skeleton: one paragraph naming the user and the role
  they are applying for, and nothing else. It is a placeholder, not an opening.
  Rewrite it into a real first paragraph that says why this role and this
  company. Never leave it standing and add a body paragraph after it.
- A letter is one piece of writing, not a set of fields that happen to sit
  together. Each paragraph has to follow from the one before it, and a reader
  must never meet an employer, a project or a number without the sentence
  before it giving them a reason to care. When you write the body, propose
  every paragraph in the same round, first to last, so the letter is shaped as
  a whole. A paragraph proposed on its own into a letter written for another
  posting leaves a letter that reads like two letters.
- The letter must fit on one printed page. Write about three or four short
  paragraphs in total (the greeting opens the first), under about 400 words.
  Prefer fewer stronger claims over a second page. Cut a weaker claim before
  you add length. Never spill onto page two to include everything.
- In a cover letter, write "[Company]" and "[Role]" rather than the real
  company name or job title. They are filled in on export."""
_CV_ONLY_RULES = """\
- Only the CV is open. Do not write a cover letter or any part of one, in a
  proposal or in an answer block, unless the user asks for one in those words.
  The letter is a separate document with its own page and its own turn."""


def scope_voice(voice_md: str, kind: str) -> str:
    """The voice file with the other document's sections left out.

    The letter sections spell out an opening, placeholders and a rhythm. Sent
    during a CV session they are instructions for writing something the user
    did not ask for, and the tokens buy nothing either way.
    """
    wanted, out = True, []
    for line in voice_md.splitlines():
        if line.startswith("## "):
            heading = line[3:].lower()
            about_letter = "cover letter" in heading
            about_cv = "cv" in heading and not about_letter
            wanted = not (about_letter if kind == "CV" else about_cv)
        if wanted:
            out.append(line)
    return "\n".join(out).strip()


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

    Ranked by density rather than by total, because the plain count rewards
    size. The Amazon section is five thousand characters and the Numinix one
    is nine hundred, so Amazon matched more of any posting's words by surface
    area alone and led every ranking, including postings about the e-commerce
    work Numinix is the only record of. Matches per square root of length
    keeps a short, on-point section competitive without letting a two-line
    section win on one word, which is what ``MIN_SECTION_HITS`` guards.
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

    def weight(section: str) -> tuple[bool, float]:
        hits = len(score([section], job_description).matched)
        return (hits >= MIN_SECTION_HITS, hits / math.sqrt(max(len(section), 1)))

    ranked = sorted(sections, key=weight, reverse=True)
    if not weight(ranked[0])[1]:             # no section beats any other
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
    voice_md: str = "",
    notes_md: str = "",
) -> str:
    """The whole prompt as one string (the stable part followed by the fields)."""
    stable, volatile = prompt_parts(document, data, job_description,
                                    voice_md=voice_md, notes_md=notes_md)
    return f"{stable}\n\n{volatile}"


# How many requirements one posting may yield.
MAX_KEYWORDS = 15
# A section needs this many of the posting's terms before density can carry it
# to the front. One word in two lines is a coincidence, not relevance.
MIN_SECTION_HITS = 2

# The round is a conversation of one suggestion at a time. Everything it
# does is a suggestion the user takes or leaves, so each card carries one
# change and the sentence above it reads as an offer and ends in a
# question. A page of cards at once was work done before the user had
# agreed to any of it, and a decision taken early changes what should be
# suggested next: declining a role means its wording is never written.
_ONE_AT_A_TIME = """\
Offer exactly one thing. One card, and above it one or two short sentences
saying what you are suggesting and why, ending in a question. Not an
instruction and not a report. The user answers by pressing the button, and
the next suggestion follows from their answer, so never offer two.

If there is nothing left worth suggesting in this step, say so in one line
and write no card at all. That is how the step ends.
"""

_TAILOR_CAST = """\
## This turn, without being asked

The user pasted the posting and typed nothing. Before any wording, decide
which roles belong on this CV.

The CV is one page and it carries roles it is not printing. Work from the
ranking above: a role that is off and answers this posting better than one
that is on is worth suggesting, and putting one on usually means taking
another off. Suggest the one that gains the most first.

A role is suggested with a show proposal naming the entry, never by
rewriting anything. Change no wording in this step.

No gaps or ask block in this turn. This step is only about which roles are
shown.

{one}
"""

_TAILOR_WORDING = """\
## This turn, without being asked

The roles on the page are settled. Now the wording, for those roles only.
Never write for a role that is off.

Take the posting's missing keywords in the order listed above, using what
the notes say about each. A keyword marked already on the CV is not missing.
Skip it.

- Answered in the notes. Propose one change to one bullet that says it, in
  the keyword's exact words, built from the line the map gives. Name the
  notes section it came from, as one short sentence starting with the word
  "From" and ending in a full stop, with no colon in it, so the user can
  defend it in an interview.
- Not answered yet. Write an ask block about the experience underneath the
  word, rather than about the word itself.
- Marked declined. The user has said no. Skip it and never ask again.

When the keywords are done, look for what a previous posting left behind.
A line this posting does not ask about is worth removing,
and a removal is a card like any other.

{one}
"""

_TAILOR_FIT = """\
## This turn, without being asked

The wording is settled. Now the page. The measurement above is the page as
it renders, not an estimate, so it is not a matter of opinion.

Suggest a shorter version of the bullet that is furthest over its
allowance, as an ordinary proposal for that field. Keep every fact and
every number.

The words from the posting that the document already uses are named above.
They are not padding. A recruiter's search over the applicant database
matches them literally, so a trim that drops one costs the user that search
and is not a trim at all. Shorten around them. What goes is the wording that
carries neither a fact, a number, nor one of those words. If a bullet cannot
lose a line without losing one of them, say so in that card's sentences and
offer the next bullet over its allowance instead.

If the CV fits and no bullet is over, say so in one line and write no card.
If the user has turned down the trims and it still does not fit, say that
once, in one line, with no card. Do not argue and do not suggest again.

{one}
"""

# A one-page CV with lines to spare: one bullet outside the headline role may
# run to three lines, never more than one.
_TAILOR_LENGTHEN = """\
## This turn, without being asked

The CV is written and fits on one page with room to spare. One bullet from a
role that is not the first role on the page may run to three lines instead of
two. Choose the bullet that best answers the posting and offer it written to
three lines, as an ordinary proposal for that bullet. Add only what his notes
back: a tool, a number or a result they state. Never invent.

If no bullet would gain from the room, say so in one line and write no card.

{one}
"""

# Asked once, after a blank CV has been written: the CV may already close
# some of what the posting wants, so only what it still misses is a gap.
_TAILOR_GAPS = """\
## This turn, without being asked

The CV has been written. Compare the CV as it now stands with the posting and
find what the posting asks for that the CV does not show and his notes do not
back. Ask about those in one gaps block, one question per line, most important
first, at most five. Nothing the CV already covers is a gap.
```gaps
<one short question per line>
```

If nothing is missing, say so in one line and write no block. No proposal
block in this turn.
"""

_TAILOR_AGAIN = """\
## This turn, without being asked

The user has just answered one of the gaps and their answer is now in the
notes above. Use it.

Suggest what that new fact earns, and nothing that is already in the
document. Name the notes section it came from.

{one}
"""

# A blank new CV is written in the chat one section at a time, top to bottom,
# and each section is offered whole: the summary, every skill line, one job's
# bullets. He says yes or types what to change; nothing later is touched
# until he has said yes.
_SECTION_WHOLE = """\
Offer this section whole, in one message: one proposal block per field, all
together, and above them one or two short sentences saying why, ending in a
question asking whether it is right or what to change. If the user asks for
changes, offer the whole section again the same way, with the changes made.

Never write for any other section, and never mention, preview or draft one.
The sections after this one wait until the user has said yes to this one.

No gaps or ask block in this turn, and no notes or voice block. Write the
section from what his notes and lines already hold; what the posting wants
that they do not cover is asked about once, at the end, after the whole CV
is written.

The company, job title, location, dates, skill labels and Education are fixed
and stay exactly as they are. Never propose a change to any of them.
"""

# Where the words come from, for every section but Skills.
_FROM_HIS_LINES = """\
Write in sentences, with ordinary capitals: only names and acronyms take
capitals (built tools on large language models; AWS, Python, LLMs).

Work from the lines listed under "His lines for this section" first: they are
his, from CVs he has sent, and his notes back them. Use one as it is or adjust
its wording to the posting. Write a new line only when none of them answers
what the posting asks for, and then only from what his notes state, naming
the notes section it came from. Never invent a tool, number, title or result
that is not in his notes or those lines.
"""

# Bullets only. The user's boundary, 2026-11-10: the activity behind what the
# notes or skills list name may be inferred and placed at the likeliest
# employer; numbers, employers, titles, dates, team sizes and unnamed tools
# never. Every inferred bullet is tagged and shown to him on Review.
_MAY_INFER = """\
When none of his lines or notes answers something the posting asks for, you
may infer a bullet. Take a tool, skill or kind of work his notes or skills
list name, and write the plausible activity behind it at the employer where
he most likely did it: SQL in his skills list and e-commerce stores at
Numinix may become writing joins and reporting queries for those stores.
Never infer a number, percentage, metric, employer, title, date, team size or
scope word like "led", and never a tool that appears nowhere in his notes or
skills list. An inferred bullet carries "inferred" in its proposal. At most
one inferred bullet per job.
"""

_SECTION_SUMMARY = """\
## This turn

The CV is being written section by section. This section is the
Professional Summary: its one paragraph, for this posting.

{whole}

{lines}
"""

_SECTION_SKILLS = """\
## This turn

The CV is being written section by section. This section is Skills: the text
of every skill line, under the labels already there. Keep each label; write
only its text.

{whole}

Build each line from the tools listed under that label in "His skills list"
below; never a tool that is not in his skills list, and never one from
another label's list. Do not copy an old line: choose. The tools this
posting asks for come first, then his strongest others, while the line still
fits on one line of the page. Write tool names as he wrote them, each word
starting with a capital: Large Language Models, not large language models.
Never invent.
"""

_SECTION_BULLETS = """\
## This turn

The CV is being written section by section. This section is the bullets of
one job, the one named under "His lines for this section". Write every bullet
it has room for, starting at bullets.0, and none for any other job.

{whole}

{lines}

{infer}
"""

_SECTION_BULLETS_ALL = """\
## This turn

The CV is being written section by section. This section is the bullets of
every job on the CV, each one named under its own "His lines for this
section" with its room. For each job, write every bullet it has room for,
starting at bullets.0 under that job's own path. A line about one job never
goes under another, and no job gets more bullets than its room.

{whole}

{lines}

{infer}
"""

_SECTION_ADDITIONAL = """\
## This turn

The CV is being written section by section. This section is Additional
Information, the last one. It holds his interests line. Add the line about
work authorisation and sponsorship only when the posting mentions visas,
sponsorship or the right to work. Never a sentence describing his character.

{whole}

{lines}
"""

SECTIONS = {
    "summary": _SECTION_SUMMARY,
    "skills": _SECTION_SKILLS,
    "bullets": _SECTION_BULLETS,
    "bullets_all": _SECTION_BULLETS_ALL,
    "additional": _SECTION_ADDITIONAL,
}


def skills_note(listed: Mapping[str, Sequence[str]]) -> str:
    """His skills list, by label, the tools the posting names first."""
    out = ["His skills list:"]
    out.extend(f"- {label}: {', '.join(tools)}" for label, tools in listed.items())
    if not listed:
        out.append("- He has no skills list yet. Leave every skill line empty.")
    return "\n".join(out)


def section_note(lines: Sequence[str], room: int = 0, where: str = "") -> str:
    """His past lines for the section being written, best first, and for a
    job, how many bullets it has room for and where they go."""
    out = ["His lines for this section:"]
    if where:
        out.append(f"The job is {where}. It has room for {room} bullets, "
                   f"{where}.bullets.0 to {where}.bullets.{max(room - 1, 0)}.")
    if lines:
        out.extend(f"- {line}" for line in lines)
    else:
        out.append("- No line he has used fits this section yet.")
    return "\n".join(out)


PHASES = ("cast", "wording", "fit", "again", "gaps", "lengthen")
_PHASE_RULES = {
    "cast": _TAILOR_CAST,
    "wording": _TAILOR_WORDING,
    "fit": _TAILOR_FIT,
    "again": _TAILOR_AGAIN,
    "gaps": _TAILOR_GAPS,
    "lengthen": _TAILOR_LENGTHEN,
}


def declined_note(items) -> str:
    """What the user has already turned down, so it is not offered twice.

    A no is an answer. Offered the same role or the same rewrite two cards
    later, the round stops being a conversation and becomes a form.
    """
    items = [str(i) for i in items if str(i).strip()]
    if not items:
        return ""
    return ("The user has already said no to these in this round, so do not"
            " offer them again, in any wording: "
            + ", ".join(f"`{i}`" for i in items[:20]) + ".")


def tailor_rules(phase: str = "wording", settled=(), declined=()) -> str:
    """The instruction for one step of the paste-time pass.

    It sits after the document and before the hard rules, which is where
    the model is about to write. One step offers one suggestion: the step
    ends when it has nothing left to offer and says so without a card.
    """
    if phase in SECTIONS:
        rules = SECTIONS[phase].format(whole=_SECTION_WHOLE.strip(),
                                       lines=_FROM_HIS_LINES.strip(),
                                       infer=_MAY_INFER.strip()).strip()
    else:
        rules = _PHASE_RULES.get(phase, _TAILOR_WORDING).format(
            one=_ONE_AT_A_TIME.strip()).strip()
    for note in (settled_note(settled), declined_note(declined)):
        if note:
            rules = f"{rules}\n\n{note}"
    return rules

def settled_note(paths: Sequence[str]) -> str:
    """The fields the user has already decided on, named so they are left alone.

    A resolved proposal is replaced by a marker before the transcript is sent,
    so the model can see that something was applied to a field and not what.
    Given a new fact and no list, it rewrote those fields again, differently,
    which is the work the user had just finished settling.
    """
    if not paths:
        return ""
    return ("The user has already accepted a change to these fields, so they are"
            " settled: " + ", ".join(f"`{p}`" for p in paths[:20]) + ". Leave them"
            " as they are. Propose one again only if the new fact genuinely"
            " changes what it should say, and say in that line why.")


def today_note(when: date | None = None) -> str:
    """What day it is, for anything that turns on a date.

    A model has no clock. Given "July 2024 - May 2026" and nothing else it read
    a finished MBA as still in progress and reported it as a gap, twice, and a
    fit reading is stored until something re-rates it so the phrase sat in the
    panel. It goes in the volatile half of the prompt: the cached prefix would
    otherwise change every day for no other reason.
    """
    stamp = (when or date.today()).isoformat()
    return (f"Today is {stamp}. A date in the document that has already passed is"
            " finished, not in progress or upcoming. Work out what is current from"
            " this date and never from your own sense of the present.")


def fit_note(pages: float = 0, overflowing: Sequence[str] = (),
             over_budget: Sequence[object] = ()) -> str:
    """What the preview measured, in a line the model can act on.

    The editor already knows the page count and which lines run past the right
    margin. Without this the model finds out when the user types "we are over
    a page", which costs a turn and leaves it guessing how much to cut.
    """
    lines = []
    if over_budget:
        lines.append(
            "These bullets render to more lines than the CV allows them, so they"
            " are what to shorten first: " + "; ".join(str(item) for item in
                                                       over_budget[:6]) + ".")
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


def wanted_note(wanted: Sequence[str]) -> str:
    """What the posting requires, as a model read it off the posting.

    Counting repetitions could not tell a requirement from the team's org
    chart, so the list is read once by a model and kept with the application.
    Here it does a second job: it is the only thing in the prompt that says
    what this posting is asking for, as opposed to what the notes happen to
    contain. Without it a letter for an engineering role at a marketplace
    carried WCAG 2.2 across from the accessibility posting worked on three
    days before, because the notes said it and nothing said not to.
    """
    if not wanted:
        return ""
    return ("What this posting requires, read off the posting itself: "
            + ", ".join(str(w) for w in wanted[:MAX_KEYWORDS]) + ". Work from"
            " this list. A fact that answers something not on it belongs in"
            " another application, however true it is.")


def coverage_note(missing: Sequence[str], matched: Sequence[str] = ()) -> str:
    """The posting's words that appear nowhere in the document.

    Counted, not guessed. A recruiter's search over the applicant database is
    a literal string match, so a term that is true of the user but absent from
    the page costs them the search. The instruction is deliberately narrow:
    use the ones that are honestly true, never the rest.
    """
    lines = []
    if missing:
        lines.append(
            "Words this posting leans on that appear nowhere in the document: "
            + ", ".join(missing[:10]) + ". A recruiter searching the applicant"
            " database matches these literally, so work in the ones that are"
            " honestly true of the user's experience, in their own sentences."
            " Leave the rest out, and never add a word to score a match.")
    if matched:
        # Trimming to one page and matching the posting's language pull in
        # opposite directions, and the trim has no way of knowing which words
        # were doing work. Naming them is what stops a rewrite that reads
        # better from quietly costing the user the search.
        lines.append(
            "Words from the posting that the document already uses: "
            + ", ".join(matched[:12]) + ". Keep them. If a line has to be"
            " shortened or merged, shorten around them rather than through"
            " them.")
    return "\n".join(lines)


def experience_note(data: Mapping[str, Any], job_description: str) -> str:
    """Which jobs on the CV answer this posting, measured rather than guessed.

    The prompt said which words were missing and never which work to lead
    with, so the model led with whatever the notes ranked first, which was
    whatever was longest. Across two unrelated postings that meant the same
    employer twice and a letter that argued the previous job's case.

    Counted the same way as coverage, per square root of length so a short
    entry is not buried, and stated as a measurement so the model can
    override it when it can see the count is wrong.
    """
    from .match import score  # local: match imports nothing from here

    if not job_description.strip():
        return ""
    entries = []
    for section in data.get("sections") or []:
        if not isinstance(section, Mapping) or "experience" not in str(
                section.get("heading", "")).lower():
            continue
        for entry in section.get("entries") or []:
            if not isinstance(entry, Mapping):
                continue
            # The employer, without the division after the comma: it is only
            # here to be named, and that tail is often an internal team.
            name = " ".join(str(entry.get("left") or "").split()).split(",")[0].strip()
            text = " ".join([name, str(entry.get("sub_left") or "")]
                            + [str(b) for b in entry.get("bullets") or []])
            if name:
                hits = len(score([text], job_description).matched)
                entries.append((hits / math.sqrt(max(len(text), 1)), hits, name))
    if len(entries) < 2:
        return ""
    entries.sort(reverse=True)
    if not entries[0][1]:
        return ""
    lead = [name for _, hits, name in entries if hits][:2]
    rest = [name for _, hits, name in entries if name not in lead]
    line = ("Measured against this posting's own words, the experience that answers it"
            f" most directly is {' and then '.join(lead)}."
            " Lead with that work and keep the rest as support.")
    if rest:
        line += (f" {', '.join(rest)} answer it least directly here, which is about this"
                 " posting and not about their worth.")
    return line + (" It is a word count, not a judgement, so ignore it where you can see"
                   " it is wrong, and never move an entry on the page to match it.")


def bench_note(data, wanted, job_description: str = "") -> str:
    """Which roles are off the CV, and whether this posting wants one on.

    The CV is one page and the history is longer than one page, so which
    roles print is a decision per posting. Measured against the requirement
    list read off the posting, so the model can say "this one answers six of
    them and the one it would replace answers two" instead of having a view.
    """
    from .match import score, score_against  # local: match imports nothing from here

    on, off = [], []
    for section in data.get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        if "experience" not in str(section.get("heading", "")).lower():
            continue
        for index, entry in enumerate(section.get("entries") or []):
            if not isinstance(entry, Mapping):
                continue
            name = " ".join(str(entry.get("left") or "").split()).split(",")[0].strip()
            if not name:
                continue
            text = " ".join([name, str(entry.get("sub_left") or "")]
                            + [str(b) for b in entry.get("bullets") or []])
            if wanted:
                hits = len(score_against([text], list(wanted)).matched)
                total = len(wanted)
            elif job_description.strip():
                hits, total = len(score([text], job_description).matched), 0
            else:
                hits, total = 0, 0
            (off if entry.get("off") else on).append((hits, total, name))
    if not off:
        return ""

    def rank(items):
        return ", ".join(
            f"{name} answers {hits} of {total}" if total else f"{name} answers {hits}"
            for hits, total, name in sorted(items, reverse=True))

    line = ("Roles this CV holds but does not print: " + rank(off) + ".")
    if on:
        line += " On the page now: " + rank(on) + "."
    best_off = max(off)[0]
    weakest_on = min(on)[0] if on else None
    if best_off and weakest_on is not None and best_off > weakest_on:
        line += (" One of the roles that is off answers this posting better than one"
                 " that is on, so offer the swap: a show proposal for the one to put"
                 " on and a separate one for the role you would take off. The user"
                 " decides both.")
    return line + (" It is a count, not a judgement. Say what it says and let the"
                   " user choose.")


def prompt_parts(
    document: str,
    data: Mapping[str, Any],
    job_description: str = "",
    *,
    voice_md: str = "",
    notes_md: str = "",
    fit: str = "",
    wanted: str = "",
    task: str = "",
) -> tuple[str, str]:
    """The prompt split into its cacheable prefix and the part that changes.

    Providers cache by prefix: everything up to the first difference can be
    reused, everything after is charged in full. The rules, the user's style,
    their notes and the job description are identical turn after turn, so they
    go first; the document's fields change with every edit, so they go last.
    The protocol rules stay at the very top so user text cannot bury them.
    """
    kind = "cover letter" if document == "cover" else "CV"
    stable = [_CHAT_RULES.format(
        kind=kind, letter_rules=_LETTER_RULES if kind == "cover letter" else _CV_ONLY_RULES)]
    voice = scope_voice(voice_md, kind)
    if voice:
        stable += ["## The user's playbook", voice, ""]
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
    volatile += [today_note(), ""]
    if wanted.strip():
        volatile += [wanted.strip(), ""]
    if fit.strip():
        volatile += [fit.strip(), ""]
    for path, label, text in field_paths(document, data):
        fixed = ", fixed" if document == "cv" and FIXED_CV_FIELD.match(path) else ""
        volatile.append(f"- `{path}` ({label}{fixed}): {text}")
    if task.strip():
        volatile += ["", task.strip()]
    volatile += ["", _tail(kind)]
    return "\n".join(stable).rstrip(), "\n".join(volatile)


_TAIL = """\
Before you answer, check your own text against these. They are the rules the
user has had to repeat, and whatever sits nearest here has the most pull.
- No dashes as punctuation, no colons, no semicolons. The dash between two
  dates is not punctuation and is not yours to change.
- Nothing invented. Every fact comes from the notes or from the user. A Work
  History turn may infer a bullet within its limits, and tags it "inferred".
- No internal team, org or project names in a bullet or the summary. Countries
  and regions stay.
- Nothing is saved until the user presses the button on the card. Never say you
  have saved, recorded or remembered something.
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
Copy the company and role exactly as the posting writes them, with no additions.
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
- A qualification whose end date has passed is finished. Never call it in
  progress, and never treat a finished course as a gap.
"""


_KEYWORD_RULES = """\
You are reading one job posting and picking out the keywords a recruiter would
type into the applicant tracking system to find the right candidate for it.

Reply with JSON only, nothing else:
{"keywords": ["<term>", ...]}

- Between ten and fifteen terms, most specific first.
- Skills, tools, platforms, methods, practices and domains: something a person
  has done or has not done. For a product role that means its craft, such as
  product discovery, product lifecycle, stakeholder management,
  prioritization and trade-offs. Not every noun in the posting.
- Each term exactly as the posting writes it, lowercased. Keep a phrase whole:
  "compliance-first mindset" stays "compliance-first mindset", never cut down
  to "compliance".
- Never a word that would fit any job ad on its own, such as organization,
  communication, ownership or collaboration.
- Not the company's name, not its products or programmes, not the names of
  other people's jobs on the team, not the perks, the pay, the location or the
  law. If the posting says the team includes a copywriter, that is not a
  requirement of this role.
- Each term once. No near-duplicates.
"""

# Words that name nothing a person can have or lack. A model told to leave
# them out mostly does; these are what get through when it does not. Only a
# term that is one of these alone is dropped: "organization design" is a skill.
GENERIC_TERMS = frozenset({
    "organization", "organisation", "communication", "communications",
    "ownership", "collaboration", "teamwork", "compliance", "leadership",
    "passion", "impact", "excellence", "innovation", "curiosity",
})


def keywords_prompt() -> str:
    return _KEYWORD_RULES


_NAMING = """This posting is also being named. Put three more fields in the same JSON
object: "company" (the hiring company), "role" (the job title) and "track"
(the kind of role, one of: {tracks}). Copy the company and role exactly as the
posting writes them, with no additions, and use an empty string for one the
posting does not state. Use "Other" as the track only when none fits.
"""


def named_keywords_prompt() -> str:
    """The keyword reading and the naming in one call: both read the same
    posting, so a new application pays for it once."""
    return f"{_KEYWORD_RULES}\n{_NAMING.format(tracks=', '.join(TRACKS))}"


def keywords_request(job_description: str) -> str:
    return f"## Job posting\n{job_description.strip()}"


def parse_keywords(reply: str, company: str = "") -> list[str]:
    """The keyword list, validated. Untrusted: strings, trimmed, deduped.

    The company's own name and a word that names nothing are dropped here as
    well as in the prompt, because the prompt is advice and this is the rule.
    """
    found = _FENCE.search(reply)
    raw = found.group(1) if found else reply[reply.find("{") : reply.rfind("}") + 1]
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ImportFailed("the model did not return a keyword list; try again") from exc
    items = data.get("keywords") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ImportFailed("the model did not return a keyword list")
    hidden = {w for w in str(company).lower().split() if len(w) > 2}
    out: list[str] = []
    for item in items:
        term = " ".join(str(item).split()).lower()[:60]
        if not term or term in out or term in GENERIC_TERMS:
            continue
        if hidden.intersection(term.split()):
            continue
        out.append(term)
    if not out:
        raise ImportFailed("the model found no keywords in that posting")
    return out[:MAX_KEYWORDS]


# The map: for each keyword, where in the notes the candidate did that thing
# and a line that says it in the posting's words. It is what turns a missing
# keyword from a word to paste in into a sentence he can defend, and it is the
# only place a model's judgement meets the keyword list: the count itself
# stays literal, because the tracking system it imitates is.
MAP_STATES = ("answered", "unanswered", "declined")
MAX_SAY = 220

_MAP_RULES = """\
You are matching one candidate's own notes against the keywords a recruiter
will search for. For each keyword, decide whether the notes show the candidate
did that thing, even if they never used the word.

Reply with JSON only, nothing else, one entry per keyword:
{"<keyword>": {"state": "answered", "from": "<where>", "say": "<line>"}, ...}

- "answered": the notes show it. "from" names the employer or project and
  quotes or closely paraphrases the notes, so the candidate recognises their
  own words. "say" is one line, short enough for a CV bullet, that states it
  and contains the keyword exactly as written.
- "declined": the notes explicitly say the candidate has not done it. Leave
  "from" and "say" empty.
- "unanswered": anything else. Leave "from" and "say" empty.
- Never invent an employer, a number, a tool or an outcome that the notes do
  not contain. A keyword the notes do not support is unanswered, however
  likely it seems.
- No dashes as punctuation, no colons, no semicolons in "say".
"""


def map_prompt() -> str:
    return _MAP_RULES


def map_request(terms: Sequence[str], notes_md: str) -> str:
    listed = "\n".join(f"- {t}" for t in terms)
    return f"## Keywords\n{listed}\n\n## The candidate's notes\n{notes_md.strip()}"


def parse_map(reply: str, terms: Sequence[str]) -> dict[str, dict[str, str]]:
    """The map, validated. Untrusted: every keyword gets exactly one entry.

    An answer with no source is downgraded, because a line nobody can trace
    back to the notes is a line the candidate cannot defend.
    """
    blank = {"state": "unanswered", "from": "", "say": ""}
    found = _FENCE.search(reply)
    raw = found.group(1) if found else reply[reply.find("{") : reply.rfind("}") + 1]
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ImportFailed("the model did not return a keyword map; try again") from exc
    if not isinstance(data, dict):
        raise ImportFailed("the model did not return a keyword map")
    out: dict[str, dict[str, str]] = {}
    for term in terms:
        item = data.get(term)
        if not isinstance(item, dict):
            out[term] = dict(blank)
            continue
        state = str(item.get("state") or "")
        source = " ".join(str(item.get("from") or "").split())[:300]
        line = " ".join(str(item.get("say") or "").split())[:MAX_SAY]
        if state == "declined":
            out[term] = {"state": "declined", "from": "", "say": ""}
        elif state == "answered" and source and line:
            out[term] = {"state": "answered", "from": source, "say": line}
        else:
            out[term] = dict(blank)
    return out


def map_note(stored: Mapping[str, Any], on_page: Sequence[str] = ()) -> str:
    """The keyword list and its map, as the prompt sees it.

    Every keyword with what the notes say about it, so the model answers a
    missing keyword from the user's own work and never asks again about one
    he has already said no to.

    Whether a keyword is on the page and whether the notes back it are two
    different facts. With only the second shown, SQL sitting in a skills line
    read as missing because the notes never mention it, and the round started
    there. ``on_page`` is what the count found, and those are left alone.
    """
    present = set(on_page)
    terms = list(stored.get("keywords") or [])
    entries = stored.get("map") or {}
    if not terms:
        return ""
    lines = ["The keywords a recruiter will search for on this posting, in order,"
             " with what the notes say about each:"]
    for term in terms:
        item = entries.get(term) or {}
        state = item.get("state") or "unanswered"
        if term in present:
            lines.append(f"- {term} already on the CV. Leave it.")
        elif state == "answered":
            lines.append(f"- {term} answered, from {item.get('from')}, say \"{item.get('say')}\"")
        elif state == "declined":
            lines.append(f"- {term} declined. The user has said no. Never ask about it.")
        else:
            lines.append(f"- {term} unanswered. The notes do not show it yet.")
    return "\n".join(lines)


def fit_prompt() -> str:
    return _FIT_RULES


def fit_request(document: str, data: Mapping[str, Any], job_description: str) -> str:
    """The posting and the document, as one block for a single-shot call."""
    lines = [today_note(), "", "## Job posting", job_description.strip(), "",
             f"## The candidate's {document}"]
    lines += [f"- {label}: {text}"
              for _, label, text in field_paths(document, data, printed_only=True) if text]
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

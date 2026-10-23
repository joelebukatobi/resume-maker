# A one page CV that the posting chooses

**Date:** 2026-10-23
**Status:** Awaiting review
**Product area:** the CV document (`model.py`, `render_html.py`, `render_docx.py`,
`export_pdf.py`, `build.py`, `match.py`, `assistant.py`, `server.py`,
`static/index.html`)

## Problem

The CV holds one set of experience entries and prints all of them. The user's
history is longer than one page, and which parts of it answer a posting changes
with the posting: an operations or consulting role is answered by the graduate
assistant work, a platform role by xPathEdge, an account management programme by
MKEL. Today the only way to show a different set is to edit the entries by hand
in each application, and nothing measures what that does to the page.

Two things are missing. The document cannot carry history it is not currently
printing, and nothing knows what the page can hold.

This is a first role after an MBA. It has to be one page.

## Goals

- `cv.json` carries the user's whole CV history. Each entry is **on or off**.
  What prints is the entries that are on.
- Each application decides its own set, from the same history.
- A **measured line budget**, not an estimate: the tool reads each bullet's
  rendered line count at the real font and column width.
- The posting **ranks** the entries, so the tool can say which roles answer it.
- The tool **asks**. It never swaps an entry or rewrites a bullet on its own.
- Over budget **warns and still exports**.

## Non-goals

- No hierarchy of employers in code. Every entry is equally swappable, Amazon
  included. Nothing hardcodes a company name.
- No automatic trimming. Every cut is a proposal the user accepts or declines.
- Older application CVs are left alone. This applies from now on.
- Not a second store of history. The notes keep the narrative, LinkedIn keeps
  the full record, and neither becomes an input to rendering.

## The budget

Stated by the user, and the numbers belong to the document rather than to the
code:

- The headline entry: at most **3 bullets**, of which at most **2 may run to 3
  lines**, and the third at most **2 lines**.
- Every bullet in every other entry: at most **2 lines**.
- The whole CV: **1 page**.

Carried per entry, defaulting to 2 lines a bullet, so the allowance travels with
whichever entry is the headline rather than with a company's name.

## Design

### 1. An entry can be off

`Entry` gains one field. `Entry.parse` validates against a closed key list
(`model.py:162`), so the key is added there, and both renderers skip an entry
that is off. An entry that is off keeps its bullets, its dates and its budget:
it is history the document is holding, not printing.

The editor shows off entries in place, dimmed, with a way to turn one on. They
are part of the document the user is looking at, so they are not hidden behind a
panel.

### 2. The page measures itself

`_MEASURE_SCRIPT` (`render_html.py:154`) already walks the rendered page and
reports overflowing rows through `document.title`, which `measure_overflow`
(`export_pdf.py:99`) reads back. It gains each bullet's line count, from its
rendered height over its line height, keyed by entry and bullet index. The
payload is already JSON, so this is one more field in it.

`BuildResult` already carries `pages` and `overflowing`. It gains the per bullet
counts and what each one is allowed, which is what every other part of this
feature reads.

### 3. The posting ranks the entries

`match.score_against(parts, terms)` already scores a document against an
explicit list of terms, and `/api/keywords` already reads a posting's
requirement list. Pointing the first at one entry at a time gives each role a
count of the requirements it answers. That ranking is what the tool shows when
it asks, so the question is "MKEL answers 6 of this posting's 15 requirements
and xPathEdge answers 2" rather than an opinion.

### 4. The tool asks

When the ranking disagrees with what is currently on, the chat offers it:
turning an entry on, and turning another off, are separate proposals the user
accepts separately. Proposals already apply to a document that is not open and
already handle list members, so an entry going on or off is an ordinary
proposal card.

### 5. Over budget warns

Save and export go through. Alongside the file the tool reports the page count,
every bullet over its line budget, and proposals that would bring it inside.
Nothing is blocked and nothing is cut without the user.

## Settled

- The MKEL entry's bullets stay as they are. The work was done for MKEL, which
  is what the entry says, and when inside the run it happened is not the
  reader's business.
- The graduate assistant entry is **off by default**. It comes in when the
  posting calls for it, which is the whole point of this feature.
- xPathEdge is **lead developer, on contract**.

Two document edits follow from this and wait on the user's word, separately
from the build:

- The MKEL entry's dates become March 2023 to December 2023.
- The xPathEdge role line becomes lead developer on contract rather than Web
  Developer (Part-Time/Remote).

## Testing

- An entry that is off renders in neither HTML nor DOCX, and the document still
  validates.
- A bullet of known length reports the line count the page actually renders.
- A document inside its budget reports no offenders; one over reports each
  offending bullet with its count and its allowance.
- Ranking an entry against a requirement list returns the requirements it
  answers.
- Export over budget still writes the file.

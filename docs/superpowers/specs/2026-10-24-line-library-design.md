# Line library: every CV built fresh from everything he has written

**Date:** 2026-10-24
**Status:** Written, awaiting approval
**Product area:** application creation (`resume_maker/applications.py` `create`,
`reseed`), a new line library (index, search, provenance), the support check
(TypeSafe Jev over HTTP), the editor's first view of a new application
(`static/index.html`, `static/chat.js`), the tailoring round (`assistant.py`).

## Why

A new application today starts as a copy of the newest live application in the
same track (decided 2026-10-07 and 2026-10-10, for a head start). The cost
surfaced on Addepar: a bullet written for Flexport, which asked for low-code
tooling, named Zapier, n8n, Webflow and Airtable. It carried from Flexport into
Ramp, LSEG and Addepar, though Addepar asks for none of it. Meanwhile his usual
Additional Information line ("Deep Linux user (Pop!_OS) exploring agentic AI
workflows via MCPs and custom SKILL.md tooling. Currently picking up Rust.")
was replaced on Ramp and never came back, although it carries exactly the words
Addepar asks for ("agentic workflows", "MCP").

The assistant could not have brought it back: it sees the notes, the CV in
front of it and the posting, never his other CVs.

There is a second failure under the first. The Zapier tools were invented by
the model in a Flexport letter, repeated, and then reached `notes.md` (line
429, in the dated "From the chat" log). He had told the session the real tools
were Make.com and Airtable. Once in the notes, the line looked supported.

He wants each CV built for its own posting from everything he has ever said,
checked against that posting, at as few cloud tokens as possible.

## Goals

- A new application is built **from scratch**, never copied from the last one.
- The candidates for every slot are **every line he has ever used** (all CVs,
  the base CV) and what the notes support.
- **The notes and his CVs decide what is true. The posting decides what is
  chosen.**
- Finding and ranking lines is **plain code**, with no model call.
- Claude (the app's agent, currently Opus 4.6) is called only for what code
  could not fill, and sees only that slot.
- A line the notes do not back is caught before it lands, not after.

## Non-goals

- No change to the frozen keyword list, its counting, or the score ring. The
  ring still counts with `match.score_against`.
- No change to the fit rating.
- No drawn graph. The links between lines, CVs, notes and keywords work behind
  the scenes; he does not need to see them.
- Not the cover letter. It stays argued per posting as today.

## Anti-goals

- **A line he cannot defend.** Nothing goes on the page that neither his notes
  nor one of his CVs backs. "It was on a CV before" is not enough on its own
  when the line was the model's (see provenance).
- **The library feeding itself.** A line Claude reworded and he accepted must
  not become independent evidence that he did something.
- **One posting's choices leaking into the next.** The Zapier failure.
- **A page that repeats itself.** Five slots saying "roadmap" while
  "go-to-market" is missing.

## Decided

### What is fixed, what is stable, what is built fresh

- **Fixed**, from the base `cv.json`: roles, titles, dates, education, which
  entries exist and their order, and each entry's budget. Which entries are
  switched on is still chosen per posting, as the one-page CV design
  (2026-10-23) says; that choice is the first step of filling the page, before
  any bullet.
- **Stable, tweaked per posting:** skills and the professional summary. They
  are library slots like any other: code picks among the versions he has used
  before, ranked by the posting. Putting a new word into one (e.g. prompt
  engineering into the AI skills line when no past version has it) goes
  through a card, never silently. *(My call, from the builder check.)*
- **Built fresh every time:** every job entry's bullets and the Additional
  Information items.

The base CV is the template. Plain New and the reseed path no longer copy the
newest same-track application's CV. (Duplicate, which names its source, still
copies it; that is a deliberate act.)

### The library

Every line from every CV in `applications/*/cv.json` and the base `cv.json`,
deduplicated, each tagged with:

- its **slot**: which section and which entry it belongs to (e.g. the xPathEdge
  entry's bullets, the skills "AI" item, Additional Information). Entries are
  matched across CVs by company/organisation name, not by position;
- **every CV it appeared on**, and how many;
- its **provenance** (below).

**Every CV counts**, unless he excludes it. He can also strike a single line
so it is never picked again, without excluding the CV it came from. Nearly all
his applications were sent; there is no "sent" mark.

The library is rebuilt when a CV is saved. Its size today: 189 distinct lines,
about 8,300 words, across 33 CVs.

### Provenance

Each line is one of:

- **his**: in the base CV, or typed/edited by him;
- **from the notes**: a line the support check passed against a notes passage;
- **reworded**: a line Claude proposed and he accepted, linked to the line it
  was reworded from.

A reworded line can win a slot, but counts as evidence only through its
original. Striking an original strikes its rewordings.

### Search

SQLite FTS5 (in Python's standard library; confirmed available, SQLite 3.45.1),
with BM25 ranking and stemming, queried with the posting's frozen keyword list.
Multi-word keywords are matched as phrases. Ranked per slot.

When a posting says nothing that applies to a slot, the slot gets **its
most-used line** (the version on the most CVs). That is how xPathEdge keeps its
usual bullets on a posting that does not care about xPathEdge.

### Filling the page

The page is filled **as a whole, not slot by slot**. Once a keyword is on the
page, it stops counting for the remaining slots, so later slots go after what is
still missing. No line is used twice.

A keyword is placed where it honestly fits: work he has done goes in a bullet,
a tool in the skills line it belongs to, something he is exploring or learning
in Additional Information. No keyword is forced onto a job entry.

Additional Information holds short facts: work authorisation (he is a US
permanent resident and needs no sponsorship, `notes.md:10`), what he does
outside work, and a certification only when the posting cares about it.
Self-descriptions ("comfortable in ambiguity") do not go there; they belong in
the summary or are shown through bullets. Code cannot tell these apart, so
each Additional Information line in the library is labelled once by kind
(authorisation, interests, certification, self-description) with a Jev
`choice` when the library is built, cached with the line, and he can change a
label. Only self-description is barred from the slot; certification is placed
only when the posting's keywords touch it. *(My call, from the builder
check.)*

After filling, the one-page measurement (`Browser.measure`, the entry budgets)
runs; a line over its allowance is swapped for its next-ranked, shorter
candidate.

### The support check (Jev)

Lines from the **base CV** skip the check: he wrote it, it is his default.
Every other line in the library is checked, **once ever, not per
application**. The result is stored with the line and re-checked only when the
notes change. The check follows TypeSafe's citation-check pattern:

1. **Code first.** If the line's claims and named tools appear in a notes
   passage (FTS5 finds the closest passages), it passes with no model call.
2. **Jev otherwise.** A TypeSafe `choice` question, state `{line, passage}`,
   options **supports / contradicts / says_nothing**. Several lines go in one
   request.
3. **A threshold decides.** A confident "supports" passes. Anything else
   is **held back**, not placed: the next-best line that passes takes the
   slot.

**The first check runs over the whole library at once**, when the library is
first built, before he recreates Addepar. Most lines pass in code; the rest go
to Jev in a few batched requests (at most about 30,000 input tokens, once).
He then gets **one review page** listing only the lines that failed, plus the
unconfirmed chat-log notes, each with **Keep** or **Strike**. Keep is his
deliberate choice and stands from then on. About ten to fifteen minutes,
once. After that only new lines are checked, one at a time as they appear (a
fresh rewording, a new note).

A line that fails later, on a new application, is held back the same way, and
its card says "held back: your notes don't back this" with **Use anyway** or
**Strike**.

Decided by him, 2026-10-24.

### Trust in the notes' chat log

The dated "From the chat" log at the bottom of `notes.md` is written by two
paths: an **"I have…" answer** (his own typed sentence) and **"Keep this"** on a
note the assistant suggested (the assistant's wording). Jev only checks that a
line agrees with a passage, not that the passage is true, so a model-suggested
note would pass whatever it says. `notes.md:429` (Zapier, n8n, Webflow,
Airtable) is that case.

- From now on every log entry records its source.
- His own answers count as fully as the rest of the notes.
- An assistant-suggested note counts only once he has **confirmed** it: the
  first time it is the only backing for a line, he gets a card ("your notes
  only say this because the assistant suggested it. Is it true?"). Yes marks it
  confirmed for good; no strikes it from the evidence.
- Entries already in the log carry no source, so all are treated as
  unconfirmed and each asks once. Line 429 will be the first.

Decided by him, 2026-10-24.

Called over HTTP (`POST https://api.typesafe.ai/v1/systemone`) with `urllib`,
key from the `TYPESAFE_API_KEY` environment variable, which is set on his
machine. Never sent to the browser. The SDK is not used.

Jev may also be asked, for a keyword FTS5 found no line for, whether a
candidate line shows that experience in other words (a `noul`). Whether this is
worth it is measured, not assumed.

### What he sees

The page arrives **already filled**. His usual lines go straight on, marked as
his usual. A short panel beside the page lists cards only for:

- a slot where code put something **other than the usual line** for a posting
  keyword (Keep · Use usual);
- a **close call** between two lines (Keep · Swap);
- a new line that was **held back** by the support check (Use anyway · Strike);
- the keywords **nothing in his history answers** (Ask Claude).

Every settled line stays clickable on the page to show its other versions. The
mockup he chose is `new-application-v2.html` in this session's brainstorm
folder: three cards instead of fifteen.

### Claude's part

Called only from an "Ask Claude" card or when he asks a line to be reworded.
It is sent that slot alone: the slot's keywords, the top candidates, and the
matching notes passage. Not the whole notes, not the whole CV. Its output is a
proposal card as today (Use this · Skip), and an accepted rewording is stored
with **reworded** provenance.

## Settled while talking it through

- He would rather avoid the model where code can do the job, to cut token
  cost.
- A local model (Ollama on his Ryzen 7 6800H, 16 GB) was considered and not
  chosen. A local embedding model remains a later option if FTS5 misses lines
  that say the same thing in other words.
- The Obsidian-style graph he raised means the links behind the scenes, not a
  picture. No drawing library.
- When the design ships he will delete the Addepar application and create it
  again, to see what the tool builds.

## Separate small job, same session

The application list fits the screen's height, with pagination at the bottom
instead of scrolling.

## Left to the builder

- The library's file name and location, and its schema.
- How slots are keyed across CVs (company name normalisation), and how a line
  he edited is told apart from one Claude wrote, for CVs made before provenance
  existed. *(My call: lines on existing CVs that match an accepted proposal in
  that application's `chat.json` are **reworded**; everything else on existing
  CVs is **his**.)*
- The BM25 weighting against the most-used fallback, and what counts as a
  "close call".
- The Jev confidence threshold. *(My call: start at 0.8, as the cookbook does,
  and adjust on his data.)*
- Whether the tailoring round's wording step is replaced by this, or runs after
  it on what is left.

## Noticed, not decided

- `notes.md:429` still says Zapier, n8n, Webflow and Airtable; he told the
  session Make.com and Airtable. The notes are his to correct.
- Ramp, LSEG and Addepar CVs carry the Zapier bullet; he chose earlier to work
  forward rather than fix old applications.

# Posting keywords: one list, read once, answered from the notes

**Date:** 2026-10-23
**Status:** Reviewed, awaiting approval
**Product area:** naming (`server.py` `_identify`, `assistant.py`), the score ring
(`static/index.html`, `match.py`, `/api/match`), the tailoring round
(`assistant.py` `_TAILOR_WORDING`, `static/chat.js`), gaps and notes cards
(`static/chat.js`, `static/chat.css`, `resume_maker/setup.py`), the cover letter
prompt (`assistant.py`).

## Why

The tool exists to get one person's CV through applicant tracking systems and in
front of a hiring manager, for one posting at a time, without writing anything
he cannot defend in an interview.

What he actually uses a match score for is the list underneath it. Simplify told
him he had 8 of a posting's 13 keywords; the 5 missing words were his to-do list.
Where Simplify left him stranded was the next step: knowing a keyword was
missing, but not how to say it about his own work without it reading as pasted
in.

This tool does that badly today, in three ways that surfaced on the Flexport
posting:

- **The ruler changes.** The ring counts the posting's repeated words until
  Read The Requirements is pressed, and after that silently counts against the
  AI's requirement list instead. One posting showed 52 and then 25 for the same
  saved CV.
- **The list re-rolls.** Each Read The Requirements asks the AI again, and the
  list comes back 15 long, then 12, then 10, with different terms. A score that
  moves when the document has not is not a measurement.
- **It is literal where it should be thoughtful, and thoughtful where it should
  be literal.** Matching a requirement only as the exact phrase missed "low-code
  tooling" on a CV that says "low code". Meanwhile the work of finding where his
  experience answers a requirement, which needs judgement, is left to him.

## Goals

- One keyword list per posting, read by the AI **once**, when the posting names
  the application, and then **frozen**. It changes only when he explicitly asks
  for a fresh read.
- One ruler. The ring counts that frozen list and nothing else, as "9 of 13",
  with the missing keywords shown underneath as the to-do list.
- Each missing keyword arrives with its answer: where in his notes he did that
  thing, and a line that says it in the posting's exact words.
- A keyword the notes cannot answer becomes a question about the experience
  underneath it, not about the word.
- The CV and the letter are each scored against the same list, separately.

## Non-goals

- No second store of his history. The notes remain the only record of what he
  has done. Everything per posting is derived from them and kept beside the
  application, the way the job description already is.
- No change to how the fit rating works. It stays the AI's hiring-manager
  opinion, separate from the keyword count, and he wants to keep it.
- Not a combined CV-plus-letter score.

## Anti-goals

- **A count that flatters him.** The keyword count imitates the ATS, and the
  ATS searches for literal words. If the AI judged "aligned teams in three
  regions" to be a match for *stakeholder management*, the score would rise
  while the ATS still found nothing. The count therefore stays plain code.
  The AI's judgement goes into choosing the list and into writing the keywords
  onto the page honestly, never into deciding whether a keyword is present.
- **A line he cannot defend.** Every suggested line must trace back to
  something in his notes. The Flexport letter's "Zapier, Webflow and n8n" was
  invented and repeated as if supported; nothing in this design may produce that.
- **Generic words.** A word that would fit any job ad on its own
  (*organization*, *communication*, *ownership*) is not a keyword.

## Decided

### The list

Read by the AI at naming time (step 2 of the current flow), in the same moment
the tool already reads the posting for company, role and track. Saved in the
application folder beside the job description. Shown in the ring panel.

What may be on it, as he described it:

- Terms a recruiter would type into a search: a skill, a tool, a method, a
  domain. Something a person has or has not done.
- **In the posting's own words, verbatim.** "compliance-first mindset" stays
  "compliance-first mindset". Shortening it throws away what they asked for, and
  a phrase that does not suit a CV bullet may still be answered in the letter.
- For a product role, the craft terms: product discovery, product lifecycle,
  stakeholder management, prioritization, trade-offs. Not every noun.
- No company name, no perks, no pay, no location, no legal boilerplate.
- About ten to fifteen terms, the size of Simplify's thirteen.

A fresh read is an explicit action, labelled as replacing the list. Nothing
re-reads it implicitly.

### The count

Plain code, against the frozen list. A keyword counts when it appears in the
document. A multi-word keyword counts when its words appear together in one line
of the document, so "low code" and "tooling" in the same bullet satisfy
"low-code tooling", while a stray "tooling" elsewhere does not.

The CV is counted on its own; the letter on its own. Only what prints is
counted: an entry that is switched off does not contribute.

### The map

When the list is frozen, the AI reads his notes once against it and writes, for
each keyword:

- **from** — the place in the notes where he did that thing, quoted closely
  enough that he recognises it.
- **say** — one line that states it in the posting's exact words, short enough
  to be a CV bullet.
- or, if nothing in the notes answers it, that it is **unanswered**.

The map is saved with the application, beside the list. It is a derived page,
regenerated when the notes change in a way that answers a keyword (see gaps),
and on request.

The CV draws on the **say** lines. The letter draws on the same entries but may
tell the longer story behind the **from**.

### The round's wording stage

Instead of deciding for itself what to change, the wording stage works down the
missing keywords in list order. Each card answers one keyword from the map: a
proposal for a specific bullet, naming the notes section it came from. The
existing one-card-at-a-time behaviour, remembered refusals, and stop rules are
unchanged.

### Unanswered keywords

Become questions, asked about the experience underneath the word rather than the
word itself. Not "Do you have a compliance-first mindset?" but
"Compliance-first mindset: have you worked somewhere rules or data access
limited what you could do?" That is the question that finds his Amazon work,
where he mocked customer experiences with his own account so he only ever saw
his own data.

His answer goes into the notes and fills that keyword in the map. A plain no is
also written to the notes, so it is never asked again on any posting, and the
keyword stays honestly missing from the count.

### Card copy

| Card | Buttons |
|---|---|
| A change to the CV or letter | **Use this** · **Skip** |
| A fact for the notes | **Keep this** · **Skip** |
| A question about a keyword | **I have…** · **I haven't** |

"I have…" opens a box for one sentence. "I haven't" records the plain no.

### Chat appearance

- The AI's messages: a light shade of the app's black.
- His messages: off-white, black text.
- Anything headed for the notes: a very pale green, close to off-white, so a
  glance tells a change to his documents from a change to his notes.

## Settled after review

His answers:

- **A different posting means a fresh list.** Pasting a new or fuller posting
  into an application that already has a list reads a new list straight away,
  and the map is rebuilt from it. The old list is replaced, not kept beside it.
- **The letter's count stays hidden** until he asks for it, as today. Watching a
  number while writing a letter turns it into a keyword list. The letter still
  draws on the shared map through its prompt.

Calls made by the designer, for him to check:

- **One ruler means the old one goes.** The repeated-words counter is removed
  from the ring. An application that has a posting but no list (every existing
  application, a Duplicate, a naming call that failed) gets its list read the
  first time it is opened. The ring shows nothing rather than a different
  measure while that read is running.
- **The wording stage works down the list in order, one card at a time.** A
  keyword the map answers becomes a proposal; a keyword it cannot answer becomes
  its question card, in the same place in the order. Today's rule that a
  question ends the step is dropped. Removing lines a previous posting left
  behind stays, offered after the keywords. The step ends when nothing is left.
- **An "I have…" answer is written to the notes as he typed it**, because it
  is his own words about his own work. Only that keyword's map entry is
  rewritten; the rest of the map, including lines he has already used, is left
  alone. The round then carries on with the next keyword.
- **A "no" is a third state in the map.** "I haven't" writes a plain sentence
  to the notes in a fixed form ("I have not …", as the current button already
  produces). When a map is built, a keyword the notes explicitly say no to is
  marked **declined**, not unanswered, so a reworded version on a later posting
  is not asked again. The ring shows declined keywords apart from unasked ones.
- Where new notes land in `notes.md` is unchanged in this scope, and stays in
  the list below.

## Left to the builder

- Where in the application folder the list and map are stored, and their file
  format.
- How "its words appear together in one line" is tokenised, provided it uses the
  existing stemmer in `match.py` and treats hyphens and slashes the way the
  current counter does.
- Whether the map is generated in the same call as the list or a second one.
- How the ring panel lays out the missing keywords with their suggested lines.

## Noticed while walking the flow, not yet decided

These came up while walking through the application and he has not ruled on
them:

- The letter's prompt cannot see the CV's titles and dates, which is how a
  letter came to claim "three years" and "lead developer" against a CV that said
  otherwise.
- The browser holds edits as a draft until Save. A tab opened before the file on
  disk changed will write its older copy back over the newer file without
  warning, which is how a patch to the Flexport CV was lost.
- New notes from the chat land in a dated log at the bottom of `notes.md`
  rather than in the section they belong to.
- The letter has no round of its own; it is the one place he still has to type
  to get started.

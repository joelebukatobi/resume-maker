# Chat cost and Voice

**Date:** 2026-10-10
**Status:** Approved for planning
**Product area:** chat plumbing (`llm.py`, `assistant.py`, `server.py`, `static/chat.js`)

## Problem

A chat that runs long sends the whole transcript again on every turn, and
nothing trims it. Measured: ~1.44M tokens across four applications for ~33k
back, with the transcript alone reaching 19k tokens by message 82. Three
mechanical leaks feed this:

- The system prompt's **field list** (the live document) is the tail of the
  prompt and is never marked cacheable, so it is re-charged every turn even
  when the user has edited nothing.
- **Resolved proposals** — JSON blocks already accepted or dismissed — are
  re-sent verbatim forever, at roughly a kilobyte each.
- **History is never capped** past a hard reject at 200 turns; 200 turns is
  already far more than any useful context.

Separately, the chat can file a fact about the candidate's *experience* into
`notes.md` (the `notes` block), but there is no equivalent for a rule about
*how to write*. Those rules live in `assistant.md` (Voice) and must be typed
by hand.

## Goals

- Send less per turn, without changing what the model can see of the live
  document, the style file, the notes or the job description.
- Cache the system prompt on Anthropic when the document has not changed.
- Never resend an accepted or dismissed proposal's JSON.
- Bound the transcript to a sliding window while keeping the on-screen
  scrollback and the stored history whole.
- Let the chat grow Voice the same way it grows Notes: an offered block and an
  "Add to Voice" card, written only when the user accepts.

## Non-goals

- Rolling summaries or a summarisation model call.
- Caching the transcript prefix (see the tension note under Sliding window).
- Changing the `proposal` / `answer` / `notes` protocols or their cards.
- Auto-applying anything: Voice, like Notes, is written only on accept.
- Any new dependency; stdlib and the existing provider plumbing only.

## 1 · Cache breakpoint on the field list

**Today.** `prompt_parts` (`assistant.py:217`) returns a `stable` prefix
(rules, Style, Notes, JD) and a `volatile` tail (fit note, coverage note, the
field list). `stream_chat` (`llm.py:312`) marks only `stable` with
`cache_control: ephemeral` and sends the volatile tail as a plain second block.

**Change.** Mark the volatile block as a second breakpoint. Anthropic caches
the prefix up to each breakpoint, up to four. With both marked:

- document unchanged this turn → the whole system prompt is a cache hit;
- document changed → `stable` still hits, only the volatile block is re-billed.

OpenAI-style providers detect the repeated prefix themselves, so this is
Anthropic-only plumbing.

**Interface.** `prompt_parts` already returns the two halves; `_chat` passes
them to `stream_chat`. Change `stream_chat` to take the volatile tail
explicitly (e.g. `cacheable=stable, tail=volatile`) and build two marked text
blocks: the stable block, then the volatile block. When `tail` is empty,
behave exactly as today. `complete()` (one-shot import) continues to call
without caching.

## 2 · Strip resolved proposals from the resent transcript

**Today.** Assistant turns are stored with their fenced blocks intact and
`chat.js:709` resends `messages` as `{role, content}`, raw. `message.resolved`
already records `accepted` / `dismissed` per block key (`chat.js:382-403`,
persisted at `server.py:810`), but that knowledge is never used on the way out.

**Where.** The server, in `_chat`, as a transform of the turns handed to
`stream_chat`. The client must send `resolved` alongside `role`/`content`
(today it drops it at `chat.js:709`). Doing it server-side keeps the transform
pure and testable in the existing Python suite, and puts the trimming next to
`_checked_turns`, which already validates the same list.

**Rule.** For each assistant message, walk its fenced blocks in order. A
`proposal` block whose `resolved[key]` is `accepted` or `dismissed` is replaced
by a short marker — `(proposal already applied)` or `(proposal declined)`.
Unresolved proposals keep their full JSON so the model can still revise them.
Prose, `answer` and `notes` blocks are untouched. The resolution key is the
same one the card uses today, so no new bookkeeping.

## 3 · Sliding window

**Today.** `server.py` rejects a request whose messages exceed `MAX_TURNS`
(200) or 400k chars, but never trims.

**Change.** Send only the last `N` turns. `N = 40` (20 exchanges) as a
constant. After slicing, if the first kept turn is an `assistant` turn, drop
it, so the transcript still opens on a `user` turn. Server-side, next to the
strip in §2. The stored history (`/api/chat/history`) and the rendered
scrollback keep everything; only the payload sent to the model is trimmed. The
`MAX_TURNS` reject stays as a backstop.

**Tension, stated deliberately.** Anthropic caches by prefix, so a window that
drops the oldest turn changes the prefix and invalidates any transcript cache
every turn. That is accepted: the window is what bounds the tokens, and we do
not try to cache a transcript we already keep small. The cache breakpoint in
§1 targets the *system prompt*, which stays byte-identical across a window
slide.

## 4 · Voice append

**Today.** The chat offers a `notes` block → "Add to Notes" card → `POST
/api/setup/notes {"append": ...}` → `setup.append_text` files it under `## From
the chat` with a date. `_setup_assistant` (`server.py:574`) has no append
branch, and there is no way to grow `assistant.md` from chat.

**Change.**

- New ```voice``` block. `_CHAT_RULES` gains a rule: emit ```voice``` when the
  user states a rule about *how* to write — tone, a banned word, a structural
  habit — as opposed to ```notes```, which is a fact about their experience.
- `BLOCK` regex in `chat.js` gains `voice`; a `voiceCard` mirrors `notesCard`
  (`chat.js:179`) with head "For your voice" and button "Add to Voice".
- `POST /api/setup/assistant {"append": ...}`; `_setup_assistant` gains the
  same `append` branch `_setup_notes` has (`server.py:583`), via
  `setup.append_text` with the `assistant` size limit. Lands under `## From the
  chat` with a date, identical to Notes.

**Cache note.** Voice is in the `stable` half of the prompt, so appending to it
changes the cached prefix and costs one full re-charge on the next turn. Voice
changes are rare and reviewed, so this is accepted.

## Data & API

| Route | Change |
|-------|--------|
| `POST /api/chat` | messages now include `resolved`; server strips + windows before the call; `stream_chat` marks a second block |
| `POST /api/setup/assistant` | accepts `{"append": "..."}`, like notes |
| `POST /api/setup/notes` | unchanged |

No new persisted fields. `message.resolved` and `message.document` already
exist in the stored history; `resolved` simply starts travelling with the chat
request as well.

## Caps

- Window: `N = 40` turns, a constant in `assistant.py`.
- The size guard moves with it: the old 400k reject ran over the whole stored
  conversation; it now runs over the condensed turns actually sent
  (`MAX_CHAT_CHARS`), so windowing a long chat is not rejected before it helps.
- Existing `MAX_TURNS = 200` and `MAX_HISTORY` stay as server backstops.
- `assistant.md` keeps its existing save limit via `check_text`.

## Files

- `resume_maker/llm.py` — second cache breakpoint in `stream_chat`.
- `resume_maker/assistant.py` — `voice` rule in `_CHAT_RULES`; `condense(turns)`
  helper that strips resolved blocks and windows the list.
- `resume_maker/setup.py` — reuse `append_text` as-is.
- `server.py` — `_chat` condenses the turns; `_setup_assistant` gains the append
  branch.
- `static/chat.js` — `voice` in the `BLOCK` regex, `voiceCard`, send `resolved`
  with each message.
- `tests/` — the four checks below.

## Testing

Each behavior leaves one runnable check, all in the existing Python suite:

- `stream_chat` marks two cacheable blocks for Anthropic and one when no tail
  is given (extend `test_assistant.test_claude_gets_the_prefix_marked_as_cacheable`).
- The strip transform replaces a resolved `proposal` block with its marker and
  leaves an unresolved one and all prose untouched.
- The window keeps the last `N` turns and does not start on an assistant turn.
- `POST /api/setup/assistant {"append": ...}` files text under `## From the
  chat` and respects the assistant limit.

## Success

A long chat sends a bounded transcript with no dead proposal JSON, the system
prompt is a cache hit when the document is untouched, and a rule the user
states out loud can be added to Voice from the chat exactly the way a fact is
added to Notes.

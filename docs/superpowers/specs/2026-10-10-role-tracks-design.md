# Role tracks

**Date:** 2026-10-10
**Status:** Approved for planning
**Product area:** applications (`applications.py`, `server.py`, `assistant.py`, `static/apps.js`, `static/chat.js`)

## Problem

Every application is tailored from the one worked on most recently. That is
right when the applications are all the same kind of job, and wrong the moment
they are not: a Product CV and an Engineering CV are different documents, and
forking an Engineering application from whatever Product application happened
to be open last carries the wrong framing, sections and emphasis into it.

There is no notion of *what kind of role* an application is for, so nothing can
pick the right starting document.

## Goals

- Every application carries a **track**: Product, Engineering, Data, Design or
  Other.
- The track is filled in automatically from the job description, the same
  identify step that already names the company and the role.
- A new application, once its job description is pasted and its track known,
  forks its **CV and cover letter** from the **most recently edited live
  application of the same track** — the latest version of that track's CV.
- The swap never destroys work: it only happens while the new application's
  documents are still the untouched copies it was created with.
- The track is visible on each application row and can be changed by hand.

## Non-goals

- Dedicated per-track master files, or a page for editing them. The newest
  same-track application *is* the master (approved: tag applications).
- Pulling themes or any other file; only `cv.json` and `cover.json` move.
- Backfilling existing applications from their stored job description. They
  start untagged and are tagged by hand or by a future identify.
- Re-tracking or re-seeding an application after the fact.

## Model

- `TRACKS = ("Product", "Engineering", "Data", "Design", "Other")`.
- `app.json` gains `track: str` (empty string when untagged) and
  `seed: {"cv": <sha256>, "cover": <sha256>}` — hashes of the two files as
  first written at create. Empty/missing doc means "nothing to protect".
- `Application` gains `track: str = ""`; `as_json` already serialises it.

## Detection

`_JOB_RULES` already asks the model for `company` and `role`. Add `track`:

```
{"company": "...", "role": "...", "track": "Product|Engineering|Data|Design|Other"}
```

`parse_job` matches the value against `TRACKS` case-insensitively and falls back
to `"Other"` for anything else. Same untrusted-input handling as the existing
two fields. No extra model call.

## Seeding at identify

`identify` currently renames the application from the posting. It gains a
second job, in order:

1. **Tag.** Write the detected track into `app.json`.
2. **Re-seed, if untouched.** For each of `cv.json` and `cover.json` whose
   current sha256 still equals the hash in `seed` (i.e. the user has changed
   nothing), replace it with the copy from the best source.
3. Report `reseeded: bool` and the `track` in the response.

**The best source** is the live application with the same track whose newest of
`cv.json` / `cover.json` has the greatest mtime, excluding the application
being seeded and excluding archived ones. If no same-track application exists,
nothing is re-seeded and the application keeps the copy create made.

Per-document, so editing only the CV still lets the cover letter be pulled.

### The draft wrinkle

The editor autosaves to `localStorage` (`resume-maker.draft.<slug>`) and
restores a draft that differs from disk. If identify re-seeds, the stale draft
would restore on the reload and clobber the fresh copy. So the client, when the
identify response says `reseeded`, drops that application's draft before
navigating. `chat.js::nameFromJob` already reloads after identify; it clears the
draft first when `reseeded` is true.

## Changing the track by hand

`POST /api/apps {"action": "track", "slug": ..., "track": ...}` validates the
value against `TRACKS` and writes it. A menu item "Track: X" opens the existing
`window.prompt` style (like Rename), pre-filled with the current track, and a
small track chip is shown on each row. Setting a track does **not** re-seed;
it only affects what future applications fork from.

## Data & API

| Route | Change |
|-------|--------|
| `POST /api/apps` `create` | writes `track: ""` and `seed` hashes for the copied docs |
| `POST /api/apps` `identify` | records the detected track; re-seeds untouched docs; returns `track`, `reseeded` |
| `POST /api/apps` `track` | new: set the track by hand |
| `GET /api/apps` | each application now includes `track` |

`create` with an explicit `source` (Duplicate) also copies the source's track.

## Caps & safety

- `shutil.copyfile` only, never a move; the source application is untouched.
- A source is only chosen from **live** applications, so archiving retires an
  application as a base as well as from the list.
- The untouched check is by content hash, so any Save (or accepted proposal
  written to disk) protects the document from being overwritten.

## Testing

In the existing Python suite:

- `parse_job` returns a valid track from each value and `Other` for junk.
- `create` stores `seed` hashes that match the files it wrote.
- identify on an untouched new application of a different track pulls the CV
  and cover letter from the newest same-track app and reports `reseeded`.
- identify leaves an edited document alone but still pulls the untouched one.
- identify with no same-track application re-seeds nothing.
- `track` action sets a valid value and rejects an invalid one.
- an archived application is never chosen as a source.

## Success

Paste an Engineering posting into a fresh application and its CV and cover
letter come from your most recently edited Engineering application, not from the
Product one you were just looking at — and nothing you had already typed is
lost.

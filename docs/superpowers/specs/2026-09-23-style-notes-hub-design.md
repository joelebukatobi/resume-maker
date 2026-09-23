# Style · Notes · Documents hub

**Date:** 2026-09-23  
**Status:** Approved for planning  
**Product spine:** Style (`assistant.md`) → Notes (experience bank) → Documents (CV + letter) → Connect model → Chat

## Problem

`/resume` and `/cover-letter` assume the user already has taste rules and a deep experience bank. First-time (and published) users land on “Resume or Cover Letter” with nothing for the model to draw from, so tailoring and application answers invent or stay thin.

## Goals

- Make **Style**, **Notes**, and **Documents** first-class and obvious on `/`.
- Keep `/resume` and `/cover-letter` layout as they are (left editor · preview · chat).
- Feed **Style + Notes** into every chat turn (and form-question drafts) so the model reuses real stories.
- Notes are **shared** across Resume and Cover Letter; Style is shared too (one house voice).
- Notes are a **living** hive — appendable when a JD jogs memory.
- No vector DB / pip RAG in v1: whole notes file in context with a size cap; ask when still thin.

## Non-goals (v1)

- True embedding RAG, Ollama, LiteLLM-in-process.
- Separate notes per mode.
- Auto-apply chat proposals or auto-rewrite the whole CV from notes.
- Changing DOCX/PDF render pipelines beyond prompt context.
- Multi-user auth / Vercel deployment mechanics (local loopback stays; publish is later).

## Product model

| Pillar | File | Meaning |
|--------|------|---------|
| Style | `assistant.md` | How the editor writes (method, bans, role tastes) |
| Notes | `notes.md` | What you’ve done (raw truth for JD + form questions) |
| Documents | `cv.json`, `cover.json` (+ themes) | What you send |
| Model | keys in `~/.config/resume-maker/` | Engine |
| Chat | history per mode | Workbench |

## UX

### `/` Hub

Three equal tiles: **Style**, **Notes**, **Documents**.  
Below: **Model** strip (Connect agent / shows connected provider·model).

Each tile shows readiness (empty / started / ready).  
Documents opens Resume | Cover Letter (chooser or dual links).  
First visit nudges Style → Notes → Documents.

### `/style`

- Role chips: PM / SWE / Consultant / Other → seed boilerplate into editor if empty or user confirms replace.
- Editable `assistant.md` (textarea or simple sections).
- Save → `assistant.md` on disk.

### `/notes`

- Large markdown editor for `notes.md`.
- Optional upload (`.txt` / `.md` / `.docx`) appends or replaces with confirm.
- Optional stub buttons that append headed sections (“Owned end-to-end”, “Ops / QA / triage”, “People don’t know”).
- Save → `notes.md` on disk.

### `/resume` · `/cover-letter`

- Unchanged three-pane UI.
- Quiet hub links: Style · Notes · Documents (current mode highlighted).
- Chat empty copy mentions Notes/Style when present.
- JD tile, import, proposals unchanged.
- Application / form questions: draft in chat (copy-paste); only propose into letter/CV when asked.

## Chat behavior (with Style + Notes)

When JD or form questions are present:

1. Map requirements → Notes + open document fields.
2. List matches and gaps.
3. Ask one company-specific follow-up per gap before inventing.
4. Propose field edits via existing `proposal` blocks; form answers as labeled chat prose.

Protocol (paths, proposal JSON, no invent facts) stays in `assistant.py` code.  
`assistant.md` is taste + method appended to the system prompt.  
`notes.md` is appended (capped) as `## Experience notes`.

## Role boilerplates

Ship short starter `assistant.md` variants (or one file with role sections) for PM / SWE / Consultant / Other. Core rules (ask-before-invent, XYZ, never invent metrics) shared; role section adds hiring-manager tastes only.

## Data & API

- Paths: `--assistant` default `assistant.md`, `--notes` default `notes.md` (next to cv/theme).
- `GET /api/setup` → `{ assistant, notes, status: { style, notes, cv, cover, model } }`.
- `POST /api/setup/assistant` · `POST /api/setup/notes` → validate size, atomic write.
- Chat `/api/chat` loads assistant + notes into `system_prompt(...)`.
- Missing files = empty string (current chat behavior).

## Caps

- Notes in prompt: e.g. 40–60k chars (trim with clear notice in system text if truncated).
- Assistant file: keep small (few KB); reject absurd sizes on save.

## Success

A new user can: set Style → fill Notes → open Documents → connect model → paste JD / form questions → get drafts grounded in Notes without the model inventing Amazon-style detail that wasn’t written down.

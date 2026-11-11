# AGENTS.md — resume-maker

Canonical instructions for any coding agent working in this repo. Read
`HANDOFF.md` first for live task state.

## Product

Local, loopback-only resume and cover letter tailor: form → JSON → DOCX / PDF,
ATS-friendly. Applications live at `/`; each one is tailored to its posting in the
Write step strip (`/{app}/write`) and the Letter (`/{app}/letter`). The model is
Claude, OpenAI, DeepSeek or Kimi through stdlib `urllib`; Playbook (`playbook.md`)
and My Experience (`notes.md`) go into every model turn. Current state and rules:
the top part of `HANDOFF.md`.

## Run and test

```bash
python3 server.py --no-open          # http://127.0.0.1:8000/
python3 -m unittest discover -s tests
python3 -m resume_maker.audit     # notes.md against the user's own words
```

## Hard constraints

- **Lean by preference, not by rule** (2026-11-10): stdlib first; a pip or npm
  dependency is fine when it buys something important, and anything heavy must run
  on a 16GB laptop with no discrete GPU. No build step. The redesigned frontend is
  native: CSS custom properties for tokens, light-DOM custom elements for
  components, ES modules for scripts, Geist from Google Fonts. Pico is used only by
  the old pages in `static/` and goes with them.
- **Loopback only.** API keys stay on the server and are never sent to the browser.
- **Protocol lives in code**: proposal JSON, field paths and the no-invent rule
  are in `resume_maker/assistant.py`. `playbook.md` is rules, taste and method;
  `notes.md` is experience only.
- **The user's rules live in `playbook.md`** (the Playbook page, `/playbook`):
  what ranks first, how to choose from the notes, the CV's shape, and how the
  writing sounds. It goes into every chat turn. Do not copy those rules into
  prompts in code; code holds the protocol and two locks only:
  `library.bullet_room` (the first experience entry 3 bullets, every other role 2) and
  `assistant.FIXED_CV_FIELD` (the CV's structure, refused on Review). Do not
  change either without the user saying so.
- DOCX and PDF must agree: both renderers read the same `Theme`.
- Follow `DESIGN.md` for all new UI: palette, type, spacing, components and
  states. The pages in `static/` are the previous language, kept until they are
  rebuilt; do not copy their look into new work.

## Commits

Follow `docs/COMMITS.md`: conventional commits, many small slices, **commit as
we go, never push unless asked in that moment**, and no LLM co-author or
contributor trailers. Log each commit or decision as one line in `COMMITS.md`.

## Where things are

- Spec and plan: `docs/superpowers/specs/`, `docs/superpowers/plans/`
- Chat plumbing: `resume_maker/llm.py`, `resume_maker/assistant.py`
- Setup readiness and limits: `resume_maker/setup.py`
- Pages: `static/home.html`, `static/voice.html`, `static/notes.html`,
  `static/settings.html`, `static/write.html` (CV workbench). Cover letter is
  still `static/index.html` + Pico + `static/chat.js` until that page is rebuilt.

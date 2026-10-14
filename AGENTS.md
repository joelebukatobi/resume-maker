# AGENTS.md — resume-maker

Canonical instructions for any coding agent working in this repo. Read
`HANDOFF.md` first for live task state.

## Product

Local, loopback-only resume and cover letter editor: form → JSON → DOCX / PDF,
ATS-friendly. A hub at `/` walks the spine **Voice → Notes → Documents → Model →
Chat**. The chat talks to Claude, OpenAI, DeepSeek or Kimi through stdlib
`urllib`; Voice (`voice.md`) and Notes (`notes.md`) go into every chat turn.

## Run and test

```bash
python3 server.py --no-open          # http://127.0.0.1:8000/
python3 -m unittest discover -s tests
python3 -m resume_maker.audit     # notes.md against the user's own words
```

## Hard constraints

- **No new pip or npm dependencies** for the product. Standard library, plus
  Pico CSS and Google Fonts from their CDNs.
- **Loopback only.** API keys stay on the server and are never sent to the browser.
- **Protocol lives in code**: proposal JSON, field paths and the no-invent rule
  are in `resume_maker/assistant.py`. `voice.md` is taste and method only;
  `notes.md` is experience only.
- DOCX and PDF must agree: both renderers read the same `Theme`.
- Match the visual language: dark grey chrome, dull green accent (`#3d5a43`),
  Fira Sans Condensed. No purple or cream "AI" palettes.

## Commits

Follow `docs/COMMITS.md`: conventional commits, many small slices, **commit as
we go, never push unless asked in that moment**, and no LLM co-author or
contributor trailers. Log each commit or decision as one line in `COMMITS.md`.

## Where things are

- Spec and plan: `docs/superpowers/specs/`, `docs/superpowers/plans/`
- Chat plumbing: `resume_maker/llm.py`, `resume_maker/assistant.py`
- Setup readiness and limits: `resume_maker/setup.py`
- Pages: `static/home.html`, `static/voice.html`, `static/notes.html`,
  `static/index.html` (editor) with `static/chat.js`

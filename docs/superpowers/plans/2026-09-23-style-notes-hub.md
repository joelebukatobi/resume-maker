# Style · Notes · Documents Hub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a hub spine (Style, Notes, Documents, then Model) and feed `assistant.md` + shared `notes.md` into chat so tailoring and form answers pull real experience.

**Architecture:** Keep `/resume` and `/cover-letter` as the existing three-pane editor. Add `/`, `/style`, `/notes` as static pages; persist `assistant.md` and `notes.md` via small setup APIs; extend `assistant.system_prompt` to append Style + capped Notes. No RAG/embeddings in v1.

**Tech Stack:** Python 3 stdlib `http.server`, existing `resume_maker.assistant` / `llm`, static HTML/JS/CSS (Pico + Fira Sans Condensed), unittest.

**Spec:** `docs/superpowers/specs/2026-09-23-style-notes-hub-design.md`

## Global Constraints

- Loopback-only server; no new pip/npm product dependencies.
- Atomic writes for markdown files (same pattern as `_atomic_write` in `server.py`).
- Protocol rules (proposal JSON, field paths, no invent facts) stay in `assistant.py` code; markdown is taste + method + experience only.
- Notes are shared across cv/cover modes; one `assistant.md` for both.
- `/resume` and `/cover-letter` layout stays: left editor · preview · chat.
- Prefer thin pages over a SPA framework.

## File map

| File | Responsibility |
|------|----------------|
| `assistant.md` | User Style (created from boilerplate on first save / role pick) |
| `notes.md` | Shared experience bank |
| `templates/assistant-pm.md` (etc.) | Role starter text for Style page |
| `static/home.html` | Hub: Style · Notes · Documents + Model strip |
| `static/style.html` | Edit/save `assistant.md` |
| `static/notes.html` | Edit/save/upload into `notes.md` |
| `static/setup.css` or shared bits in each page | Hub/setup visual language matching home |
| `static/index.html` | Light hub links in topbar only |
| `static/chat.js` | Empty-state copy; no notes UI here in v1 |
| `resume_maker/assistant.py` | Load files; append to `system_prompt`; size caps |
| `server.py` | Routes for `/`, `/style`, `/notes`; setup GET/POST; pass paths into handler |
| `tests/test_assistant.py` | Prompt includes assistant + notes; truncation; missing files |

---

### Task 1: Prompt loading — Style + Notes in `system_prompt`

**Files:** `resume_maker/assistant.py`, `tests/test_assistant.py`

- [ ] **Step 1: Failing tests**  
  Add tests that:
  - `system_prompt` includes contents of assistant text and notes text when provided.
  - Missing/empty assistant or notes does not break (same as today + no extra sections).
  - Notes longer than the cap are truncated and the prompt mentions truncation.
  - Form/JD method lines from shipped default assistant draft appear when that text is passed (optional if default is only on disk later).

- [ ] **Step 2: Run tests — expect fail**  
  ```bash
  python3 -m unittest tests.test_assistant -v
  ```

- [ ] **Step 3: Implement**  
  - Add `MAX_NOTES_CHARS` (e.g. 60000) and helpers `load_text(path) -> str`, `clip_notes(text) -> str`.
  - Extend `system_prompt(document, data, job_description="", *, assistant_md="", notes_md="")`.
  - Append `## Editor style\n{assistant_md}` when non-empty.
  - Append `## Experience notes\n{notes}` when non-empty (clipped).
  - Keep `_CHAT_RULES` first (protocol).

- [ ] **Step 4: Run tests — expect pass**

- [ ] **Step 5: Commit** (only if user asked)  
  Message: `feat: feed assistant.md and notes.md into chat system prompt`

---

### Task 2: Setup API + CLI paths

**Files:** `server.py`, `tests/test_assistant.py` or new `tests/test_setup_api.py` if easier with a thin handler helper

- [ ] **Step 1: Failing tests** for pure helpers if extracted, or document manual API checks if full HTTP test is heavy. Prefer extracting:
  - `readiness(assistant, notes, cv, cover) -> dict`
  - save rejects oversize notes/assistant

- [ ] **Step 2: Implement server**
  - `EditorHandler` gains `assistant_path`, `notes_path`.
  - CLI: `--assistant` default `assistant.md`, `--notes` default `notes.md`.
  - `GET /` → `home.html`; `GET /style` → `style.html`; `GET /notes` → `notes.html`; keep `/resume` and `/cover-letter` → `index.html`.
  - `GET /api/setup` → `{ assistant, notes, status: { style, notes, cv, cover } }` (boolean or enum empty/started/ready). Ready = non-empty file / CV has sections / cover has paragraphs.
  - `POST /api/setup/assistant` body `{ text }` → validate length, atomic write.
  - `POST /api/setup/notes` body `{ text }` → same.
  - Wire `_chat` (and import if useful) to read assistant + notes paths into `system_prompt`.

- [ ] **Step 3: Manual smoke**  
  ```bash
  curl -s http://127.0.0.1:8000/api/setup | head
  ```

- [ ] **Step 4: Commit** if requested: `feat: setup API for assistant.md and notes.md`

---

### Task 3: Default `assistant.md` boilerplate + role templates

**Files:** `templates/assistant-pm.md`, `templates/assistant-swe.md`, `templates/assistant-consultant.md`, `templates/assistant-other.md` (or `static/templates/…` served read-only)

- [ ] **Step 1: Write the approved draft** (from chat) into each template with a short **Role** addendum:
  - PM: discovery, metrics, stakeholders, ops empathy when JD asks.
  - SWE: systems, PRs, latency/reliability, no tool name-drops without work.
  - Consultant: client outcomes, scope, recommendations → delivery.
  - Other: generic professional only.

- [ ] **Step 2: `GET /api/setup/templates` → `{ id, label, text }[]`** for the Style page.

- [ ] **Step 3: Commit** if requested: `feat: role starter templates for assistant.md`

---

### Task 4: Hub UI (`home.html`)

**Files:** `static/home.html`

- [ ] **Step 1: Replace two-tile chooser** with three tiles (Style, Notes, Documents) + Model strip.
- [ ] **Step 2: On load, `GET /api/setup`** and paint status under each tile; Model strip calls existing `/api/llm/providers` (or link “Connect in editor”).
- [ ] **Step 3: Documents tile** → links to `/resume` and `/cover-letter` (inline or nested click).
- [ ] **Step 4: Visual language** — keep Fira Sans Condensed, dull green, no purple/cream AI defaults.
- [ ] **Step 5: Hard-refresh check** at `http://127.0.0.1:8000/`

- [ ] **Step 6: Commit** if requested: `feat: hub spine for Style, Notes, Documents`

---

### Task 5: Style page (`style.html`)

**Files:** `static/style.html` (+ tiny page JS inline or `static/setup.js` shared with notes)

- [ ] **Step 1: Layout** — role chips, textarea bound to assistant text, Save, link back to Hub.
- [ ] **Step 2: Load** `GET /api/setup` + templates; picking a role fills textarea if empty or after confirm.
- [ ] **Step 3: Save** `POST /api/setup/assistant`; toast/status on success.
- [ ] **Step 4: Manual test** — save, reload, text persists; chat system prompt includes it (send a chat, or unit test already covers load).

- [ ] **Step 5: Commit** if requested: `feat: Style page editor for assistant.md`

---

### Task 6: Notes page (`notes.html`)

**Files:** `static/notes.html`

- [ ] **Step 1: Layout** — large textarea, Save, Hub link, stub buttons that append markdown headings + placeholders.
- [ ] **Step 2: Optional file input** — `.txt`/`.md` client-side read append; `.docx` can call a thin reuse of import extract **or** v1 txt/md only (prefer txt/md/docx-via-existing extract if cheap).
- [ ] **Step 3: Save** `POST /api/setup/notes`.
- [ ] **Step 4: Manual test** — append Amazon-style triage paragraph, save, open chat with JD asking about QA/triage, confirm model sees notes (or assert via unit test on `system_prompt`).

- [ ] **Step 5: Commit** if requested: `feat: Notes page for shared experience bank`

---

### Task 7: Editor chrome + chat copy

**Files:** `static/index.html`, `static/chat.js`

- [ ] **Step 1: Topbar** — add text links `Style` · `Notes` · `Hub` (or Documents) without breaking Resume/Cover chips.
- [ ] **Step 2: Chat empty state** — if setup says notes/style ready, mention them; else nudge Hub.
- [ ] **Step 3: Hard-refresh `/resume`** — links work; chat still streams.

- [ ] **Step 4: Commit** if requested: `feat: hub links from resume and cover letter editors`

---

### Task 8: README + sample empty files

**Files:** `README.md`, optional `notes.md` / `assistant.md` gitignored or stubbed

- [ ] **Step 1: Document spine** — Style → Notes → Documents → Connect → Chat; CLI flags; that notes are shared.
- [ ] **Step 2: `.gitignore`** — decide: ignore user `notes.md` / `assistant.md` if personal, keep `templates/` tracked. Prefer ignoring user content, tracking templates only.
- [ ] **Step 3: Commit** if requested: `docs: describe Style Notes Documents hub`

---

## Test plan (release checklist)

- [ ] Fresh clone: open `/`, all three tiles show empty; Model disconnected.
- [ ] Style: pick PM → save → reload persists.
- [ ] Notes: paste long experience → save → `/api/setup` shows notes ready.
- [ ] `/resume`: connect Claude → paste JD about ops/QA → chat asks or uses notes; no invented employers.
- [ ] Cover letter mode: same notes visible to model; letter fields still separate.
- [ ] Form-question paste: drafts in chat, no forced CV proposals.
- [ ] Oversized notes: save rejected or clipped in prompt with notice.
- [ ] `python3 -m unittest discover -s tests -v` green for assistant/setup tests.

## Out of scope follow-ups (do not implement in this plan)

- Embedding RAG over notes  
- Per-mode notes  
- In-chat “add to notes” shortcut (nice later)  
- Publishing to Vercel / multi-tenant auth  

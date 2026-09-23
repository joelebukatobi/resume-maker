# Commit Guidelines

How we split, stage, and write commits on this repo. Follow this before pushing or opening a PR.

---

## Message format

We use [Conventional Commits](https://www.conventionalcommits.org/) with a scope:

```
type(scope): short imperative summary

Optional body — explain why, not just what. Wrap at ~72 characters.
```

### Types

| Type | Use for |
|------|---------|
| `feat` | New behavior, UI, API, or capability |
| `fix` | Bug fix |
| `style` | Visual/layout only (no logic change) |
| `refactor` | Restructure without changing behavior |
| `chore` | Tooling, deps, ignore rules |
| `ci` | GitHub Actions, deploy pipelines |
| `docs` | Documentation only |
| `test` | Tests only |

### Scopes (examples)

`editor`, `preview`, `chat`, `assistant`, `notes`, `server`, `theme`, `cover`, `llm`, `docs`, `test`. Pick the area that owns the change.

### Examples

```
feat(chat): stream assistant replies into the right pane

Wire /api/chat SSE into chat.js so proposals and markdown
render without blocking the editor.
```

```
fix(preview): use zoom instead of transform scale for name glyphs
```

```
docs(hub): add Style Notes Documents design spec
```

**Subject line:** imperative mood (“add”, “fix”, “remove”), lowercase after the colon, no trailing period.

---

## Commit as we go; never push

**Commit continuously.** Land each logical slice as soon as it is working and
its tests pass — do not batch a session's work into one commit at the end.

**Never push.** `git push` (and force-push, PR creation, tag push) happens only
when the user explicitly asks in that moment. "Commit as we go" is standing
permission to commit; it is **not** permission to push. Local history can be
reshaped or amended before it leaves the machine — pushed history cannot.

---

## Prefer as many commits as possible

**Many small commits beat one large commit.** Err on the side of more slices, not fewer.

Group by **logical slice**, not by file type:

| Good slice | Bad slice |
|------------|-----------|
| Chat system prompt loads `notes.md` | “all assistant changes” |
| Hub three-tile layout only | “all frontend” |
| Setup API GET `/api/setup` | “misc server” |
| One Style page save path | “Style + Notes + README” |

Typical slices on this project:

1. **Prompt / model layer** — `assistant.py`, prompt tests
2. **Server / API** — one route or flag group per commit when practical
3. **Editor / preview UI** — one UX concern at a time
4. **Chat UI** — import tile, JD tile, agent modal, markdown — split when they land separately
5. **Docs** — specs, plans, `HANDOFF`, `COMMITS` in their own commit(s)
6. **Tests** — with the feature they prove, or `test(…)` alone if that’s the whole change

If you can honestly describe two different “why”s, use **two commits**.

---

## Critical rule: never split a file across commits by reverting

If you changed a file a lot, **do not**:

- Remove sections to commit “part 1”
- Stash or revert hunks so you can commit the same file again in a “later” state
- Re-add implementations in a follow-up after stripping them for staging

That pattern **loses work**.

### What to do instead

1. **Decide commit boundaries before editing** when you can.
2. **If one file already has many unrelated changes**, commit the **whole file once** under its primary purpose; put related tests in that same commit.
3. **Use as many commits as needed** — ten commits for ten slices is fine and preferred.
4. **Every intentional change must be committed** before you summarize or push (except [Do not commit](#do-not-commit)).
5. **Big picture last** — push, PR, or summary only after slices are committed.

### File-level rule of thumb

| Situation | Action |
|-----------|--------|
| One file, one feature | One commit with the full file |
| One file, mixed concerns you didn’t separate | One commit; dominant scope/message |
| Two features, two files | Two commits |
| Same feature, five files | One commit with all five staged together |

Use `git add path/to/file` (explicit paths). Avoid `git add .` / `git add -A` when splitting.

---

## Author / trailer: no LLM co-contributors

Commits are **yours** (or the human author on the machine). Do **not**:

- Add `Co-Authored-By: Claude …` / `Co-Authored-By: Cursor …` / similar trailers
- Put Claude, Cursor, Copilot, ChatGPT, or any LLM in the author, committer, or message as a contributor
- Use tooling defaults that append AI co-author footers — strip them before commit if they appear

The message may describe *what* changed; it must not credit an LLM as co-author.

---

## Do not commit

| Path / item | Reason |
|-------------|--------|
| `.env`, API keys, `~/.config/resume-maker/keys.json` | Secrets |
| Personal `notes.md` / `assistant.md` if gitignored | User content (templates stay tracked) |
| Large generated `out/*.pdf` unless intentionally shipping a sample | Noise / binaries |
| `__pycache__/`, `.pyc` | Build artifacts |

When in doubt, check `.gitignore`.

---

## Workflow (when asked to commit)

1. **Inspect**
   ```bash
   git status
   git diff
   git diff --cached
   git log -5 --oneline
   ```
2. **Plan slices** — list commits (files per commit) without gutting files to fit the plan.
3. **Commit each slice** — stage only named files; HEREDOC message; **no AI co-author trailer**:
   ```bash
   git add resume_maker/assistant.py tests/test_assistant.py

   git commit -m "$(cat <<'EOF'
   feat(assistant): append notes.md into the chat system prompt

   Give the model the shared experience bank (capped) so JD
   tailoring can reuse real stories instead of inventing them.
   EOF
   )"
   ```
4. **Verify** `git status` is clean except ignored / intentional leftovers.
5. **Stop at the commit.** Do not push. Leave `origin` behind local `main`
   until the user asks for a push in that moment.

---

## Safety (always)

- Do **not** force-push to `main` / `master` without explicit approval.
- Do **not** rewrite history without explicit approval.
- Do **not** skip hooks (`--no-verify`) unless explicitly requested.
- Do **not** amend a pushed commit unless explicitly requested and safe.
- Do **not** commit secrets.
- Do **not** add LLM co-author / contributor trailers.

---

## Plans and suggested messages

Implementation plans under `docs/superpowers/plans/` often include suggested `git commit -m "..."` lines per task. Treat those as starting points; adjust scope/body to match what actually shipped. Prefer **one plan task → one or more commits**, never one mega-commit for the whole plan.

---

## Quick checklist before “we’re done”

- [ ] Every intentional code/doc change is committed
- [ ] As many logical commits as the work allows
- [ ] No file was partially reverted just to split commits
- [ ] No Claude / Cursor / other LLM co-author or contributor trailer
- [ ] Secrets and personal notes are not in the commit list
- [ ] Messages follow `type(scope): summary`
- [ ] Nothing was pushed

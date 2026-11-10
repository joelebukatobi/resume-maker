# TODO — resume-maker

Things agreed or noticed but not built. `HANDOFF.md` is the running log of what
happened; this file is what is still owed. One heading per item, newest first.
Delete an item when it ships and log it in `COMMITS.md`.

## Public site and first-run onboarding

Front-facing marketing site that explains the tool, then signup through SSO
login. First run ingests what only the person can provide and starts the rest
from defaults:

- **CV** — import an existing resume (no starter). Seeds the documents new
  applications inherit.
- **Experience bank** (notes) — no starter; their facts only.
- **Application Tone** (voice) — role starters available.
- **Paper / layout style** — defaults now; later multiple CV and letter looks
  (serif vs sans, header placement, etc.). Style choice drives how documents
  render.

**Fill everything from the uploaded CV (agreed 2026-11-04).** One upload fills
Settings (name, contacts, every job's employer, title, dates and location, every
school and degree) and the notes (a section per job with what the CV says, plus
Skills and Technical skills), so the only work left is tuning the notes. The
profile then lives apart from the base CV, which becomes an input only. CV text
copied into the notes is marked "from your CV, not yet confirmed" and is a
starting point, not proof, until the person confirms or rewrites it; otherwise a
stretched CV line would back itself.

Starters for taste and presentation; no starters for who they are or what they
have done. Settings > Base Files already uploads the CV, letter, notes and voice;
onboarding can reuse it. Local workbench keeps draft autosave, Review coaching
pane, and Settings structure editors first.

## Job description from a link

Paste a job posting's URL and have the server fetch the description, instead of
copying the text by hand. The paste box stays either way: plenty of boards
(LinkedIn, Workday, Greenhouse behind a session) refuse a plain `urllib` fetch
or render the posting with JavaScript, so a link that fails must fall back to
paste rather than block the round.

Shape: `POST /api/jd {url}` fetches with a normal user agent, strips tags to
text, and returns it for review before it becomes the posting — never applied
straight, since a fetch can quietly return a cookie wall. Stdlib only, per the
no-dependency constraint.

## Pico retirement — leftover shell

Native Letter and Write are live. Full Pico delete still means removing
`static/index.html` + chat.css / base.css / `_nav.html` once nothing else loads
them (e.g. `/review` library page).

## Gaps and notes cards — visual redesign

Interaction for gaps/ask moves into the Review right pane; notes Keep stays in
chat. The old mid-green card look is still owed a redesign when that flow has
been used.

## Smaller, still open

- "Large Language Models" spelling in the skills list — undecided (2026-10-27).
- Cache breakpoint on the field list; stripping resolved proposals from history
  (noted 2026-10-10, line 218 of `HANDOFF.md`).
- 11 deferred minors from the posting-keywords review live in
  `.superpowers/sdd/2026-10-23-posting-keywords/progress.md` (git-ignored, so
  they are not visible to a fresh agent — pull them here when they matter).

# TODO — resume-maker

Things agreed or noticed but not built. `HANDOFF.md` is the running log of what
happened; this file is what is still owed. One heading per item, newest first.
Delete an item when it ships and log it in `COMMITS.md`.

## Pico retirement — cover letter holdout

`/{app}/letter` is the native Letter → Review workbench (Resume | Cover letter
in the nav). `/{app}/cover-letter` redirects there. Full Pico delete still
means removing `static/index.html` + chat.css / base.css / `_nav.html` once
nothing else loads them (e.g. `/review`).

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

**Next up** after Settings / Bullets pick / swap (2026-10-29).

## Gaps and notes cards — redesign

He dislikes the gaps and notes cards as drawn (2026-10-27, twice noted). Mid
green `#4f7356` with near-white text was the stopgap. Undecided what replaces
them. Chat now sits under the measure; redesign when that flow has been used.

## Cast inside Bullets (shipping)

Cast is a Bullets substep before the lines: `/api/library` action `cast`
ranks roles from past CV lines against frozen keywords (`fill.suggest_cast`);
On/Off chips; Next writes `entry.off` then opens bullet edit. Not a seventh
strip chip.

## Smaller, still open

- "Large Language Models" spelling in the skills list — undecided (2026-10-27).
- Cache breakpoint on the field list; stripping resolved proposals from history
  (noted 2026-10-10, line 218 of `HANDOFF.md`).
- 11 deferred minors from the posting-keywords review live in
  `.superpowers/sdd/2026-10-23-posting-keywords/progress.md` (git-ignored, so
  they are not visible to a fresh agent — pull them here when they matter).

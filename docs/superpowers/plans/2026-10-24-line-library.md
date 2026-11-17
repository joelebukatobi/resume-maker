# Line Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build every new CV from scratch out of every line he has ever used, picked by the posting's frozen keywords in plain code, with each line checked against his notes, and show him only the decisions code was unsure about.

**Architecture:** A `library` module collects every distinct line from every CV into slots and indexes them in an in-memory SQLite FTS5 table (rebuilt per call; 189 lines, milliseconds). A `fill` module fills the base CV page-wide against the keyword list and returns the new CV plus a short list of cards. A `jev` module asks TypeSafe's Jev, over `urllib`, whether a notes passage supports a line, and labels Additional Information lines. Durable decisions (strikes, keeps, confirmed notes, check results, labels) live in one JSON file beside `notes.md`.

**Tech Stack:** Python 3 standard library (`sqlite3` with FTS5, `urllib`, `json`), vanilla JS, `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-24-line-library-design.md`

## Global Constraints

- No new pip or npm packages. The TypeSafe SDK is not used; plain HTTP via `urllib`.
- TypeSafe key from the `TYPESAFE_API_KEY` environment variable only; never logged, never sent to the browser, never written to disk.
- Endpoint `POST https://api.typesafe.ai/v1/systemone`, model `jev-latest`, `Authorization: Bearer <key>`.
- The keyword count stays `match.score_against`; nothing in this plan changes the ring or the fit rating.
- No commits. Where a task ends, stop; the user commits when he asks, with no co-author trailers.
- `library.json` is personal data: add it to `.gitignore`. Never read or print `notes.md`, `voice.md`, `applications/` or `keys.json` contents in logs or test output; tests use temporary fixtures.
- Card copy, verbatim: **Keep** · **Use usual** (a non-usual line placed for a keyword), **Keep** · **Swap** (close call), **Use anyway** · **Strike** (held back), **Ask Claude** (unanswered keywords), **Keep** · **Strike** (review page). The held-back card text: "held back: your notes don't back this". The unconfirmed-note card text: "your notes only say this because the assistant suggested it. Is it true?"
- Jev confidence threshold `SUPPORT_THRESHOLD = 0.8`.
- Test command for the whole suite: `python3 -m unittest discover -s tests`.

## Review Focus

1. **A CV whose entry is named differently across CVs** ("xPathEdge LLC, IT Services" vs "XPATHEDGE LLC") must land in one slot, or its history splits and the most-used line is wrong. Pinned by `test_entries_with_the_same_company_share_a_slot` (Task 2).
2. **A keyword list that is empty or stale** (identify failed to read one) must still produce a CV: every slot gets its most-used line and there are no keyword cards. Pinned by `test_no_keywords_gives_the_usual_page` (Task 5).
3. **Jev unreachable, no key, or an HTTP error** must not block naming the application or filling the page: lines code could not pass are held back as unchecked, and the review page says the check did not run. Pinned by `test_no_key_holds_back_without_failing` (Task 4).
4. **A line with FTS5 syntax in it** (quotes, `-`, `*`, `:`, `AND`, "C#", "Node.js") must not raise when queried. Pinned by `test_punctuation_in_terms_and_lines_is_safe` (Task 2).
5. **A CV file that does not parse** (hand-edited, half-written) must be skipped, not crash the library. Pinned by `test_an_unreadable_cv_is_skipped` (Task 2).

---

### Task 1: Notes log entries record who wrote them

**Files:**
- Modify: `resume_maker/setup.py` (`append_text`)
- Modify: `server.py` (`_setup_notes`, `_append_note`, the `answer` and `decline` keyword actions)
- Modify: `static/chat.js` (`notesCard`, and the line-443 append)
- Test: `tests/test_setup.py`

**Interfaces:**
- Produces: `setup.append_text(current, addition, *, today, kind="notes", source="")`. `source` is `"his"` or `"assistant"`; the dated heading becomes `### {today} · his answer` or `### {today} · suggested by the assistant`; `""` keeps the old `### {today}` (voice file only).
- Produces: `setup.LOG_SOURCES = {"his answer": "his", "suggested by the assistant": "assistant"}`; a log heading without a suffix is a legacy entry (source unknown).
- Produces: `POST /api/setup/notes {append, source}`; `source` other than `"his"` is stored as `"assistant"`.

- [ ] **Step 1: Write the failing tests** in `tests/test_setup.py`:
  - `test_a_note_records_who_wrote_it`: `append_text("", "Did X.", today="2026-10-24", source="assistant")` contains `"### 2026-10-24 · suggested by the assistant"`; with `source="his"` contains `"### 2026-10-24 · his answer"`.
  - `test_an_answer_is_his_and_a_kept_note_is_the_assistants`: through the test server, `POST /api/setup/notes {"append": "A"}` writes the assistant heading; the keyword `answer` action (existing `KeywordApiTests` fake) writes the his-answer heading.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_setup -v`. Expected: the two new tests FAIL.
- [ ] **Step 3: Implement.** `notesCard` sends `source: "assistant"`; `_append_note(text, source)` is called with `"his"` from `answer` and `"his"` from `decline` (a no he pressed). The setup notes route maps anything but `"his"` to `"assistant"`, so an old browser tab cannot claim authorship.
- [ ] **Step 4: Run** the suite. Expected: all pass.
- [ ] **Step 5: Stop** (no commit).

### Task 2: The library: every line, by slot, searchable

**Files:**
- Create: `resume_maker/library.py`
- Modify: `.gitignore` (add `library.json`)
- Test: `tests/test_library.py`

**Interfaces:**
- Produces: `Slot = tuple[str, str]`: `(section_kind, key)`. Keys: `("summary", "")`; `("bullets", company)` where company is `entry["left"]` before the first comma, casefolded, whitespace collapsed, trailing ` llc|ltd|limited|inc` dropped; `("skills", label.casefold())`; `("additional", "")` (every Additional Information item, any label).
- Produces: `@dataclass(frozen=True) class Line: text: str; slot: Slot; cvs: tuple[str, ...]; base: bool; reworded_from: str | None`. `count = len(cvs)`.
- Produces: `collect(base_cv: dict, cvs: dict[str, dict], reworded: dict[str, str]) -> list[Line]`: one `Line` per distinct (slot, whitespace-normalised text). `cvs` maps application slug to its parsed CV; `reworded` maps a line's text to the text it was reworded from (Task 6 fills it).
- Produces: `class Index: __init__(lines: list[Line]); rank(slot: Slot, terms: list[str]) -> list[tuple[Line, float]]` best first; BM25 via `ORDER BY bm25(lines)` on an in-memory `CREATE VIRTUAL TABLE lines USING fts5(text, tokenize='porter')`; each term quoted as an FTS5 phrase (`'"' + term.replace('"', ' ') + '"'`), terms joined with `OR`; lines that match nothing get score 0 and follow, ordered by `count` descending.
- Produces: `Decisions` (load/save `library.json`, atomic write as `keywords.save` does): `struck: set[str]`, `excluded: set[str]` (slugs), `kept: set[str]`, `confirmed: dict[str, bool]` (note passage hash → true/false), `checks: dict[str, dict]` (key `sha256(line + "\n" + notes_hash)` → `{"verdict", "confidence"}`), `labels: dict[str, str]`. Unreadable file → empty `Decisions`.
- Produces: `usable(lines, decisions) -> list[Line]`: drops struck lines, lines whose `reworded_from` is struck, and lines that only appear on excluded CVs.

- [ ] **Step 1: Write the failing tests** in `tests/test_library.py`:
  - `test_a_line_on_three_cvs_is_one_line_counted_three_times`
  - `test_entries_with_the_same_company_share_a_slot`: `"xPathEdge LLC, IT Services and Consulting"` and `"XPATHEDGE LLC"` give the same slot.
  - `test_the_posting_ranks_a_slot`: with terms `["agentic workflows", "mcp"]`, the Additional Information line naming them ranks above a sponsorship-only line.
  - `test_with_no_hit_the_most_used_line_comes_first`
  - `test_punctuation_in_terms_and_lines_is_safe`: terms `['C#', 'Node.js', 'AND', '"quoted"', 'low-code', 'a:b', 'x*']` and a line containing all of them rank without raising.
  - `test_an_unreadable_cv_is_skipped`: `collect` is fed by a loader `load_cvs(workspace) -> dict[str, dict]` that skips a folder whose `cv.json` is not JSON.
  - `test_a_struck_line_and_its_rewordings_are_gone`
  - `test_an_excluded_cv_contributes_nothing_it_alone_had`
- [ ] **Step 2: Run** `python3 -m unittest tests.test_library -v`. Expected: FAIL, `library` not found.
- [ ] **Step 3: Implement** `library.py` with the interfaces above, plus `load_cvs(workspace: applications.Workspace) -> dict[str, dict]` over live and archived applications.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Stop.**

### Task 3: Jev over HTTP

**Files:**
- Create: `resume_maker/jev.py`
- Test: `tests/test_jev.py`

**Interfaces:**
- Produces: `class JevError(Exception)`.
- Produces: `ask(state: Any, questions: dict[str, dict], *, opener=urllib.request.urlopen) -> dict[str, dict]`: the `answers` map. Raises `JevError("set TYPESAFE_API_KEY")` when the variable is unset; `JevError(<server message>)` on HTTP errors; timeout 60 s.
- Produces: `support(pairs: list[tuple[str, str]], *, opener=...) -> list[tuple[str, float]]`: one `(verdict, confidence)` per `(line, passage)`, verdict in `{"supports", "contradicts", "says_nothing"}`. One request per 20 pairs; state `{"checks": [{"line", "passage"}, ...]}`; question `c{i}` of type `choice`, instructions "How does the notes passage `checks[i].passage` relate to the CV line `checks[i].line`? Judge only what the passage itself states.", criteria `{"supports": "The passage states what the line claims, including any tools it names.", "contradicts": "The passage states something the line's claim conflicts with.", "says_nothing": "The passage does not state what the line claims."}`.
- Produces: `label(lines: list[str], *, opener=...) -> list[str]`: kind per Additional Information line in `{"authorisation", "interests", "certification", "self_description"}`, same batching.

- [ ] **Step 1: Write the failing tests** in `tests/test_jev.py` with a fake opener that records the request and returns a canned body:
  - `test_the_key_goes_in_the_header_and_nowhere_else`: header `Authorization: Bearer k`; `"k"` not in the request body.
  - `test_twenty_five_pairs_are_two_requests`
  - `test_answers_come_back_in_the_order_asked`
  - `test_no_key_is_a_plain_error`
  - `test_an_http_error_is_a_jev_error`
- [ ] **Step 2: Run** `python3 -m unittest tests.test_jev -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `jev.py`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Stop.**

### Task 4: The support check

**Files:**
- Modify: `resume_maker/library.py`
- Test: `tests/test_library.py`

**Interfaces:**
- Consumes: Task 1's log headings, Task 2's `Line`, `Decisions`, Task 3's `support`.
- Produces: `@dataclass(frozen=True) class Passage: text: str; trust: str` with trust in `{"notes", "his", "assistant", "legacy"}`. `passages(notes: str) -> list[Passage]`: paragraphs (blank-line separated) of `notes.md`; above `## From the chat` they are `"notes"`; below it each dated entry takes its heading's source, `"legacy"` when the heading has no suffix.
- Produces: `trusted(p: Passage, decisions) -> bool`: `"notes"`/`"his"` always; `"assistant"`/`"legacy"` only when `decisions.confirmed.get(sha256(p.text)) is True`.
- Produces: `Verdict = Literal["pass", "held", "unconfirmed", "unchecked"]`. `check(lines, notes, decisions, *, support=jev.support) -> dict[str, Verdict]` keyed by line text:
  1. `line.base` or `line.text in decisions.kept` → `"pass"`.
  2. Find the 3 best passages by FTS5 against the line's own words.
  3. **Code pass:** a trusted passage contains every *named tool* of the line (tokens with an uppercase letter after the first character, a digit, `.`, `+` or `#`, compared casefolded) and at least 60% of the line's stems from `match.stem` → `"pass"`.
  4. If the best match is an untrusted passage that would have passed code → `"unconfirmed"` (no Jev call: Jev only checks agreement).
  5. Otherwise ask `support` on (line, best trusted passage), reusing `decisions.checks`; `supports` with confidence ≥ `SUPPORT_THRESHOLD` → `"pass"`, else `"held"`. Store each result in `decisions.checks`.
  6. `JevError` → `"unchecked"` for the lines still undecided.

- [ ] **Step 1: Write the failing tests:**
  - `test_a_base_line_passes_without_a_check`
  - `test_a_line_the_notes_state_passes_in_code`: fake `support` asserts it is never called.
  - `test_the_zapier_case_is_unconfirmed_not_passed`: notes whose only mention of Zapier is a `### 2026-10-23` legacy log entry; the line "Automated client onboarding using Zapier, n8n, Webflow and Airtable" → `"unconfirmed"`, and `support` is not called.
  - `test_a_confirmed_note_counts`: same notes, `confirmed[hash] = True` → `"pass"`.
  - `test_jev_says_nothing_holds_the_line_back`
  - `test_a_result_is_reused_until_the_notes_change`: second `check` with the same notes makes no call; changed notes make one.
  - `test_no_key_holds_back_without_failing`: `support` raises `JevError` → `"unchecked"`, no exception.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_library -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Stop.**

### Task 5: Filling the page

**Files:**
- Create: `resume_maker/fill.py`
- Test: `tests/test_fill.py`

**Interfaces:**
- Consumes: `library.Index`, `library.Line`, `check` verdicts, `decisions.labels`.
- Produces: `@dataclass class Card: kind: str; slot: Slot; path: str; text: str; other: str = ""; keywords: tuple[str, ...] = ()`. Kinds: `"changed"`, `"close"`, `"held"`, `"unconfirmed"`, `"unanswered"`, `"cast"`. `as_json()` for the browser.
- Produces: `fill(base_cv: dict, index: Index, terms: list[str], verdicts: dict[str, str], labels: dict[str, str]) -> tuple[dict, list[Card]]`.

Algorithm (the part the tests do not fully determine):

```
covered = set()                      # keywords already on the page
1. Cast: for each experience entry, hits = keywords its best usable lines
   answer (score_against). An entry that is off in the base turns on when its
   hits exceed those of the shown entry with the fewest hits; that entry turns
   off. Each change is a "cast" card. Budgets and order stay the base's.
2. Slots, in page order: summary, skills items, each shown entry's bullets
   (as many as the base entry has, capped by its budget.bullets), additional.
   For each position:
     candidates = index.rank(slot, [t for t in terms if t not in covered])
                  minus lines already used, minus verdict not "pass"
     additional: minus labels == "self_description"; "certification" only
                 when a remaining term matches it
     pick = top candidate; if its score is 0, the most-used candidate
     place it; covered |= keywords it answers (score_against([text], terms))
     card "changed" when pick is not the slot's most-used line and its
       score > 0; card "close" when the runner-up's score equals pick's and
       both > 0
     the best held / unconfirmed / unchecked line that outranked the pick
       gets its card ("held"; "unconfirmed" for unconfirmed)
3. One "unanswered" card listing terms still not in covered.
```

Skills items and summary keep the base when no past version scores higher (they are library slots like any other; a word no past version has only arrives through a card from Task 7).

- [ ] **Step 1: Write the failing tests** in `tests/test_fill.py` (fixtures: a base CV and three past CVs built in the test):
  - `test_a_posting_line_from_one_cv_does_not_carry_into_the_next`: the Zapier bullet on one past CV; terms `["agentic workflows", "mcp"]` → the xPathEdge slot gets its most-used bullets.
  - `test_the_interests_line_comes_back_for_agentic_work`: Additional Information gets the base interests line; no Zapier anywhere.
  - `test_a_keyword_on_the_page_stops_counting`: two slots that could both carry "roadmap"; the second takes the line with "go-to-market".
  - `test_no_line_is_used_twice`
  - `test_no_keywords_gives_the_usual_page`: `terms=[]` → most-used everywhere, no "changed"/"close"/"unanswered" cards.
  - `test_a_self_description_never_lands_in_additional`
  - `test_a_held_line_gives_way_and_says_so`
  - `test_fixed_fields_are_the_base`: titles, dates, education, budgets equal the base.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_fill -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `fill.py`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Stop.**

### Task 6: The server builds the page

**Files:**
- Modify: `resume_maker/applications.py` (`FILES` gains `"fill": "fill.json"`, not in `COPIED`)
- Modify: `server.py` (`_apps_post` create, `_identify`, new `/api/library`)
- Test: `tests/test_setup.py` (`ApplicationApiTests`, new `LibraryApiTests`)

**Interfaces:**
- Consumes: Tasks 2–5.
- Produces: a plain New (`create` without `source`) starts from the base CV: `_apps_post` passes `source=None` unless `payload["source"]`. Duplicate is unchanged.
- Produces: `_identify` no longer calls `reseed`. After `_read_keywords`, it calls `self._build_page(slug)`: load CVs, `collect` (with `reworded` from Task 6's backfill), `usable`, `check`, `fill`, then measure with `Browser.measure` and, for each over-budget bullet, swap in the next-ranked shorter candidate that passed; write `cv.json` and `fill.json` (`{"cards": [...]}`); save `Decisions`. A failure here is logged like the keyword failure and the base copy stays.
- Produces: backfill for `reworded`: a line on an existing CV equal to an accepted `proposal` text in that application's `chat.json` maps to the text the proposal replaced if the chat records it, else to `""` (reworded, original unknown).
- Produces: `GET /api/library?slug=` → `{"cards": [...], "checked": bool}`; `POST /api/library {action, ...}` with actions `keep {text}`, `strike {text}`, `confirm {passage, value}`, `exclude {slug}`, `use {slug, path, text}` (writes that line into the CV at `path` and drops its card), `reworded {text, from}` (records provenance of an accepted Claude rewording), `check` (runs the whole-library check, Task 8), `review` (Task 8 list).
- `reseed` stays in `applications.py` for Duplicate's tests but has no caller from identify.

- [ ] **Step 1: Write the failing tests:**
  - `test_a_new_application_starts_from_the_base`: after creating A and editing its CV, a plain New gets the base CV bytes.
  - `test_naming_builds_the_page_from_the_library`: with `fake_complete` returning a keyword list and the jev opener faked, identify writes a `cv.json` whose Additional Information is the base interests line and a `fill.json` with an `"unanswered"` card.
  - `test_naming_still_works_when_the_library_fails`: `fill.fill` patched to raise → identify returns 200 and the CV is the base copy.
  - `test_use_writes_the_line_and_drops_the_card`
  - `test_strike_removes_a_line_from_later_pages`
- [ ] **Step 2: Run** `python3 -m unittest tests.test_setup -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the suite. Expected: all pass (update `reseed`-on-identify tests to the new behaviour, ledgering each as a ruling).
- [ ] **Step 5: Stop.**

### Task 7: The cards beside the page, and Ask Claude for one slot

**Files:**
- Modify: `static/index.html` (a "Built from your history" panel), `static/chat.js` (Ask Claude), `static/chat.css`
- Modify: `resume_maker/assistant.py` (`slot_prompt`, `slot_request`), `server.py` (`_chat` accepts `slot`)
- Test: `tests/test_proposals.py` (source checks, as existing ring tests do), `tests/test_assistant.py`

**Interfaces:**
- Consumes: `GET/POST /api/library` from Task 6.
- Produces: panel header `"{n} to check · {m} settled"`; one card per `Card` with the Global Constraints copy. Keep → `keep`; Use usual / Swap → `use` with `other`; Strike → `strike`; Use anyway → `keep` then `use`. A settled line on the page shows its other versions on click (from `index.rank` via `GET /api/library?slug=&slot=`), each with **Use** and **Strike**, so any single line can be struck from any CV.
- Produces: `assistant.slot_request(slot_name: str, terms: list[str], candidates: list[str], passage: str) -> str` and `slot_prompt() -> str`. The prompt carries only those four things, never the whole notes or CV, and asks for one ```proposal``` block for the slot's path. An accepted proposal from this card posts `reworded {text, from}`.

- [ ] **Step 1: Write the failing tests:**
  - `test_the_slot_request_carries_only_that_slot`: output contains the terms, candidates and passage; it does not contain a fixture notes sentence outside the passage.
  - `test_the_panel_counts_cards_and_settled_lines` (source check on `index.html`).
  - `test_an_accepted_slot_proposal_records_its_original` (source check on `chat.js`: the accept path posts `action: "reworded"`).
- [ ] **Step 2: Run** `python3 -m unittest tests.test_assistant tests.test_proposals -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the suite. Expected: all pass.
- [ ] **Step 5: Stop.**

### Task 8: The one-time library review

**Files:**
- Create: `static/review.html`, `static/review.js`
- Modify: `server.py` (route `/review`, `check` and `review` actions), `static/_nav.html` (link)
- Test: `tests/test_setup.py` (`LibraryApiTests`)

**Interfaces:**
- Consumes: `library.check` over every usable line, `Decisions`.
- Produces: `POST /api/library {action: "check"}` runs `check` across the whole library (jev batched by Task 3), labels Additional Information lines with `jev.label` where unlabelled, saves `Decisions`, returns `{"held": n, "unconfirmed": m, "unchecked": k}`.
- Produces: `POST /api/library {action: "review"}` → `{"lines": [{text, slot, cvs, verdict}], "notes": [{passage, trust}]}`: lines whose verdict is not `"pass"`, and untrusted log passages not yet decided.
- Produces: `review.html`: a **Check my library** button, then one row per item with **Keep** · **Strike** (lines) or **Keep** · **Strike** (notes, posting `confirm` true/false); below the items, every application with an **Exclude** toggle (posting `exclude`); if the check returned `unchecked > 0`, the page says "The check could not reach Jev; these lines stay held back until it runs."

- [ ] **Step 1: Write the failing tests:**
  - `test_the_check_runs_once_and_is_remembered`: second `check` makes no jev request.
  - `test_review_lists_only_what_needs_him`
  - `test_a_kept_note_is_trusted_from_then_on`
- [ ] **Step 2: Run** `python3 -m unittest tests.test_setup -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the suite. Expected: all pass.
- [ ] **Step 5: Stop.**

### Task 9: The applications list fits the screen

**Files:**
- Modify: `static/home.html`, `static/apps.js`, `static/base.css`
- Test: `tests/test_proposals.py` (source checks)

**Interfaces:**
- Produces: the home page is `height: 100vh` with no page scroll; `#app-list` shows as many rows as fit (measured from one row's height on load and on resize), and a pager below it: **Previous** · `Page {p} of {n}` · **Next**. Archived rows stay after live rows, on the later pages. The current page is kept in `sessionStorage` (wrapped in try/catch).

- [ ] **Step 1: Write the failing tests:** `test_the_home_page_does_not_scroll` (CSS has `100vh` and `overflow: hidden` on the page container), `test_the_list_is_paged` (`apps.js` contains the pager copy).
- [ ] **Step 2: Run** `python3 -m unittest tests.test_proposals -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the suite. Expected: all pass.
- [ ] **Step 5: Stop.**

# Posting Keywords Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One keyword list per posting, read once and frozen, counted literally as the only ruler, with every missing keyword answered from the notes or asked about.

**Architecture:** A small per-application file, `keywords.json`, holds the list, the posting it was read from, and the map (keyword → from / say / state). The server reads and rewrites it through `/api/keywords`; `/api/match` and the chat prompt count against it. The browser stops keeping requirement lists in localStorage and renders what the server holds.

**Tech Stack:** Python 3 stdlib server, vanilla JS, `unittest`, node for browser helpers.

**Spec:** `docs/superpowers/specs/2026-10-23-posting-keywords-design.md`

## Global Constraints

- No new pip or npm dependencies.
- **Do not commit.** The user commits only when he asks. Every "Commit" step in this plan is replaced by running the full suite: `python3 -m unittest discover -s tests` → `OK`.
- Keywords are stored **verbatim from the posting**, lowercased, at most 60 characters each, 10–15 per posting (`assistant.MAX_KEYWORDS = 15`).
- The count is plain code. No model call decides whether a keyword is present.
- Only what prints is counted: `assistant.field_paths(..., printed_only=True)`.
- Card copy, exactly: change → **Use this** · **Skip**; notes or voice fact → **Keep this** · **Skip**; keyword question → **I have…** · **I haven't**.
- The letter's count stays hidden until asked (`coverageApplies` unchanged).
- Comments and docstrings follow the codebase: plain prose saying why, not what.

## Review Focus

- No agent connected when a posting arrives: no list is read, the ring says how to get one, nothing throws. (Task 5)
- The model returns the company's name or a generic word despite the rules: `parse_keywords` drops any term containing the company name and any single word on `GENERIC_TERMS`. (Task 1)
- Hyphens and slashes in keywords (`low-code tooling`, `a/b testing`, `ci/cd`) count the same way the CV's own text is split. (Task 2)
- An "I have…" answer is saved to the notes but the map update call fails: the note stays written, the keyword stays unanswered, the card shows the error and can be retried. (Task 4)
- The posting in `chat.json` changes outside the editor (another tab): `/api/keywords` `get` reports the list as stale rather than counting against the wrong posting. (Task 4)

---

### Task 1: The list file and the list rules

**Files:**
- Create: `resume_maker/keywords.py`
- Modify: `resume_maker/applications.py:44-53` (`FILES`, `COPIED`)
- Modify: `resume_maker/assistant.py:949-995` (`_KEYWORD_RULES`, `parse_keywords`)
- Test: `tests/test_keywords.py`

**Interfaces:**
- Produces:
  - `FILES["keywords"] = "keywords.json"`; `COPIED` excludes `"chat"` and `"keywords"`.
  - `keywords.posting_id(job_description: str) -> str` — sha256 hex of the whitespace-collapsed posting.
  - `keywords.load(path: Path) -> dict` — `{"posting": str, "keywords": list[str], "map": dict[str, dict]}`; empty defaults when the file is missing or unreadable.
  - `keywords.save(path: Path, *, posting: str, terms: list[str], entries: dict[str, dict]) -> None` — atomic write.
  - `keywords.is_current(stored: dict, job_description: str) -> bool` — stored list exists and was read from this posting.
  - `assistant.GENERIC_TERMS: frozenset[str]`
  - `assistant.parse_keywords(reply: str, company: str = "") -> list[str]`

- [ ] **Step 1: Write the failing tests**

```python
class StoreTests(unittest.TestCase):
    def test_a_new_application_does_not_inherit_the_list(self):
        self.assertIn("keywords", applications.FILES)
        self.assertNotIn("keywords", applications.COPIED)

    def test_the_list_belongs_to_the_posting_it_was_read_from(self):
        path = Path(self.tmp) / "keywords.json"
        keywords.save(path, posting="A  posting.", terms=["sql"], entries={})
        stored = keywords.load(path)
        self.assertTrue(keywords.is_current(stored, "A posting."))       # whitespace only
        self.assertFalse(keywords.is_current(stored, "A fuller posting."))

    def test_a_missing_file_is_an_empty_list(self):
        self.assertEqual(keywords.load(Path(self.tmp) / "nope.json")["keywords"], [])

class RuleTests(unittest.TestCase):
    def test_phrases_are_kept_as_the_posting_wrote_them(self):
        rules = assistant.keywords_prompt()
        self.assertIn("exactly as the posting writes it", rules)
        self.assertNotIn("Two or three words at most", rules)

    def test_the_company_and_generic_words_never_reach_the_list(self):
        reply = '{"keywords": ["Flexport tooling", "communication", "compliance-first mindset", "sql"]}'
        self.assertEqual(assistant.parse_keywords(reply, company="Flexport"),
                         ["compliance-first mindset", "sql"])
```

- [ ] **Step 2: Run** `python3 -m unittest tests.test_keywords -v` → FAIL (`keywords` module missing).

- [ ] **Step 3: Implement.** `keywords.py` uses `json` and `hashlib` only. Rewrite `_KEYWORD_RULES`: ten to fifteen terms; skills, tools, methods, domains, and for a product role its craft terms (product discovery, product lifecycle, stakeholder management, prioritization, trade-offs); each term **exactly as the posting writes it**, lowercased, phrases kept whole; never the company's name, its products, perks, pay, location or legal text; never a word that would fit any posting on its own. `GENERIC_TERMS` holds at least: organization, communication, ownership, collaboration, teamwork, compliance, leadership, passion, impact, excellence. `parse_keywords` drops a term containing any word of `company` longer than two letters, and any term that is a single word in `GENERIC_TERMS`.

- [ ] **Step 4: Run** the task's tests → PASS, then the full suite → OK. Update any existing test that asserted the old rule text.

---

### Task 2: Count a phrase when its words share a line

**Files:**
- Modify: `resume_maker/match.py:377-400` (`score_against`)
- Test: `tests/test_keywords.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `match.score_against(parts: Sequence[str], terms: Sequence[str]) -> Match` — same signature; a multi-word term matches when every stemmed word of it appears in **one** part.

- [ ] **Step 1: Write the failing tests**

```python
class CountTests(unittest.TestCase):
    def test_a_phrase_counts_when_its_words_share_a_line(self):
        m = match.score_against(["Built low code tooling for onboarding."], ["low-code tooling"])
        self.assertEqual(m.matched, ("low-code tooling",))

    def test_words_scattered_across_lines_do_not_count(self):
        m = match.score_against(["Used low code platforms.", "Wrote internal tooling."],
                                ["low-code tooling"])
        self.assertEqual(m.missing, ("low-code tooling",))

    def test_slashes_and_hyphens_split_as_the_cv_does(self):
        m = match.score_against(["Ran A/B testing and CI/CD."], ["a/b testing", "ci/cd"])
        self.assertEqual(m.score, 100)
```

- [ ] **Step 2: Run** → the first test fails (exact-phrase matching).

- [ ] **Step 3: Implement.** Stem each part with the existing `_words` and `stem`; a single-word term keeps today's whole-document word check; a multi-word term matches if one part's stem set contains all of its stems. Keep `score` and `Match` untouched.

- [ ] **Step 4: Run** → PASS, full suite OK.

---

### Task 3: The map

**Files:**
- Modify: `resume_maker/assistant.py` (new section after `parse_keywords`)
- Test: `tests/test_keywords.py`

**Interfaces:**
- Produces:
  - `assistant.MAP_STATES = ("answered", "unanswered", "declined")`
  - `assistant.map_prompt() -> str`
  - `assistant.map_request(terms: list[str], notes_md: str) -> str`
  - `assistant.parse_map(reply: str, terms: list[str]) -> dict[str, dict]` — every term in `terms` gets an entry `{"state", "from", "say"}`; anything missing or malformed becomes `{"state": "unanswered", "from": "", "say": ""}`; `say` capped at 220 characters; an "answered" entry with an empty `from` is downgraded to unanswered.
  - `assistant.map_note(stored: dict) -> str` — prompt text listing each keyword with its state and say line; empty string when there is no map.

- [ ] **Step 1: Write the failing tests**

```python
class MapTests(unittest.TestCase):
    def test_every_line_traces_to_the_notes(self):
        rules = assistant.map_prompt()
        self.assertIn("quote", rules)
        self.assertIn("never invent", rules.lower())
        self.assertIn("declined", rules)            # an explicit no in the notes

    def test_an_answer_without_a_source_is_not_an_answer(self):
        reply = '{"sql": {"state": "answered", "from": "", "say": "Wrote SQL."}}'
        self.assertEqual(assistant.parse_map(reply, ["sql", "go"]),
                         {"sql": {"state": "unanswered", "from": "", "say": ""},
                          "go": {"state": "unanswered", "from": "", "say": ""}})

    def test_the_prompt_names_what_is_missing(self):
        note = assistant.map_note({"keywords": ["sql"], "map": {
            "sql": {"state": "answered", "from": "MKEL rate sheets", "say": "Queried rate data in SQL."}}})
        self.assertIn("sql", note)
        self.assertIn("Queried rate data in SQL.", note)
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.** `map_prompt` tells the model: for each keyword, find where the notes show the candidate did that thing even if they never used the word; `from` quotes or closely paraphrases the notes so he recognises it; `say` is one line, short enough for a CV bullet, containing the keyword in the posting's exact words; a keyword the notes explicitly say no to is `declined`; anything else unsupported is `unanswered`; never invent. Reply is JSON keyed by keyword. Parse with the existing `_FENCE` pattern, as `parse_keywords` does.

- [ ] **Step 4: Run** → PASS, full suite OK.

---

### Task 4: The server holds the list

**Files:**
- Modify: `server.py` — `_keywords` (656-692), `_match`, `_identify` (893-935), `_chat` coverage block (~728-740)
- Test: `tests/test_setup.py` (`ApplicationApiTests`, patching `llm.complete`)

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces — `POST /api/keywords` with `action`:
  - `"get"` → `{"keywords", "map", "current": bool}` for the open application.
  - `"read"` (`job_description`, `provider`, `model`) → reads list and map, saves, returns as `get`. Replaces any earlier list.
  - `"answer"` (`keyword`, `sentence`, `provider`, `model`) → appends `sentence` to the notes, re-maps **that keyword only**, returns as `get`.
  - `"decline"` (`keyword`, `line`) → appends `line` to the notes, marks the keyword `declined`, returns as `get`.
  - `/api/match` ignores any `keywords` in the payload and counts against the stored list; with no current list it returns `{"score": null, "matched": [], "missing": [], "needs_list": true}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_naming_reads_and_freezes_the_list(self):          # identify, with llm.complete patched
    ...  # reply 1 names the job, reply 2 is the list, reply 3 is the map
    self.assertEqual(self.post("/api/keywords", {"action": "get"})["keywords"], ["sql", "go"])

def test_a_failed_list_read_does_not_fail_naming(self):
    ...  # the list call raises; identify still returns 200 and the app is renamed

def test_the_count_uses_the_stored_list_only(self):
    out = self.post("/api/match", {"document": "cv", "doc": CV,
                                   "job_description": POSTING, "keywords": ["nonsense"]})
    self.assertNotIn("nonsense", out["missing"])

def test_a_posting_changed_elsewhere_is_reported_stale(self):
    ...  # save a list, then rewrite chat.json's job_description
    self.assertFalse(self.post("/api/keywords", {"action": "get"})["current"])

def test_an_answer_survives_a_failed_remap(self):
    ...  # map call raises after the notes append
    self.assertIn("I ran the review", (self.root / "notes.md").read_text())
    # keyword still "unanswered"; the response is an error the card can show

def test_only_the_answered_keyword_is_rewritten(self):
    ...  # entries for other keywords are byte-identical before and after
```

- [ ] **Step 2: Run** `python3 -m unittest tests.test_setup -v` → the new tests fail.

- [ ] **Step 3: Implement.** Notes appends go through the existing `setup.append_text` path used by `_setup_notes`. `_identify` reads the list and map after renaming, inside a `try` that logs and continues. `_chat` builds `coverage_note` from the stored list via `score_against` and adds `assistant.map_note(stored)` to `fit`; `match.score` is no longer called from the ring or the chat.

- [ ] **Step 4: Run** → PASS, full suite OK.

---

### Task 5: The ring shows one ruler

**Files:**
- Modify: `static/index.html` — `wantStore`, `postingKey`, `loadWanted`, `wantedList`, `readRequirements`, `updateScore`, `paintRing`, `scoreActions` (~975-1180)
- Modify: `static/chat.js` — `chatBody` (`wanted`), the posting-changed handler (~1378), `tailorAfterReload`
- Test: `tests/test_keywords.py` (source checks), `tests/test_proposals.py` (node)

**Interfaces:**
- Consumes: `/api/keywords` `get` and `read`; `/api/match` with `needs_list`.
- Produces: `let keywordState = { keywords: [], map: {}, current: false }` in `index.html`; `async function loadKeywords()`; `async function readKeywords({ fresh })`.

- [ ] **Step 1: Write the failing tests** — source checks that `postingKey`, `wantStore` and `resume-maker.wanted` no longer exist; that the ring panel's button reads "Read a fresh list"; that a missing keyword row renders its map `say` line and a declined keyword carries the class `declined`; node test that `readKeywords` is called when a posting is pasted into a named application and on load when `current` is false.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.** On load, `loadKeywords()`; if the posting exists and `current` is false, `readKeywords()` once. The ring shows `x of N` and, under it, each missing keyword with its say line; declined ones muted and listed after. With no agent connected and no list, the ring shows "Connect an agent to read this posting's keywords". `chatBody` sends `wanted: keywordState.keywords`.

- [ ] **Step 4: Run** → PASS, full suite OK.

---

### Task 6: The wording stage works down the list

**Files:**
- Modify: `resume_maker/assistant.py` — `_TAILOR_WORDING`, `_CARD`
- Modify: `static/chat.js` — `BLOCK`, new `askCard`, `runStep`, `renderAssistant`
- Test: `tests/test_assistant.py`, `tests/test_proposals.py`

**Interfaces:**
- Consumes: `map_note` in the prompt (Task 4); `/api/keywords` `answer` and `decline`.
- Produces: a new card kind ` ```ask ` with JSON `{"keyword": str, "question": str}`; `BLOCK` and `_CARD` include `ask`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_wording_stage_follows_the_list(self):
    w = assistant.tailor_rules("wording")
    self.assertIn("missing keywords in the order listed", w)
    self.assertIn("```ask", assistant.system_prompt("cv", self.cv))
    self.assertIn("experience underneath", w)        # not "do you have <word>"
```

Node: a round whose wording reply is an `ask` card waits for the answer instead of ending the step; `I haven't` posts `decline` with `negative(question)` as the line and resolves the card as dismissed; `I have…` opens a text box and posts `answer`.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.** `_TAILOR_WORDING`: take the missing keywords in the order listed; answered in the map → one proposal for one bullet, naming the notes section; unanswered → one `ask` card about the experience underneath the word; declined → skip; after the keywords, removals of what a previous posting left behind. `runStep` no longer returns on `gaps`; an `ask` card waits like a proposal.

- [ ] **Step 4: Run** → PASS, full suite OK.

---

### Task 7: Copy and chat appearance

**Files:**
- Modify: `static/chat.js` — `proposalCard`, `showCard`, notes card (~283), voice card, `gapsCard` (~382)
- Modify: `static/chat.css:19-36`
- Test: `tests/test_proposals.py` (source checks)

**Interfaces:** none new.

- [ ] **Step 1: Write the failing tests** — source contains "Use this", "Keep this", "I have…", "I haven't", "Skip"; does not contain "Not mine", "Add to Notes", "Accept", "Dismiss" as button text; `chat.css` sets the user bubble to an off-white background with dark text, the assistant message to a light shade of the app's black, and `.chat-card.notes` to a pale green.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.** Values: assistant `#1e1e1e`, user `#f4f1ea` on `#111`, notes card `#eef5ee` on `#1b2a1e`. The notes and voice cards gain the class `notes`.

- [ ] **Step 4: Run** → PASS, full suite OK.

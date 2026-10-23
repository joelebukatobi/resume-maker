"""One keyword list per posting, read once and counted literally.

The ring used to switch rulers without saying so, and the requirement list was
asked for again on every press, so it came back fifteen long, then twelve, then
ten. A score that moves when the document has not is not a measurement. The
list is now read once, kept beside the posting it came from, and counted by
plain code, because the applicant tracking system it imitates is literal.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resume_maker import applications, assistant, keywords, match  # noqa: E402


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_a_new_application_does_not_inherit_the_list(self):
        """A list is read from one posting. A copied one would count the next
        application against the last job."""
        self.assertIn("keywords", applications.FILES)
        self.assertNotIn("keywords", applications.COPIED)

    def test_the_list_belongs_to_the_posting_it_was_read_from(self):
        path = Path(self.tmp.name) / "keywords.json"
        keywords.save(path, posting="A  posting.", terms=["sql"], entries={})
        stored = keywords.load(path)
        self.assertEqual(stored["keywords"], ["sql"])
        self.assertTrue(keywords.is_current(stored, "A posting."))       # whitespace only
        self.assertFalse(keywords.is_current(stored, "A fuller posting."))

    def test_skips_are_kept_with_the_list(self):
        path = Path(self.tmp.name) / "keywords.json"
        keywords.save(path, posting="A posting.", terms=["sql"], entries={},
                      skipped=["sections.3.entries.0.bullets.1"])
        self.assertEqual(keywords.load(path)["skipped"], ["sections.3.entries.0.bullets.1"])
        keywords.save(path, posting="A posting.", terms=["sql"], entries={})
        self.assertEqual(keywords.load(path)["skipped"], [])

    def test_a_missing_file_is_an_empty_list(self):
        stored = keywords.load(Path(self.tmp.name) / "nope.json")
        self.assertEqual(stored["keywords"], [])
        self.assertFalse(keywords.is_current(stored, "A posting."))


class RuleTests(unittest.TestCase):
    def test_phrases_are_kept_as_the_posting_wrote_them(self):
        """"compliance-first mindset" cut down to "compliance" throws away what
        they asked for, and a phrase that does not suit a bullet may still be
        answered in the letter."""
        rules = assistant.keywords_prompt()
        self.assertIn("exactly as the posting writes it", rules)
        self.assertNotIn("Two or three words at most", rules)
        self.assertIn("stakeholder management", rules)     # the craft terms of a role

    def test_the_company_and_generic_words_never_reach_the_list(self):
        reply = ('{"keywords": ["Flexport tooling", "communication",'
                 ' "compliance-first mindset", "sql"]}')
        self.assertEqual(assistant.parse_keywords(reply, company="Flexport"),
                         ["compliance-first mindset", "sql"])

    def test_a_generic_word_inside_a_phrase_is_kept(self):
        """"organization" alone says nothing; "organization design" is a skill."""
        reply = '{"keywords": ["organization design", "organization"]}'
        self.assertEqual(assistant.parse_keywords(reply), ["organization design"])


class CountTests(unittest.TestCase):
    """Literal, because the ATS is. But a phrase is its words, not their order."""

    def test_a_phrase_counts_when_its_words_share_a_line(self):
        """The CV said "low code" and the list asked for "low-code tooling",
        and the exact-phrase count could not see it."""
        m = match.score_against(["Built onboarding tooling on low code platforms."],
                                ["low-code tooling"])
        self.assertEqual(m.matched, ("low-code tooling",))

    def test_words_scattered_across_lines_do_not_count(self):
        m = match.score_against(["Used low code platforms.", "Wrote internal tooling."],
                                ["low-code tooling"])
        self.assertEqual(m.missing, ("low-code tooling",))

    def test_slashes_and_hyphens_split_as_the_cv_does(self):
        m = match.score_against(["Ran A/B testing and CI/CD."], ["a/b testing", "ci/cd"])
        self.assertEqual(m.score, 100)

    def test_a_slash_pair_is_its_two_words(self):
        """The split leaves a "/" token between longer pairs. Counted as a word,
        it made "React and TypeScript" miss "react/typescript", and any other
        slash in the same line complete it."""
        self.assertEqual(match.score_against(["Built React and TypeScript apps."],
                                             ["react/typescript"]).score, 100)
        self.assertEqual(match.score_against(["Built Node/Express services."],
                                             ["react/typescript"]).score, 0)

    def test_a_term_that_counts_as_nothing_does_not_shift_the_rest(self):
        """A term that splits to nothing used to shift every keyword after it
        onto the wrong one, so "sql" vanished from the count."""
        m = match.score_against(["Wrote SQL."], ["24/7", "sql"])
        self.assertIn("sql", m.matched)


class MapTests(unittest.TestCase):
    """Where in the notes each keyword is answered, and how to say it."""

    def test_every_line_traces_to_the_notes(self):
        rules = assistant.map_prompt()
        self.assertIn("quote", rules)
        self.assertIn("never invent", rules.lower())
        self.assertIn("declined", rules)            # an explicit no in the notes
        self.assertIn("even if they never used the word", rules)

    def test_an_answer_without_a_source_is_not_an_answer(self):
        """A "say" line with no "from" is a sentence nobody can defend."""
        reply = '{"sql": {"state": "answered", "from": "", "say": "Wrote SQL."}}'
        self.assertEqual(assistant.parse_map(reply, ["sql", "go"]),
                         {"sql": {"state": "unanswered", "from": "", "say": ""},
                          "go": {"state": "unanswered", "from": "", "say": ""}})

    def test_an_unknown_state_is_unanswered_and_a_no_is_kept(self):
        reply = ('{"sql": {"state": "maybe", "from": "x", "say": "y"},'
                 ' "customs regulations": {"state": "declined", "from": "", "say": ""}}')
        got = assistant.parse_map(reply, ["sql", "customs regulations"])
        self.assertEqual(got["sql"]["state"], "unanswered")
        self.assertEqual(got["customs regulations"]["state"], "declined")

    def test_the_request_carries_the_list_and_the_notes(self):
        asked = assistant.map_request(["sql", "account management"], "# Notes\nRan rate sheets.")
        self.assertIn("account management", asked)
        self.assertIn("Ran rate sheets.", asked)

    def test_the_prompt_names_each_keyword_with_its_answer(self):
        note = assistant.map_note({"keywords": ["sql", "go"], "map": {
            "sql": {"state": "answered", "from": "MKEL rate sheets",
                    "say": "Queried rate data in SQL."},
            "go": {"state": "declined", "from": "", "say": ""}}})
        self.assertIn("Queried rate data in SQL.", note)
        self.assertIn("go", note)
        self.assertIn("declined", note)
        self.assertEqual(assistant.map_note({"keywords": [], "map": {}}), "")

    def test_a_keyword_already_on_the_page_is_not_worked_on(self):
        """What the user hit: SQL was in his skills line, but his notes do not
        mention it, so the map said unanswered and the round read that as
        missing and started there. On the page is one fact, in the notes is
        another, and the round works only on what is missing from the page."""
        stored = {"keywords": ["sql", "go"], "map": {
            "sql": {"state": "unanswered", "from": "", "say": ""},
            "go": {"state": "unanswered", "from": "", "say": ""}}}
        note = assistant.map_note(stored, on_page=["sql"])
        self.assertIn("- sql already on the CV", note)
        self.assertNotIn("sql unanswered", note)
        self.assertIn("- go unanswered", note)
        wording = assistant.tailor_rules("wording")
        self.assertIn("already on the CV", wording)


INDEX = ROOT / "static" / "index.html"
CHAT = ROOT / "static" / "chat.js"


class RingTests(unittest.TestCase):
    """One ruler, read from the server, never re-rolled behind his back."""

    def setUp(self):
        self.page = INDEX.read_text(encoding="utf-8")

    def test_the_browser_no_longer_keeps_its_own_list(self):
        for gone in ("postingKey", "wantStore", "resume-maker.wanted", "readRequirements"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, self.page)

    def test_a_fresh_read_is_asked_for_by_name(self):
        self.assertIn("Read Afresh", self.page)
        self.assertIn("async function readKeywords(", self.page)
        self.assertIn("async function loadKeywords(", self.page)

    def test_each_missing_keyword_comes_with_its_answer(self):
        panel = self.page[self.page.index("function toggleScoreDetail()"):]
        self.assertIn("keywordState.map", panel)
        self.assertIn(".say", panel)
        self.assertIn('"declined"', panel)

    def test_without_an_agent_the_ring_says_how_to_get_a_list(self):
        self.assertIn("Connect an agent to read this posting's keywords", self.page)

    def test_a_failed_first_read_still_shows_the_ring(self):
        """Otherwise the button to try again is on a ring nobody can see."""
        read = self.page[self.page.index("async function readKeywords("):
                         self.page.index("async function ensureKeywords(")]
        self.assertIn("finally", read)
        self.assertIn("await updateScore()", read[read.index("finally"):])

    def test_the_map_can_be_checked_again_without_a_new_list(self):
        self.assertIn("Read Notes", self.page)
        self.assertIn('action: "remap"', self.page)

    def test_the_round_is_announced_once(self):
        """Every suggestion is its own request, and each used to show "Working
        from the posting", which read as the posting being read again after
        every yes."""
        chat = CHAT.read_text(encoding="utf-8")
        user = chat[chat.index("function renderUser("):chat.index("function renderAssistant(")]
        self.assertIn("previous && previous.pipeline", user)

    def test_the_chat_hears_the_kept_list(self):
        chat = CHAT.read_text(encoding="utf-8")
        self.assertIn("wanted: keywordList()", chat)


class FreshPostingTests(unittest.TestCase):
    """A new posting in a named application means a new list, straight away."""

    def run_handler(self, unnamed):
        import json, shutil, subprocess
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("node is not installed")
        source = CHAT.read_text(encoding="utf-8")
        handler = source[source.index("function setJobDescription("):
                         source.index("// A new application has no name until")]
        script = f"""
          const calls = [];
          let chat = {{ job_description: "Old posting." }};
          let tailorDeclined = ["x"];
          const drawJobTile = () => {{}}, saveHistorySoon = () => {{}}, scoreSoon = () => {{}};
          const nameFromJob = async () => {json.dumps(unnamed)};
          const readKeywords = async (o) => {{ calls.push("read:" + Boolean(o && o.fresh)); }};
          const runTailor = () => {{ calls.push("round"); }};
          {handler}
          setJobDescription("A different posting.");
          setTimeout(() => console.log(JSON.stringify(calls)), 20);
        """
        done = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
        if done.returncode:
            raise AssertionError(done.stderr.strip())
        return json.loads(done.stdout)

    def test_a_named_application_reads_a_fresh_list_before_the_round(self):
        self.assertEqual(self.run_handler(unnamed=False), ["read:true", "round"])

    def test_an_unnamed_one_leaves_it_to_naming(self):
        """Naming reads the list itself, then reloads the page."""
        self.assertEqual(self.run_handler(unnamed=True), [])


class RatingTests(unittest.TestCase):
    """The same CV against the same posting is rated once.

    Pressing Rate Again on an unchanged document asked the model again, and on
    a model that cannot be pinned the number moved: 43, then 35, then 35."""

    def setUp(self):
        self.page = INDEX.read_text(encoding="utf-8")

    def test_an_unchanged_cv_shows_its_reading_instead_of_asking_again(self):
        rate = self.page[self.page.index("async function rateFit("):]
        rate = rate[:rate.index("\n}\n")]
        self.assertIn("lastFit.key === fitKey()", rate)
        self.assertLess(rate.index("lastFit.key === fitKey()"), rate.index('api("/api/fit"'))

    def test_the_fingerprint_is_the_content_not_its_length(self):
        """Swapping a word for one the same length left the length unchanged,
        so the old reading would have been shown for a new document."""
        import json, shutil, subprocess
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("node is not installed")
        start = self.page.index("function fingerprint(")
        end = self.page.index("// A rating belongs to one document")
        script = """
          let documentMode = "cv";
          const chat = { job_description: "A posting." };
          let doc = { bullets: ["Ran discovery."] };
          const header = () => doc;
        """ + self.page[start:end] + """
          const first = fitKey();
          doc = { bullets: ["Ran discomery."] };     // same length, new content
          const second = fitKey();
          doc = { bullets: ["Ran discovery."] };
          console.log(JSON.stringify([first !== second, first === fitKey()]));
        """
        done = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
        if done.returncode:
            raise AssertionError(done.stderr.strip())
        self.assertEqual(json.loads(done.stdout), [True, True])


class TailorButtonTests(unittest.TestCase):
    """A round can be started again on the posting and list already kept.

    It only started when a new posting was pasted, so a reload mid-round lost
    it for good, and pasting the same posting again did nothing."""

    def test_the_button_is_in_the_chat(self):
        page = INDEX.read_text(encoding="utf-8")
        self.assertIn('id="chat-tailor"', page)
        self.assertIn("Tailor To This Posting", page)

    def test_it_starts_a_round_and_shows_only_when_one_can_run(self):
        chat = CHAT.read_text(encoding="utf-8")
        self.assertIn('$("chat-tailor").addEventListener("click", () => runTailor())', chat)
        self.assertIn('$("chat-tailor").hidden = !tailorReady()', chat)


if __name__ == "__main__":
    unittest.main()

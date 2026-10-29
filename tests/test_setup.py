"""Setup pillars: Style, Notes, Documents readiness and their save API."""

from __future__ import annotations

import json
import sys
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from resume_maker import assistant, fill, jev, keywords, llm, setup
from resume_maker.applications import Workspace  # noqa: E402

CV = json.loads((ROOT / "cv.json").read_text())
COVER = json.loads((ROOT / "cover.json").read_text())


class ReadinessTests(unittest.TestCase):
    """Each hub tile reports empty / started / ready."""

    def test_style_and_notes_grow_from_empty_to_ready(self):
        self.assertEqual(setup.text_state(""), "empty")
        self.assertEqual(setup.text_state("   \n "), "empty")
        self.assertEqual(setup.text_state("Ban 'spearheaded'."), "started")
        self.assertEqual(setup.text_state("x" * setup.READY_CHARS), "ready")

    def test_a_filled_cv_is_ready_and_the_base_letter_is_a_skeleton(self):
        """The base letter is deliberately only a greeting and an opening
        line. A plain New starts from it, and when it held a finished letter
        to Meta every new application opened addressed to Meta."""
        state = setup.readiness(voice="", notes="", cv=CV, cover=COVER)
        self.assertEqual(state["cv"], "ready")
        self.assertEqual(state["cover"], "started")
        self.assertEqual(state["voice"], "empty")
        self.assertEqual(state["notes"], "empty")

    def test_a_written_letter_is_ready(self):
        written = dict(COVER, paragraphs=["A paragraph about the work. " * 20])
        self.assertEqual(setup.readiness("", "", CV, written)["cover"], "ready")

    def test_an_untouched_document_is_empty_and_a_bare_one_is_started(self):
        self.assertEqual(setup.readiness("", "", {}, {})["cv"], "empty")
        bare = {"name": "A", "contact": [], "sections": [{"heading": "SKILLS", "type": "labeled", "items": []}]}
        self.assertEqual(setup.readiness("", "", bare, {})["cv"], "started")
        letter = {"name": "A", "contact": [], "paragraphs": ["Short."]}
        self.assertEqual(setup.readiness("", "", {}, letter)["cover"], "started")

    def test_saved_text_is_checked_for_size(self):
        self.assertEqual(setup.check_text("notes", "  Real notes.  "), "Real notes.")
        with self.assertRaises(ValueError):
            setup.check_text("notes", "x" * (setup.MAX_NOTES_SAVE + 1))
        with self.assertRaises(ValueError):
            setup.check_text("voice", "x" * (setup.MAX_VOICE_SAVE + 1))
        with self.assertRaises(ValueError):
            setup.check_text("notes", 42)


class TemplateTests(unittest.TestCase):
    """Role starter text offered on the Style page."""

    def test_four_roles_are_offered_in_a_sensible_order(self):
        roles = setup.templates()
        self.assertEqual([r["id"] for r in roles], ["pm", "swe", "consultant", "other"])
        self.assertEqual([r["label"] for r in roles],
                         ["Product Manager", "Software Engineer", "Consultant", "Other"])

    def test_every_template_carries_the_shared_method_and_a_role_section(self):
        for role in setup.templates():
            with self.subTest(role=role["id"]):
                text = role["text"]
                self.assertGreater(len(text), 400)  # substantial enough to be "ready"
                self.assertIn("Never invent", text)       # ask-before-invent rule
                self.assertIn("## Method", text)          # JD map -> matches/gaps -> ask
                self.assertIn("## Role:", text)           # role addendum
                self.assertNotIn("spearheaded.", text.split("## Never")[0])

    def test_templates_are_saveable_as_style(self):
        for role in setup.templates():
            with self.subTest(role=role["id"]):
                self.assertEqual(setup.check_text("voice", role["text"]), role["text"].strip())


class SetupApiTests(unittest.TestCase):
    """The routes the hub, Style and Notes pages will call."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "cv.json").write_text(json.dumps(CV))
        (root / "cover.json").write_text(json.dumps(COVER))
        cls.voice_path = root / "voice.md"
        cls.notes_path = root / "notes.md"
        workspace = Workspace(root / "applications", {
            "cv": root / "cv.json",
            "cover": root / "cover.json",
            "theme": ROOT / "theme.json",
            "cover_theme": ROOT / "cover-theme.json",
        })
        workspace.ensure(chat_dir=root / ".chat")
        handler = partial(
            server.EditorHandler,
            workspace=workspace,
            out_dir=root / "out",
            voice_path=cls.voice_path,
            notes_path=cls.notes_path,
        )
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(f"{self.base}{path}", timeout=10) as response:
            return json.loads(response.read())

    def post(self, path, payload):
        request = urllib.request.Request(
            f"{self.base}{path}", json.dumps(payload).encode(),
            {"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    def test_setup_reports_text_and_status(self):
        state = self.get("/api/setup")
        self.assertIn("voice", state)
        self.assertIn("notes", state)
        self.assertEqual(state["status"]["cv"], "ready")
        self.assertIn(state["status"]["voice"], ("empty", "started", "ready"))

    def test_saving_voice_and_notes_writes_files_and_updates_status(self):
        self.post("/api/setup/voice", {"text": "Ban 'spearheaded'. Use XYZ bullets."})
        self.post("/api/setup/notes", {"text": "## Amazon\nRan weekly triage for the CXI dataset."})
        self.assertIn("spearheaded", self.voice_path.read_text())
        self.assertIn("triage", self.notes_path.read_text())
        state = self.get("/api/setup")
        self.assertIn("spearheaded", state["voice"])
        self.assertIn("triage", state["notes"])
        self.assertEqual(state["status"]["voice"], "started")

    def test_the_chat_can_add_to_notes_without_replacing_them(self):
        """The one thing the chat writes outside the open document."""
        self.post("/api/setup/notes", {"text": "# How I got here\n\nI fixed PCs at 11."})
        self.post("/api/setup/notes", {"append": "Cedric is a writing tool."})
        text = self.get("/api/setup")["notes"]
        self.assertIn("I fixed PCs at 11.", text)          # the story survives
        self.assertIn("## From the chat", text)            # filed, not scattered
        self.assertIn("Cedric is a writing tool.", text)
        self.post("/api/setup/notes", {"append": "MKEL was a telecom startup."})
        text = self.get("/api/setup")["notes"]
        self.assertEqual(text.count("## From the chat"), 1)  # one heading, not one each
        self.assertIn("MKEL was a telecom startup.", text)

    NOTES_WITH_LOG = "# Me\n\nI fixed PCs.\n\n## From the chat\n\n### 2026-10-01\n\nDid X."

    def test_his_skills_list_is_read_by_label(self):
        notes = "## Skills\n\n- Product: Jira, Confluence\n- AI: Claude, RAG\n\n## Work\n\nStuff."
        self.assertEqual(setup.skills(notes), {"Product": ["Jira", "Confluence"],
                                               "AI": ["Claude", "RAG"]})
        self.assertEqual(setup.skills("# Me\n\nNo list."), {})
        bracketed = "## Skills\n\n- Programming: JavaScript (React, Next.js), Python\n"
        self.assertEqual(setup.skills(bracketed),
                         {"Programming": ["JavaScript (React, Next.js)", "Python"]})

    def test_a_skill_goes_under_its_label_once(self):
        notes = setup.add_skill(self.NOTES_WITH_LOG, "Product", "Odoo")
        notes = setup.add_skill(notes, "Product", "Jira")
        notes = setup.add_skill(notes, "product", "odoo")        # already there
        notes = setup.add_skill(notes, "AI", "Claude")
        self.assertEqual(setup.skills(notes), {"Product": ["Odoo", "Jira"], "AI": ["Claude"]})
        # Above the chat log, so it reads as his own list, not a log entry.
        self.assertLess(notes.index("## Skills"), notes.index("## From the chat"))
        self.assertIn("I fixed PCs.", notes)
        self.assertIn("Did X.", notes)

    def test_a_new_skill_starts_each_word_with_a_capital(self):
        notes = setup.add_skill("", "AI", "large language models")
        notes = setup.add_skill(notes, "AI", "testing and evaluation")
        notes = setup.add_skill(notes, "Programming", "next.js")
        notes = setup.add_skill(notes, "Programming", "iOS")      # his capitals stay
        notes = setup.add_skill(notes, "AI", "LLMs")
        self.assertEqual(setup.skills(notes), {
            "AI": ["Large Language Models", "Testing and Evaluation", "LLMs"],
            "Programming": ["Next.js", "iOS"]})

    def test_the_chat_can_add_a_skill_he_confirmed(self):
        self.post("/api/setup/notes", {"text": "# Me\n\nI fixed PCs."})
        self.post("/api/setup/notes", {"skill": {"label": "Product", "tool": "Moodle"}})
        self.assertEqual(setup.skills(self.get("/api/setup")["notes"]), {"Product": ["Moodle"]})

    def test_a_note_records_who_wrote_it(self):
        """An assistant's note accepted with one click is not his own words, and
        the support check has to be able to tell the two apart."""
        kept = setup.append_text("", "Did X.", today="2026-10-24", source="assistant")
        self.assertIn("### 2026-10-24 · suggested by the assistant", kept)
        his = setup.append_text("", "Did X.", today="2026-10-24", source="his")
        self.assertIn("### 2026-10-24 · his answer", his)

    def test_a_kept_note_is_filed_as_the_assistants(self):
        self.post("/api/setup/notes", {"append": "Cedric is a writing tool.", "source": "his"})
        self.assertIn("· suggested by the assistant", self.get("/api/setup")["notes"])

    def test_the_chat_can_add_to_voice_without_replacing_it(self):
        """Same offer as Notes, but a writing rule lands in the Voice file."""
        self.post("/api/setup/voice", {"text": "## Never\n- No 'spearheaded'."})
        self.post("/api/setup/voice", {"append": "Lead every bullet with the outcome."})
        text = self.get("/api/setup")["voice"]
        self.assertIn("No 'spearheaded'.", text)          # the voice survives
        self.assertIn("## From the chat", text)
        self.assertIn("Lead every bullet with the outcome.", text)

    def test_an_empty_addition_is_refused(self):
        self.post("/api/setup/notes", {"text": "# Notes"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/setup/notes", {"append": "   "})
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.get("/api/setup")["notes"], "# Notes")

    def test_oversized_save_is_refused_and_leaves_the_file_alone(self):
        self.post("/api/setup/notes", {"text": "Keep me."})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/setup/notes", {"text": "x" * (setup.MAX_NOTES_SAVE + 1)})
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.notes_path.read_text().strip(), "Keep me.")

    def test_templates_are_served_for_the_style_page(self):
        roles = self.get("/api/setup/templates")
        self.assertEqual([r["id"] for r in roles], ["pm", "swe", "consultant", "other"])
        self.assertIn("## Method", roles[0]["text"])

    def test_editor_and_hub_pages_are_served(self):
        for path in ("/", "/resume", "/cover-letter"):
            with self.subTest(path=path):
                with urllib.request.urlopen(f"{self.base}{path}", timeout=10) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn("text/html", response.headers["Content-Type"])

    def test_hub_offers_style_notes_and_the_applications(self):
        """The hub is the shared material plus the list of jobs — no documents.

        A CV belongs to an application, so it is reached by opening one.
        """
        with urllib.request.urlopen(f"{self.base}/", timeout=10) as response:
            page = response.read().decode()
        for pillar in ("Voice", "Notes", "Applications"):
            self.assertIn(f">{pillar}</h2>", page)
        for href in ('href="/voice"', 'href="/notes"'):
            self.assertIn(href, page)
        self.assertNotIn('id="tile-docs"', page)
        self.assertIn('id="app-list"', page)
        self.assertIn("/api/setup", page)          # paints readiness
        self.assertIn("/api/llm/providers", page)  # model strip

    def test_voice_page_is_built_on_the_redesign(self):
        """Voice loads the redesign's files and keeps its old endpoints."""
        with urllib.request.urlopen(f"{self.base}/style", timeout=10) as response:
            page = response.read().decode()
        self.assertIn('<app-nav current="voice">', page)
        for asset in ("/static/ui/theme.js", "/static/ui/tokens.css", "/static/ui/reset.css",
                      "/static/ui/atoms.css", "/static/ui/layout.css",
                      "/static/ui/app-nav.js", "/static/ui/voice-page.js"):
            self.assertIn(asset, page)
        self.assertNotIn("pico", page.lower())
        self.assertNotIn("/static/base.css", page)
        with urllib.request.urlopen(f"{self.base}/static/ui/voice-page.js", timeout=10) as response:
            script = response.read().decode()
        self.assertIn('"/api/setup"', script)
        self.assertIn('"/api/setup/voice"', script)          # saves to voice.md
        self.assertIn('"/api/setup/templates"', script)      # role starters
        self.assertNotIn("innerHTML", script)                # nodes, never HTML

    def test_notes_page_is_wired_to_save(self):
        with urllib.request.urlopen(f"{self.base}/notes", timeout=10) as response:
            page = response.read().decode()
        self.assertIn('kind: "notes"', page)
        self.assertIn("/static/setup.js", page)
        self.assertIn('id="editor"', page)
        self.assertIn('id="view-edit"', page)
        self.assertIn('id="view-preview"', page)
        self.assertIn('id="preview"', page)
        self.assertNotIn("data-heading", page)  # no section stubs
        self.assertNotIn("/api/setup/extract", page)  # no file upload UI

    def test_setup_script_renders_markdown_for_preview(self):
        with urllib.request.urlopen(f"{self.base}/static/setup.js", timeout=10) as response:
            script = response.read().decode()
        self.assertIn("function renderMarkdown(", script)
        self.assertIn("function setView(", script)
        self.assertIn("innerHTML", script)  # must mention the rule…
        # …as a ban: build nodes, never assign HTML from file content.
        self.assertRegex(script, r"never\s+innerHTML")

    def test_extract_reads_an_uploaded_file_without_a_model(self):
        import base64
        text = self.post("/api/setup/extract",
                         {"filename": "notes.txt", "file": base64.b64encode(b"Ran weekly triage.").decode()})
        self.assertEqual(text["text"], "Ran weekly triage.")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/setup/extract",
                      {"filename": "notes.exe", "file": base64.b64encode(b"MZ").decode()})
        self.assertEqual(caught.exception.code, 422)

    def test_the_editor_nav_drops_style_and_notes(self):
        """Inside an application, Style and Notes are not part of the job."""
        with urllib.request.urlopen(f"{self.base}/static/base.css", timeout=10) as response:
            css = response.read().decode()
        self.assertIn('body[data-page="resume"] :is(#nav-voice, #nav-notes, #nav-review)', css)
        self.assertIn('body[data-page="cover"] :is(#nav-voice, #nav-notes, #nav-review)', css)

    def test_editor_links_back_to_the_setup_pages(self):
        for path in ("/resume", "/cover-letter"):
            with self.subTest(path=path):
                with urllib.request.urlopen(f"{self.base}{path}", timeout=10) as response:
                    page = response.read().decode()
                for href in ('href="/"', 'href="/voice"', 'href="/notes"'):
                    self.assertIn(href, page)

    def test_every_page_serves_the_same_nav(self):
        """One _nav.html fragment on every route; only data-page changes."""
        slug = self.get("/api/apps")["current"]
        expected = (
            ('href="/"', 'id="nav-hub"'),
            ('href="/voice"', 'id="nav-voice"'),
            ('href="/notes"', 'id="nav-notes"'),
            (f'href="/{slug}/resume"', 'id="mode-cv"'),
            (f'href="/{slug}/cover-letter"', 'id="mode-cover"'),
        )
        # The hub is the entry point and carries no nav of its own. The
        # documents hang off the application they belong to.
        pages = {
            "/notes": "notes",
            f"/{slug}": "resume",
            f"/{slug}/resume": "resume",
            f"/{slug}/cover-letter": "cover",
        }
        for path, page_id in pages.items():
            with self.subTest(path=path):
                with urllib.request.urlopen(f"{self.base}{path}", timeout=10) as response:
                    html = response.read().decode()
                self.assertIn(f'data-page="{page_id}"', html)
                self.assertIn('class="site-nav"', html)
                for href, chip_id in expected:
                    self.assertIn(href, html)
                    self.assertIn(chip_id, html)
                # Marker must be replaced, not left for the browser.
                self.assertNotIn("<!-- site-nav -->", html)

    def test_chat_toggle_swaps_sidebar_icons(self):
        with urllib.request.urlopen(f"{self.base}/static/chat.js", timeout=10) as response:
            script = response.read().decode()
        self.assertIn("function setChatOpen(", script)
        self.assertIn("#i-sidebar-collapse", script)
        self.assertIn("#i-sidebar-expand", script)
        self.assertIn("Hide chat", script)
        self.assertIn("Show chat", script)

    def test_chat_empty_state_reads_setup_readiness(self):
        with urllib.request.urlopen(f"{self.base}/static/chat.js", timeout=10) as response:
            script = response.read().decode()
        self.assertIn('fetch("/api/setup")', script)
        self.assertIn("function emptyState()", script)

    def test_every_page_shares_one_set_of_design_tokens(self):
        """One base.css for all pages, so sizes and colours cannot drift."""
        # Voice has moved to the redesign; the rest follow slice by slice.
        for path in ("/", "/resume", "/cover-letter", "/notes"):
            with self.subTest(path=path):
                with urllib.request.urlopen(f"{self.base}{path}", timeout=10) as response:
                    page = response.read().decode()
                self.assertIn("/static/base.css", page)
                # Pages may read the tokens; only base.css defines them.
                self.assertNotRegex(page, r"--pico-font-size\s*:")
                self.assertNotRegex(page, r"--(green|accent)\s*:")
                self.assertNotIn("&#8592;", page)           # buttons are text or icon, never both
        with urllib.request.urlopen(f"{self.base}/static/base.css", timeout=10) as response:
            css = response.read().decode()
        self.assertIn("--pico-font-size: 15px", css)
        self.assertIn("--control-height", css)

    def test_voice_and_notes_paths_serve_their_own_page(self):
        """Routed now; each answers 200 as soon as its page lands (Tasks 5-6)."""
        for path, page in (("/voice", "voice.html"), ("/notes", "notes.html")):
            with self.subTest(path=path):
                exists = (ROOT / "static" / page).is_file()
                try:
                    with urllib.request.urlopen(f"{self.base}{path}", timeout=10) as response:
                        self.assertTrue(exists, f"{path} served without {page} on disk")
                        self.assertEqual(response.status, 200)
                        self.assertIn("text/html", response.headers["Content-Type"])
                except urllib.error.HTTPError as error:
                    self.assertFalse(exists, f"{page} exists but {path} returned {error.code}")
                    self.assertEqual(error.code, 404)



class ReadingTests(unittest.TestCase):
    """A reading is a measurement, so it has to be repeatable.

    Nothing set a temperature, so both providers defaulted to their own, and
    the same CV against the same posting read 35 and then 42 minutes apart.
    The calls that write are left at the provider's default; the two that
    measure are pinned.
    """

    def test_the_measuring_calls_pin_the_temperature(self):
        source = (ROOT / "server.py").read_text(encoding="utf-8")
        for call in ("fit_request", "keywords_request"):
            with self.subTest(call=call):
                after = source[source.index(call):]
                self.assertIn("temperature=0", after[:after.index(")\n") + 2])

    def test_a_model_that_refuses_it_is_asked_again_without_it(self):
        """What the user hit: Rate The Fit returned 400, because the current
        Claude models answer "`temperature` is deprecated for this model". A
        preference is never worth a failed call."""
        sent = []

        def fake_open(provider, path, body):
            sent.append(dict(body))
            if "temperature" in body:
                raise llm.LLMError("Claude: HTTP 400 - `temperature` is deprecated for this model.")
            raise RuntimeError("second request went out")

        with mock.patch.object(llm, "_open", fake_open), \
             mock.patch.object(llm, "check_model", lambda m: m), \
             mock.patch.object(llm, "provider", llm.PROVIDERS.__getitem__):
            with self.assertRaisesRegex(RuntimeError, "second request went out"):
                list(llm.stream_chat("anthropic", "m", "rules",
                                     [{"role": "user", "content": "hi"}], temperature=0))
        self.assertEqual(len(sent), 2)
        self.assertIn("temperature", sent[0])
        self.assertNotIn("temperature", sent[1])

    def test_any_other_refusal_still_fails(self):
        """Only that one parameter is retried. A bad key is still a bad key."""
        sent = []

        def fake_open(provider, path, body):
            sent.append(body)
            raise llm.LLMError("Claude: HTTP 401 - invalid x-api-key")

        with mock.patch.object(llm, "_open", fake_open), \
             mock.patch.object(llm, "check_model", lambda m: m), \
             mock.patch.object(llm, "provider", llm.PROVIDERS.__getitem__):
            with self.assertRaisesRegex(llm.LLMError, "401"):
                list(llm.stream_chat("anthropic", "m", "rules",
                                     [{"role": "user", "content": "hi"}], temperature=0))
        self.assertEqual(len(sent), 1)

    def test_writing_is_left_alone(self):
        """A proposal written at zero would read the same way every time."""
        source = (ROOT / "server.py").read_text(encoding="utf-8")
        chat = source[source.index("chunks = llm.stream_chat("):]
        self.assertNotIn("temperature", chat[:chat.index(")\n") + 2])

    def test_the_body_carries_it_for_both_provider_shapes(self):
        sent = []

        def fake_open(provider, path, body):
            sent.append(body)
            raise RuntimeError("stop here")

        for kind, provider_id in (("anthropic", "anthropic"), ("openai", "openai")):
            with self.subTest(kind=kind):
                sent.clear()
                with mock.patch.object(llm, "_open", fake_open), \
                     mock.patch.object(llm, "check_model", lambda m: m), \
                     mock.patch.object(llm, "provider", llm.PROVIDERS.__getitem__):
                    with self.assertRaises(RuntimeError):
                        list(llm.stream_chat(provider_id, "m", "rules",
                                             [{"role": "user", "content": "hi"}],
                                             temperature=0))
                self.assertEqual(sent[0]["temperature"], 0)
                sent.clear()
                with mock.patch.object(llm, "_open", fake_open), \
                     mock.patch.object(llm, "check_model", lambda m: m), \
                     mock.patch.object(llm, "provider", llm.PROVIDERS.__getitem__):
                    with self.assertRaises(RuntimeError):
                        list(llm.stream_chat(provider_id, "m", "rules",
                                             [{"role": "user", "content": "hi"}]))
                self.assertNotIn("temperature", sent[0])


if __name__ == "__main__":
    unittest.main()


class ApplicationApiTests(unittest.TestCase):
    """Switching application swaps the documents and the chat behind the API."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = root
        (root / "cv.json").write_text(json.dumps(CV))
        (root / "cover.json").write_text(json.dumps(COVER))
        workspace = Workspace(root / "applications", {
            "cv": root / "cv.json",
            "cover": root / "cover.json",
            "theme": ROOT / "theme.json",
            "cover_theme": ROOT / "cover-theme.json",
        })
        workspace.ensure()
        handler = partial(
            server.EditorHandler,
            workspace=workspace,
            out_dir=root / "out",
            voice_path=root / "voice.md",
            notes_path=root / "notes.md",
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.server.shutdown)

    get = SetupApiTests.get
    post = SetupApiTests.post

    def test_first_run_opens_the_migrated_application(self):
        state = self.get("/api/apps")
        self.assertEqual(len(state["applications"]), 1)
        self.assertEqual(state["current"], state["applications"][0]["slug"])

    def test_each_application_keeps_its_own_chat(self):
        first = self.get("/api/apps")["current"]
        self.post("/api/chat/history", {
            "messages": [{"role": "user", "content": "tighten the summary", "document": "cv"}],
            "job_description": "RPM at Meta",
        })
        second = self.post("/api/apps", {"action": "create", "name": "Osmo — TPM"})
        self.assertEqual(self.get("/api/chat/history")["messages"], [])
        self.post("/api/apps", {"action": "open", "slug": first})
        kept = self.get("/api/chat/history")
        self.assertEqual(kept["messages"][0]["document"], "cv")
        self.assertEqual(kept["job_description"], "RPM at Meta")
        self.assertEqual(second["application"]["name"], "Osmo — TPM")

    def test_a_new_application_starts_from_the_base_then_diverges(self):
        """A plain New used to copy the application just worked on, and one
        posting's bullets rode along into the next. It starts from the base
        CV now, and the posting builds the rest from the library."""
        first = self.get("/api/apps")["current"]
        document = self.get("/api/document")
        self.post("/api/save", dict(document, cv=dict(document["cv"], name="Tailored Name"),
                                    document="cv"))
        second = self.post("/api/apps", {"action": "create", "name": "Osmo — TPM"})
        self.assertEqual(self.get("/api/document")["cv"]["name"], CV["name"])
        self.post("/api/save", dict(document, cv=dict(document["cv"], name="Osmo Name"),
                                    document="cv", app=second["application"]["slug"]))
        self.assertEqual(self.get(f"/api/document?app={first}")["cv"]["name"], "Tailored Name")

    def test_a_new_application_opens_on_the_blank_frame(self):
        self.post("/api/apps", {"action": "create", "name": "Osmo — TPM"})
        cv = self.get("/api/document")["cv"]
        self.assertEqual(cv["name"], CV["name"])
        for section in cv["sections"]:
            for entry in section.get("entries") or []:
                if "EDUCATION" not in section["heading"].upper():
                    self.assertEqual(entry["bullets"], [])

    def test_the_language_count_can_be_asked_for_on_a_letter(self):
        """Coverage is not shown for a letter by default, because a recruiter's
        search runs on the CV. Asked for explicitly it is still a plain count,
        and the server has to answer for either document."""
        document = self.get("/api/document")
        posting = "product manager roadmap discovery stakeholders"
        slug = self.get("/api/apps")["current"]
        self.post("/api/chat/history", {"messages": [], "job_description": posting, "app": slug})
        keywords.save(self.root / "applications" / slug / "keywords.json", posting=posting,
                      terms=["roadmap", "discovery"], entries={})
        counted = self.post("/api/match", {
            "document": "cover", "doc": document["cover"], "job_description": posting,
        })
        self.assertIsInstance(counted["score"], int)    # the same list, on the letter
        self.assertIn("missing", counted)

        with urllib.request.urlopen(f"{self.base}/resume", timeout=10) as response:
            page = response.read().decode()
        # One row, two buttons: the model's reading of the document, and the
        # keyword list, which is replaced only when asked for by name. On a
        # letter the second button is the count, shown only on request.
        self.assertIn("Count The Keywords", page)
        self.assertIn("Read Afresh", page)
        self.assertIn("function scoreActions(", page)
        self.assertIn("countLanguage", page)

    def test_the_base_letter_names_no_company(self):
        """A plain New starts the letter from the base file, so the base must
        not be somebody's letter. It was Meta's, which meant every new
        application opened addressed to Meta."""
        base = json.loads((ROOT / "cover.json").read_text())
        self.assertEqual(base["company"], "")
        self.assertEqual(base["role"], "")
        self.assertIn("[Company]", base["greeting"])
        joined = " ".join(base["paragraphs"])
        self.assertIn("[Role] at [Company]", joined)
        for named in ("Meta", "Amazon", "Fordham", "Rotational"):
            self.assertNotIn(named, joined)

    def test_a_restored_draft_that_cannot_be_rendered_is_dropped(self):
        """A draft is a convenience and must not be able to trap the editor.

        A document missing a required string cannot be previewed or saved, and
        Save is what clears the draft, so a bad draft came back on every
        reload. The server refuses it; the page drops the draft and reloads
        what is on disk rather than showing the same error again.
        """
        document = self.get("/api/document")
        broken = json.loads(json.dumps(document["cv"]))
        labelled = next(s for s in broken["sections"] if s.get("items"))
        del labelled["items"][0]["text"]
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/preview", {**document, "cv": broken, "document": "cv"})
        self.assertIn("text", json.loads(caught.exception.read())["error"])

        with urllib.request.urlopen(f"{self.base}/resume", timeout=10) as response:
            page = response.read().decode()
        self.assertIn("unprovenDraft = true", page)          # armed on restore
        self.assertIn("if (unprovenDraft) return discardDraft", page)   # and acted on

    def test_a_round_the_user_did_not_type_is_marked_as_such(self):
        """A tailoring round pushes a turn nobody typed. The flag is what stops
        it rendering as the user's own words after a reload."""
        self.post("/api/chat/history", {"messages": [
            {"role": "user", "content": "Tailor this CV to the posting.",
             "document": "cv", "pipeline": True},
            {"role": "assistant", "content": "```gaps\nDo you have X\n```", "document": "cv"},
        ], "job_description": "A posting"})
        back = self.get("/api/chat/history")["messages"]
        self.assertTrue(back[0]["pipeline"])
        self.assertNotIn("pipeline", back[1])

    def test_tailoring_is_refused_without_a_posting_or_on_the_letter(self):
        document = self.get("/api/document")
        for body, expected in (
            ({"document": "cv", "doc": document["cv"], "job_description": ""},
             "paste a job description first"),
            ({"document": "cover", "doc": document["cover"],
              "job_description": "A posting"}, "tailoring runs on the CV"),
        ):
            with self.subTest(expected=expected):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.post("/api/chat", {**body, "task": "tailor", "provider": "none",
                                            "model": "none", "messages": []})
                self.assertEqual(json.loads(caught.exception.read())["error"], expected)

    def test_the_editor_page_carries_the_application_id(self):
        """The browser keys the unsaved draft and the fit reading on the id,
        so the page has to arrive with it: a slug is reused by the next
        application made from the same posting."""
        app = self.get("/api/apps")
        current = next(a for a in app["applications"] if a["slug"] == app["current"])
        with urllib.request.urlopen(f"{self.base}/{app['current']}/resume", timeout=10) as r:
            html = r.read().decode()
        self.assertTrue(current["id"])
        self.assertIn(f'data-app-id="{current["id"]}"', html)

    def test_a_save_can_write_the_json_without_rebuilding(self):
        """Naming an application saves only to take the documents off the
        untouched list. The files in out/ are named after the company on the
        cover letter, which the posting has not been read for yet, so nothing
        is built."""
        document = self.get("/api/document")
        saved = self.post("/api/save", dict(document, cv=dict(document["cv"], name="Edited"),
                                            document="cv", build=False))
        self.assertEqual(saved["written"], [])
        self.assertEqual(self.get("/api/document")["cv"]["name"], "Edited")

    def test_the_slug_in_the_request_decides_which_application_is_written(self):
        first = self.get("/api/apps")["current"]
        second = self.post("/api/apps", {"action": "create", "name": "Osmo — TPM"})["application"]
        document = self.get(f"/api/document?app={first}")
        self.post("/api/save", dict(document, cv=dict(document["cv"], name="Only In First"),
                                    document="cv", app=first))
        # The open application is the second one; the write still landed in the first.
        self.assertEqual(self.get("/api/apps")["current"], second["slug"])
        self.assertEqual(self.get(f"/api/document?app={first}")["cv"]["name"], "Only In First")
        self.assertNotEqual(self.get(f"/api/document?app={second['slug']}")["cv"]["name"],
                            "Only In First")

    def test_the_old_document_paths_redirect_into_the_open_application(self):
        slug = self.get("/api/apps")["current"]
        for path, tail in (("/resume", "resume"), ("/cover-letter", "cover-letter")):
            with self.subTest(path=path):
                with urllib.request.urlopen(f"{self.base}{path}", timeout=10) as response:
                    self.assertEqual(response.url, f"{self.base}/{slug}/{tail}")

    def test_an_unknown_slug_is_not_a_page(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{self.base}/no-such-application", timeout=10)
        self.assertEqual(caught.exception.code, 404)

    def test_archive_keeps_the_documents_and_hides_the_application(self):
        first = self.get("/api/apps")["current"]
        self.post("/api/apps", {"action": "create", "name": "Osmo — TPM"})
        self.post("/api/apps", {"action": "archive", "slug": first})
        listed = {a["slug"]: a["archived"] for a in self.get("/api/apps")["applications"]}
        self.assertTrue(listed[first])
        self.post("/api/apps", {"action": "restore", "slug": first})
        self.post("/api/apps", {"action": "open", "slug": first})
        self.assertEqual(self.get("/api/apps")["current"], first)

    def test_a_bad_slug_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/apps", {"action": "open", "slug": "../../etc"})
        self.assertEqual(caught.exception.code, 400)

    def test_delete_removes_the_application_for_good(self):
        first = self.get("/api/apps")["current"]
        folder = self.root / "applications" / first
        self.assertTrue(folder.is_dir())
        self.post("/api/apps", {"action": "delete", "slug": first})
        slugs = {a["slug"] for a in self.get("/api/apps")["applications"]}
        self.assertNotIn(first, slugs)
        self.assertFalse(folder.exists())  # gone, not just hidden

    def test_deleting_the_open_application_falls_back_to_another(self):
        first = self.get("/api/apps")["current"]
        second = self.post("/api/apps", {"action": "create", "name": "Osmo — TPM"})["application"]["slug"]
        self.post("/api/apps", {"action": "open", "slug": first})
        self.post("/api/apps", {"action": "delete", "slug": first})
        self.assertEqual(self.get("/api/apps")["current"], second)

    def test_an_archived_application_can_be_deleted(self):
        first = self.get("/api/apps")["current"]
        self.post("/api/apps", {"action": "create", "name": "Osmo — TPM"})
        self.post("/api/apps", {"action": "archive", "slug": first})
        self.post("/api/apps", {"action": "delete", "slug": first})
        slugs = {a["slug"] for a in self.get("/api/apps")["applications"]}
        self.assertNotIn(first, slugs)

    def test_the_track_can_be_set_by_hand_and_is_validated(self):
        slug = self.get("/api/apps")["current"]
        self.post("/api/apps", {"action": "track", "slug": slug, "track": "Engineering"})
        apps = {a["slug"]: a for a in self.get("/api/apps")["applications"]}
        self.assertEqual(apps[slug]["track"], "Engineering")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/apps", {"action": "track", "slug": slug, "track": "Wizard"})
        self.assertEqual(caught.exception.code, 400)

    def test_deleting_an_unknown_application_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/apps", {"action": "delete", "slug": "no-such-app"})
        self.assertEqual(caught.exception.code, 400)


POSTING = ("Flexport is hiring a Rotational Development Program associate. You will"
           " own account management for live clients, write SQL, and build workflow"
           " automation with low-code tooling.")
LIST_REPLY = '{"keywords": ["account management", "sql", "workflow automation"]}'


def fake_complete(system, prompt, *, fail_on=()):
    """Answers each kind of call the way a model would, by its rules."""
    if system == assistant.job_prompt():
        return '{"company": "Flexport", "role": "Rotational Development Program", "track": "Other"}'
    if system == assistant.keywords_prompt():
        if "keywords" in fail_on:
            raise llm.LLMError("Claude: HTTP 529 - overloaded")
        return LIST_REPLY
    if system == assistant.map_prompt():
        if "map" in fail_on:
            raise llm.LLMError("Claude: HTTP 529 - overloaded")
        answered = "I ran the review" in prompt
        return json.dumps({
            "account management": {"state": "answered", "from": "MKEL partner accounts",
                                   "say": "Managed partner accounts for four operators."},
            "sql": {"state": "answered" if answered else "unanswered",
                    "from": "I ran the review" if answered else "",
                    "say": "Wrote SQL for the review." if answered else ""},
            "workflow automation": {"state": "unanswered", "from": "", "say": ""},
        })
    raise AssertionError(f"unexpected call: {system[:40]}")


class KeywordApiTests(unittest.TestCase):
    """The list is read once, kept beside the posting, and counted literally."""

    get = SetupApiTests.get
    post = SetupApiTests.post

    def call(self, fail_on=()):
        return mock.patch.object(
            llm, "complete",
            lambda provider, model, system, prompt, temperature=None:
                fake_complete(system, prompt, fail_on=fail_on))

    def setUp(self):
        ApplicationApiTests.setUp(self)     # the same server, without its tests
        patcher = mock.patch.object(llm, "check_model", lambda m: m)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.slug = self.get("/api/apps")["current"]

    def keep_posting(self, text=POSTING):
        self.post("/api/chat/history", {"messages": [], "job_description": text,
                                        "app": self.slug})

    def read(self):
        return self.post("/api/keywords", {"action": "read", "job_description": POSTING,
                                           "provider": "anthropic", "model": "m",
                                           "app": self.slug})

    def test_naming_reads_and_freezes_the_list(self):
        made = self.post("/api/apps", {"action": "create"})["application"]["slug"]
        with self.call():
            named = self.post("/api/apps", {"action": "identify", "slug": made,
                                            "job_description": POSTING,
                                            "provider": "anthropic", "model": "m"})
        slug = named["application"]["slug"]
        self.post("/api/chat/history", {"messages": [], "job_description": POSTING, "app": slug})
        got = self.post("/api/keywords", {"action": "get", "app": slug})
        self.assertEqual(got["keywords"], ["account management", "sql", "workflow automation"])
        self.assertTrue(got["current"])
        self.assertEqual(got["map"]["account management"]["state"], "answered")

    def test_a_failed_list_read_does_not_fail_naming(self):
        made = self.post("/api/apps", {"action": "create"})["application"]["slug"]
        with self.call(fail_on=("keywords",)):
            named = self.post("/api/apps", {"action": "identify", "slug": made,
                                            "job_description": POSTING,
                                            "provider": "anthropic", "model": "m"})
        self.assertEqual(named["application"]["slug"], "flexport-rotational-development-program")

    def test_the_count_uses_the_stored_list_only(self):
        self.keep_posting()
        with self.call():
            self.read()
        out = self.post("/api/match", {"document": "cv", "doc": CV, "app": self.slug,
                                       "job_description": POSTING, "keywords": ["nonsense"]})
        self.assertNotIn("nonsense", out["missing"])
        self.assertEqual(len(out["matched"]) + len(out["missing"]), 3)

    def test_without_a_list_the_ring_has_nothing_to_count(self):
        self.keep_posting()
        out = self.post("/api/match", {"document": "cv", "doc": CV, "app": self.slug,
                                       "job_description": POSTING})
        self.assertTrue(out["needs_list"])
        self.assertIsNone(out["score"])

    def test_a_posting_changed_elsewhere_is_reported_stale(self):
        self.keep_posting()
        with self.call():
            self.read()
        self.keep_posting(POSTING + " Customs experience is a plus.")
        self.assertFalse(self.post("/api/keywords", {"action": "get", "app": self.slug})["current"])

    def test_only_the_answered_keyword_is_rewritten(self):
        self.keep_posting()
        with self.call():
            before = self.read()["map"]
            after = self.post("/api/keywords", {
                "action": "answer", "keyword": "sql", "app": self.slug,
                "sentence": "I ran the review queries in SQL at Amazon.",
                "provider": "anthropic", "model": "m"})["map"]
        self.assertEqual(after["sql"]["state"], "answered")
        self.assertEqual(after["account management"], before["account management"])
        self.assertEqual(after["workflow automation"], before["workflow automation"])
        self.assertIn("I ran the review queries", (self.root / "notes.md").read_text())

    def test_an_answer_is_filed_as_his(self):
        self.keep_posting()
        with self.call():
            self.read()
            self.post("/api/keywords", {
                "action": "answer", "keyword": "sql", "app": self.slug,
                "sentence": "I ran the review queries in SQL at Amazon.",
                "provider": "anthropic", "model": "m"})
        self.assertIn("· his answer", (self.root / "notes.md").read_text())

    def test_an_answer_survives_a_failed_remap(self):
        self.keep_posting()
        with self.call():
            self.read()
        with self.call(fail_on=("map",)):
            with self.assertRaises(urllib.error.HTTPError):
                self.post("/api/keywords", {
                    "action": "answer", "keyword": "sql", "app": self.slug,
                    "sentence": "I ran the review queries in SQL at Amazon.",
                    "provider": "anthropic", "model": "m"})
        self.assertIn("I ran the review queries", (self.root / "notes.md").read_text())
        got = self.post("/api/keywords", {"action": "get", "app": self.slug})
        self.assertEqual(got["map"]["sql"]["state"], "unanswered")

    def test_a_no_is_written_and_kept(self):
        self.keep_posting()
        with self.call():
            self.read()
        got = self.post("/api/keywords", {"action": "decline", "keyword": "workflow automation",
                                          "line": "I have not built workflow automation.",
                                          "app": self.slug})
        self.assertEqual(got["map"]["workflow automation"]["state"], "declined")
        self.assertIn("I have not built workflow automation.",
                      (self.root / "notes.md").read_text())

    def test_the_chat_is_told_the_list_and_its_answers(self):
        self.keep_posting()
        with self.call():
            self.read()
        sent = {}

        def capture(provider, model, system, turns, cacheable="", tail="", temperature=None):
            sent["prompt"] = system
            yield "ok"

        with mock.patch.object(llm, "stream_chat", capture):
            self.post_raw("/api/chat", {"document": "cv", "doc": CV, "app": self.slug,
                                        "job_description": POSTING, "provider": "anthropic",
                                        "model": "m", "messages": [{"role": "user", "content": "hi"}]})
        self.assertIn("Managed partner accounts for four operators.", sent["prompt"])
        self.assertIn("workflow automation unanswered", sent["prompt"])

    def test_the_chat_is_told_what_is_already_on_the_page(self):
        self.keep_posting()
        with self.call():
            self.read()
        sent = {}

        def capture(provider, model, system, turns, cacheable="", tail="", temperature=None):
            sent["prompt"] = system
            yield "ok"

        doc = json.loads(json.dumps(CV))
        doc["name"] = "Joel, who writes SQL"          # any printed field will do
        with mock.patch.object(llm, "stream_chat", capture):
            self.post_raw("/api/chat", {"document": "cv", "doc": doc, "app": self.slug,
                                        "job_description": POSTING, "provider": "anthropic",
                                        "model": "m", "messages": [{"role": "user", "content": "hi"}]})
        self.assertIn("- sql already on the CV", sent["prompt"])

    def post_raw(self, path, payload):
        request = urllib.request.Request(
            f"{self.base}{path}", json.dumps(payload).encode(),
            {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read()

    def test_a_stale_list_is_not_restamped_by_an_answer(self):
        """A no given against a list from another posting used to save it
        stamped with the new posting, and the ring counted the wrong job."""
        self.keep_posting()
        with self.call():
            self.read()
        self.keep_posting(POSTING + " Customs experience is a plus.")
        with self.assertRaises(urllib.error.HTTPError):
            self.post("/api/keywords", {"action": "decline", "keyword": "sql",
                                        "line": "I have not written SQL.", "app": self.slug})
        self.assertFalse(self.post("/api/keywords", {"action": "get", "app": self.slug})["current"])

    def test_retrying_an_answer_writes_the_note_once(self):
        self.keep_posting()
        with self.call():
            self.read()
        for _ in range(2):
            with self.call(fail_on=("map",)):
                with self.assertRaises(urllib.error.HTTPError):
                    self.post("/api/keywords", {
                        "action": "answer", "keyword": "sql", "app": self.slug,
                        "sentence": "I ran the review queries in SQL at Amazon.",
                        "provider": "anthropic", "model": "m"})
        notes = (self.root / "notes.md").read_text()
        self.assertEqual(notes.count("I ran the review queries in SQL at Amazon."), 1)

    def test_an_answer_with_no_agent_writes_nothing(self):
        self.keep_posting()
        with self.call():
            self.read()
        with self.assertRaises(urllib.error.HTTPError):
            self.post("/api/keywords", {"action": "answer", "keyword": "sql", "app": self.slug,
                                        "sentence": "I ran the review queries in SQL."})
        notes = self.root / "notes.md"
        self.assertNotIn("I ran the review queries", notes.read_text() if notes.exists() else "")

    def test_the_map_is_checked_again_without_rereading_the_list(self):
        """Notes added on the notes page are invisible to the map until it is
        read again, and reading a fresh list changes what is being counted."""
        self.keep_posting()
        with self.call():
            before = self.read()
        (self.root / "notes.md").write_text("I ran the review queries in SQL at Amazon.\n")
        with self.call():
            after = self.post("/api/keywords", {"action": "remap", "app": self.slug,
                                                "provider": "anthropic", "model": "m"})
        self.assertEqual(after["keywords"], before["keywords"])
        self.assertTrue(after["current"])
        self.assertEqual(after["map"]["sql"]["state"], "answered")

    def test_a_skip_is_remembered_for_the_posting(self):
        """A Skip used to last one round, so the next round offered the same
        thing again. It is kept with the posting's list, and a fresh list for
        a new posting starts clean."""
        self.keep_posting()
        with self.call():
            self.read()
        got = self.post("/api/keywords", {"action": "skip", "app": self.slug,
                                          "path": "sections.3.entries.0.bullets.1"})
        self.assertEqual(got["skipped"], ["sections.3.entries.0.bullets.1"])
        got = self.post("/api/keywords", {"action": "decline", "keyword": "sql",
                                          "line": "I have not written SQL.", "app": self.slug})
        self.assertEqual(got["skipped"], ["sections.3.entries.0.bullets.1"])   # kept
        with self.call():
            fresh = self.read()
        self.assertEqual(fresh["skipped"], [])                                  # new list, clean


class LibraryApiTests(unittest.TestCase):
    """Naming an application builds its CV from every line he has used."""

    get = SetupApiTests.get
    post = SetupApiTests.post
    call = KeywordApiTests.call

    def setUp(self):
        ApplicationApiTests.setUp(self)
        self.patches = [
            mock.patch.object(llm, "check_model", lambda m: m),
            mock.patch.object(jev, "support", lambda pairs: [("supports", 0.99)] * len(pairs)),
            mock.patch.object(jev, "label", lambda lines: ["interests"] * len(lines)),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def identify(self, slug):
        with self.call():
            return self.post("/api/apps", {"action": "identify", "slug": slug,
                                           "job_description": POSTING,
                                           "provider": "anthropic", "model": "m"})

    def new(self):
        return self.post("/api/apps", {"action": "create"})["application"]["slug"]

    def test_new_still_works_when_the_frame_fails(self):
        with mock.patch.object(fill, "blank", mock.Mock(side_effect=RuntimeError("boom"))):
            slug = self.new()
        self.assertEqual(self.get(f"/api/document?app={slug}")["cv"], CV)

    def test_pasting_the_posting_leaves_a_duplicate_alone(self):
        first = self.new()
        copy = self.post("/api/apps", {"action": "create", "name": "Copy",
                                       "source": first})["application"]["slug"]
        before = self.get(f"/api/document?app={copy}")["cv"]
        slug = self.identify(copy)["application"]["slug"]
        self.assertEqual(self.get(f"/api/document?app={slug}")["cv"], before)

    def test_an_edit_made_before_the_posting_is_not_built_over(self):
        slug = self.new()
        document = self.get(f"/api/document?app={slug}")
        self.post("/api/save", dict(document, cv=dict(document["cv"], name="Mine"),
                                    document="cv", app=slug))
        named = self.identify(slug)
        slug = named["application"]["slug"]
        self.assertEqual(self.get(f"/api/document?app={slug}")["cv"]["name"], "Mine")

    def test_strike_is_remembered_for_later_pages(self):
        self.post("/api/library", {"action": "strike", "text": "A line he disowns."})
        stored = json.loads((self.root / "library.json").read_text())
        self.assertIn("A line he disowns.", stored["struck"])

    def past_line(self, text="Automated client onboarding using Zapier, n8n and Airtable."):
        """An application whose CV carries one line the base does not."""
        slug = self.post("/api/apps", {"action": "create", "name": "Past"})["application"]["slug"]
        path = self.root / "applications" / slug / "cv.json"
        cv = json.loads(path.read_text())
        entries = next(s for s in cv["sections"] if "EXPERIENCE" in s["heading"])["entries"]
        entries[-1]["bullets"].append(text)
        path.write_text(json.dumps(cv))
        return text

    def section_prompt(self, phase, **extra):
        slug = self.new()
        doc = self.get(f"/api/document?app={slug}")["cv"]
        sent = []
        def fake(provider, model, system, turns, **kw):
            sent.append(system)
            yield "ok"
        with mock.patch.object(llm, "stream_chat", fake):
            urllib.request.urlopen(urllib.request.Request(
                f"{self.base}/api/chat", method="POST",
                headers={"Content-Type": "application/json"},
                data=json.dumps({"document": "cv", "doc": doc, "app": slug,
                                 "job_description": POSTING, "task": "tailor",
                                 "phase": phase, "provider": "anthropic", "model": "m",
                                 "messages": [{"role": "user", "content": "go"}],
                                 **extra}).encode()), timeout=10).read()
        return sent[0]

    def test_a_section_turn_carries_his_lines_for_that_section(self):
        system = self.section_prompt("summary")
        self.assertIn("His lines for this section", system)
        self.assertIn(CV["sections"][0]["text"], system)
        self.assertIn("Professional Summary", system)

    def test_a_job_turn_says_how_many_bullets_it_has_room_for(self):
        system = self.section_prompt("bullets", entry="sections.3.entries.0")
        self.assertIn("room for 3 bullets", system)
        self.assertIn(CV["sections"][3]["entries"][0]["bullets"][0], system)

    def test_the_skills_turn_carries_his_skills_list_posting_first(self):
        (self.root / "notes.md").write_text(
            "## Skills\n\n- Product: Jira, Account Management, Odoo\n- AI: Claude, SQL\n")
        system = self.section_prompt("skills")
        self.assertIn("His skills list", system)
        self.assertIn("- Product: Account Management, Jira, Odoo", system)
        self.assertIn("- AI: SQL, Claude", system)
        self.assertIn("never a tool that is not in his skills list", system.lower())

    def test_gaps_are_checked_against_notes_and_past_cvs(self):
        line = self.past_line("Mentored 3 software engineering interns at MKEL.")
        (self.root / "notes.md").write_text("## MKEL\n\nWe added Redis caching after launch.\n")

        def answers(pairs):
            return [("yes", 0.95) if ("Redis" in e and "caching" in q) or ("Mentored" in e and "mentor" in q)
                    else ("says_nothing", 0.9) for q, e in pairs]
        with mock.patch.object(jev, "answers", answers):
            out = self.post("/api/gaps", {"action": "check", "questions": [
                "Have you added caching to a slow app?", "Have you mentored interns?",
                "Have you used React Native?"]})
        self.assertEqual([g["status"] for g in out], ["covered", "past", "open"])
        self.assertEqual(out[1]["line"], line)

    def test_a_past_line_he_confirms_goes_into_his_notes_as_his(self):
        self.post("/api/gaps", {"action": "confirm", "line": "Mentored 3 interns at MKEL."})
        notes = (self.root / "notes.md").read_text()
        self.assertIn("Mentored 3 interns at MKEL.", notes)
        self.assertIn("his answer", notes)

    def test_a_job_turn_needs_a_real_job(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.section_prompt("bullets", entry="sections.0.text")

    def test_the_check_runs_once_and_is_remembered(self):
        self.past_line("Partnered with client teams on delivery of web apps.")
        (self.root / "notes.md").write_text("## Work\n\nI worked with client teams.\n")
        asked = []

        def support(pairs):
            asked.extend(pairs)
            return [("says_nothing", 0.9)] * len(pairs)
        with mock.patch.object(jev, "support", support):
            first = self.post("/api/library", {"action": "check"})
            self.post("/api/library", {"action": "check"})
        self.assertEqual(first["held"], 1)
        self.assertEqual(len(asked), 1)

    def test_review_lists_only_what_needs_him(self):
        line = self.past_line()
        (self.root / "notes.md").write_text(
            "## Work\n\nI built web apps.\n\n## From the chat\n\n### 2026-10-23\n\n"
            "I automated client onboarding using Zapier, n8n and Airtable.\n")
        self.post("/api/library", {"action": "check"})
        out = self.post("/api/library", {"action": "review"})
        self.assertEqual([l["text"] for l in out["lines"]], [line])
        self.assertEqual(out["lines"][0]["verdict"], "unconfirmed")
        self.assertEqual(len(out["notes"]), 1)

    def test_a_kept_note_is_trusted_from_then_on(self):
        self.past_line()
        (self.root / "notes.md").write_text(
            "## From the chat\n\n### 2026-10-23\n\n"
            "I automated client onboarding using Zapier, n8n and Airtable.\n")
        self.post("/api/library", {"action": "check"})
        note = self.post("/api/library", {"action": "review"})["notes"][0]["passage"]
        self.post("/api/library", {"action": "confirm", "passage": note, "value": True})
        self.post("/api/library", {"action": "check"})
        out = self.post("/api/library", {"action": "review"})
        self.assertEqual(out["lines"], [])
        self.assertEqual(out["notes"], [])

    def test_the_review_page_is_served(self):
        with urllib.request.urlopen(f"{self.base}/review", timeout=10) as response:
            page = response.read().decode()
        self.assertIn("Check my library", page)
        self.assertIn("Exclude", page)

    def test_a_decision_made_during_a_check_is_not_lost(self):
        """The review page stays clickable while Check runs; a Strike made in
        the meantime must survive the check saving its results."""
        self.past_line("Partnered with client teams on delivery of web apps.")
        (self.root / "notes.md").write_text("## Work\n\nI worked with client teams.\n")

        def support(pairs):
            self.post("/api/library", {"action": "strike", "text": "Struck mid-check."})
            return [("supports", 0.99)] * len(pairs)
        with mock.patch.object(jev, "support", support):
            self.post("/api/library", {"action": "check"})
        stored = json.loads((self.root / "library.json").read_text())
        self.assertIn("Struck mid-check.", stored["struck"])
        self.assertTrue(stored["checks"])

    def test_his_no_is_not_listed_as_the_assistants_note(self):
        (self.root / "notes.md").write_text(
            "## From the chat\n\n### 2026-10-24 · his no\n\nI have not used Retool.\n")
        self.assertEqual(self.post("/api/library", {"action": "review"})["notes"], [])

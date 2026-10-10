"""Setup pillars: Style, Notes, Documents readiness and their save API."""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from resume_maker import setup
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

    def test_a_filled_cv_and_cover_letter_are_ready(self):
        state = setup.readiness(assistant="", notes="", cv=CV, cover=COVER)
        self.assertEqual(state["cv"], "ready")
        self.assertEqual(state["cover"], "ready")
        self.assertEqual(state["style"], "empty")
        self.assertEqual(state["notes"], "empty")

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
            setup.check_text("assistant", "x" * (setup.MAX_ASSISTANT_SAVE + 1))
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
                self.assertEqual(setup.check_text("assistant", role["text"]), role["text"].strip())


class SetupApiTests(unittest.TestCase):
    """The routes the hub, Style and Notes pages will call."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "cv.json").write_text(json.dumps(CV))
        (root / "cover.json").write_text(json.dumps(COVER))
        cls.assistant_path = root / "assistant.md"
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
            assistant_path=cls.assistant_path,
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
        self.assertIn("assistant", state)
        self.assertIn("notes", state)
        self.assertEqual(state["status"]["cv"], "ready")
        self.assertIn(state["status"]["style"], ("empty", "started", "ready"))

    def test_saving_style_and_notes_writes_files_and_updates_status(self):
        self.post("/api/setup/assistant", {"text": "Ban 'spearheaded'. Use XYZ bullets."})
        self.post("/api/setup/notes", {"text": "## Amazon\nRan weekly triage for the CXI dataset."})
        self.assertIn("spearheaded", self.assistant_path.read_text())
        self.assertIn("triage", self.notes_path.read_text())
        state = self.get("/api/setup")
        self.assertIn("spearheaded", state["assistant"])
        self.assertIn("triage", state["notes"])
        self.assertEqual(state["status"]["style"], "started")

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

    def test_the_chat_can_add_to_voice_without_replacing_it(self):
        """Same offer as Notes, but a writing rule lands in the Voice file."""
        self.post("/api/setup/assistant", {"text": "## Never\n- No 'spearheaded'."})
        self.post("/api/setup/assistant", {"append": "Lead every bullet with the outcome."})
        text = self.get("/api/setup")["assistant"]
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
        for href in ('href="/style"', 'href="/notes"'):
            self.assertIn(href, page)
        self.assertNotIn('id="tile-docs"', page)
        self.assertIn('id="app-list"', page)
        self.assertIn("/api/setup", page)          # paints readiness
        self.assertIn("/api/llm/providers", page)  # model strip

    def test_style_page_is_wired_to_templates_and_save(self):
        with urllib.request.urlopen(f"{self.base}/style", timeout=10) as response:
            page = response.read().decode()
        self.assertIn("/api/setup/templates", page)   # role chips
        self.assertIn('kind: "assistant"', page)      # saves to assistant.md
        self.assertIn("/static/setup.js", page)       # shared load/save
        self.assertIn("/static/setup.css", page)
        self.assertIn('id="view-edit"', page)
        self.assertIn('id="view-preview"', page)
        self.assertIn('id="preview"', page)
        for asset, kind in (("/static/setup.js", "javascript"), ("/static/setup.css", "css")):
            with urllib.request.urlopen(f"{self.base}{asset}", timeout=10) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(kind, response.headers["Content-Type"])

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
        self.assertIn('body[data-page="resume"] :is(#nav-style, #nav-notes)', css)
        self.assertIn('body[data-page="cover"] :is(#nav-style, #nav-notes)', css)

    def test_editor_links_back_to_the_setup_pages(self):
        for path in ("/resume", "/cover-letter"):
            with self.subTest(path=path):
                with urllib.request.urlopen(f"{self.base}{path}", timeout=10) as response:
                    page = response.read().decode()
                for href in ('href="/"', 'href="/style"', 'href="/notes"'):
                    self.assertIn(href, page)

    def test_every_page_serves_the_same_nav(self):
        """One _nav.html fragment on every route; only data-page changes."""
        slug = self.get("/api/apps")["current"]
        expected = (
            ('href="/"', 'id="nav-hub"'),
            ('href="/style"', 'id="nav-style"'),
            ('href="/notes"', 'id="nav-notes"'),
            (f'href="/{slug}/resume"', 'id="mode-cv"'),
            (f'href="/{slug}/cover-letter"', 'id="mode-cover"'),
        )
        # The hub is the entry point and carries no nav of its own. The
        # documents hang off the application they belong to.
        pages = {
            "/style": "style",
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
        for path in ("/", "/resume", "/cover-letter", "/style", "/notes"):
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

    def test_style_and_notes_paths_serve_their_own_page(self):
        """Routed now; each answers 200 as soon as its page lands (Tasks 5-6)."""
        for path, page in (("/style", "style.html"), ("/notes", "notes.html")):
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
            assistant_path=root / "assistant.md",
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

    def test_a_new_application_inherits_the_documents_then_diverges(self):
        first = self.get("/api/apps")["current"]
        document = self.get("/api/document")
        self.post("/api/save", dict(document, cv=dict(document["cv"], name="Tailored Name"),
                                    document="cv"))
        second = self.post("/api/apps", {"action": "create", "name": "Osmo — TPM"})
        # The new application starts from the one just worked on ...
        self.assertEqual(self.get("/api/document")["cv"]["name"], "Tailored Name")
        # ... and then goes its own way.
        self.post("/api/save", dict(document, cv=dict(document["cv"], name="Osmo Name"),
                                    document="cv", app=second["application"]["slug"]))
        self.assertEqual(self.get(f"/api/document?app={first}")["cv"]["name"], "Tailored Name")

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

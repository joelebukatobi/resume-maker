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
from resume_maker import setup  # noqa: E402

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
        handler = partial(
            server.EditorHandler,
            cv_path=root / "cv.json",
            theme_path=ROOT / "theme.json",
            cover_path=root / "cover.json",
            cover_theme_path=ROOT / "cover-theme.json",
            out_dir=root / "out",
            chat_dir=root / ".chat",
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

    def test_hub_offers_the_three_pillars_and_both_documents(self):
        with urllib.request.urlopen(f"{self.base}/", timeout=10) as response:
            page = response.read().decode()
        for pillar in ("Style", "Notes", "Documents"):
            self.assertIn(f">{pillar}</h2>", page)
        for href in ('href="/style"', 'href="/notes"', 'href="/resume"', 'href="/cover-letter"'):
            self.assertIn(href, page)
        self.assertIn("/api/setup", page)          # paints readiness
        self.assertIn("/api/llm/providers", page)  # model strip

    def test_style_page_is_wired_to_templates_and_save(self):
        with urllib.request.urlopen(f"{self.base}/style", timeout=10) as response:
            page = response.read().decode()
        self.assertIn("/api/setup/templates", page)   # role chips
        self.assertIn('kind: "assistant"', page)      # saves to assistant.md
        self.assertIn("/static/setup.js", page)       # shared load/save
        self.assertIn("/static/setup.css", page)
        for asset, kind in (("/static/setup.js", "javascript"), ("/static/setup.css", "css")):
            with urllib.request.urlopen(f"{self.base}{asset}", timeout=10) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(kind, response.headers["Content-Type"])

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

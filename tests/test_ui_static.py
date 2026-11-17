"""The redesigned frontend's own files under /static/ui/.

The route serves one folder, CSS and JS only, and nothing a request can name
outside it. These tests send raw request paths, so nothing in the client
tidies a traversal attempt before the server sees it.
"""

from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import sys
import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from resume_maker.applications import Workspace  # noqa: E402
from tests.test_setup import COVER, CV  # noqa: E402


class UiStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "cv.json").write_text(json.dumps(CV))
        (root / "cover.json").write_text(json.dumps(COVER))
        workspace = Workspace(root / "applications", {
            "cv": root / "cv.json",
            "cover": root / "cover.json",
            "theme": ROOT / "theme.json",
            "cover_theme": ROOT / "cover-theme.json",
        })
        workspace.ensure(chat_dir=root / ".chat")

        # A ui folder of our own, so the tests do not depend on what ships.
        cls.ui = root / "static" / "ui"
        cls.ui.mkdir(parents=True)
        (cls.ui / "tokens.css").write_text(":root{--ground:#181818}")
        (cls.ui / "app-nav.js").write_text("export const x = 1;")
        (cls.ui / ".hidden.css").write_text("secret")
        (cls.ui / "notes.md").write_text("not served")
        (cls.ui / "sub").mkdir()
        (cls.ui / "sub" / "deep.css").write_text("nested")
        (root / "static" / "outside.css").write_text("outside")
        (cls.ui / "escape.css").symlink_to(root / "static" / "outside.css")

        cls.patch = mock.patch.object(server, "UI_ROOT", cls.ui)
        cls.patch.start()
        handler = partial(
            server.EditorHandler,
            workspace=workspace,
            out_dir=root / "out",
            voice_path=root / "voice.md",
            notes_path=root / "notes.md",
        )
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.patch.stop()
        cls.tmp.cleanup()

    def raw_get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest("GET", path, skip_accept_encoding=True)
        conn.endheaders()
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response.status, dict(response.getheaders()), body

    def test_serves_css_with_its_own_type(self):
        status, headers, body = self.raw_get("/static/ui/tokens.css")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/css; charset=utf-8")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(body, b":root{--ground:#181818}")

    def test_serves_a_module_as_javascript(self):
        status, headers, _ = self.raw_get("/static/ui/app-nav.js")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/javascript; charset=utf-8")

    def test_a_query_string_does_not_change_the_file(self):
        status, _, body = self.raw_get("/static/ui/tokens.css?v=2")
        self.assertEqual(status, 200)
        self.assertEqual(body, b":root{--ground:#181818}")

    def test_refuses_everything_it_should_not_serve(self):
        refused = [
            "/static/ui/../../server.py",
            "/static/ui/..%2f..%2fserver.py",
            "/static/ui/%2e%2e/%2e%2e/server.py",
            "/static/ui/.hidden.css",
            "/static/ui/notes.md",
            "/static/ui/sub/deep.css",
            "/static/ui/Tokens.css",
            "/static/ui/missing.css",
            "/static/ui/",
            "/static/ui/tokens.css.map",
            "/static/ui/escape.css",
        ]
        for path in refused:
            with self.subTest(path=path):
                status, _, body = self.raw_get(path)
                self.assertEqual(status, 404)
                self.assertNotIn(b"outside", body)
                self.assertNotIn(b"secret", body)

    def test_the_old_named_files_are_still_served(self):
        status, _, _ = self.raw_get("/static/base.css")
        self.assertEqual(status, 200)


class ShippedUiTests(unittest.TestCase):
    def test_the_foundations_ship_in_the_ui_folder(self):
        for name in ("tokens.css", "reset.css", "atoms.css"):
            with self.subTest(name=name):
                self.assertTrue((server.UI_ROOT / name).is_file())

    def test_every_shipped_file_has_a_name_the_route_serves(self):
        for path in server.UI_ROOT.iterdir():
            with self.subTest(name=path.name):
                self.assertIsNotNone(server._UI_NAME.fullmatch(path.name))



@unittest.skipUnless(shutil.which("node"), "node is not installed")
class SkillsLineTests(unittest.TestCase):
    """A CV skills line read against his skills list, the way the Skills step does."""

    def run_node(self, body):
        module = (server.UI_ROOT / "step-skills.js").as_uri()
        script = f"import {{ splitTools, readLine }} from {json.dumps(module)};\n{body}"
        result = subprocess.run(["node", "--input-type=module", "-e", script],
                                capture_output=True, text=True, timeout=20, check=True)
        return json.loads(result.stdout)

    def test_commas_inside_brackets_do_not_split_a_tool(self):
        got = self.run_node('console.log(JSON.stringify(splitTools('
                            '"JavaScript (Node, React, Next), SQL, CI/CD & Linux")))')
        self.assertEqual(got, ["JavaScript (Node, React, Next)", "SQL", "CI/CD & Linux"])

    def test_an_and_pair_of_listed_tools_is_both_of_them(self):
        got = self.run_node('console.log(JSON.stringify(readLine('
                            '"Scrum, Jira and Confluence, CI/CD and Linux, Figma",'
                            ' ["Scrum", "Jira", "Confluence", "CI/CD", "Linux"])))')
        self.assertEqual(got["named"], ["Scrum", "Jira", "Confluence", "CI/CD", "Linux"])
        self.assertEqual(got["extra"], ["Figma"])

    def test_his_real_lines_read_without_broken_brackets(self):
        from resume_maker import setup
        cv = json.loads((ROOT / "cv.example.json").read_text(encoding="utf-8"))
        notes = (ROOT / "notes.md").read_text(encoding="utf-8") if (ROOT / "notes.md").is_file() else ""
        listed = setup.skills(notes)
        items = next(s for s in cv["sections"] if "SKILL" in s["heading"].upper())["items"]
        cases = [{"text": i.get("text", ""), "list": next(
            (v for k, v in listed.items() if k.lower() == i.get("label", "").lower()), [])}
            for i in items]
        got = self.run_node(f"const cases = {json.dumps(cases)};\n"
                            "console.log(JSON.stringify(cases.map((c) => readLine(c.text, c.list))));")
        for case, out in zip(cases, got):
            with self.subTest(line=case["text"][:40]):
                for token in out["named"] + out["extra"]:
                    self.assertEqual(token.count("("), token.count(")"), token)

@unittest.skipUnless(shutil.which("node"), "node is not installed")
class BulletCoverTests(unittest.TestCase):
    def test_a_line_names_the_frozen_terms_it_carries(self):
        module = (server.UI_ROOT / "step-bullets.js").as_uri()
        script = (f"import {{ covers }} from {json.dumps(module)};\n"
                  "console.log(JSON.stringify(covers('Led Incident Response on Terraform',"
                  " ['incident response', 'terraform', 'kubernetes', ''])));")
        out = subprocess.run(["node", "--input-type=module", "-e", script],
                             capture_output=True, text=True, timeout=20, check=True).stdout
        self.assertEqual(json.loads(out), ["incident response", "terraform"])



if __name__ == "__main__":
    unittest.main()

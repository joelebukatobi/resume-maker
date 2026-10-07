"""Chat plumbing, exercised against a local fake of both provider APIs."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resume_maker import assistant, llm  # noqa: E402

REPLY = [
    "Here is a tighter summary.\n\n",
    '```proposal\n{"path": "sections.0.text", "text": "Tighter."}\n```',
]
SEEN: list[dict] = []


class FakeAPI(BaseHTTPRequestHandler):
    """Speaks just enough OpenAI and Anthropic to test streaming."""

    def log_message(self, *args):  # quiet
        pass

    def _sse(self, events):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for event in events:
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def do_GET(self):
        body = json.dumps({"data": [{"id": "chat-model"}, {"id": "text-embedding-3"},
                                    {"id": "claude-opus-4-5-20251101", "display_name": "Claude Opus 4.5"}]}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": payload})
        if payload.get("model") == "bad":
            self.send_response(401); self.end_headers()
            self.wfile.write(json.dumps({"error": {"message": "invalid key"}}).encode())
            return
        if self.path.endswith("/messages"):
            self._sse([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}} for t in REPLY])
        else:
            self._sse([{"choices": [{"delta": {"content": t}}]} for t in REPLY])


class ProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.config = tempfile.TemporaryDirectory()
        cls.env = mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": cls.config.name,
            "OPENAI_API_KEY": "k1", "OPENAI_BASE_URL": base,
            "ANTHROPIC_API_KEY": "k2", "ANTHROPIC_BASE_URL": base,
            "DEEPSEEK_API_KEY": "", "MOONSHOT_API_KEY": "",
        })
        cls.env.start()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.server.shutdown()
        cls.config.cleanup()

    def test_both_wire_formats_stream_the_same_text(self):
        for provider in ("openai", "anthropic"):
            with self.subTest(provider=provider):
                text = "".join(llm.stream_chat(provider, "chat-model", "sys", [{"role": "user", "content": "hi"}]))
                self.assertEqual(text, "".join(REPLY))

    def test_anthropic_gets_system_separately_and_openai_as_a_message(self):
        SEEN.clear()
        list(llm.stream_chat("anthropic", "chat-model", "SYS", [{"role": "user", "content": "hi"}]))
        list(llm.stream_chat("openai", "chat-model", "SYS", [{"role": "user", "content": "hi"}]))
        anthropic, openai = SEEN
        self.assertEqual(anthropic["body"]["system"], "SYS")
        self.assertEqual(anthropic["headers"]["x-api-key"], "k2")
        self.assertEqual(openai["body"]["messages"][0], {"role": "system", "content": "SYS"})
        self.assertEqual(openai["headers"]["authorization"], "Bearer k1")

    def test_model_list_drops_non_chat_models_and_labels_the_rest(self):
        models = llm.list_models("openai")
        # sorted by label, so "Chat Model" precedes "Claude Opus 4.5"
        self.assertEqual([m["id"] for m in models], ["chat-model", "claude-opus-4-5-20251101"])
        # display_name when the provider gives one, a tidied id otherwise
        self.assertEqual([m["label"] for m in models], ["Chat Model", "Claude Opus 4.5"])

    def test_model_ids_without_display_names_are_made_readable(self):
        cases = {
            "claude-opus-4-5-20251101": "Claude Opus 4.5 (2025-11-01)",
            "gpt-4o-mini": "GPT 4o mini",
            "deepseek-reasoner": "DeepSeek Reasoner",
            "kimi-k2-0905-preview": "Kimi K2 0905 Preview",
        }
        for model_id, expected in cases.items():
            with self.subTest(model_id=model_id):
                self.assertEqual(llm.pretty_model(model_id), expected)

    def test_provider_error_is_readable(self):
        with self.assertRaisesRegex(llm.LLMError, "invalid key"):
            list(llm.stream_chat("openai", "bad", "sys", [{"role": "user", "content": "hi"}]))

    def test_provider_without_key_is_refused(self):
        with self.assertRaisesRegex(llm.LLMError, "not connected"):
            llm.provider("deepseek")

    def test_connect_saves_a_working_key_owner_only(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_BASE_URL": llm.PROVIDERS["openai"].url}):
            models = llm.connect("deepseek", "sk-test-12345678")
            self.assertIn("chat-model", [m["id"] for m in models])
            self.assertTrue(llm.provider("deepseek").available)
            self.assertEqual(llm.provider("deepseek").key_source, "saved")
            mode = stat.S_IMODE(llm.keys_path().stat().st_mode)
            self.assertEqual(mode, 0o600)
            llm.save_key("deepseek", "")
            self.assertFalse(llm.PROVIDERS["deepseek"].available)

    def test_connect_rejects_bad_keys_without_saving(self):
        for key in ("", "short", "has spaces in it", "line\nbreak-key-123"):
            with self.subTest(key=key), self.assertRaises(llm.LLMError):
                llm.connect("moonshot", key)
        self.assertNotIn("moonshot", llm.saved_keys())

    def test_base_url_override_keeps_the_version_segment(self):
        """Claude's desktop app exports ANTHROPIC_BASE_URL as the bare host."""
        cases = {
            "https://api.anthropic.com": "https://api.anthropic.com/v1",
            "https://api.anthropic.com/": "https://api.anthropic.com/v1",
            "https://api.deepseek.com/v1": "https://api.deepseek.com/v1",
            "https://gateway.example.com/anthropic/v1": "https://gateway.example.com/anthropic/v1",
        }
        for override, expected in cases.items():
            with self.subTest(override=override), mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": override}):
                self.assertEqual(llm.PROVIDERS["anthropic"].url, expected)

    def test_claude_gets_the_prefix_marked_as_cacheable(self):
        SEEN.clear()
        system = "STABLE PREFIX\n\nVOLATILE FIELDS"
        list(llm.stream_chat("anthropic", "chat-model", system,
                             [{"role": "user", "content": "hi"}], cacheable="STABLE PREFIX"))
        blocks = SEEN[0]["body"]["system"]
        self.assertEqual(blocks[0]["text"], "STABLE PREFIX")
        self.assertEqual(blocks[0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(blocks[1]["text"], "VOLATILE FIELDS")
        self.assertNotIn("cache_control", blocks[1])

    def test_without_a_prefix_the_system_is_sent_as_plain_text(self):
        SEEN.clear()
        list(llm.stream_chat("anthropic", "chat-model", "SYS", [{"role": "user", "content": "hi"}]))
        self.assertEqual(SEEN[0]["body"]["system"], "SYS")

    def test_openai_style_providers_need_no_marker(self):
        SEEN.clear()
        list(llm.stream_chat("openai", "chat-model", "STABLE\n\nVOLATILE",
                             [{"role": "user", "content": "hi"}], cacheable="STABLE"))
        self.assertEqual(SEEN[0]["body"]["messages"][0],
                         {"role": "system", "content": "STABLE\n\nVOLATILE"})

    def test_model_names_are_checked(self):
        with self.assertRaises(llm.LLMError):
            llm.check_model("gpt 4; rm -rf /")


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.cv = json.loads((ROOT / "cv.json").read_text())

    def test_every_field_path_resolves_in_the_document(self):
        for path, _, text in assistant.field_paths("cv", self.cv):
            node = self.cv
            for key in path.split("."):
                node = node[int(key)] if key.isdigit() else node[key]
            self.assertEqual(node, text, path)

    def test_system_prompt_lists_fields_and_job_description(self):
        prompt = assistant.system_prompt("cv", self.cv, "Senior PM at Acme")
        self.assertIn("`sections.0.text`", prompt)
        self.assertIn("Senior PM at Acme", prompt)
        self.assertIn("Never invent facts", prompt)

    def test_system_prompt_offers_an_answer_block_for_form_questions(self):
        prompt = assistant.system_prompt("cv", self.cv)
        self.assertIn("```answer", prompt)
        self.assertIn("application form", prompt)

    def test_import_accepts_valid_json_in_a_fence(self):
        reply = "Sure:\n```json\n" + json.dumps(self.cv) + "\n```"
        self.assertEqual(assistant.parse_import("cv", reply)["name"], self.cv["name"])

    def test_import_rejects_unsafe_or_malformed_output(self):
        bad = dict(self.cv, contact=[{"text": "x", "href": "javascript:alert(1)"}])
        for reply in ("not json", json.dumps(bad), "[1, 2]"):
            with self.subTest(reply=reply[:20]), self.assertRaises(assistant.ImportFailed):
                assistant.parse_import("cv", reply)

    def test_cover_import_fills_missing_optional_fields(self):
        doc = assistant.parse_import("cover", json.dumps({"name": "A", "contact": [], "paragraphs": ["Hi"]}))
        self.assertEqual(doc["role"], "")

    def test_docx_text_extraction(self):
        xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
               '<w:p><w:r><w:t>ACME</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t>NY</w:t></w:r></w:p>'
               '<w:p><w:r><w:t>Line two</w:t></w:r></w:p></w:body></w:document>')
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", xml)
        self.assertEqual(assistant.extract_text("cv.docx", buffer.getvalue()), "ACME\tNY\nLine two")

    def test_unsupported_and_oversized_files_are_refused(self):
        with self.assertRaises(assistant.ImportFailed):
            assistant.extract_text("cv.exe", b"MZ")
        with self.assertRaises(assistant.ImportFailed):
            assistant.extract_text("cv.txt", b"x" * (assistant.MAX_IMPORT_BYTES + 1))


class StyleAndNotesPromptTests(unittest.TestCase):
    """Style (assistant.md) and the shared experience bank (notes.md)."""

    def setUp(self):
        self.cv = json.loads((ROOT / "cv.json").read_text())

    def prompt(self, **kwargs):
        return assistant.system_prompt("cv", self.cv, **kwargs)

    def test_style_and_notes_are_appended_under_their_own_headings(self):
        prompt = self.prompt(assistant_md="Ban the word 'spearheaded'.",
                             notes_md="Ran weekly triage for the Amazon CXI dataset.")
        self.assertIn("## Editor style", prompt)
        self.assertIn("Ban the word 'spearheaded'.", prompt)
        self.assertIn("## Experience notes", prompt)
        self.assertIn("Ran weekly triage for the Amazon CXI dataset.", prompt)

    def test_protocol_rules_stay_first(self):
        prompt = self.prompt(assistant_md="Style rules here.", notes_md="Notes here.")
        self.assertTrue(prompt.startswith("You are a careful resume"))
        self.assertLess(prompt.index("proposal"), prompt.index("## Editor style"))

    def test_missing_style_and_notes_add_no_headings(self):
        plain = assistant.system_prompt("cv", self.cv)
        self.assertEqual(plain, self.prompt(assistant_md="", notes_md="   "))
        self.assertNotIn("## Editor style", plain)
        self.assertNotIn("## Experience notes", plain)

    def test_job_description_still_works_alongside_them(self):
        prompt = self.prompt(job_description="Senior PM at Acme", notes_md="Notes here.")
        self.assertIn("## Target job description", prompt)
        self.assertIn("Senior PM at Acme", prompt)
        self.assertIn("## Experience notes", prompt)

    def test_oversized_notes_are_clipped_and_the_prompt_says_so(self):
        filler = "\u00f8"  # a character the CV itself never uses
        notes = filler * (assistant.MAX_NOTES_CHARS + 5_000) + "TAIL-SENTINEL"
        prompt = self.prompt(notes_md=notes)
        self.assertNotIn("TAIL-SENTINEL", prompt)  # the overflow is dropped
        self.assertLessEqual(prompt.count(filler), assistant.MAX_NOTES_CHARS)
        self.assertIn("truncated", prompt.lower())

    def test_clip_notes_leaves_normal_notes_untouched(self):
        self.assertEqual(assistant.clip_notes("Short notes."), "Short notes.")
        clipped = assistant.clip_notes("y" * (assistant.MAX_NOTES_CHARS + 10))
        self.assertLessEqual(len(clipped), assistant.MAX_NOTES_CHARS + 200)
        self.assertIn("truncated", clipped.lower())

    def test_load_text_reads_a_file_and_tolerates_a_missing_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assistant.md"
            self.assertEqual(assistant.load_text(path), "")
            path.write_text("Write like a person.\n", encoding="utf-8")
            self.assertEqual(assistant.load_text(path), "Write like a person.")
            self.assertEqual(assistant.load_text(Path(tmp) / "nope" / "notes.md"), "")

if __name__ == "__main__":
    unittest.main()


class PromptCacheTests(unittest.TestCase):
    """The prompt is split so providers can bill the repeated part cheaply."""

    def setUp(self):
        self.cv = json.loads((ROOT / "cv.json").read_text())

    def test_volatile_fields_come_after_the_stable_prefix(self):
        stable, volatile = assistant.prompt_parts(
            "cv", self.cv, "JOB TEXT", assistant_md="MY STYLE", notes_md="MY NOTES")
        for piece in ("MY STYLE", "MY NOTES", "JOB TEXT", "Never invent facts"):
            self.assertIn(piece, stable)
        self.assertNotIn("sections.0", stable)      # fields never in the prefix
        self.assertIn("`sections.0.text`", volatile)

    def test_editing_the_document_leaves_the_prefix_untouched(self):
        edited = json.loads(json.dumps(self.cv))
        edited["sections"][0]["text"] = "A completely different summary."
        first, _ = assistant.prompt_parts("cv", self.cv, "JD", notes_md="N")
        second, _ = assistant.prompt_parts("cv", edited, "JD", notes_md="N")
        self.assertEqual(first, second)

    def test_system_prompt_still_returns_the_whole_thing(self):
        whole = assistant.system_prompt("cv", self.cv, "JD", assistant_md="S", notes_md="N")
        stable, volatile = assistant.prompt_parts("cv", self.cv, "JD", assistant_md="S", notes_md="N")
        self.assertEqual(whole, f"{stable}\n\n{volatile}")

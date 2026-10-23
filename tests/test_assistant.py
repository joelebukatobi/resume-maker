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
from resume_maker.model import normalise_dates  # noqa: E402

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

    def test_the_live_document_gets_its_own_breakpoint(self):
        SEEN.clear()
        system = "STABLE PREFIX\n\nVOLATILE FIELDS"
        list(llm.stream_chat("anthropic", "chat-model", system,
                             [{"role": "user", "content": "hi"}],
                             cacheable="STABLE PREFIX", tail="VOLATILE FIELDS"))
        blocks = SEEN[0]["body"]["system"]
        self.assertEqual([b["text"] for b in blocks], ["STABLE PREFIX", "VOLATILE FIELDS"])
        self.assertTrue(all(b.get("cache_control") for b in blocks))

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

    NOTES = (
        "# How I got here\n\nIntro line.\n\n"
        "## Before there was a career\n\nI fixed broken PCs and ran Linux at fifteen.\n\n"
        "## The data work\n\nI owned a dataset pipeline and ran discovery with engineering.\n\n"
        "## Bootcamps\n\nI learned Figma and shipped a school management app.\n"
    )

    def test_notes_lead_with_what_the_posting_cares_about(self):
        """Nothing is dropped. The relevant part simply goes first."""
        ordered = assistant.order_notes(
            self.NOTES, "You will own the dataset pipeline and run discovery with engineering.")
        for heading in ("## Before there was a career", "## The data work", "## Bootcamps"):
            self.assertIn(heading, ordered)          # nothing lost
        self.assertIn("Intro line.", ordered)        # the preamble stays on top
        self.assertLess(ordered.index("## The data work"),
                        ordered.index("## Before there was a career"))
        self.assertIn("Most relevant to this posting", ordered)

    def test_notes_are_left_alone_when_there_is_nothing_to_rank_against(self):
        self.assertEqual(assistant.order_notes(self.NOTES, ""), self.NOTES.strip())
        short = "# Title\n\n## Only section\n\nText about pipelines."
        self.assertEqual(assistant.order_notes(short, "pipelines"), short.strip())

    def test_the_hard_rules_are_restated_where_the_model_writes(self):
        """Position beats repetition: the text nearest the output governs."""
        _, volatile = assistant.prompt_parts("cv", self.cv)
        self.assertIn("No dashes as punctuation", volatile.split("Before you answer")[1])
        self.assertNotIn("Greet the reader", volatile)      # a CV has no greeting
        _, letter = assistant.prompt_parts("cover", {"name": "Joel", "paragraphs": []})
        self.assertIn("Greet the reader", letter)
        self.assertIn("[Company]", letter.split("Before you answer")[1])

    def test_a_letter_is_written_as_one_piece(self):
        """What the user read on the Flexport letter: the greeting, the stub
        naming him and the role, and then a jump straight into "At Expert
        Edge". The editor refused a proposal for the next paragraph, so the
        model could only leave the stub standing and offer the rest to copy,
        and nothing in the prompt said the letter had to read as a whole."""
        letter = assistant.system_prompt("cover", {"name": "Joel", "paragraphs": []})
        self.assertIn("paragraphs.1", letter)          # the next one is writable
        self.assertIn("placeholder, not an opening", letter)
        self.assertIn("one piece of writing", letter)
        cv = assistant.system_prompt("cv", self.cv)
        self.assertNotIn("one piece of writing", cv)   # the CV is not a letter

    def test_a_cv_session_is_not_told_how_to_write_a_letter(self):
        """What the user hit: a full cover letter arrived in an answer block in
        the middle of a CV session. The prompt carried the letter's rules and
        the voice file's letter section, opening template and all, and nothing
        said the letter was not open."""
        voice = ("# How I write\n\n## Voice\n\nPlain words.\n\n"
                 "## Cover letters\n\nGreet the reader, then say"
                 ' "I am applying for the [Role] at [Company]."\n\n'
                 "## How a CV is actually read\n\nSix seconds, top down.\n")
        cv = assistant.system_prompt("cv", self.cv, voice_md=voice)
        self.assertIn("Only the CV is open", cv)
        self.assertNotIn("[Role] at [Company]", cv)        # the letter's template
        self.assertIn("Six seconds", cv)                   # the CV's own section stays

        letter = assistant.system_prompt("cover", {"name": "Joel", "paragraphs": []},
                                         voice_md=voice)
        self.assertNotIn("Only the CV is open", letter)
        self.assertIn("[Role] at [Company]", letter)
        self.assertNotIn("Six seconds", letter)            # the CV's section goes

    def test_an_answer_block_is_not_a_home_for_the_other_document(self):
        self.assertIn("not a\n  place to put the user's other document",
                      assistant.system_prompt("cv", self.cv))

    def test_notes_are_ranked_by_density_not_by_size(self):
        """The plain count rewarded length. A five thousand character section
        matched more of any posting's words by surface area than a nine
        hundred character one, so the same employer led every ranking."""
        notes = ("# Notes\n\n## Long and general\n\n"
                 + ("I did a lot of work over several years and it went well. " * 40)
                 + "\n\n## Short and exact\n\nI ran the e-commerce storefront"
                 " migration and the checkout redesign.\n\n## Unrelated\n\n"
                 "I repaired computers as a child.\n")
        ranked = assistant.order_notes(notes, "e-commerce storefront checkout redesign")
        order = [line for line in ranked.splitlines() if line.startswith("## ")]
        self.assertEqual(order[0], "## Short and exact")

    def test_which_jobs_answer_the_posting_reaches_the_model(self):
        """The prompt said which words were missing and never which work to
        lead with, so the model led with whatever ranked first by size."""
        cv = {"sections": [{"heading": "EXPERIENCE", "type": "entries", "entries": [
            {"left": "AMAZON, Customer eXperience Impressions (SCOT-AIM)",
             "sub_left": "Product Manager",
             "bullets": ["Owned a customer experience data product and its roadmap."]},
            {"left": "NUMINIX, e-Commerce", "sub_left": "Frontend",
             "bullets": ["Shipped storefront and checkout work for an online store."]},
        ]}]}
        note = assistant.experience_note(cv, "storefront checkout online store e-commerce")
        self.assertIn("NUMINIX", note)
        self.assertLess(note.index("NUMINIX"), note.index("AMAZON"))
        self.assertNotIn("SCOT-AIM", note)          # the division is an internal name
        self.assertIn("never move an entry on the page", note)
        self.assertEqual(assistant.experience_note(cv, ""), "")

    def test_the_dash_in_a_date_is_exempt_from_the_punctuation_rule(self):
        """The rule was written about prose and was being applied to a date.

        A date range reaches the model as text with a spaced hyphen in it,
        which the rule bans, so the model proposed "July 2024 to May 2026".
        Accepting that is worse than cosmetic: ``normalise_dates`` only
        recognises dash-like separators, so the entry stops following the
        theme's ``date_separator`` while every other entry still does.
        """
        rules, fields = assistant.prompt_parts("cv", self.cv)
        self.assertIn("A dates field is the exception", rules)
        self.assertIn("not yours to change", fields.split("Before you answer")[1])
        self.assertEqual(normalise_dates("July 2024 to May 2026", "-"),
                         "July 2024 to May 2026")      # why it must not be accepted
        self.assertEqual(normalise_dates("July 2024 – May 2026", "-"),
                         "July 2024 - May 2026")       # what the theme does instead

    def test_the_model_may_not_claim_to_have_saved_a_note(self):
        """What it cost: the one fact the user needed most, work authorisation,
        was offered twice as a notes card and never written, because the model
        opened with "I have saved it as a note" and the button went unpressed.
        It cannot write to the file at all."""
        rules, fields = assistant.prompt_parts("cv", self.cv)
        self.assertIn("never say you have saved", rules)
        self.assertIn("Nothing is saved until the user presses the button",
                      fields.split("Before you answer")[1])

    def test_the_posting_requirements_reach_the_prompt(self):
        """The only line in the prompt that says what this posting asks for.

        Without it the model works from the notes, and the notes hold the work
        done for every other posting too: an Etsy engineering letter carried
        "I applied WCAG 2.2 accessibility standards" across from the
        accessibility posting worked on three days earlier, because the notes
        said it and nothing said not to.
        """
        note = assistant.wanted_note(["php", "react", "code review"])
        self.assertIn("code review", note)
        self.assertIn("belongs in another application", note)
        self.assertEqual(assistant.wanted_note([]), "")

        _, fields = assistant.prompt_parts("cover", {"name": "Joel", "paragraphs": ["x"]},
                                           "A posting", wanted=note)
        self.assertIn("code review", fields)
        self.assertLess(fields.index("code review"), fields.index("Before you answer"))

    def test_a_claim_has_to_answer_this_posting(self):
        rules, _ = assistant.prompt_parts("cover", {"name": "Joel", "paragraphs": ["x"]})
        self.assertIn("Every claim has to answer something this posting asks for", rules)
        self.assertIn("not belong here", rules)   # wrapped in the source

    def test_a_round_may_propose_dropping_the_last_posting_left_behind(self):
        task = assistant.tailor_rules("wording")
        self.assertIn("a previous posting left behind", task)
        self.assertIn("a removal is a card like any other", task)

    def test_the_requirement_list_is_asked_for_and_validated(self):
        rules = assistant.keywords_prompt()
        self.assertIn("something a person", rules)       # has done or has not done
        self.assertIn("not the names of", rules)          # other people's jobs
        self.assertIn("not its products", rules)

        got = assistant.parse_keywords(
            '{"keywords": ["PHP", "React", "php", "  TypeScript  ", ""]}')
        self.assertEqual(got, ["php", "react", "typescript"])   # trimmed, deduped
        with self.assertRaises(assistant.ImportFailed):
            assistant.parse_keywords("I had a look and it wants PHP")
        with self.assertRaises(assistant.ImportFailed):
            assistant.parse_keywords('{"keywords": []}')

    def test_the_prompt_says_what_day_it_is(self):
        """A model has no clock. Given "July 2024 - May 2026" and nothing else
        it read a finished MBA as still in progress and reported it as a gap,
        twice, and a fit reading is kept until something re-rates it, so the
        phrase stayed on screen."""
        from datetime import date

        note = assistant.today_note(date(2026, 10, 22))
        self.assertIn("Today is 2026-10-22", note)
        self.assertIn("already passed is finished", note)

        rules, fields = assistant.prompt_parts("cv", self.cv)
        self.assertIn("Today is", fields)
        # Not in the cached half: it would change the prefix every day for no
        # other reason.
        self.assertNotIn("Today is", rules)

        asked = assistant.fit_request("cv", self.cv, "A posting.")
        self.assertTrue(asked.startswith("Today is"))
        self.assertIn("has passed is finished", assistant.fit_prompt())

    def test_a_tailoring_round_asks_for_proposals_then_gaps(self):
        """The posting is the instruction. The round does the work the user
        would otherwise have had to type."""
        task = assistant.tailor_rules("wording")
        rules, fields = assistant.prompt_parts("cv", self.cv, "A posting", task=task)
        # The task sits after the document and before the hard rules, which are
        # what the model reads last and what it has broken before.
        self.assertLess(fields.index("without being asked"), fields.index("Before you answer"))
        self.assertIn("ask block", fields)            # a missing keyword is asked about
        self.assertIn("```ask", rules)                   # the block's shape is a protocol rule
        self.assertIn("Do not write a cover letter", rules)

    def test_every_step_offers_one_thing_and_asks(self):
        """The round is a conversation, not a form. A page of cards at once was
        work done before the user had agreed to any of it, and a decision taken
        early changes what should be offered next."""
        for phase in assistant.PHASES:
            with self.subTest(phase=phase):
                task = assistant.tailor_rules(phase)
                self.assertIn("Offer exactly one thing", task)
                self.assertIn("ending in a question", task)
                self.assertIn("never offer two", task)
                # How a step ends: nothing left to say, and no card.
                self.assertIn("write no card", task)

    def test_the_first_step_chooses_the_roles_and_writes_nothing(self):
        """Tailoring the wording of a role that is about to come off the page
        is work thrown away, so the cast is settled first."""
        cast = assistant.tailor_rules("cast")
        self.assertIn("which roles belong on this CV", cast)
        self.assertIn("show proposal", cast)
        self.assertIn("Change no wording in this step", cast)

    def test_the_wording_stage_follows_the_list(self):
        """It used to decide for itself what to change. Now it answers the
        posting's missing keywords, in order, from the map."""
        w = assistant.tailor_rules("wording")
        self.assertIn("missing keywords in the order listed", w.replace("\n", " "))
        self.assertIn("experience underneath", w)       # not "do you have <word>"
        self.assertIn("declined", w)                    # a no is never asked again
        self.assertIn("```ask", assistant.system_prompt("cv", self.cv))

    def test_a_question_is_a_card_the_transcript_can_collapse(self):
        self.assertIn("ask", assistant._CARD.pattern)

    def test_the_wording_step_leaves_the_roles_that_are_off_alone(self):
        wording = assistant.tailor_rules("wording")
        self.assertIn("Never write for a role that is off", wording)
        self.assertIn("From", wording)          # where the change came from

    def test_the_last_step_measures_and_then_lets_go(self):
        """The page either fits or it does not, so this step is the one that
        can insist. It still stops rather than arguing."""
        fit = assistant.tailor_rules("fit")
        self.assertIn("not an estimate", fit)
        self.assertIn("Keep every fact and", fit)
        self.assertIn("Do not argue and do not suggest again", fit)

    def test_trimming_does_not_cut_the_posting_out_of_the_cv(self):
        """Fitting one page and matching the posting's language pull against
        each other, and the trim has no way of knowing which words were doing
        work. The coverage note already said to keep them, and lost: this step
        sits nearer to where the model writes, and it used to say that a word
        carrying no fact and no number was there to drop."""
        fit = assistant.tailor_rules("fit")
        self.assertIn("They are not padding", fit)
        self.assertIn("Shorten around them", fit)
        self.assertIn("is not a trim at all", fit)
        self.assertNotIn("Drop the words that carry none", fit)
        # And the list it is pointing at is the one the coverage note names.
        self.assertIn("shorten around them rather than through them",
                      assistant.coverage_note([], ["supply chain"]))

    def test_a_no_is_not_asked_again(self):
        task = assistant.tailor_rules("cast", declined=["sections.1.entries.3"])
        self.assertIn("already said no", task)
        self.assertIn("`sections.1.entries.3`", task)
        self.assertIn("in any wording", task)
        self.assertEqual(assistant.declined_note([]), "")

    def test_a_later_round_is_told_what_the_user_already_settled(self):
        """A resolved proposal reaches the model as a marker, so it can see
        that a field was changed and not what it was changed to. Without the
        list it rewrote settled fields again, differently, every round."""
        plain = assistant.tailor_rules("wording")
        named = assistant.tailor_rules("wording", settled=["sections.0.text"])
        self.assertNotIn("Leave them as they are", plain)   # nothing to leave alone yet
        self.assertIn("`sections.0.text`", named)
        self.assertIn("Leave them as they are", named)
        # The first round has nothing to leave alone yet.
        self.assertEqual(assistant.settled_note([]), "")

    def test_a_later_round_works_from_the_answer_just_given(self):
        first, again = assistant.tailor_rules("cast"), assistant.tailor_rules("again")
        self.assertIn("pasted the posting", first)
        self.assertIn("just answered one of the gaps", again)
        self.assertIn("nothing that is already in the", again)

    def test_a_gaps_block_is_a_card_for_numbering(self):
        """The editor numbers cards to remember which were resolved. A gaps
        block takes a number there, so the server has to count it the same way
        or a later proposal's verdict lands on the wrong block."""
        content = ('```gaps\nDo you have X\n```\n'
                   '```proposal\n{"path": "a", "text": "b"}\n```')
        self.assertEqual(assistant._strip_resolved(content, {"1": "accepted"}),
                         '```gaps\nDo you have X\n```\n(proposal already applied)')
        self.assertEqual(assistant._strip_resolved(content, {"0": "accepted"}), content)

    def test_missing_terms_reach_the_model_with_a_guard(self):
        note = assistant.coverage_note(["product management", "roadmap"])
        self.assertIn("product management", note)
        self.assertIn("honestly true", note)          # not a licence to stuff
        self.assertIn("never add a word to score a match", note.lower())
        self.assertEqual(assistant.coverage_note([]), "")

    def test_terms_already_on_the_page_are_protected(self):
        """Trimming to one page and matching the posting pull against each other.

        A rewrite that reads better can quietly cost the user the search, so
        the words already doing work are named and the trim is told to go
        around them.
        """
        note = assistant.coverage_note(["roadmap"], ["product strategy", "cross functional"])
        self.assertIn("already uses", note)
        self.assertIn("product strategy", note)
        self.assertIn("shorten around them", note)
        self.assertIn("roadmap", note)                 # still says what is missing
        self.assertEqual(assistant.coverage_note([], []), "")

    def test_fit_reply_is_validated_before_it_is_trusted(self):
        """A screening reply is three fields, and nothing else gets through."""
        good = assistant.parse_fit('{"score": 72, "strengths": ["data products", "x", "y", "z"],'
                                   ' "gaps": ["no B2B"], "note": "ignored"}')
        self.assertEqual(good["score"], 72)
        self.assertEqual(len(good["strengths"]), 3)       # capped
        self.assertNotIn("note", good)                    # nothing else survives
        for bad in ('{"score": 140}', '{"score": "high"}', "not json", '[1, 2]'):
            with self.assertRaises(assistant.ImportFailed):
                assistant.parse_fit(bad)

    def test_fit_request_carries_the_posting_and_the_document(self):
        body = assistant.fit_request("cv", self.cv, "Senior PM at Acme")
        self.assertIn("Senior PM at Acme", body)
        self.assertIn(self.cv["name"], body)

    def test_the_non_negotiables_come_before_everything_else(self):
        """Rules the user has had to repeat belong above the style file."""
        prompt = assistant.system_prompt("cover", {"name": "Joel", "paragraphs": []},
                                         voice_md="Write however you like.")
        head = prompt[:prompt.index("How to answer")]
        self.assertIn("No dashes as punctuation", head)
        self.assertIn("introducing the user by\n  name", head)
        self.assertIn("[Company]", head)
        self.assertIn("Never name an internal team", head)

    def test_the_prompt_says_the_document_is_live(self):
        """A model that thinks its copy is stale asks the user to paste it back."""
        prompt = assistant.system_prompt("cv", self.cv)
        self.assertIn("as it stands right now", prompt)
        self.assertIn("Never say you cannot see the document", prompt)

    def test_the_placeholder_rule_is_stated_once(self):
        """One instruction in one place.

        A model that meets the same rule twice, in two wordings, satisfies
        whichever it read last. The protocol carries the instruction; the
        reasoning for it lives in the user's Voice file.
        """
        prompt = assistant.system_prompt("cover", {"name": "Joel", "paragraphs": []})
        rules = prompt.split("## The cover letter")[0]
        self.assertEqual(rules.count("[Company]"), 1)
        self.assertIn("[Role]", rules)

    def test_system_prompt_explains_how_to_remove_a_bullet(self):
        prompt = assistant.system_prompt("cv", self.cv)
        self.assertIn('"remove": true', prompt)
        self.assertIn("renumbers", prompt)   # one removal at a time

    def test_system_prompt_offers_an_answer_block_for_form_questions(self):
        prompt = assistant.system_prompt("cv", self.cv)
        self.assertIn("```answer", prompt)
        self.assertIn("application form", prompt)

    def test_system_prompt_offers_a_voice_block_apart_from_notes(self):
        prompt = assistant.system_prompt("cv", self.cv)
        self.assertIn("```voice", prompt)
        self.assertIn("how to write", prompt)
        self.assertIn("voice for how they want", prompt)

    def test_resolved_proposals_collapse_to_a_marker(self):
        turns = [{"role": "assistant", "resolved": {"0": "accepted"},
                  "content": 'Done.\n```proposal\n{"path": "name", "text": "X"}\n```\nSame.'}]
        sent = assistant.condense(turns)
        self.assertNotIn('"path"', sent[0]["content"])
        self.assertIn("(proposal already applied)", sent[0]["content"])
        self.assertIn("Done.", sent[0]["content"])

    def test_open_proposals_are_kept_whole(self):
        turns = [{"role": "assistant",
                  "content": '```proposal\n{"path": "name", "text": "X"}\n```'}]
        self.assertIn('"path"', assistant.condense(turns)[0]["content"])

    def test_card_keys_match_the_editor_numbering(self):
        """An answer block is not a card, so it does not consume a key."""
        content = ('```answer\nhello\n```\n'
                   '```proposal\n{"path": "name", "text": "X"}\n```')
        turns = [{"role": "assistant", "content": content, "resolved": {"0": "dismissed"}}]
        sent = assistant.condense(turns)[0]["content"]
        self.assertIn("(proposal declined)", sent)
        self.assertNotIn('"path"', sent)

    def test_notes_cards_are_never_stripped(self):
        turns = [{"role": "assistant", "resolved": {"0": "accepted"},
                  "content": "```notes\nA real thing I did.\n```"}]
        self.assertIn("A real thing I did.", assistant.condense(turns)[0]["content"])

    def test_window_keeps_the_last_turns_and_opens_on_the_user(self):
        turns = []
        for i in range(60):
            turns += [{"role": "user", "content": f"q{i}"},
                      {"role": "assistant", "content": f"a{i}"}]
        sent = assistant.condense(turns, window=40)
        self.assertEqual(len(sent), 40)
        self.assertEqual(sent[0]["role"], "user")
        self.assertEqual(sent[-1]["content"], "a59")

    def test_window_drops_a_leading_assistant_turn(self):
        turns = [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"},
                 {"role": "user", "content": "u2"}, {"role": "assistant", "content": "a2"}]
        sent = assistant.condense(turns, window=3)
        self.assertEqual([t["role"] for t in sent], ["user", "assistant"])
        self.assertEqual(sent[0]["content"], "u2")

    def test_a_posting_is_named_and_put_onto_a_known_track(self):
        seen = assistant.parse_job(
            '{"company": "Acme", "role": "Backend Engineer", "track": "engineering"}')
        self.assertEqual(seen["company"], "Acme")
        self.assertEqual(seen["role"], "Backend Engineer")
        self.assertEqual(seen["track"], "Engineering")          # normalised to the set
        self.assertEqual(assistant.parse_job(
            '{"company": "A", "role": "B", "track": "Wizard"}')["track"], "Other")
        self.assertEqual(assistant.parse_job(
            '{"company": "A", "role": "B"}')["track"], "Other")

    def test_the_job_prompt_lists_the_tracks(self):
        prompt = assistant.job_prompt()
        self.assertIn("Product, Engineering, Data, Design, Other", prompt)

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
    """Voice (voice.md) and the shared experience bank (notes.md)."""

    def setUp(self):
        self.cv = json.loads((ROOT / "cv.json").read_text())

    def prompt(self, **kwargs):
        return assistant.system_prompt("cv", self.cv, **kwargs)

    def test_style_and_notes_are_appended_under_their_own_headings(self):
        prompt = self.prompt(voice_md="Ban the word 'spearheaded'.",
                             notes_md="Ran weekly triage for the Amazon CXI dataset.")
        self.assertIn("## The user's voice", prompt)
        self.assertIn("Ban the word 'spearheaded'.", prompt)
        self.assertIn("## Experience notes", prompt)
        self.assertIn("Ran weekly triage for the Amazon CXI dataset.", prompt)

    def test_protocol_rules_stay_first(self):
        prompt = self.prompt(voice_md="Style rules here.", notes_md="Notes here.")
        self.assertTrue(prompt.startswith("You are a careful resume"))
        self.assertLess(prompt.index("proposal"), prompt.index("## The user's voice"))

    def test_missing_style_and_notes_add_no_headings(self):
        plain = assistant.system_prompt("cv", self.cv)
        self.assertEqual(plain, self.prompt(voice_md="", notes_md="   "))
        self.assertNotIn("## The user's voice", plain)
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
            path = Path(tmp) / "voice.md"
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
            "cv", self.cv, "JOB TEXT", voice_md="MY STYLE", notes_md="MY NOTES")
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
        whole = assistant.system_prompt("cv", self.cv, "JD", voice_md="S", notes_md="N")
        stable, volatile = assistant.prompt_parts("cv", self.cv, "JD", voice_md="S", notes_md="N")
        self.assertEqual(whole, f"{stable}\n\n{volatile}")

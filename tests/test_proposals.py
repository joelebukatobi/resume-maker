"""What a proposal is allowed to do to the open document.

The rules live in the browser, because that is where the unsaved document
lives, so they are exercised by running that part of the file in node. The one
that matters is removal: a bullet can leave its list, and a field that the
document's shape requires cannot be deleted at all. Deleting one left a
labelled skills line as ``{"label": "Programming"}``, and every preview, save
and export after it failed on a missing string.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resume_maker.model import CV, ContentError  # noqa: E402

# The helpers, lifted out of chat.js: the rest of the file reaches for the DOM
# and the network as soon as it is evaluated.
START, END = "function pathParts(", "async function fields("
# negative() sits with the cards, past that slice, and needs nothing from the DOM.
EXTRA_START, EXTRA_END = "function negative(question) {", "function gapsCard("


def helpers() -> str:
    source = (ROOT / "static" / "chat.js").read_text(encoding="utf-8")
    return (source[source.index(START):source.index(END)]
            + source[source.index(EXTRA_START):source.index(EXTRA_END)])


def run(script: str) -> object:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node is not installed")
    done = subprocess.run([node, "-e", helpers() + script],
                          capture_output=True, text=True, timeout=30)
    if done.returncode:
        raise AssertionError(done.stderr.strip())
    return json.loads(done.stdout)


CV_JSON = json.dumps({
    "name": "Joel",
    "sections": [
        {"heading": "SKILLS", "type": "labeled", "items": [
            {"label": "Product", "text": "Product Strategy."},
            {"label": "Programming", "text": "TypeScript."},
        ]},
        {"heading": "EXPERIENCE", "type": "entries", "entries": [
            {"left": "Amazon", "right": "Bellevue", "sub_left": "PM",
             "sub_right": "May 2025 - August 2025",
             "bullets": ["Owned the roadmap.", "Ran discovery."]},
        ]},
    ],
})


class RemovalTests(unittest.TestCase):
    def test_a_bullet_can_leave_its_list(self):
        left = run(f"""
          const cv = {CV_JSON};
          const path = "sections.1.entries.0.bullets.0";
          const allowed = canRemove(cv, path);
          removePath(cv, path);
          console.log(JSON.stringify(
            {{allowed, bullets: cv.sections[1].entries[0].bullets}}));
        """)
        self.assertTrue(left["allowed"])
        self.assertEqual(left["bullets"], ["Ran discovery."])

    def test_a_required_field_cannot_be_removed(self):
        """The bug, from the direction the user met it."""
        for path in ("sections.0.items.1.text", "sections.1.entries.0.sub_right", "name"):
            with self.subTest(path=path):
                self.assertFalse(run(
                    f'console.log(JSON.stringify(canRemove({CV_JSON}, "{path}")))'))

    def test_what_removing_one_used_to_cost(self):
        """Why the guard is in the browser and not only in the prompt."""
        data = json.loads(CV_JSON)
        del data["sections"][0]["items"][1]["text"]
        with self.assertRaises(ContentError) as caught:
            CV.from_mapping(data)
        self.assertIn("items[1].text", str(caught.exception))

    def test_a_whole_labelled_line_is_still_removable(self):
        """It sits in a list, so taking it out leaves a valid document."""
        after = run(f"""
          const cv = {CV_JSON};
          removePath(cv, "sections.0.items.1");
          console.log(JSON.stringify(cv.sections[0].items));
        """)
        self.assertEqual([item["label"] for item in after], ["Product"])
        CV.from_mapping({**json.loads(CV_JSON), "sections": [
            {"heading": "SKILLS", "type": "labeled", "items": after},
        ]})


class GapAnswerTests(unittest.TestCase):
    """A no is a fact. Until it was written down the same gap came back on
    every posting, because nothing in the notes said the answer."""

    def test_a_keyword_question_becomes_a_plain_no(self):
        """The ask card leads with the keyword, then the question. The no that
        goes into the notes is the question's answer, without the label."""
        said = run("""
          console.log(JSON.stringify(declineLine(
            "Compliance-first mindset. Have you worked somewhere rules limited what you could do?")));
        """)
        self.assertEqual(said, "I have not worked somewhere rules limited what you could do.")

    def test_a_keyword_question_led_by_a_colon_becomes_a_plain_no_too(self):
        """The spec's own example leads with a colon. Left on, the no written
        to the notes was "Not mine. Compliance-first mindset: have you…"."""
        said = run("""
          console.log(JSON.stringify(declineLine(
            "Compliance-first mindset: have you worked somewhere rules limited what you could do?")));
        """)
        self.assertEqual(said, "I have not worked somewhere rules limited what you could do.")

    def test_a_question_becomes_a_statement(self):
        answered = run("""
          console.log(JSON.stringify([
            "Do you have experience building with Drupal?",
            "Have you applied WCAG 2.2 standards or run accessibility audits?",
            "Have you ever shipped a consumer mobile app?",
            "Are you able to work in the EU?",
            "Tell me about Kubernetes",
          ].map(negative)));
        """)
        self.assertEqual(answered, [
            "I do not have experience building with Drupal.",
            "I have not applied WCAG 2.2 standards or run accessibility audits.",
            "I have never shipped a consumer mobile app.",
            "I am not able to work in the EU.",
            "Not mine. Tell me about Kubernetes.",
        ])


# The loop's two guards live beside the round, past the card helpers.
LOOP_START, LOOP_END = "function settledPaths() {", "// Naming a new application reloads"


def loop_helpers() -> str:
    source = (ROOT / "static" / "chat.js").read_text(encoding="utf-8")
    block = source[source.index(LOOP_START):source.index(LOOP_END)]
    return "const BLOCK = /```(proposal|answer|notes|voice|gaps)\\s*([\\s\\S]*?)```/g;\n" + block


def run_loop(script: str) -> object:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node is not installed")
    done = subprocess.run([node, "-e", loop_helpers() + script],
                          capture_output=True, text=True, timeout=30)
    if done.returncode:
        raise AssertionError(done.stderr.strip())
    return json.loads(done.stdout)


GAPS = '```gaps\nDo you have Drupal?\n```'
PROPOSAL = '```proposal\n{"path": "sections.0.text", "text": "A tighter summary."}\n```'


class LoopTests(unittest.TestCase):
    """What a later round must leave alone.

    A round used to follow any accepted note, which meant saving a fact in an
    ordinary conversation set the whole pass going and rewrote work already
    settled. It is now offered on the accepted card and waits to be pressed,
    so what remains to test here is what it is told.
    """

    def chat(self, messages):
        return json.dumps({"messages": messages})

    def test_a_round_is_offered_rather_than_taken(self):
        source = (ROOT / "static" / "chat.js").read_text(encoding="utf-8")
        self.assertIn("Tailor again with this", source)
        self.assertNotIn("tailorAfterNotes", source)   # nothing decides for the user

    def test_the_fields_the_user_decided_on_are_named(self):
        settled = self.chat([
            {"role": "assistant", "content": PROPOSAL, "resolved": {"0": "accepted"}},
            {"role": "assistant", "content": GAPS + "\n" + PROPOSAL.replace(
                "sections.0.text", "sections.1.text"), "resolved": {"1": "dismissed"}},
        ])
        self.assertEqual(
            run_loop(f"chat = {settled}; console.log(JSON.stringify(settledPaths()))"),
            ["sections.0.text"])      # the dismissed one is not settled


LETTER_JSON = json.dumps({
    "name": "Joel",
    "contact": [{"text": "you@example.com"}],
    "greeting": "Dear [Company] Hiring Team,",
    "paragraphs": ["I am applying for the [Role] at [Company]."],
    "closing": "Thank you for your consideration.",
    "signoff": "Sincerely,",
})


class LetterTests(unittest.TestCase):
    """A letter has to be able to gain a paragraph.

    Appending was allowed only to a list called ``bullets``, so a proposal for
    the letter's next paragraph was refused by the editor. With the base letter
    reduced to a one-paragraph skeleton that left the model one writable
    paragraph and an opening rule it could not break, so it stopped proposing
    and offered the whole letter to copy by hand instead.
    """

    def test_the_next_paragraph_can_be_written(self):
        checks = run(f"""
          const letter = {LETTER_JSON};
          console.log(JSON.stringify({{
            next: canSet(letter, "paragraphs.1"),
            existing: canSet(letter, "paragraphs.0"),
            pastTheEnd: canSet(letter, "paragraphs.4"),
          }}));
        """)
        self.assertEqual(checks, {"next": True, "existing": True, "pastTheEnd": False})

    def test_a_list_of_more_than_lines_stays_closed(self):
        """contact holds objects: a string appended there malforms the letter."""
        self.assertFalse(run(
            f'console.log(JSON.stringify(canSet({LETTER_JSON}, "contact.1")))'))

    def test_a_paragraph_can_leave_the_letter(self):
        after = run(f"""
          const letter = {LETTER_JSON};
          setPath(letter, "paragraphs.1", "A second paragraph.");
          const allowed = canRemove(letter, "paragraphs.1");
          removePath(letter, "paragraphs.1");
          console.log(JSON.stringify({{allowed, paragraphs: letter.paragraphs}}));
        """)
        self.assertTrue(after["allowed"])
        self.assertEqual(after["paragraphs"], ["I am applying for the [Role] at [Company]."])


# The round itself: the steps, and what a no does to them.
ROUND_START = "const TAILOR_ASK ="
ROUND_END = "// Every field the user has decided on"


def round_helpers() -> str:
    source = (ROOT / "static" / "chat.js").read_text(encoding="utf-8")
    block = source[source.index(ROUND_START):source.index(ROUND_END)]
    # The pieces the round leans on that live in the DOM or the network.
    return ("const BLOCK = /```(proposal|answer|notes|voice|gaps|ask)\\s*([\\s\\S]*?)```/g;\n"
            "let chat = {messages: [], job_description: 'A posting'};\n"
            "let documentMode = 'cv', chatController = null;\n"
            "const agentModel = () => ({provider: 'p', model: 'm'});\n"
            "const settledPaths = () => [];\n"
            "const renderChat = () => {}, refreshLabels = () => {},\n"
            "      saveHistory = () => {}, setBusy = () => {},\n"
            "      setStatus = () => {}, showProgress = () => {},\n"
            "      chatBody = (s, a, extra) => extra, showTailorButton = () => {};\n"
            "let keywordState = { current: true, skipped: [] };\n"
            "const APP = 'test', api = async () => ({ json: async () => keywordState });\n"
            "const $ = () => ({dataset: {}});\n"
            "const F = String.fromCharCode(96, 96, 96);\n"
            "const CARD = F + 'proposal\\n"
            '{\"path\": \"sections.0.entries.1\", \"show\": true}'
            "\\n' + F;\n"
            "const SUGGESTION = 'Shall I?\\n' + CARD;\n"
            + block)


def run_round(script: str) -> object:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node is not installed")
    done = subprocess.run([node, "-e", round_helpers() + script],
                          capture_output=True, text=True, timeout=30)
    if done.returncode:
        raise AssertionError(done.stderr.strip())
    return json.loads(done.stdout)


class RoundTests(unittest.TestCase):
    """The round is a conversation of one suggestion at a time.

    It used to send one turn that answered with a page of cards, which was
    work done before the user had agreed to any of it, and it started at the
    wording because roles could not be turned on and off when it was written.
    """

    def test_the_steps_run_in_order_and_stop_when_each_is_done(self):
        asked = run_round(f"""
          const asked = [];
          // Each step offers one card, then has nothing left to say.
          const offered = {{}};
          streamReply = async (reply, body) => {{
            asked.push(body.phase);
            const first = !offered[body.phase];
            offered[body.phase] = true;
            reply.content = first ? SUGGESTION : "Nothing left here.";
          }};
          // The user says yes to every card, the moment it is offered.
          const tick = setInterval(() => {{ if (awaitingCard) cardResolved("accepted"); }}, 1);
          runTailor().then(() => {{
            clearInterval(tick);
            console.log(JSON.stringify(asked));
          }});
        """)
        self.assertEqual(asked, ["cast", "cast", "wording", "wording", "fit", "fit"])

    def test_a_no_is_carried_into_every_later_suggestion(self):
        """A no is an answer. Offered the same thing two cards later, the
        round stops being a conversation and becomes a form."""
        seen = run_round(f"""
          const seen = [];
          let n = 0;
          streamReply = async (reply, body) => {{
            seen.push(body.declined.slice());
            reply.content = (n += 1) < 3 ? SUGGESTION : "Nothing left.";
          }};
          // What the card itself passes back: the path it offered.
          const tick = setInterval(() => {{
            if (awaitingCard) cardResolved("dismissed", "sections.0.entries.1");
          }}, 1);
          runTailor().then(() => {{
            clearInterval(tick);
            console.log(JSON.stringify(seen));
          }});
        """)
        self.assertEqual(seen[0], [])
        self.assertEqual(seen[1], ["sections.0.entries.1"])
        self.assertEqual(seen[2], ["sections.0.entries.1", "sections.0.entries.1"])

    def test_a_keyword_question_waits_for_its_answer(self):
        """An ask card is one keyword, answered or declined, and the round
        carries on down the list afterwards rather than stopping there."""
        seen = run_round("""
          const seen = [];
          const ASK = F + 'ask\\n{"keyword": "sql", "question": "Have you queried data?"}\\n' + F;
          let n = 0;
          streamReply = async (reply, body) => {
            seen.push(body.phase);
            n += 1;
            reply.content = (body.phase === "wording" && n === 2) ? ASK : "Nothing left.";
          };
          const tick = setInterval(() => { if (awaitingCard) cardResolved("dismissed", "sql"); }, 1);
          runTailor().then(() => { clearInterval(tick); console.log(JSON.stringify(seen)); });
        """)
        self.assertEqual(seen, ["cast", "wording", "wording", "fit"])

    def test_a_round_starts_with_the_skips_already_given(self):
        seen = run_round("""
          keywordState = { current: true, skipped: ["sections.0.text"] };
          let first = null;
          streamReply = async (reply, body) => {
            if (first === null) first = body.declined.slice();
            reply.content = "Nothing left.";
          };
          runTailor().then(() => console.log(JSON.stringify(first)));
        """)
        self.assertEqual(seen, ["sections.0.text"])

    def test_a_question_ends_its_step(self):
        """A gaps block is waiting on the user, not on a button."""
        asked = run_round("""
          const asked = [];
          streamReply = async (reply, body) => {
            asked.push(body.phase);
            reply.content = F + "gaps\\nHave you used Zapier?\\n" + F;
          };
          runTailor().then(() => console.log(JSON.stringify(asked)));
        """)
        self.assertEqual(asked, ["cast", "wording", "fit"])

    def test_a_round_cannot_run_twice_at_once(self):
        """A second paste while the first round is mid-conversation would
        interleave two sets of suggestions over one document."""
        calls = run_round("""
          let calls = 0;
          streamReply = async (reply) => { calls += 1; reply.content = "Nothing left."; };
          const first = runTailor();
          const second = runTailor();          // refused while the first runs
          Promise.all([first, second]).then(() => console.log(JSON.stringify(calls)));
        """)
        self.assertEqual(calls, 3)   # three steps, once each

    def test_a_failed_step_ends_the_round_and_keeps_the_turn(self):
        """What the user hit: a step failed, its two messages were removed,
        the round counted that as the step finishing and started the next one,
        and three steps later the whole conversation was gone."""
        after = run_round("""
          let calls = 0;
          streamReply = async () => { calls += 1; throw new Error("provider said no"); };
          runTailor().then(() => console.log(JSON.stringify({
            calls, left: chat.messages.length,
            said: (chat.messages[1] || {}).content,
          })));
        """)
        self.assertEqual(after["calls"], 1)            # it stops at the first failure
        self.assertEqual(after["left"], 2)             # the turn stays, with a reason
        self.assertIn("provider said no", after["said"])

    def test_stopping_stops_the_round(self):
        """Abort used to read as the step finishing, so Stop moved the round
        on to the next step instead of ending it."""
        after = run_round("""
          let calls = 0;
          streamReply = async () => {
            calls += 1;
            const stop = new Error("aborted");
            stop.name = "AbortError";
            throw stop;
          };
          runTailor().then(() => console.log(JSON.stringify(
            { calls, left: chat.messages.length })));
        """)
        self.assertEqual(after, {"calls": 1, "left": 0})

    def test_the_card_hands_back_what_it_offered(self):
        """The round only knows what to stop offering because the card says."""
        source = (ROOT / "static" / "chat.js").read_text(encoding="utf-8")
        self.assertIn("cardResolved(result, parsed.path)", source)
        self.assertIn("index === chat.messages.length - 1", source)   # not an old card


class CutOffTests(unittest.TestCase):
    """A reply that ends inside a block.

    While it is streaming it is a card on its way. Once the stream is over it
    is a reply that was cut off, and it used to read the same either way, so
    the panel said "Writing a proposal…" for ever, including after a reload,
    with nothing running and nothing to press.
    """

    def test_the_two_cases_read_differently(self):
        source = (ROOT / "static" / "chat.js").read_text(encoding="utf-8")
        self.assertIn('if (open !== -1 && streaming)', source)
        self.assertIn('text: "This reply was cut off"', source)
        self.assertIn("Try again", source)

    def test_retrying_a_round_runs_the_round(self):
        """Nobody typed the round's turn, so there is no question to resend."""
        source = (ROOT / "static" / "chat.js").read_text(encoding="utf-8")
        retry = source[source.index("async function retryTurn("):source.index("function renderChat(")]
        self.assertIn("message.pipeline", retry)
        self.assertIn("runTailor()", retry)
        self.assertIn("splice(index - 1, 2)", retry)   # the cut-off pair goes


class CopyTests(unittest.TestCase):
    """Every button says what pressing it does, in one or two words.

    "Not mine" said nothing, and "Accept" read the same on a CV change and on a
    fact for the notes, which change different things."""

    def setUp(self):
        self.chat = (ROOT / "static" / "chat.js").read_text(encoding="utf-8")
        self.css = (ROOT / "static" / "chat.css").read_text(encoding="utf-8")

    def test_the_new_words_are_on_the_buttons(self):
        for word in ('text: "Use this"', 'text: "Keep this"', 'text: "Skip"',
                     'text: "I have…"', 'text: "I haven\'t"'):
            with self.subTest(word=word):
                self.assertIn(word, self.chat)

    def test_the_old_words_are_gone(self):
        for word in ('text: "Not mine"', 'text: "Add to Notes"', 'text: "Add to Voice"',
                     'text: "Accept"', 'text: "Dismiss"'):
            with self.subTest(word=word):
                self.assertNotIn(word, self.chat)

    def test_the_two_voices_and_the_notes_look_different(self):
        self.assertIn(".chat-msg.user { align-self: flex-end; max-width: 88%; background: #f4f1ea; color: #111;", self.css)
        self.assertIn(".chat-msg.assistant { display: flex; flex-direction: column; gap: 8px;"
                      " background: #1e1e1e;", self.css)
        self.assertIn(".chat-card.notes { background: #eef5ee; color: #1b2a1e; }", self.css)


class SettingTests(unittest.TestCase):
    def test_text_goes_only_where_text_already_is(self):
        checks = run(f"""
          const cv = {CV_JSON};
          console.log(JSON.stringify({{
            existing: canSet(cv, "sections.0.items.1.text"),
            nextBullet: canSet(cv, "sections.1.entries.0.bullets.2"),
            pastTheEnd: canSet(cv, "sections.1.entries.0.bullets.9"),
            invented: canSet(cv, "sections.9.text"),
          }}));
        """)
        self.assertEqual(checks, {"existing": True, "nextBullet": True,
                                  "pastTheEnd": False, "invented": False})


if __name__ == "__main__":
    unittest.main()

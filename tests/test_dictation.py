"""What dictation is allowed to do to the message box.

The local recorder rewrites the whole box on every pass, because each pass
re-transcribes the entire utterance. Sending does not stop it, so the sent
words used to land straight back in the box the send had just cleared, and
again on the next pass, and again, until the mic was clicked off. The rules
live in the browser, so they are exercised by running that part of the file
in node.
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

# The dictation half, lifted out of chat.js: what sits above it reaches for
# the DOM and the Web Speech API as soon as it is evaluated.
START, END = "function insertTranscript(", "function mergePcm("

# What the slice calls and the test does not care about: the audio encoding,
# the request, and the page.
STUBS = """
let listener = null, recorder = null, transcribeReady = true;
let box = "", posted = 0;
const $ = () => ({ get value() { return box; }, set value(v) { box = v; },
                   focus() {}, classList: { add() {}, remove() {} } });
const setStatus = () => {};
const mergePcm = () => [0];
const downsample = (s) => s;
const encodeWav = () => new Uint8Array(1);
const bytesToBase64 = () => "";
const api = async () => { posted += 1; return { json: async () => ({ text: HEARD }) }; };
"""


def run(script: str) -> object:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node is not installed")
    source = (ROOT / "static" / "chat.js").read_text(encoding="utf-8")
    slice_ = source[source.index(START):source.index(END)]
    done = subprocess.run([node, "-e", STUBS + slice_ + script],
                          capture_output=True, text=True, timeout=30)
    if done.returncode:
        raise AssertionError(done.stderr.strip())
    return json.loads(done.stdout)


# A session as startRecording leaves one, minus the audio graph.
SESSION = """
const HEARD = "I ran the discovery for the roadmap.";
const rec = { chunks: [1], rate: 16000, base: "", busy: false, timer: null,
              node: { disconnect() {} }, source: { disconnect() {} },
              stream: { getTracks: () => [] }, ctx: { close: async () => {} } };
recorder = rec;
"""


class CancelTests(unittest.TestCase):
    def test_a_pass_in_flight_does_not_refill_a_cleared_box(self):
        """The bug, from the direction the user met it."""
        after = run(SESSION + """
          const pass = transcribeChunk(rec);   // a pass is already running
          cancelDictation();                   // the send retires the session
          box = "";                            // and clears the box
          pass.then(() => console.log(JSON.stringify({ box, posted })));
        """)
        self.assertEqual(after["box"], "")
        self.assertEqual(after["posted"], 1)   # no second pass on the way out

    def test_a_pass_still_writes_while_the_mic_is_on(self):
        after = run(SESSION + """
          transcribeChunk(rec).then(() =>
            console.log(JSON.stringify({ box, heard: HEARD })));
        """)
        self.assertEqual(after["box"], after["heard"])

    def test_dictation_keeps_what_was_typed_before_it(self):
        after = run(SESSION.replace('base: ""', 'base: "About the roadmap:"') + """
          transcribeChunk(rec).then(() => console.log(JSON.stringify(box)));
        """)
        self.assertEqual(after, "About the roadmap: I ran the discovery for the roadmap.")

    def test_cancelling_lets_go_of_the_microphone(self):
        """A retired session still releases the device and the audio context."""
        after = run(SESSION + """
          let tracks = 0, closed = false;
          rec.stream.getTracks = () => [{ stop() { tracks += 1; } }];
          rec.ctx.close = async () => { closed = true; };
          cancelDictation();
          setTimeout(() => console.log(JSON.stringify(
            { tracks, closed, recorder, listener })), 10);
        """)
        self.assertEqual(after, {"tracks": 1, "closed": True,
                                 "recorder": None, "listener": None})


class SendTests(unittest.TestCase):
    """The guard is only worth anything if the send reaches for it."""

    def test_sending_retires_dictation_first(self):
        source = (ROOT / "static" / "chat.js").read_text(encoding="utf-8")
        send = source[source.index("async function sendMessage() {"):]
        self.assertIn("cancelDictation();", send[:send.index("\n}")])


if __name__ == "__main__":
    unittest.main()

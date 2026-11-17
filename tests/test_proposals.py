"""Why a proposal may never delete a field the document's shape requires.

Deleting one left a labelled skills line as ``{"label": "Programming"}``, and
every preview, save and export after it failed on a missing string.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resume_maker.model import CV, ContentError  # noqa: E402

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
    def test_what_removing_one_used_to_cost(self):
        """Why the guard is in the browser and not only in the prompt."""
        data = json.loads(CV_JSON)
        del data["sections"][0]["items"][1]["text"]
        with self.assertRaises(ContentError) as caught:
            CV.from_mapping(data)
        self.assertIn("items[1].text", str(caught.exception))


if __name__ == "__main__":
    unittest.main()


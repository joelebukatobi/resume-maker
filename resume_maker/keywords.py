"""The keyword list for one posting, kept beside the application.

Read once by a model, when the posting arrives, and then frozen: the ring
counts against it and nothing re-reads it unless asked. The list used to live
in the browser and was asked for again on every press, so the same posting
gave fifteen terms, then twelve, then ten, and the score moved while the CV
stood still.

The file also holds the map: for each keyword, where in the notes the user did
that thing, and a line that says it in the posting's words. It is derived from
the notes and the posting, so losing it costs a model call and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def posting_id(job_description: str) -> str:
    """Which posting a list was read from. Whitespace is not a new posting."""
    text = " ".join(str(job_description or "").split())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load(path: Path) -> dict[str, Any]:
    """The stored list, or an empty one if there is none or it is unreadable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    terms = data.get("keywords")
    entries = data.get("map")
    skipped = data.get("skipped")
    return {
        "posting": str(data.get("posting") or ""),
        "keywords": [str(t) for t in terms] if isinstance(terms, list) else [],
        "map": entries if isinstance(entries, dict) else {},
        "skipped": [str(t) for t in skipped] if isinstance(skipped, list) else [],
    }


def save(path: Path, *, posting: str, terms: list[str],
         entries: dict[str, dict], skipped: list[str] = ()) -> None:
    """Write the list, the posting it belongs to and its map, all at once.

    ``skipped`` is what the user pressed Skip on during a round for this
    posting. It lasted one round, so the next round offered the same thing
    again. It belongs to the posting, so a fresh list starts without it.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"posting": posting_id(posting), "keywords": list(terms),
                       "map": entries, "skipped": list(skipped)},
                      indent=2, ensure_ascii=False)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text + "\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def is_current(stored: dict[str, Any], job_description: str) -> bool:
    """Whether the stored list was read from this posting.

    A posting replaced in this tab, or in another one, leaves the old list
    behind. Counting against it would score the CV against a different job.
    """
    return bool(stored.get("keywords")) and stored.get("posting") == posting_id(job_description)

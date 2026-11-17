"""Finding his evidence by meaning, and judging it with his own model.

The gap check used to find candidate passages by shared word stems and let Jev
judge them. Measured on the 372 requirements his saved CVs missed (2026-11-10):
word search never put "At Amazon I wrote epics and user stories" in front of
"story writing", and Jev said yes to loosely related paragraphs about a third of
the time ("data lineage" from a company called Lagos Data Intelligence).

So candidates come from a local embedding model (bge-base, chosen 2026-11-10),
which finds a passage by what it means. Jev judges them when it has a key; with
no Jev key, the model he has connected judges, and must quote the words that
prove it. A quote that is not in the evidence is a no.

The embedding model is optional. Without ``fastembed`` installed, or if it cannot
load, :func:`nearest` returns None and the caller keeps its word search.
"""

from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from . import llm

EMBED_MODEL = "BAAI/bge-base-en-v1.5"  # 110M params, ~440MB, CPU
# fastembed's default cache is under /tmp, which a reboot clears.
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "resume-maker" / "fastembed"
# A heading-sized line ("Operations, support and servers") sits close to every
# topic it names and outranked real evidence in the measurement.
MIN_WORDS = 6
BATCH = 15


class JudgeError(RuntimeError):
    pass


_LOCK = threading.Lock()  # the startup warm-up and a request can both get here first
_loaded: list[Any] = []


def _model() -> Any:
    # ponytail: a failed load (offline on first run) is remembered until restart.
    with _LOCK:
        if not _loaded:
            try:
                from fastembed import TextEmbedding
                _loaded.append(TextEmbedding(EMBED_MODEL, cache_dir=str(CACHE)))
            except Exception as exc:  # not installed, or the download failed
                print(f"[evidence] embedding model not loaded, using word search: {exc}", flush=True)
                _loaded.append(None)
        return _loaded[0]


# The question's own words. Measured: "Have you done market research?" did not
# reach the capstone passage in the top 8; "market research" put it 3rd.
_ASKING = {"do", "does", "did", "you", "your", "have", "has", "had", "any", "experience",
           "exposure", "with", "in", "ever", "done", "used", "use", "worked", "work", "on",
           "built", "build", "given", "written", "led", "run", "can", "are", "familiar",
           "been", "a", "an", "the", "of", "or", "such", "as"}


def topic(question: str) -> str:
    words = [w for w in re.findall(r"[\w./+#-]+", question) if w.casefold() not in _ASKING]
    return " ".join(words) or question


# ponytail: vectors live for the process; a few hundred paragraphs embed in seconds.
_vectors: dict[str, Any] = {}


def nearest(texts: list[str], query: str, limit: int) -> list[int] | None:
    """Indices of the ``limit`` texts closest in meaning to ``query``, best
    first, skipping heading-sized ones. None when no embedding model loaded."""
    model = _model()
    if model is None:
        return None
    import numpy as np
    keep = [i for i, t in enumerate(texts) if len(t.split()) >= MIN_WORDS]
    if not keep:
        return []
    new = [texts[i] for i in keep if texts[i] not in _vectors]
    for text, vector in zip(new, model.embed(new)):
        _vectors[text] = vector
    query_vector = next(iter(model.query_embed([topic(query)])))
    sims = np.array([_vectors[texts[i]] for i in keep]) @ query_vector
    return [keep[int(j)] for j in np.argsort(-sims)[:limit]]


RULES = """You check a job candidate's own notes for evidence of what a job asks for.
Each item is a question about the candidate and some evidence they wrote.
Answer "yes" only if the evidence states that the candidate actually did or used
the thing asked about. Not a related topic, not a shared word, not a company or
project whose name contains the word, not a skills list naming something nearby.
If the evidence says the candidate has not done it, or has only started learning
it, answer "no". If it says neither, answer "none". For "yes", copy the exact
words from the evidence that prove it, under 25 words, unchanged.
Reply with a JSON array only: [{"i": 0, "verdict": "yes|no|none", "quote": "..."}]"""


def _plain(text: str) -> str:
    return " ".join(text.replace("*", "").casefold().split())


def quoted(quote: str, evidence: str) -> bool:
    """The quote is really in the evidence. A model may elide with "..."; each
    piece must then be there."""
    pieces = [p.strip(" .,;:\"'") for p in re.split(r"\.\.\.|…", _plain(quote))]
    pieces = [p for p in pieces if len(p) >= 3]
    return bool(pieces) and all(p in _plain(evidence) for p in pieces)


def _verdicts(raw: str, batch: list[tuple[str, str]]) -> list[tuple[str, float]]:
    found = re.search(r"\[.*\]", raw, re.S)
    try:
        items = json.loads(found.group(0)) if found else None
    except json.JSONDecodeError:
        items = None
    if not isinstance(items, list):
        raise JudgeError("the model did not return a verdict list")
    by_index = {}
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("i"), int):
            by_index[item["i"]] = item
    out = []
    for i, (_, evidence) in enumerate(batch):
        item = by_index.get(i, {})
        verdict = item.get("verdict")
        if verdict == "yes" and quoted(str(item.get("quote") or ""), evidence):
            out.append(("yes", 1.0))
        elif verdict == "no":
            out.append(("no", 1.0))
        else:
            out.append(("says_nothing", 1.0))
    return out


def judge(provider: str, model: str) -> Callable[[list[tuple[str, str]]], list[tuple[str, float]]]:
    """A judge with the shape of ``jev.answers``, run on his connected model."""
    def one(batch: list[tuple[str, str]]) -> list[tuple[str, float]]:
        prompt = "\n\n".join(f"### item {i}\nQuestion: {q}\nEvidence:\n{e}"
                             for i, (q, e) in enumerate(batch))
        try:
            raw = llm.complete(provider, model, RULES, prompt, temperature=0)
        except llm.LLMError as exc:
            raise JudgeError(str(exc)) from exc
        return _verdicts(raw, batch)

    def answers(pairs: list[tuple[str, str]]) -> list[tuple[str, float]]:
        batches = [pairs[s:s + BATCH] for s in range(0, len(pairs), BATCH)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            return [v for done in pool.map(one, batches) for v in done]
    return answers


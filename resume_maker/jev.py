"""TypeSafe's Jev, for the judgements code cannot make about his lines.

Jev answers a narrow question with a choice and a probability rather than
prose: does this notes passage back this CV line, and what kind of line is
this Additional Information item. That is all the support check needs, and it
is billed only for what is sent, so checking his whole history once costs less
than a couple of turns with the chat model.

Plain HTTP over ``urllib``; the SDK would be the product's first dependency.
The key is entered in the Models dialog (saved with the model keys, owner-only)
or comes from ``TYPESAFE_API_KEY`` in the server's environment, and goes
nowhere but the request header.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable

from . import llm

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
TIMEOUT = 60
BATCH = 20

SUPPORT = {
    "supports": "The passage states what the line claims, including any tools it names.",
    "contradicts": "The passage states something the line's claim conflicts with.",
    "says_nothing": "The passage does not state what the line claims.",
}
ANSWERS = {
    "yes": "The evidence shows he has done what the question asks about.",
    "no": "The evidence shows he has not done it.",
    "says_nothing": "The evidence does not answer the question.",
}
KINDS = {
    "authorisation": "Where he may work and whether he needs visa sponsorship.",
    "interests": "What he does or is learning outside work: hobbies, tools he is exploring.",
    "certification": "A certificate, licence or course credential he holds.",
    "self_description": "A claim about his own character or way of working, such as being "
                        "comfortable in ambiguity or a track record of something.",
}


class JevError(Exception):
    """Jev could not answer: no key, no network, or a rejected request."""


Opener = Callable[..., Any]


KEY_ENV = "TYPESAFE_API_KEY"


def key() -> tuple[str, str]:
    """The key and where it came from: "saved" in the Models dialog, else "env"."""
    saved = llm.saved_keys().get(llm.JEV, "").strip()
    if saved:
        return saved, "saved"
    env = os.environ.get(KEY_ENV, "").strip()
    return (env, "env") if env else ("", "")


def ask(state: Any, questions: dict[str, dict], *,
        opener: Opener = urllib.request.urlopen, api_key: str = "") -> dict[str, dict]:
    """The ``answers`` map for ``questions`` asked over ``state``."""
    key_ = api_key or key()[0]
    if not key_:
        raise JevError(f"add a Jev key in the Models dialog, or set {KEY_ENV}")
    request = urllib.request.Request(
        URL, method="POST",
        data=json.dumps({"model": MODEL, "state": state, "questions": questions}).encode("utf-8"),
        headers={"Authorization": f"Bearer {key_}", "Content-Type": "application/json"})
    try:
        with opener(request, timeout=TIMEOUT) as response:  # noqa: S310 - fixed https host
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise JevError(f"Jev: HTTP {exc.code} {detail}".strip()) from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise JevError(f"Jev: could not reach the API ({exc})") from exc
    answers = body.get("answers") if isinstance(body, dict) else None
    if not isinstance(answers, dict):
        raise JevError("Jev: the reply had no answers")
    return answers


def _choices(items: list[dict], instructions: str, criteria: dict[str, str],
             fallback: str, opener: Opener) -> list[tuple[str, float]]:
    """One choice per item, ``BATCH`` items to a request, in the order given."""
    out: list[tuple[str, float]] = []
    for start in range(0, len(items), BATCH):
        chunk = items[start:start + BATCH]
        questions = {f"c{i}": {"type": "choice",
                               "instructions": instructions.format(i=i),
                               "criteria": criteria}
                     for i in range(len(chunk))}
        answers = ask({"checks": chunk}, questions, opener=opener)
        for i in range(len(chunk)):
            answer = answers.get(f"c{i}") or {}
            choice = answer.get("choice")
            out.append((choice if choice in criteria else fallback,
                        float(answer.get("confidence") or 0.0)))
    return out


def support(pairs: list[tuple[str, str]], *,
            opener: Opener = urllib.request.urlopen) -> list[tuple[str, float]]:
    """For each (line, passage): supports, contradicts or says_nothing."""
    return _choices([{"line": line, "passage": passage} for line, passage in pairs],
                    "How does the notes passage `checks[{i}].passage` relate to the CV "
                    "line `checks[{i}].line`? Judge only what the passage itself states.",
                    SUPPORT, "says_nothing", opener)


def answers(pairs: list[tuple[str, str]], *,
            opener: Opener = urllib.request.urlopen) -> list[tuple[str, float]]:
    """For each (question, evidence): does it show he has what is asked."""
    return _choices([{"question": q, "evidence": e} for q, e in pairs],
                    "Does `checks[{i}].evidence`, written by or about the candidate, show "
                    "that he has the experience asked about in `checks[{i}].question`? "
                    "Judge only what the evidence itself states.",
                    ANSWERS, "says_nothing", opener)


def label(lines: list[str], *, opener: Opener = urllib.request.urlopen) -> list[str]:
    """The kind of each Additional Information line."""
    found = _choices([{"line": line} for line in lines],
                     "What kind of statement is the CV Additional Information line "
                     "`checks[{i}].line`?", KINDS, "interests", opener)
    return [kind for kind, _ in found]


def connect(api_key: str, *, opener: Opener = urllib.request.urlopen) -> None:
    """Check a key with one tiny question, then save it beside the model keys."""
    if not llm._KEY.match(api_key or ""):
        raise JevError("that does not look like an API key")
    ask({"line": "test"}, {"c0": {"type": "choice", "instructions": "Is `line` a word?",
                                  "criteria": {"yes": "It is.", "no": "It is not."}}},
        opener=opener, api_key=api_key)
    llm.save_key(llm.JEV, api_key)

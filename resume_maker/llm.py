"""Provider-agnostic chat completion.

Two wire formats cover every provider we support:

* ``openai``: the OpenAI chat-completions protocol, also spoken by DeepSeek
  and Moonshot (Kimi). Only the base URL and the key differ.
* ``anthropic``: Anthropic's Messages API (Claude).

Keys come from an environment variable, or from a key the user saved through
the editor (``~/.config/resume-maker/keys.json``, mode 0600, outside the repo).
Either way they stay on the server; the browser only ever sees a masked hint
(first four and last four characters).
Each provider's base URL can be overridden with ``<ID>_BASE_URL`` (for
example ``MOONSHOT_BASE_URL=https://api.moonshot.cn/v1``). Adding a provider
that speaks one of these formats is one entry in :data:`PROVIDERS`.

Only the standard library is used: requests go through ``urllib`` and
server-sent events are parsed line by line.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

TIMEOUT = 120
_KEY = re.compile(r"\A[\x21-\x7e]{8,400}\Z")  # printable, no spaces or newlines
ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = 8192

_MODEL_ID = re.compile(r"\A[A-Za-z0-9._:/@\-]{1,120}\Z")
# Models that cannot chat (embeddings, speech, images, moderation...). Model
# lists are otherwise shown as the provider returns them.
_NOT_CHAT = re.compile(
    r"embed|tts|whisper|dall-e|davinci|babbage|moderation|audio|image|"
    r"transcribe|realtime|search|computer-use|sora",
    re.I,
)


class LLMError(RuntimeError):
    """A provider call failed; the message is safe to show the user."""


@dataclass(frozen=True, slots=True)
class Provider:
    id: str
    label: str
    kind: str  # "openai" | "anthropic"
    base_url: str
    key_env: str

    @property
    def url(self) -> str:
        """API base, with the version segment filled in when it is missing.

        ``ANTHROPIC_BASE_URL`` is set to the bare host by other Anthropic
        tooling, so an override without a path gets ``/v1`` added; an override
        that already has one (``https://api.deepseek.com/v1``, a gateway path)
        is used as given.
        """
        override = os.environ.get(f"{self.id.upper()}_BASE_URL")
        if not override:
            return self.base_url
        url = override.rstrip("/")
        return url if urllib.parse.urlsplit(url).path else f"{url}/v1"

    @property
    def key(self) -> str | None:
        return os.environ.get(self.key_env) or saved_keys().get(self.id) or None

    @property
    def key_source(self) -> str | None:
        if os.environ.get(self.key_env):
            return "env"
        return "saved" if saved_keys().get(self.id) else None

    @property
    def available(self) -> bool:
        return self.key is not None


PROVIDERS: Mapping[str, Provider] = {
    p.id: p
    for p in (
        Provider("anthropic", "Claude", "anthropic", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
        Provider("openai", "OpenAI", "openai", "https://api.openai.com/v1", "OPENAI_API_KEY"),
        Provider("deepseek", "DeepSeek", "openai", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
        Provider("moonshot", "Kimi", "openai", "https://api.moonshot.ai/v1", "MOONSHOT_API_KEY"),
    )
}


# ------------------------------------------------------------- saved keys


def keys_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "resume-maker" / "keys.json"


def saved_keys() -> dict[str, str]:
    try:
        data = json.loads(keys_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def mask_key(key: str) -> str:
    """Show a short prefix and the last four characters; hide the middle."""
    text = (key or "").strip()
    if len(text) < 12:
        return "••••••••••••"
    return f"{text[:7]}••••••••{text[-4:]}"


def save_key(provider_id: str, key: str) -> None:
    """Store (or, with an empty key, forget) a provider's key, owner-only."""
    if provider_id not in PROVIDERS:
        raise LLMError(f"unknown provider: {provider_id!r}")
    keys = saved_keys()
    if key:
        if not _KEY.match(key):
            raise LLMError("that does not look like an API key")
        keys[provider_id] = key
    else:
        keys.pop(provider_id, None)
    path = keys_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".keys.")
    try:
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(keys, stream)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def connect(provider_id: str, key: str) -> list[str]:
    """Check a key against the provider, save it if it works, return models."""
    if provider_id not in PROVIDERS:
        raise LLMError(f"unknown provider: {provider_id!r}")
    if not _KEY.match(key or ""):
        raise LLMError("that does not look like an API key")
    trial = PROVIDERS[provider_id]
    models = _models_with(trial, key)
    save_key(provider_id, key)
    return models


def provider(provider_id: str) -> Provider:
    found = PROVIDERS.get(provider_id)
    if found is None:
        raise LLMError(f"unknown provider: {provider_id!r}")
    if not found.available:
        raise LLMError(f"{found.label} is not connected; add a key under Agent")
    return found


def check_model(model: str) -> str:
    if not isinstance(model, str) or not _MODEL_ID.match(model):
        raise LLMError("invalid model name")
    return model


# ------------------------------------------------------------------ requests


def _headers(p: Provider, key: str | None = None) -> dict[str, str]:
    key = key or p.key or ""
    if p.kind == "anthropic":
        return {
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _open(p: Provider, path: str, body: dict | None = None, key: str | None = None):
    request = urllib.request.Request(
        p.url + path,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers=_headers(p, key),
        method="GET" if body is None else "POST",
    )
    try:
        return urllib.request.urlopen(request, timeout=TIMEOUT)  # noqa: S310 - fixed https hosts
    except urllib.error.HTTPError as exc:
        detail = _error_message(exc)
        # Server-side breadcrumb for diagnosing a provider rejection. Never the key.
        print(f"[llm] {p.id} {request.method} {p.url + path} -> {detail}", flush=True)
        raise LLMError(f"{p.label}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"{p.label}: could not reach the API ({exc})") from exc


def _error_message(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8", "replace"))
        error = payload.get("error", payload)
        message = error.get("message") if isinstance(error, dict) else str(error)
    except (ValueError, AttributeError):
        message = None
    return f"HTTP {exc.code}" + (f" - {message}" if message else "")


def list_models(provider_id: str) -> list[dict[str, str]]:
    """Chat-capable models as ``{"id", "label"}``, newest first."""
    return _models_with(provider(provider_id))


def _models_with(p: Provider, key: str | None = None) -> list[dict[str, str]]:
    path = "/models?limit=1000" if p.kind == "anthropic" else "/models"
    with _open(p, path, key=key) as response:
        data = json.loads(response.read().decode("utf-8"))
    models, seen = [], set()
    for entry in data.get("data", []):
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id") or ""
        if not model_id or model_id in seen or _NOT_CHAT.search(model_id):
            continue
        seen.add(model_id)
        # Anthropic returns display_name ("Claude Opus 4.5"); the
        # OpenAI-style APIs return bare ids, so tidy those ourselves.
        models.append({"id": model_id, "label": entry.get("display_name") or pretty_model(model_id)})
    models.sort(key=lambda m: m["label"].casefold())
    return models


_WORDS = {"gpt": "GPT", "ai": "AI", "deepseek": "DeepSeek", "openai": "OpenAI",
          "kimi": "Kimi", "moonshot": "Moonshot", "claude": "Claude", "vl": "VL",
          "k2": "K2", "r1": "R1", "v3": "V3", "mini": "mini", "nano": "nano",
          "turbo": "Turbo", "chat": "Chat", "coder": "Coder", "reasoner": "Reasoner",
          "preview": "Preview", "latest": "Latest", "instruct": "Instruct"}
_DATE = re.compile(r"\A(20\d{2})[-]?(\d{2})[-]?(\d{2})\Z")


def pretty_model(model_id: str) -> str:
    """``gpt-4o-mini`` -> ``GPT 4o mini``; a trailing date becomes a suffix."""
    parts = model_id.replace("_", "-").split("-")
    date = None
    if parts and _DATE.match(parts[-1]):
        y, m, d = _DATE.match(parts.pop()).groups()
        date = f"{y}-{m}-{d}"
    words: list[str] = []
    for part in (p for p in parts if p):
        # "4-5" is a version, not two words: claude-opus-4-5 -> Claude Opus 4.5
        if part.isdigit() and words and words[-1].replace(".", "").isdigit():
            words[-1] += f".{part}"
            continue
        words.append(_WORDS.get(part.casefold(), part[:1].upper() + part[1:]))
    label = " ".join(words) or model_id
    return f"{label} ({date})" if date else label


# ------------------------------------------------------------------ streaming


def sse_data(lines: Iterable[bytes]) -> Iterator[str]:
    """Yield the ``data:`` payloads of a server-sent event stream."""
    for raw in lines:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line.startswith("data:"):
            yield line[5:].lstrip()


def text_deltas(kind: str, payloads: Iterable[str]) -> Iterator[str]:
    """Extract generated text from a stream of SSE data payloads."""
    for payload in payloads:
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except ValueError:
            continue
        if kind == "anthropic":
            if event.get("type") == "error":
                raise LLMError(f"Claude: {event.get('error', {}).get('message', 'stream error')}")
            delta = event.get("delta") or {}
            if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                yield delta.get("text", "")
        else:
            if "error" in event:
                raise LLMError(str(event["error"].get("message", event["error"])))
            for choice in event.get("choices") or []:
                text = (choice.get("delta") or {}).get("content")
                if text:
                    yield text


def stream_chat(
    provider_id: str,
    model: str,
    system: str,
    messages: Sequence[Mapping[str, str]],
    cacheable: str = "",
    tail: str = "",
    temperature: float | None = None,
) -> Iterator[str]:
    """Stream the reply as text chunks. Close the iterator to stop early.

    ``temperature`` is left to the provider unless a caller sets it. A call
    that writes wants the default; a call that measures does not. Reading the
    same CV against the same posting twice returned 35 and then 42, because
    nothing had pinned it and every reading was a fresh sample.

    ``cacheable`` is the part of the system prompt that repeats turn after
    turn. Providers bill a repeated prefix at a fraction of the usual rate:
    Anthropic needs it marked, the OpenAI-style APIs detect it themselves, so
    either way it must come first and stay byte-identical. ``tail`` is the rest
    of the system prompt — the live document — marked with its own breakpoint so
    a turn that edits nothing reuses it too, and a turn that edits the document
    only re-bills from there.
    """
    p = provider(provider_id)
    check_model(model)
    turns = [{"role": m["role"], "content": m["content"]} for m in messages]
    if p.kind == "anthropic":
        path = "/messages"
        if cacheable:
            mark = {"type": "ephemeral"}
            if tail:
                blocks = [{"type": "text", "text": cacheable, "cache_control": mark},
                          {"type": "text", "text": tail, "cache_control": mark}]
            else:
                blocks = [{"type": "text", "text": cacheable, "cache_control": mark},
                          {"type": "text", "text": system[len(cacheable):].lstrip("\n")}]
        else:
            blocks = system
        body = {"model": model, "max_tokens": MAX_TOKENS, "system": blocks,
                "messages": turns, "stream": True}
        if temperature is not None:
            body["temperature"] = temperature
    else:
        path = "/chat/completions"
        body = {"model": model, "stream": True,
                "messages": [{"role": "system", "content": system}, *turns]}
        if temperature is not None:
            body["temperature"] = temperature
    try:
        response = _open(p, path, body)
    except LLMError as exc:
        # Newer models refuse the parameter outright ("`temperature` is
        # deprecated for this model") while older ones and other providers
        # take it. It is a preference, never worth a failed call, so a request
        # turned away for it goes again without it.
        if "temperature" not in body or "temperature" not in str(exc):
            raise
        body = {k: v for k, v in body.items() if k != "temperature"}
        response = _open(p, path, body)
    try:
        yield from text_deltas(p.kind, sse_data(response))
    finally:
        response.close()


def complete(provider_id: str, model: str, system: str, prompt: str,
             temperature: float | None = None) -> str:
    """One-shot completion, used for import and for the readings."""
    return "".join(stream_chat(provider_id, model, system,
                               [{"role": "user", "content": prompt}],
                               temperature=temperature))

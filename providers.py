"""LM Studio provider (v1 only). Pass-through by default."""

import os
import re
import requests

BASE = os.environ.get("LIBREARENA_LMSTUDIO", "http://localhost:1234/v1")
TIMEOUT = 120

EMBED_PAT = re.compile(
    r"embed|e5-|bge-|gte-|nomic-embed|sentence-|instructor|retrieval|rerank|text-embedding",
    re.I,
)


def _native_base():
    if BASE.endswith("/v1"):
        return BASE[: -len("/v1")]
    return BASE


def _native_models():
    """Raw entries from native API with type+state. Empty list on failure."""
    native = _native_base()
    try:
        r = requests.get(f"{native}/api/v0/models", timeout=10)
        if r.ok:
            return r.json().get("data", [])
    except Exception:
        pass
    try:
        r = requests.get(f"{native}/api/v1/models", timeout=10)
        if r.ok:
            d = r.json()
            return d.get("models", d.get("data", []))
    except Exception:
        pass
    return []


def _is_chat_entry(m):
    t = str(m.get("type", "")).lower()
    if t:
        return t in ("llm", "vlm")
    return not EMBED_PAT.search(m.get("id", "") or "")


def _is_loaded_entry(m):
    state = m.get("state")
    if state is None:
        loaded = m.get("loaded_instances", m.get("loaded"))
        if loaded is not None:
            return bool(loaded)
        return True  # unknown: assume loaded, fail fast at chat time
    return str(state).lower() == "loaded"


def list_all():
    """All visible chat models with loaded flags. Falls back to /v1 ids (assumed loaded)."""
    native = _native_models()
    if native:
        out = []
        for m in native:
            mid = m.get("id") or m.get("key")
            if not mid or not _is_chat_entry(m):
                continue
            out.append({"id": mid, "loaded": _is_loaded_entry(m)})
        return out
    r = requests.get(f"{BASE}/models", timeout=10)
    r.raise_for_status()
    return [{"id": m.get("id"), "loaded": True}
            for m in r.json().get("data", []) if m.get("id") and not EMBED_PAT.search(m.get("id"))]


def list_models(chat_only=True):
    if not chat_only:
        r = requests.get(f"{BASE}/models", timeout=10)
        r.raise_for_status()
        return [m.get("id") for m in r.json().get("data", []) if m.get("id")]
    return [m["id"] for m in list_all() if m["loaded"]]


def chat(model, messages, override=None, timeout=TIMEOUT):
    body = {"model": model, "messages": messages}
    if override:
        body.update(override)
    r = requests.post(f"{BASE}/chat/completions", json=body, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    text = ""
    reasoning = ""
    try:
        msg = data["choices"][0]["message"]
        text = msg.get("content") or ""
        reasoning = msg.get("reasoning_content") or ""
    except (KeyError, IndexError, TypeError):
        pass
    return {
        "text": text,
        "reasoning": reasoning,
        "usage": data.get("usage", {}),
        "raw_model": data.get("model", model),
    }


def chat_stream(model, messages, override=None, on_token=None, timeout=TIMEOUT):
    """Streaming chat via SSE. on_token(kind, delta_text, full_text) with kind in
    ('reasoning', 'content'). Returns {text, reasoning, usage, raw_model}."""
    body = {"model": model, "messages": messages, "stream": True}
    if override:
        body.update(override)
    import json as _json

    text_parts: list[str] = []
    reason_parts: list[str] = []
    raw_model = model
    usage: dict = {}
    with requests.post(f"{BASE}/chat/completions", json=body, timeout=timeout, stream=True) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = _json.loads(payload)
            except ValueError:
                continue
            raw_model = chunk.get("model", raw_model)
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            try:
                delta = chunk["choices"][0]["delta"]
            except (KeyError, IndexError, TypeError):
                continue
            if not isinstance(delta, dict):
                continue
            d_reason = delta.get("reasoning_content") or ""
            d_text = delta.get("content") or ""
            if d_reason:
                reason_parts.append(d_reason)
                if on_token:
                    on_token("reasoning", d_reason, "".join(reason_parts))
            if d_text:
                text_parts.append(d_text)
                if on_token:
                    on_token("content", d_text, "".join(text_parts))
    return {
        "text": "".join(text_parts),
        "reasoning": "".join(reason_parts),
        "usage": usage,
        "raw_model": raw_model,
    }

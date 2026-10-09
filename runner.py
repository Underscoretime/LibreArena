"""Task runner: runs a task against LM Studio models, saves runs/*.json."""

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import providers

ROOT = Path(__file__).parent
TASKS_DIR = ROOT / "tasks"
RUNS_DIR = ROOT / "runs"


def load_task(task_id):
    p = TASKS_DIR / f"{task_id}.json"
    return json.loads(p.read_text())


def check_response(task, text):
    results = []
    for c in task.get("checks", []):
        t = c.get("type")
        if t == "contains":
            ok = c.get("value", "").lower() in (text or "").lower()
            results.append({"type": t, "value": c.get("value"), "pass": ok})
        elif t == "regex":
            try:
                ok = re.search(c.get("value", ""), text or "", re.DOTALL) is not None
            except re.error as e:
                ok = False
                results.append({"type": t, "value": c.get("value"), "pass": ok, "error": str(e)})
                continue
            results.append({"type": t, "value": c.get("value"), "pass": ok})
        elif t == "not-contains":
            ok = c.get("value", "").lower() not in (text or "").lower()
            results.append({"type": t, "value": c.get("value"), "pass": ok})
        elif t == "manual-only":
            results.append({"type": t, "pass": None})
    if not results:
        return {"pass": None, "detail": "manual-only"}
    passes = [r["pass"] for r in results if r["pass"] is not None]
    if not passes and all(r["pass"] is None for r in results):
        return {"pass": None, "detail": "manual-only", "checks": results}
    overall = all(p is True for p in passes) if passes else None
    return {"pass": overall, "detail": "; ".join(
        f"{r['type']}:{r.get('value', '')}={'pass' if r['pass'] else 'fail'}" for r in results if r["pass"] is not None
    ) or "manual-only", "checks": results}


def safe_name(s):
    return re.sub(r"[^a-zA-Z0-9-_]+", "_", s)[:64]


class Cancelled(Exception):
    """Raised by streaming callbacks to abort a run (e.g. user_cancel)."""
    pass


def build_messages(task):
    messages = []
    if task.get("system"):
        messages.append({"role": "system", "content": task["system"]})
    messages.append({"role": "user", "content": task["prompt"]})
    return messages


def _is_infra_error(exc):
    import requests as _rq

    if isinstance(exc, (_rq.Timeout, _rq.ConnectionError)):
        return True
    if isinstance(exc, _rq.HTTPError):
        status = exc.response.status_code if exc.response is not None else 0
        return status == 429 or 500 <= status < 600
    return False


def _run_settings(task, model, raw_model, override, retried=False, mismatch=False):
    try:
        info = providers.model_info(raw_model)
    except Exception:
        info = {}
    ov = override or {}
    return {
        "model": raw_model,
        "requested_model": model,
        "override": override,
        "temperature": ov.get("temperature"),  # None = LM Studio server default
        "max_tokens": ov.get("max_tokens"),
        "system": task.get("system") or "",
        "quantization": info.get("quantization"),
        "model_arch": info.get("arch"),
        "context_length": info.get("context_length"),
        "kv_cache_quant": None,  # not exposed by any LM Studio API
        **({"model_mismatch": True} if mismatch else {}),
        **({"retried": True} if retried else {}),
    }


def _error_run(task_id, task, model, override, started, dur_ms, exc, retried=False):
    return {
        "task_id": task_id, "model": model,
        "prompt": task["prompt"], "response": "",
        "reasoning": "",
        "duration_ms": dur_ms,
        "effective_config": _run_settings(task, model, model, override, retried=retried),
        "started_at": started,
        "auto": {"pass": None, "detail": f"infra-error: {exc}"},
        "human": {"score": None, "verdict": None, "note": ""},
        "error": str(exc),
    }


def _finish_run(task_id, task, model, text, reasoning, usage, raw_model, override, started, dur_ms, retried=False):
    # Model-mismatch guard: LM Studio may route chat for an embedding id to a loaded LLM.
    mismatch = bool(raw_model and raw_model != model)
    if mismatch:
        return {
            "task_id": task_id, "model": model,
            "prompt": task["prompt"], "response": "",
            "reasoning": "",
            "duration_ms": dur_ms,
            "effective_config": _run_settings(task, model, raw_model, override, retried=retried, mismatch=True),
            "started_at": started,
            "auto": {"pass": None, "detail": "skipped: routed to different model"},
            "human": {"score": None, "verdict": None, "note": ""},
            "error": f"requested {model} but LM Studio answered as {raw_model}",
        }
    auto = check_response(task, text)
    return {
        "task_id": task_id, "model": model,
        "prompt": task["prompt"], "response": text,
        "reasoning": reasoning,
        "duration_ms": dur_ms,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "effective_config": _run_settings(task, model, raw_model, override, retried=retried),
        "started_at": started,
        "auto": auto,
        "human": {"score": None, "verdict": None, "note": ""},
    }


def run_one_model(task_id, task, model, messages, override=None):
    """Single attempt + one retry on transient errors only. Returns run dict (no id, not saved)."""
    started = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    try:
        out = providers.chat(model, messages, override=override)
        dur = int((time.perf_counter() - t0) * 1000)
        return _finish_run(task_id, task, model, out["text"], out.get("reasoning", ""),
                           out.get("usage", {}), out.get("raw_model", model), override, started, dur)
    except Exception as e:
        if not _is_infra_error(e):
            dur = int((time.perf_counter() - t0) * 1000)
            return _error_run(task_id, task, model, override, started, dur, e)
        time.sleep(1.5)
    # retry once, timer restarts so duration reflects the retry only
    t1 = time.perf_counter()
    try:
        out = providers.chat(model, messages, override=override)
        dur = int((time.perf_counter() - t1) * 1000)
        return _finish_run(task_id, task, model, out["text"], out.get("reasoning", ""),
                           out.get("usage", {}), out.get("raw_model", model), override, started, dur, retried=True)
    except Exception as e2:
        dur = int((time.perf_counter() - t1) * 1000)
        return _error_run(task_id, task, model, override, started, dur, e2, retried=True)


def run_one_model_stream(task_id, task, model, messages, override=None, on_token=None):
    """Streaming variant: accumulates tokens via providers.chat_stream, no retry."""
    started = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    try:
        out = providers.chat_stream(model, messages, override=override, on_token=on_token)
        dur = int((time.perf_counter() - t0) * 1000)
        return _finish_run(task_id, task, model, out["text"], out.get("reasoning", ""),
                           out.get("usage", {}), out.get("raw_model", model), override, started, dur)
    except Cancelled:
        raise
    except Exception as e:
        dur = int((time.perf_counter() - t0) * 1000)
        return _error_run(task_id, task, model, override, started, dur, e)


def save_run(run, task_id, model):
    import secrets

    ts = int(time.time() * 1000)
    rid = f"{safe_name(task_id)}_{safe_name(model)}_{ts}_{secrets.token_hex(2)}"
    run["id"] = rid
    RUNS_DIR.mkdir(exist_ok=True)
    tmp = RUNS_DIR / f"{rid}.tmp"
    final = RUNS_DIR / f"{rid}.json"
    tmp.write_text(json.dumps(run, indent=2))
    tmp.replace(final)
    return run


def run_task(task_id, models=None, override=None):
    task = load_task(task_id)
    if models is None:
        models = providers.list_models()
    if not models:
        raise RuntimeError("No models loaded in LM Studio (GET /v1/models empty).")
    messages = build_messages(task)
    saved = []
    for model in models:
        saved.append(save_run(run_one_model(task_id, task, model, messages, override), task_id, model))
    return saved


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--model", action="append", default=None)
    ap.add_argument("--temperature", type=float, default=None)
    args = ap.parse_args()
    override = {"temperature": args.temperature} if args.temperature is not None else None
    for r in run_task(args.task, models=args.model, override=override):
        print(r["id"], r["model"], r["auto"])

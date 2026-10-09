"""FastAPI server for LibreArena. Local-only."""

import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import providers
import runner

ROOT = Path(__file__).parent
TASKS_DIR = ROOT / "tasks"
RUNS_DIR = ROOT / "runs"
JOBS_DIR = ROOT / "jobs"
FRONTEND = ROOT / "frontend"

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app):
    mark_stale_jobs()
    yield


app = FastAPI(title="LibreArena", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8521", "http://127.0.0.1:8521"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_THREADS: dict[str, threading.Thread] = {}
_RUN_LOCK = threading.Lock()


def read_json(p):
    return json.loads(Path(p).read_text())


def write_json_atomic(p: Path, obj):
    p.parent.mkdir(exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    tmp.replace(p)


def read_job(jid: str):
    p = JOBS_DIR / f"{jid}.json"
    if not p.exists():
        raise HTTPException(404, "job not found")
    return read_json(p)


def save_job(job):
    job["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json_atomic(JOBS_DIR / f"{job['id']}.json", job)


def mark_stale_jobs():
    JOBS_DIR.mkdir(exist_ok=True)
    for p in JOBS_DIR.glob("*.json"):
        try:
            job = read_json(p)
        except json.JSONDecodeError:
            continue
        if job.get("status") in ("queued", "running"):
            for item in job.get("items", []):
                if item.get("status") in ("queued", "running"):
                    item["status"] = "error"
                    item["error"] = "server restarted mid-run"
            job["status"] = "error"
            job["error"] = "server restarted mid-run"
            save_job(job)


@app.get("/api/health")
def api_health():
    try:
        models = providers.list_models()
        return {"server": "ok", "lmstudio": "ok" if models else "empty", "models": models}
    except Exception as e:
        return {"server": "ok", "lmstudio": "down", "error": str(e)[:200]}


@app.get("/api/models")
def api_models():
    try:
        all_models = providers.list_all()
        return {"models": [m["id"] for m in all_models if m["loaded"]], "all": all_models}
    except Exception as e:
        raise HTTPException(502, f"LM Studio unreachable: {e}")


@app.get("/api/tasks")
def api_tasks():
    TASKS_DIR.mkdir(exist_ok=True)
    return [read_json(p) for p in sorted(TASKS_DIR.glob("*.json"))]


@app.post("/api/tasks")
def api_create_task(task: dict):
    tid = task.get("id") or re.sub(r"[^a-z0-9-]+", "-", task.get("name", "task").lower()).strip("-")
    if not tid:
        raise HTTPException(400, "task needs a name")
    if not (task.get("prompt") or "").strip():
        raise HTTPException(400, "task needs a prompt")
    for c in task.get("checks", []):
        if c.get("type") == "regex":
            try:
                re.compile(c.get("value", ""))
            except re.error as e:
                raise HTTPException(400, f"invalid regex: {e}")
    task["id"] = tid
    TASKS_DIR.mkdir(exist_ok=True)
    write_json_atomic(TASKS_DIR / f"{tid}.json", task)
    return task


def _new_job_id():
    import secrets

    return f"job_{int(time.time())}_{secrets.token_hex(3)}"


def run_job(jid: str):
    with _RUN_LOCK:
        try:
            job = read_job(jid)
        except HTTPException:
            return
        if job.get("status") not in ("queued", "running"):
            return
        job["status"] = "running"
        save_job(job)
        task_ids = job.get("task_ids") or ([job["task_id"]] if job.get("task_id") else [])
        task_cache = {}
        for _ in job["items"]:
            job = read_job(jid)
            if job.get("cancel_requested"):
                for it in job["items"]:
                    if it.get("status") in ("queued", "running") and "run_id" not in it:
                        it["status"] = "cancelled"
                        it["error"] = "cancelled by user"
                        it.pop("partial_text", None)
                        it.pop("partial_reasoning", None)
                job_counts(job)
                save_job(job)
                break
            item = next((it for it in job["items"] if it.get("status") == "queued"), None)
            if item is None:
                break
            tid = item.get("task", task_ids[0] if task_ids else job.get("task_id"))
            try:
                if tid not in task_cache:
                    task_cache[tid] = runner.load_task(tid)
            except FileNotFoundError:
                item["status"] = "error"
                item["error"] = f"task {tid} not found"
                job_counts(job)
                save_job(job)
                continue
            task = task_cache[tid]
            # Fresh context: every run sends ONLY this task's messages.
            # No history is carried between runs, so each test starts cold
            # in its own context window.
            messages = runner.build_messages(task)
            item["status"] = "running"
            item["partial_text"] = ""
            item["partial_reasoning"] = ""
            save_job(job)
            last_flush = [0.0]

            def on_token(kind, delta, full, _item=item, _jid=jid):
                now = time.monotonic()
                if kind == "reasoning":
                    _item["partial_reasoning"] = full[-4000:]
                else:
                    _item["partial_text"] = full[-4000:]
                if now - last_flush[0] > 0.5:
                    last_flush[0] = now
                    try:
                        cur = read_job(_jid)
                        if cur.get("cancel_requested"):
                            raise runner.Cancelled()
                        for it in cur["items"]:
                            if it.get("model") == _item["model"] and it.get("status") == "running":
                                it["partial_text"] = _item.get("partial_text", "")
                                it["partial_reasoning"] = _item.get("partial_reasoning", "")
                        save_job(cur)
                    except runner.Cancelled:
                        raise
                    except HTTPException:
                        pass

            try:
                run = runner.run_one_model_stream(
                    item.get("task", job.get("task_id")), task, item["model"], messages, job.get("override"), on_token=on_token
                )
            except runner.Cancelled:
                item["status"] = "cancelled"
                item["error"] = "cancelled by user"
                item.pop("partial_text", None)
                item.pop("partial_reasoning", None)
                job["cancel_requested"] = True  # keep flag: local copy predates it
                job_counts(job)
                save_job(job)
                continue
            # run_one_model_stream never raises, but guard anyway
            saved = runner.save_run(run, item.get("task", job.get("task_id")), item["model"])
            item["status"] = "done" if not saved.get("error") else "error"
            if saved.get("error"):
                item["error"] = str(saved["error"])[:500]
            item["run_id"] = saved["id"]
            item["duration_ms"] = saved.get("duration_ms")
            item["auto"] = saved.get("auto")
            item.pop("partial_text", None)
            item.pop("partial_reasoning", None)
            job_counts(job)
            save_job(job)
        job = read_job(jid)
        job_counts(job)
        cancelled_n = sum(1 for i in job["items"] if i.get("status") == "cancelled")
        if job.get("cancel_requested") or (cancelled_n and job["counts"]["done"] == 0
                                           and cancelled_n + job["counts"]["error"] == job["counts"]["total"]):
            job["status"] = "done" if job["counts"]["done"] > 0 else "cancelled"
            job["error"] = "cancelled by user"
        else:
            job["status"] = "error" if job["counts"]["done"] == 0 else "done"
        save_job(job)


@app.post("/api/jobs/{jid}/cancel")
def api_cancel(jid: str):
    if re.search(r"[^a-zA-Z0-9_\-]", jid):
        raise HTTPException(404, "job not found")
    job = read_job(jid)
    if job.get("status") in ("done", "error", "cancelled"):
        return job
    job["cancel_requested"] = True
    save_job(job)
    return job


def job_counts(job):
    items = job.get("items", [])
    job["counts"] = {
        "done": sum(1 for i in items if i.get("status") == "done"),
        "error": sum(1 for i in items if i.get("status") == "error"),
        "cancelled": sum(1 for i in items if i.get("status") == "cancelled"),
        "total": len(items),
    }


def _tested_pairs(task_id):
    """Models that already have a completed run for this task (same prompt)."""
    try:
        task = runner.load_task(task_id)
    except FileNotFoundError:
        return set()
    done = set()
    RUNS_DIR.mkdir(exist_ok=True)
    for p in RUNS_DIR.glob("*.json"):
        try:
            r = read_json(p)
        except json.JSONDecodeError:
            continue
        if r.get("task_id") == task_id and r.get("prompt") == task.get("prompt"):
            done.add(r.get("model"))
    return done


@app.post("/api/run", status_code=202)
def api_run(body: dict, sync: int = 0):
    task_ids = body.get("task_ids") or ([body["task_id"]] if body.get("task_id") else [])
    if not task_ids:
        raise HTTPException(400, "task_id or task_ids required")
    for tid in task_ids:
        if re.search(r"[^a-zA-Z0-9_\-]", tid) or not (TASKS_DIR / f"{tid}.json").exists():
            raise HTTPException(404, f"task {tid} not found")
    if sync:
        if len(task_ids) != 1:
            raise HTTPException(400, "sync mode runs one task at a time")
        try:
            runs = runner.run_task(task_ids[0], models=body.get("models"), override=body.get("override"))
            return {"runs": runs}
        except RuntimeError as e:
            raise HTTPException(502, str(e))
    try:
        models = body.get("models") or providers.list_models()
    except requests.RequestException as e:
        raise HTTPException(502, f"LM Studio unreachable: {e}")
    if not models:
        raise HTTPException(502, "No loaded chat models in LM Studio — load one first.")
    # skip (task, model) pairs already tested, unless forced
    skipped = []
    pairs = []
    if not body.get("force"):
        for tid in task_ids:
            tested = _tested_pairs(tid)
            for m in models:
                if m in tested:
                    skipped.append(f"{tid}×{m}")
                else:
                    pairs.append((tid, m))
        if not pairs:
            return {"job_id": None, "status": "skipped",
                    "detail": "Everything already tested. Tick force to re-run.",
                    "skipped": skipped}
    else:
        pairs = [(tid, m) for tid in task_ids for m in models]
    # dedupe: identical active job started <30s ago
    now = time.time()
    try:
        for p in sorted(JOBS_DIR.glob("*.json"), reverse=True)[:10]:
            try:
                j = read_json(p)
            except json.JSONDecodeError:
                continue
            if j.get("status") in ("queued", "running") and j.get("task_ids", [j.get("task_id")]) == task_ids:
                if j.get("models") == models and now - j.get("created_ts", now) < 30:
                    return {"job_id": j["id"], "status": j["status"], "deduped": True,
                            "poll": f"/api/jobs/{j['id']}"}
    except FileNotFoundError:
        pass
    jid = _new_job_id()
    job = {
        "id": jid, "task_id": task_ids[0], "task_ids": task_ids, "models": models,
        "override": body.get("override"),
        "status": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "created_ts": now,
        "counts": {"done": 0, "error": 0, "total": len(pairs)},
        "items": [{"task": tid, "model": m, "status": "queued", "run_id": None} for tid, m in pairs],
        "error": None,
    }
    save_job(job)
    t = threading.Thread(target=run_job, args=(jid,), daemon=True)
    _THREADS[jid] = t
    t.start()
    out = {"job_id": jid, "status": "queued", "models": models, "poll": f"/api/jobs/{jid}"}
    if skipped:
        out["skipped"] = skipped
    return out


@app.get("/api/jobs")
def api_jobs(limit: int = 20):
    JOBS_DIR.mkdir(exist_ok=True)
    jobs = []
    for p in sorted(JOBS_DIR.glob("*.json"), reverse=True)[: max(1, min(limit, 100))]:
        try:
            j = read_json(p)
        except json.JSONDecodeError:
            continue
        jobs.append({k: j.get(k) for k in ("id", "task_id", "status", "counts", "created_at", "updated_at", "error")})
    return jobs


@app.get("/api/jobs/{jid}")
def api_job_one(jid: str):
    if re.search(r"[^a-zA-Z0-9_\-]", jid):
        raise HTTPException(404, "job not found")
    return read_job(jid)


@app.get("/api/runs")
def api_runs():
    RUNS_DIR.mkdir(exist_ok=True)
    runs = []
    for p in sorted(RUNS_DIR.glob("*.json"), reverse=True):
        try:
            runs.append(read_json(p))
        except json.JSONDecodeError:
            continue
    return runs


@app.get("/api/runs/{rid}")
def api_run_one(rid: str):
    if re.search(r"[^a-zA-Z0-9_\-]", rid):
        raise HTTPException(404, "run not found")
    p = RUNS_DIR / f"{rid}.json"
    if not p.exists():
        raise HTTPException(404, "run not found")
    return read_json(p)


@app.put("/api/runs/{rid}/score")
def api_score(rid: str, body: dict):
    if re.search(r"[^a-zA-Z0-9_\-]", rid):
        raise HTTPException(404, "run not found")
    p = RUNS_DIR / f"{rid}.json"
    if not p.exists():
        raise HTTPException(404, "run not found")
    run = read_json(p)
    h = run.get("human", {})
    if "score" in body:
        s = body["score"]
        if s is not None:
            try:
                s = int(s)
            except (TypeError, ValueError):
                raise HTTPException(400, "score must be 1-5 or null")
            if not (1 <= s <= 5):
                raise HTTPException(400, "score must be 1-5 or null")
            h["score"] = s
        else:
            h["score"] = None
    if "verdict" in body:
        if body["verdict"] not in ("pass", "fail", None):
            raise HTTPException(400, "verdict must be pass/fail/null")
        h["verdict"] = body["verdict"]
    if "note" in body:
        h["note"] = str(body["note"])[:2000]
    run["human"] = h
    write_json_atomic(p, run)
    return run


@app.post("/api/runs/import")
def api_import_run(run: dict):
    """Import someone else's run file. Gets a fresh id. Never overwrites."""
    task_id = run.get("task_id")
    model = run.get("model")
    if not task_id or not model:
        raise HTTPException(400, "run needs task_id and model")
    run = dict(run)
    run.setdefault("prompt", "")
    run.setdefault("response", "")
    run.setdefault("auto", {"pass": None, "detail": "imported"})
    run.setdefault("human", {"score": None, "verdict": None, "note": ""})
    run["imported"] = True
    return runner.save_run(run, task_id, model)


app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8521)

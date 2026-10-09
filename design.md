# LibreArena — Design

Local model compare arena. LM Studio only for v1.
Standalone repo, local-first, no auth. Inspired by OpenBlox patterns but separate code.

## 1. Architecture + layout

```
LibreArena/
  server.py      # FastAPI, serves frontend/ + /api/*
  runner.py      # takes task + models, calls LM Studio, times it, saves run
  providers.py   # LM Studio interface (list_models, chat), room for Ollama later
  tasks/*.json   # task definitions, git-friendly
  runs/*.json    # one file per run
  frontend/
    index.html   # leaderboard + filters
    review.html  # single run view + human score
    tasks.html   # list/add tasks
```

Flow: define task (UI or JSON) -> runner POSTs to LM Studio -> saves `runs/` -> auto-check -> human review patches same file.

Backend binds `127.0.0.1:8521` (not 8520, to avoid OpenBlox clash). CORS locked to localhost. No DB.

## 2. Task format

Task defines prompt + tags + checks only. No forced temperature.

`tasks/ping.json`:
```json
{
  "id": "ping",
  "name": "Ping test",
  "prompt": "ping",
  "system": "",
  "tags": ["fun", "misc"],
  "category": "instruction",
  "checks": [{ "type": "contains", "value": "pong" }]
}
```

- `tags`: free-form for filtering.
- `category`: `coding | instruction | fun`.
- `checks[]`: `contains` (case-insensitive) | `regex` | `manual-only`.
- No `params` in task. Optional per-run override set in UI at run time.

Runner snapshots effective config per run (see §3).

## 3. Runner (LM Studio only)

- Discovery: `GET http://localhost:1234/v1/models` -> model IDs. Empty = UI shows "load a model in LM Studio".
- Run: `POST /v1/chat/completions { model, messages }`. No temp sent unless override set in UI.
- Captured: `model_id`, `started_at`, `duration_ms` (perf_counter), `usage.prompt_tokens / completion_tokens` if present, `response_text`, `effective_config: { model, override: null|{temperature,...}, note: "lmstudio-server-default" }`.
- `runner.py run_task(task_id, override=None)` loops models, writes one `runs/<task>_<model>_<ts>.json` per model so crashes don't lose others. 1 retry on timeout/5xx, then saves `error` run.
- API: `POST /api/run { task_id, override? }`. CLI: `python runner.py --task ping`.

`providers.py` exposes `list_models()`, `chat(model, messages, override)` so Ollama is a later add.

## 4. Scoring + storage

`runs/<task>_<model>_<ts>.json`:
```json
{
  "task_id": "ping", "model": "qwen3-8b",
  "prompt": "ping", "response": "...",
  "duration_ms": 842, "prompt_tokens": 5, "completion_tokens": 12,
  "effective_config": { "model": "qwen3-8b", "override": null },
  "auto": { "pass": true, "detail": "contains:pong" },
  "human": { "score": null, "verdict": null, "note": "" }
}
```

- Auto scored at save. `manual-only` leaves `auto=null`.
- Human via `PUT /api/runs/<id>/score { score 1-5, verdict pass/fail, note }`, patches same file.
- Leaderboard computed on read: per model -> `auto pass%`, `avg human`, `avg duration_ms`, `avg tokens`, `run count`. Unscored excluded from human avg only.

## 5. WebUI

Plain HTML/CSS/JS, `fetch()` to FastAPI, no build.

- `index.html`: table `model | runs | auto pass% | avg human | avg time | avg tokens`. Filters: category, tag multi-select, model multi-select, verdict (auto-pass / auto-fail / human-pass / unscored), sort by column. Filters in URL params.
- `review.html?id=<run>`: prompt + response, auto result, timing/tokens, effective_config, human form (stars + pass/fail + note + save), prev/next for unscored queue.
- `tasks.html`: list tasks, new-task form (name, prompt, category, tags, check type + value), test-run button.

## Non-goals for v1

No Ollama/vLLM, no multi-turn prompts, no SQLite, no auth, no dataset included (2 examples only: `ping`, `code-hello`).

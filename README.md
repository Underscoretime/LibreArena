# LibreArena

Local model compare arena. Run the same benchmarks against your LM Studio models,
score them (auto + human), and compare on a leaderboard and an
Artificial-Analysis-style Intelligence graph. Local-first, no cloud, no auth.

## Requirements

- Python 3.10+
- [LM Studio](https://lmstudio.ai) running locally with at least one chat model loaded

## Quickstart

```
pip install -r requirements.txt
python server.py
```

Open http://127.0.0.1:8521 — go to Tasks, pick models, run. Refresh mid-run safely:
jobs live on disk and the UI resumes polling.

## How it works

- `tasks/*.json` — benchmark definitions (prompt, checks, tags, Intelligence eligibility)
- `runs/*.json` — one file per run: prompt, response, timing, tokens, auto + human scores
- `jobs/*.json` — background run jobs, so refreshes never lose progress
- `frontend/` — plain HTML/CSS/JS, no build step
- Each run sends only its own task messages: every test starts in a fresh context,
  no history is carried between runs.

## Sharing runs

`runs/` and `jobs/` are git-ignored (your history stays yours). To share results,
send someone your `runs/*.json` files and they import them:

```
curl -X POST http://127.0.0.1:8521/api/runs/import \
  -H 'Content-Type: application/json' -d @someone-elses-run.json
```

Imported runs get a fresh id and show up on the leaderboard and Intelligence graph.

## Safety

Model output is never executed and never rendered as HTML — grading is
substring/regex matching on text only. `judge.py` holds the AST safety gate
(allowlisted imports, no eval/open/dunders) for the day execution checks exist.

## License

MIT — see LICENSE.

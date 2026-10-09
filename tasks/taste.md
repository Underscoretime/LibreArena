# Benchmark taste (user verdicts, for designing future batches)

User is not a coding expert. Explain benchmarks in plain language, no jargon.
Wants frameworks first, small example sets, human review over clever auto-grading.

## Batch 1 — coding (2026-10-09): ALL APPROVED (C1..C15 yes)
Needed plain-English explanations for C5 (ordered dedupe: set() scrambles order)
and C15 (retry 3x then re-raise: loops + error handling, real-world pattern).

## Batch 2 — instruction (2026-10-09): ALL APPROVED with 4 fixes
- I1/I2: contains -> exact regex (ONLY the answer, nothing else).
- I3: prompt must say "the words, not the letters".
- I10: dropped not-contains HACKED (a legit translation could contain it);
  injection-following replies fail naturally via missing bonjour.
- Added `not-contains` check type to runner.py (case-insensitive).

## Conventions learned
- Value-answer tasks: prompt must NOT contain the answer literal, and must ask for
  a trailing `# <result>` comment so the text grader can see it.
- Approach checks (contains mid/nonlocal/raise, regex for Counter/findall) are
  approximate but fine for a fun arena. Prefer tokens the correct code MUST have.
- Difficulty mix that worked: 4 easy smoke + 7 medium + 4 hard.
- Python only for now. LuaU pack proposed for later (user's niche, not yet asked).

## Safety contract
- Grader never executes model output (substring/regex on text only).
- Frontend renders responses as escaped text, never HTML.
- Future exec checks MUST pass judge.py scan_safe() (import allowlist, no
  eval/open/dunders/network) then run in scrubbed-env subprocess, 5s timeout.

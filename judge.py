"""Safety gate for executing model-generated code (future exec checks).

Contract:
- Model output is NEVER executed in the server process and NEVER rendered as HTML.
- Today the grader only does substring/regex matching on response text (safe by construction).
- If an `exec` check type is added later, every snippet MUST pass scan_safe() first,
  then run via run_snippet() in a locked-down subprocess.

Usage: python judge.py --file snippet.py  -> prints SAFE / UNSAFE: <reason>
"""

import argparse
import ast
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_BYTES = 20_000
TIMEOUT_S = 5

ALLOW_MODULES = {
    "math", "re", "json", "itertools", "functools", "collections",
    "string", "statistics", "heapq", "bisect", "datetime", "enum",
    "dataclasses", "typing",
}

BANNED_NAMES = {
    "eval", "exec", "open", "compile", "__import__", "input",
    "globals", "locals", "vars", "getattr", "setattr", "delattr",
    "breakpoint", "exit", "quit", "help", "memoryview",
    "os", "sys", "subprocess", "socket", "pathlib", "shutil",
    "importlib", "ctypes", "threading", "multiprocessing", "signal",
}


def scan_safe(code):
    """Return (True, '') or (False, reason). Pure AST check, no execution."""
    if len(code.encode()) > MAX_BYTES:
        return False, f"too large ({len(code.encode())} bytes)"
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"syntax error: {e}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if (a.name or "").split(".")[0] not in ALLOW_MODULES:
                    return False, f"blocked import: {a.name}"
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in ALLOW_MODULES:
                return False, f"blocked import: {node.module}"
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            return False, f"blocked name: {node.id}"
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return False, f"blocked dunder: {node.attr}"
    return True, ""


def run_snippet(code, timeout=TIMEOUT_S):
    """Run pre-scanned code in a subprocess with scrubbed env. Returns (rc, stdout, stderr)."""
    ok, reason = scan_safe(code)
    if not ok:
        raise ValueError(f"refusing to run unsafe code: {reason}")
    with tempfile.TemporaryDirectory() as tmp:
        env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": "", "HOME": tmp}
        p = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
            timeout=timeout, cwd=tmp, env=env,
        )
        return p.returncode, p.stdout[:4000], p.stderr[:1000]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    args = ap.parse_args()
    code = Path(args.file).read_text()
    ok, reason = scan_safe(code)
    print("SAFE" if ok else f"UNSAFE: {reason}")

#!/usr/bin/env python3
"""Run the agent-incidents corpus against ANY command guard and report FP/FN.

This runner is tool-agnostic: it does NOT import cmdbelt and never executes the
corpus commands themselves. Each command is passed as a single argument to a
guard program of your choice; the guard's exit code decides the verdict.

Contract (override with --allow-exit):
    exit code in --allow-exit  -> the guard ALLOWED the command
    any other exit code        -> the guard CAUGHT it (blocked or asked)

Scoring:
    false positive = a benign command the guard caught
    false negative = a destructive command the guard allowed

Examples:
    # cmdbelt (0 allow, 10 ask, 20 deny -> only 0 counts as allowed):
    python run.py --guard 'cmdbelt check --' --allow-exit 0

    # a guard that returns 0=allow, 1=block:
    python run.py --guard 'my-guard --check' --allow-exit 0

    # dry list of what each command is labelled, no guard run:
    python run.py --self-check

No network, no telemetry. Pure stdlib. Python 3.9+.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CORPUS = HERE / "incidents.jsonl"


def load(corpus: Path) -> list[dict[str, str]]:
    return [json.loads(ln) for ln in corpus.read_text().splitlines() if ln.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--guard", default="cmdbelt check --",
        help="guard program + flags; the command is appended as one argv "
             "(default: 'cmdbelt check --')",
    )
    ap.add_argument(
        "--allow-exit", type=int, nargs="+", default=[0],
        help="exit code(s) that mean ALLOWED; everything else means CAUGHT (default: 0)",
    )
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--show-errors", action="store_true", help="list each FP and FN")
    ap.add_argument(
        "--self-check", action="store_true", help="just print the label counts and exit"
    )
    args = ap.parse_args()

    rows = load(args.corpus)
    if args.self_check:
        by: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for r in rows:
            by[r["source"]][r["label"]] += 1
        print(f"{len(rows)} commands")
        for s, c in sorted(by.items()):
            print(f"  {s}: {dict(c)}")
        return 0

    guard = shlex.split(args.guard)
    allow = set(args.allow_exit)
    fp: list[str] = []
    fn: list[str] = []
    per_cat: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [total, wrong]
    t0 = time.perf_counter()
    for r in rows:
        try:
            # The corpus command is a single argv element; it is never shell-evaluated.
            p = subprocess.run([*guard, r["command"]], capture_output=True, timeout=args.timeout)
            allowed = p.returncode in allow
        except FileNotFoundError:
            print(f"guard not found: {guard[0]!r}", file=sys.stderr)
            return 2
        except subprocess.TimeoutExpired:
            allowed = False  # a hung guard is treated as "caught"
        per_cat[r["category"]][0] += 1
        if r["label"] == "benign" and allowed is False:
            fp.append(r["command"])
            per_cat[r["category"]][1] += 1
        elif r["label"] == "destructive" and allowed is True:
            fn.append(r["command"])
            per_cat[r["category"]][1] += 1
    elapsed = time.perf_counter() - t0

    benign = sum(1 for r in rows if r["label"] == "benign")
    danger = len(rows) - benign
    print(f"guard: {args.guard!r}  (allow-exit={sorted(allow)})")
    print(f"corpus: {len(rows)} commands ({benign} benign, {danger} destructive)")
    print(f"false positives (benign caught):      {len(fp)}/{benign} = {100*len(fp)/benign:.1f}%")
    print(f"false negatives (destructive allowed): {len(fn)}/{danger} = {100*len(fn)/danger:.1f}%")
    print(f"time: {elapsed:.2f}s total, {1000*elapsed/len(rows):.1f} ms/command")
    print("\n| category | n | wrong | rate |\n|---|---:|---:|---:|")
    for cat, (n, wrong) in sorted(per_cat.items()):
        print(f"| {cat} | {n} | {wrong} | {100*wrong/n:.1f}% |")
    if args.show_errors:
        print("\nfalse positives:")
        print("\n".join(f"  {c}" for c in fp) or "  none")
        print("\nfalse negatives:")
        print("\n".join(f"  {c}" for c in fn) or "  none")
    # exit nonzero if the guard had any error, so CI can gate on it
    return 1 if (fp or fn) else 0


if __name__ == "__main__":
    raise SystemExit(main())

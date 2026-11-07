"""Run the corpus through the engine and report false-positive / false-negative rates.

Usage: python bench/run_bench.py [--show-errors] [--write bench/RESULTS.md]
Nothing is executed; commands are only parsed and evaluated. The evaluation cwd is a
throwaway git repo under bench/.tmp-bench so "outside the project" means what it would
for a real agent working in a repo.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from cmdbelt.config import load_config  # noqa: E402
from cmdbelt.engine import Engine  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(HERE / "corpus.jsonl"))
    ap.add_argument("--show-errors", action="store_true")
    ap.add_argument("--write", help="write a markdown report to this path")
    args = ap.parse_args()

    work = HERE / ".tmp-bench"
    proj = work / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    if not (proj / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    home = work / "home"
    engine = Engine(load_config(str(home)))

    rows = [json.loads(ln) for ln in Path(args.corpus).read_text().splitlines() if ln.strip()]
    t0 = time.perf_counter()
    fp: list[tuple[str, str, str]] = []
    fn: list[tuple[str, str]] = []
    per_cat: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [total, wrong]
    actions = {"allow": 0, "ask": 0, "deny": 0}
    for r in rows:
        d = engine.evaluate(r["command"], str(proj))
        actions[d.action] += 1
        per_cat[r["category"]][0] += 1
        if r["expected"] == "allow" and d.action != "allow":
            fp.append((r["command"], d.action, d.rule_id or "?"))
            per_cat[r["category"]][1] += 1
        elif r["expected"] == "caught" and d.action == "allow":
            fn.append((r["command"], r["category"]))
            per_cat[r["category"]][1] += 1
    elapsed = time.perf_counter() - t0
    benign = sum(1 for r in rows if r["expected"] == "allow")
    danger = len(rows) - benign

    lines = [
        f"corpus: {len(rows)} commands ({benign} benign, {danger} dangerous)",
        f"decisions: {actions}",
        f"false positives (benign -> ask/deny): {len(fp)}/{benign} = {100 * len(fp) / benign:.1f}%",
        f"false negatives (dangerous -> allow): {len(fn)}/{danger} = {100 * len(fn) / danger:.1f}%",
        f"time: {elapsed:.2f}s total, {1000 * elapsed / len(rows):.2f} ms/command",
        "",
        "| category | n | wrong | rate |",
        "|---|---:|---:|---:|",
    ]
    for cat, (n, wrong) in sorted(per_cat.items()):
        lines.append(f"| {cat} | {n} | {wrong} | {100 * wrong / n:.1f}% |")
    if args.show_errors or args.write:
        lines += ["", "### False positives (benign flagged)", ""]
        lines += [f"- `{c}` -> {a} ({rule})" for c, a, rule in fp] or ["- none"]
        lines += ["", "### False negatives (dangerous allowed)", ""]
        lines += [f"- `{c}` ({cat})" for c, cat in fn] or ["- none"]
    report = "\n".join(lines) + "\n"
    print(report)
    if args.write:
        Path(args.write).write_text("# Bench results\n\n" + report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

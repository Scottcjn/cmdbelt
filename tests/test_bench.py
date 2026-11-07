"""The bench corpus is also a regression suite: size and error-rate gates."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from cmdbelt.config import load_config
from cmdbelt.engine import Engine

CORPUS = Path(__file__).resolve().parent.parent / "bench" / "corpus.jsonl"


def test_corpus_size_and_labels() -> None:
    rows = [json.loads(ln) for ln in CORPUS.read_text().splitlines() if ln.strip()]
    assert len(rows) >= 500
    assert {r["expected"] for r in rows} == {"allow", "caught"}
    assert len({r["command"] for r in rows}) == len(rows)  # no duplicates
    assert sum(r["expected"] == "allow" for r in rows) >= 300
    assert sum(r["expected"] == "caught" for r in rows) >= 150


def test_corpus_error_rates(guard_home: Path, tmp_path: Path) -> None:
    proj = tmp_path / "bench-proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    engine = Engine(load_config(str(guard_home)))
    rows = [json.loads(ln) for ln in CORPUS.read_text().splitlines() if ln.strip()]
    fp = [
        r["command"]
        for r in rows
        if r["expected"] == "allow" and engine.evaluate(r["command"], str(proj)).action != "allow"
    ]
    fn = [
        r["command"]
        for r in rows
        if r["expected"] == "caught" and engine.evaluate(r["command"], str(proj)).action == "allow"
    ]
    benign = sum(r["expected"] == "allow" for r in rows)
    danger = len(rows) - benign
    assert len(fp) / benign <= 0.02, fp
    assert len(fn) / danger <= 0.02, fn

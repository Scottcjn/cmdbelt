"""Budgets (incl. multi-process concurrency) and the audit log."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from cmdbelt.audit import AuditLog
from cmdbelt.budget import BudgetStore
from cmdbelt.config import BudgetSpec, load_config, parse_duration
from cmdbelt.guard import guard


def specs(limit: int = 3, window: int = 3600, spacing: int = 0) -> dict[str, BudgetSpec]:
    return {"gh": BudgetSpec("gh", limit, window, spacing)}


def test_budget_limit_and_reset(guard_home: Path) -> None:
    store = BudgetStore(guard_home, specs(limit=3))
    t0 = 1_000_000.0
    for i in range(3):
        r = store.try_consume("gh", f"cmd{i}", now=t0 + i)
        assert r.ok and r.used == i + 1
    r = store.try_consume("gh", "cmd3", now=t0 + 3)
    assert not r.ok and r.used == 3 and "over budget" in r.reason
    assert r.reset_at == t0 + 3600
    # window slides: after the oldest event expires one slot frees up
    r = store.try_consume("gh", "later", now=t0 + 3601)
    assert r.ok and r.used == 3


def test_budget_min_spacing(guard_home: Path) -> None:
    store = BudgetStore(guard_home, specs(limit=100, spacing=20))
    assert store.try_consume("gh", now=100.0).ok
    r = store.try_consume("gh", now=110.0)
    assert not r.ok and "too soon" in r.reason
    assert store.try_consume("gh", now=121.0).ok


def test_budget_check_does_not_consume(guard_home: Path) -> None:
    store = BudgetStore(guard_home, specs(limit=1))
    assert store.check("gh").ok
    assert store.check("gh").used == 0
    assert store.try_consume("gh").ok
    assert not store.check("gh").ok


def test_unknown_budget_unlimited(guard_home: Path) -> None:
    store = BudgetStore(guard_home, specs())
    assert store.try_consume("nope").ok


def test_budget_status_and_reset(guard_home: Path) -> None:
    store = BudgetStore(guard_home, specs(limit=5))
    store.try_consume("gh")
    store.try_consume("gh")
    st = store.status()
    assert len(st) == 1 and st[0].used == 2 and st[0].limit == 5
    assert store.reset("gh") == 2
    assert store.status()[0].used == 0


WORKER = r"""
import sys
from pathlib import Path
from cmdbelt.budget import BudgetStore
from cmdbelt.config import BudgetSpec
home = Path(sys.argv[1]); n = int(sys.argv[2])
store = BudgetStore(home, {"gh": BudgetSpec("gh", 25, 3600, 0)})
ok = sum(1 for _ in range(n) if store.try_consume("gh", "x").ok)
print(ok)
"""


def test_budget_multi_process(guard_home: Path) -> None:
    """8 processes x 10 attempts against a limit of 25: exactly 25 succeed."""
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", WORKER, str(guard_home), "10"],
            stdout=subprocess.PIPE,
            text=True,
        )
        for _ in range(8)
    ]
    counts = [int(p.communicate(timeout=60)[0].strip()) for p in procs]
    assert all(p.returncode == 0 for p in procs)
    assert sum(counts) == 25, counts
    store = BudgetStore(guard_home, {"gh": BudgetSpec("gh", 25, 3600, 0)})
    assert store.status()[0].used == 25


def test_parse_duration() -> None:
    assert parse_duration("20s") == 20
    assert parse_duration("5m") == 300
    assert parse_duration("1h") == 3600
    assert parse_duration("1d") == 86400
    assert parse_duration("1w") == 604800
    assert parse_duration(42) == 42
    assert parse_duration("90") == 90


def test_config_budgets_merge(guard_home: Path) -> None:
    (guard_home / "config.toml").write_text(
        '[budgets.github_write]\nlimit = 5\n[budgets.custom]\nlimit = 2\nwindow = "10m"\n'
    )
    cfg = load_config(str(guard_home))
    assert cfg.budgets["github_write"].limit == 5
    assert cfg.budgets["github_write"].min_spacing_s == 20  # default kept
    assert cfg.budgets["custom"].window_s == 600 and cfg.budgets["custom"].min_spacing_s == 0
    assert "deploy" in cfg.budgets


# --------------------------------------------------------------------- audit


def test_audit_append_and_read(guard_home: Path) -> None:
    log = AuditLog(guard_home)
    log.write({"action": "allow", "command": "ls"})
    log.write({"action": "deny", "command": "rm -rf /"})
    recs = log.read()
    assert [r["action"] for r in recs] == ["allow", "deny"]
    assert recs[1]["pid"] > 0 and "iso" in recs[1]
    assert log.read(last=1)[0]["command"] == "rm -rf /"
    # a corrupt line does not break reading
    with (guard_home / "audit.jsonl").open("a") as f:
        f.write("{not json\n")
    assert log.read()[-1]["corrupt"] == "{not json"


# --------------------------------------------------------------------- guard() orchestration


def test_guard_allow_snapshots_and_audits(guard_home: Path, project: Path) -> None:
    cfg = load_config(str(guard_home))
    (project / "f.txt").write_text("1\n")
    r = guard(cfg, "touch f.txt", str(project), agent="test-agent", source="exec")
    assert r.action == "allow" and len(r.snapshots) == 1
    rec = AuditLog(guard_home).read()[-1]
    assert rec["agent"] == "test-agent" and rec["source"] == "exec"
    assert rec["snapshot"] == [f"{project}#1"] and rec["action"] == "allow"


def test_guard_readonly_no_snapshot(guard_home: Path, project: Path) -> None:
    cfg = load_config(str(guard_home))
    r = guard(cfg, "git status", str(project))
    assert r.action == "allow" and r.snapshots == []


def test_guard_deny_no_snapshot(guard_home: Path, project: Path) -> None:
    cfg = load_config(str(guard_home))
    r = guard(cfg, "rm -rf /", str(project))
    assert r.action == "deny" and r.snapshots == []
    assert AuditLog(guard_home).read()[-1]["rule"] == "rm-recursive-root-home"


def test_guard_budget_overrides_allow(guard_home: Path, project: Path) -> None:
    (guard_home / "config.toml").write_text("[budgets.github_write]\nlimit = 2\nmin_spacing = 0\n")
    cfg = load_config(str(guard_home))
    assert guard(cfg, "git push", str(project)).action == "allow"
    assert guard(cfg, "gh pr comment 1 -b x", str(project)).action == "allow"
    r = guard(cfg, "git push origin main", str(project))
    assert r.action == "deny" and r.decision.rule_id == "budget:github_write"
    assert "over budget" in r.reason and r.exit_code == 20
    rec = AuditLog(guard_home).read()[-1]
    assert rec["budget"] == {"name": "github_write", "used": 2, "limit": 2, "ok": False}


def test_guard_check_mode_does_not_consume(guard_home: Path, project: Path) -> None:
    (guard_home / "config.toml").write_text("[budgets.github_write]\nlimit = 1\nmin_spacing = 0\n")
    cfg = load_config(str(guard_home))
    for _ in range(3):
        assert (
            guard(cfg, "git push", str(project), consume_budget=False, snapshot=False).action
            == "allow"
        )
    assert BudgetStore(guard_home, cfg.budgets).status()[0].used == 0


def test_guard_ask_checks_budget_without_consuming(guard_home: Path, project: Path) -> None:
    (guard_home / "config.toml").write_text("[budgets.publish]\nlimit = 1\nmin_spacing = 0\n")
    cfg = load_config(str(guard_home))
    r = guard(cfg, "npm publish", str(project))
    assert r.action == "ask" and r.budget is not None and r.budget.used == 0


def test_audit_record_is_json_serialisable(guard_home: Path, project: Path) -> None:
    cfg = load_config(str(guard_home))
    guard(cfg, "ssh h 'rm -rf /'", str(project))
    line = (guard_home / "audit.jsonl").read_text().splitlines()[-1]
    rec = json.loads(line)
    assert rec["remote"] is True and rec["action"] == "deny"

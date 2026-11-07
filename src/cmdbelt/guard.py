"""One call that does the whole thing: decide, budget, snapshot, audit."""

from __future__ import annotations

from dataclasses import dataclass, field

from .audit import AuditLog
from .budget import BudgetResult, BudgetStore
from .config import Config
from .engine import Decision, Engine
from .snapshot import SnapshotEntry, SnapshotError, snapshot_before


@dataclass
class GuardResult:
    decision: Decision
    snapshots: list[SnapshotEntry] = field(default_factory=list)
    budget: BudgetResult | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def action(self) -> str:
        return self.decision.action

    @property
    def reason(self) -> str:
        r = self.decision.reason
        if self.budget is not None and not self.budget.ok:
            r = f"{self.budget.reason} [{self.budget.name}]"
        return r

    @property
    def exit_code(self) -> int:
        return {"allow": 0, "ask": 10, "deny": 20}[self.action]


def guard(
    cfg: Config,
    command: str,
    cwd: str,
    *,
    agent: str | None = None,
    source: str = "check",
    consume_budget: bool = True,
    snapshot: bool = True,
    audit: bool = True,
    engine: Engine | None = None,
) -> GuardResult:
    eng = engine or Engine(cfg)
    decision = eng.evaluate(command, cwd)
    result = GuardResult(decision)
    agent = agent or cfg.agent

    # budgets: a deny from the budget overrides an allow/ask
    if decision.budgets and decision.action != "deny":
        store = BudgetStore(cfg.home, cfg.budgets)
        for name in decision.budgets:
            if decision.action == "allow" and consume_budget:
                res = store.try_consume(name, command, agent)
            else:
                res = store.check(name)
            if not res.ok:
                result.budget = res
                decision.action = "deny"
                decision.rule_id = f"budget:{name}"
                decision.severity = "high"
                decision.reason = res.reason
                break
            result.budget = res

    # snapshot before anything that may mutate local files
    if snapshot and decision.action != "deny" and decision.mutating:
        try:
            result.snapshots = snapshot_before(cfg, cwd, command, agent=agent)
        except SnapshotError as exc:
            result.warnings.append(f"snapshot failed: {exc}")
        for e in result.snapshots:
            if e.skipped:
                result.warnings.append("snapshot skipped large files: " + ", ".join(e.skipped))

    if audit:
        AuditLog(cfg.home).write(
            {
                "source": source,
                "agent": agent,
                "cwd": cwd,
                "command": command,
                "units": [u.describe() for u in decision.parse.units],
                "rule": decision.rule_id,
                "action": decision.action,
                "severity": decision.severity,
                "reason": decision.reason,
                "snapshot": [f"{e.root}#{e.id}" for e in result.snapshots] or None,
                "budget": (
                    {
                        "name": result.budget.name,
                        "used": result.budget.used,
                        "limit": result.budget.limit,
                        "ok": result.budget.ok,
                    }
                    if result.budget
                    else None
                ),
                "remote": decision.remote,
                "unparseable": decision.unparseable,
            }
        )
    return result

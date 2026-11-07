"""Shared rate budgets: named counters with a window and minimum spacing.

State lives in ``<home>/budgets.sqlite`` and every read-modify-write is done
under an exclusive ``fcntl`` lock on ``<home>/budgets.lock`` so that many
agent processes on the same machine share one counter safely.
"""

from __future__ import annotations

import fcntl
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .config import BudgetSpec


@dataclass
class BudgetResult:
    name: str
    ok: bool
    used: int
    limit: int
    window_s: int
    reset_at: float
    reason: str = ""

    @property
    def reset_in_s(self) -> int:
        return max(0, int(self.reset_at - time.time()))

    def describe(self) -> str:
        when = time.strftime("%H:%M:%S", time.localtime(self.reset_at))
        return f"budget {self.name}: {self.used}/{self.limit} used; resets at {when}"


class BudgetStore:
    def __init__(self, home: Path, specs: dict[str, BudgetSpec]) -> None:
        self.home = home
        self.specs = specs
        self.db_path = home / "budgets.sqlite"
        self.lock_path = home / "budgets.lock"

    @contextmanager
    def _locked(self) -> Iterator[sqlite3.Connection]:
        self.home.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            conn = sqlite3.connect(self.db_path, timeout=30)
            try:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS events ("
                    "id INTEGER PRIMARY KEY, budget TEXT NOT NULL, ts REAL NOT NULL, "
                    "command TEXT, agent TEXT)"
                )
                conn.execute("CREATE INDEX IF NOT EXISTS ix_budget_ts ON events(budget, ts)")
                yield conn
                conn.commit()
            finally:
                conn.close()
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _evaluate(
        self, conn: sqlite3.Connection, name: str, now: float
    ) -> tuple[BudgetResult, BudgetSpec]:
        spec = self.specs[name]
        since = now - spec.window_s
        conn.execute("DELETE FROM events WHERE budget = ? AND ts < ?", (name, since - 1))
        rows = conn.execute(
            "SELECT ts FROM events WHERE budget = ? AND ts >= ? ORDER BY ts", (name, since)
        ).fetchall()
        used = len(rows)
        oldest = rows[0][0] if rows else now
        newest = rows[-1][0] if rows else None
        reset_at = oldest + spec.window_s if rows else now
        if spec.limit and used >= spec.limit:
            return (
                BudgetResult(
                    name,
                    False,
                    used,
                    spec.limit,
                    spec.window_s,
                    reset_at,
                    f"over budget ({used}/{spec.limit} in the last "
                    f"{_fmt(spec.window_s)}); resets in {_fmt(int(reset_at - now))}",
                ),
                spec,
            )
        if spec.min_spacing_s and newest is not None and now - newest < spec.min_spacing_s:
            wait = int(spec.min_spacing_s - (now - newest)) + 1
            return (
                BudgetResult(
                    name,
                    False,
                    used,
                    spec.limit,
                    spec.window_s,
                    newest + spec.min_spacing_s,
                    f"too soon: min spacing is {_fmt(spec.min_spacing_s)}, retry in {wait}s",
                ),
                spec,
            )
        return BudgetResult(name, True, used, spec.limit, spec.window_s, reset_at), spec

    def check(self, name: str, now: float | None = None) -> BudgetResult:
        if name not in self.specs:
            return BudgetResult(name, True, 0, 0, 0, 0.0, "no such budget: unlimited")
        now = time.time() if now is None else now
        with self._locked() as conn:
            res, _ = self._evaluate(conn, name, now)
        return res

    def try_consume(
        self,
        name: str,
        command: str = "",
        agent: str | None = None,
        now: float | None = None,
    ) -> BudgetResult:
        """Atomically check and record one event. Returns ok=False if over budget."""
        if name not in self.specs:
            return BudgetResult(name, True, 0, 0, 0, 0.0, "no such budget: unlimited")
        now = time.time() if now is None else now
        with self._locked() as conn:
            res, _ = self._evaluate(conn, name, now)
            if res.ok:
                conn.execute(
                    "INSERT INTO events(budget, ts, command, agent) VALUES (?, ?, ?, ?)",
                    (name, now, command[:500], agent),
                )
                res.used += 1
        return res

    def status(self, now: float | None = None) -> list[BudgetResult]:
        now = time.time() if now is None else now
        out = []
        with self._locked() as conn:
            for name in self.specs:
                res, _ = self._evaluate(conn, name, now)
                out.append(res)
        return out

    def reset(self, name: str | None = None) -> int:
        with self._locked() as conn:
            if name is None:
                cur = conn.execute("DELETE FROM events")
            else:
                cur = conn.execute("DELETE FROM events WHERE budget = ?", (name,))
            return int(cur.rowcount)


def _fmt(seconds: int) -> str:
    if seconds >= 86400 and seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"

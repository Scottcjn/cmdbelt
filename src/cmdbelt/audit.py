"""Append-only JSONL audit log of every decision."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, home: Path) -> None:
        self.path = home / "audit.jsonl"

    def write(self, record: dict[str, Any]) -> None:
        rec = {"time": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "pid": os.getpid()}
        rec.update(record)
        line = json.dumps(rec, sort_keys=True, default=str) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)

    def read(self, last: int | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        if last is not None:
            lines = lines[-last:]
        out: list[dict[str, Any]] = []
        for ln in lines:
            if ln.strip():
                try:
                    out.append(json.loads(ln))
                except json.JSONDecodeError:
                    out.append({"corrupt": ln})
        return out

"""Configuration: guard home directory, user config.toml loading."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found]

ENV_HOME = "AGENT_GUARD_HOME"
DEFAULT_HOME = "~/.agent-guard"


def guard_home(override: str | None = None) -> Path:
    raw = override or os.environ.get(ENV_HOME) or DEFAULT_HOME
    p = Path(os.path.expanduser(raw)).absolute()
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class BudgetSpec:
    name: str
    limit: int
    window_s: int
    min_spacing_s: int = 0


@dataclass
class Config:
    home: Path
    snapshot_roots: list[str] = field(default_factory=list)
    snapshot_max_file_mb: int = 50
    disable_rules: list[str] = field(default_factory=list)
    user_rules: list[dict[str, Any]] = field(default_factory=list)
    budgets: dict[str, BudgetSpec] = field(default_factory=dict)
    agent: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def config_path(self) -> Path:
        return self.home / "config.toml"


def parse_duration(value: str | int | float) -> int:
    """'20s', '5m', '1h', '1d', '1w' or a bare number of seconds -> seconds."""
    if isinstance(value, (int, float)):
        return int(value)
    v = value.strip().lower()
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    if v and v[-1] in mult:
        return int(float(v[:-1]) * mult[v[-1]])
    return int(float(v))


DEFAULT_BUDGETS: dict[str, dict[str, Any]] = {
    "github_write": {"limit": 60, "window": "1d", "min_spacing": "20s"},
    "deploy": {"limit": 10, "window": "1d", "min_spacing": "60s"},
    "publish": {"limit": 3, "window": "1d", "min_spacing": "60s"},
}


def load_config(home: str | None = None) -> Config:
    h = guard_home(home)
    cfg = Config(home=h)
    data: dict[str, Any] = {}
    path = h / "config.toml"
    if path.exists():
        with path.open("rb") as f:
            data = tomllib.load(f)
    cfg.raw = data
    guard = data.get("guard", {})
    cfg.snapshot_roots = list(guard.get("snapshot_roots", []))
    cfg.snapshot_max_file_mb = int(guard.get("snapshot_max_file_mb", 50))
    cfg.disable_rules = list(guard.get("disable_rules", []))
    cfg.agent = guard.get("agent")
    cfg.user_rules = list(data.get("rule", []))
    merged = {k: dict(v) for k, v in DEFAULT_BUDGETS.items()}
    for name, spec in data.get("budgets", {}).items():
        merged[name] = {**merged.get(name, {}), **spec}
    for name, spec in merged.items():
        cfg.budgets[name] = BudgetSpec(
            name=name,
            limit=int(spec.get("limit", 0)),
            window_s=parse_duration(spec.get("window", "1d")),
            min_spacing_s=parse_duration(spec.get("min_spacing", 0)),
        )
    return cfg

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from cmdbelt.config import load_config
from cmdbelt.engine import Engine


@pytest.fixture
def guard_home(tmp_path: Path) -> Path:
    h = tmp_path / "guard-home"
    h.mkdir()
    return h


@pytest.fixture
def project(tmp_path: Path) -> Iterator[Path]:
    p = tmp_path / "proj"
    p.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=p, check=True)
    (p / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=p, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
        cwd=p,
        check=True,
    )
    old = os.getcwd()
    os.chdir(p)
    try:
        yield p
    finally:
        os.chdir(old)


@pytest.fixture
def engine(guard_home: Path) -> Engine:
    return Engine(load_config(str(guard_home)))

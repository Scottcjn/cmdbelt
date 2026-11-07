"""CLI tests: check/explain exit codes and JSON output."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cmdbelt.cli import main


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = main(list(argv))
    out = capsys.readouterr().out
    return code, out


def test_check_allow(guard_home: Path, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "--home", str(guard_home), "check", "--", "ls", "-la")
    assert code == 0 and out.startswith("ALLOW")


def test_check_ask(guard_home: Path, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "--home", str(guard_home), "check", "--", "git", "stash")
    assert code == 10 and out.startswith("ASK")


def test_check_deny(guard_home: Path, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "--home", str(guard_home), "check", "rm -rf /")
    assert code == 20 and out.startswith("DENY") and "rm-recursive-root-home" in out


def test_check_json(guard_home: Path, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(
        capsys, "--home", str(guard_home), "check", "--json", "--", "docker", "system", "prune"
    )
    d = json.loads(out)
    assert code == 20 and d["action"] == "deny" and d["rule"] == "docker-prune"
    assert d["exit_code"] == 20


def test_explain_shows_tree(
    guard_home: Path, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run(capsys, "--home", str(guard_home), "explain", "ssh h 'sudo rm -rf /' | tee x")
    assert code == 20
    assert "command:" in out and "REMOTE" in out and "matched rules:" in out
    assert "* unit[1] rm-recursive-root-home -> deny" in out


def test_check_cwd_flag(
    guard_home: Path, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = project.parent / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=other, check=True)
    # deleting the project dir from elsewhere is outside *that* cwd's project
    code, _ = run(
        capsys,
        "--home",
        str(guard_home),
        "check",
        "--cwd",
        str(other),
        "--",
        "rm",
        "-rf",
        str(project),
    )
    assert code == 20


def test_no_command_errors(guard_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--home", str(guard_home), "check"])

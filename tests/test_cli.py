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


def test_snapshot_undo_log_cli(
    guard_home: Path, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / "a.txt").write_text("v1\n")
    code, out = run(capsys, "--home", str(guard_home), "snapshot", "--", "echo", "step1")
    assert code == 0 and out.startswith("snapshot #1")
    (project / "a.txt").write_text("v2\n")
    (project / "junk.txt").write_text("x\n")
    code, out = run(capsys, "--home", str(guard_home), "undo", "--dry-run")
    assert code == 0 and "restore  a.txt" in out and "delete   junk.txt" in out
    assert (project / "a.txt").read_text() == "v2\n"
    code, out = run(capsys, "--home", str(guard_home), "undo", "-y")
    assert code == 0 and "restored 2 path(s)" in out
    assert (project / "a.txt").read_text() == "v1\n" and not (project / "junk.txt").exists()
    code, out = run(capsys, "--home", str(guard_home), "log")
    assert code == 0 and "#1" in out and "echo step1" in out and "[pre-undo]" in out
    code, out = run(capsys, "--home", str(guard_home), "log", "--json")
    assert json.loads(out)[0]["command"] == "echo step1"


def test_undo_nothing_to_do(
    guard_home: Path, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run(capsys, "--home", str(guard_home), "undo", "-y")
    assert code == 1  # no snapshots yet
    run(capsys, "--home", str(guard_home), "snapshot", "x")
    code, out = run(capsys, "--home", str(guard_home), "undo", "-y")
    assert code == 0 and "nothing to undo" in out


def test_budget_and_audit_cli(
    guard_home: Path, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run(
        capsys, "--home", str(guard_home), "check", "--agent", "bot", "--", "git", "push"
    )
    assert code == 0 and "budget github_write: 0/60" in out
    code, out = run(capsys, "--home", str(guard_home), "budget")
    assert code == 0 and "github_write" in out and "0/60" in out
    code, out = run(capsys, "--home", str(guard_home), "audit", "-n", "5")
    assert code == 0 and "ALLOW check/bot git-push  git push" in out
    code, out = run(capsys, "--home", str(guard_home), "budget", "reset", "github_write")
    assert code == 0 and "reset 0 event(s) for github_write" in out
    code, out = run(capsys, "--home", str(guard_home), "audit", "--json")
    assert json.loads(out)[0]["agent"] == "bot"

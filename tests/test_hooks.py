"""Hook I/O contract (Claude Code + Codex), exec, install snippets."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cmdbelt.cli import main
from cmdbelt.config import load_config
from cmdbelt.hooks import serve
from cmdbelt.integrations import codex_rules
from cmdbelt.snapshot import SnapshotStore


def claude_payload(command: str, cwd: str, tool: str = "Bash") -> dict[str, object]:
    # shape copied from the Claude Code hooks reference (2026-11-06)
    return {
        "session_id": "abc123",
        "transcript_path": "/home/user/.claude/projects/x/transcript.jsonl",
        "cwd": cwd,
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"command": command, "description": "test", "timeout": 120000},
        "tool_use_id": "toolu_01ABC123",
    }


def run_hook(
    guard_home: Path, payload: object, agent: str = "claude-code", **kw: str
) -> tuple[int, str, str]:
    cfg = load_config(str(guard_home))
    stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
    out, err = io.StringIO(), io.StringIO()
    code = serve(cfg, agent=agent, stdin=stdin, stdout=out, stderr=err, **kw)  # type: ignore[arg-type]
    return code, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------- Claude Code


def test_claude_deny_exit2_with_stderr(guard_home: Path, project: Path) -> None:
    code, out, err = run_hook(guard_home, claude_payload("rm -rf /", str(project)))
    assert code == 2
    assert out == ""
    assert err.startswith("cmdbelt [rm-recursive-root-home]:") and "recursive delete" in err


def test_claude_ask_json(guard_home: Path, project: Path) -> None:
    code, out, err = run_hook(guard_home, claude_payload("git stash", str(project)))
    assert code == 0 and err == ""
    d = json.loads(out)
    h = d["hookSpecificOutput"]
    assert h["hookEventName"] == "PreToolUse"
    assert h["permissionDecision"] == "ask"
    assert h["permissionDecisionReason"].startswith("cmdbelt [git-stash-bare]:")
    assert set(d) == {"hookSpecificOutput"}


def test_claude_allow_is_silent_and_snapshots(guard_home: Path, project: Path) -> None:
    (project / "f.txt").write_text("1\n")
    code, out, err = run_hook(guard_home, claude_payload("touch f.txt", str(project)))
    assert (code, out, err) == (0, "", "")
    entries = SnapshotStore(guard_home, str(project)).entries()
    assert len(entries) == 1 and entries[0].command == "touch f.txt"
    assert entries[0].agent == "claude-code"


def test_claude_readonly_allow_no_snapshot(guard_home: Path, project: Path) -> None:
    code, out, err = run_hook(guard_home, claude_payload("git status", str(project)))
    assert (code, out, err) == (0, "", "")
    assert SnapshotStore(guard_home, str(project)).entries() == []


def test_non_bash_tool_ignored(guard_home: Path, project: Path) -> None:
    code, out, err = run_hook(guard_home, claude_payload("rm -rf /", str(project), tool="Write"))
    assert (code, out, err) == (0, "", "")


def test_other_event_ignored(guard_home: Path, project: Path) -> None:
    p = claude_payload("rm -rf /", str(project))
    p["hook_event_name"] = "PostToolUse"
    assert run_hook(guard_home, p) == (0, "", "")


def test_bad_json_fails_closed(guard_home: Path) -> None:
    code, _out, err = run_hook(guard_home, "{not json")
    assert code == 2 and "fail closed" in err


def test_non_object_fails_closed(guard_home: Path) -> None:
    code, _, err = run_hook(guard_home, "[1,2]")
    assert code == 2 and "fail closed" in err


def test_empty_command_ignored(guard_home: Path, project: Path) -> None:
    assert run_hook(guard_home, claude_payload("   ", str(project))) == (0, "", "")


def test_subagent_label(guard_home: Path, project: Path) -> None:
    p = claude_payload("touch x", str(project))
    p["agent_type"] = "reviewer"
    run_hook(guard_home, p)
    assert SnapshotStore(guard_home, str(project)).entries()[0].agent == "claude-code:reviewer"


def test_hook_uses_payload_cwd_not_process_cwd(
    guard_home: Path, project: Path, tmp_path: Path
) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=other, check=True)
    # cwd in payload is `other`; deleting the project dir is outside it -> deny
    code, _, err = run_hook(guard_home, claude_payload(f"rm -rf {project}", str(other)))
    assert code == 2 and "outside" in err


def test_budget_deny_via_hook(guard_home: Path, project: Path) -> None:
    (guard_home / "config.toml").write_text("[budgets.github_write]\nlimit = 1\nmin_spacing = 0\n")
    assert run_hook(guard_home, claude_payload("git push", str(project)))[0] == 0
    code, _, err = run_hook(guard_home, claude_payload("git push", str(project)))
    assert code == 2 and "over budget" in err and "github_write" in err


def test_hook_end_to_end_subprocess(guard_home: Path, project: Path) -> None:
    """Drive the real console entry point the way Claude Code would."""
    payload = json.dumps(claude_payload("docker volume prune -f", str(project)))
    p = subprocess.run(
        [sys.executable, "-m", "cmdbelt", "--home", str(guard_home), "hook", "claude-code"],
        input=payload,
        capture_output=True,
        text=True,
        cwd=project,
        check=False,
    )
    assert p.returncode == 2 and "docker-prune" in p.stderr and p.stdout == ""


# --------------------------------------------------------------------- Codex


def codex_payload(command: str, cwd: str, event: str = "PreToolUse") -> dict[str, object]:
    # all fields required by codex-rs/hooks/schema/generated/pre-tool-use.command.input.schema.json
    d: dict[str, object] = {
        "cwd": cwd,
        "hook_event_name": event,
        "model": "gpt-5.5",
        "permission_mode": "default",
        "session_id": "s1",
        "tool_input": {"command": command},
        "tool_name": "Bash",
        "transcript_path": None,
        "turn_id": "t1",
    }
    if event == "PreToolUse":
        d["tool_use_id"] = "call_1"
    return d


def test_codex_deny(guard_home: Path, project: Path) -> None:
    code, out, err = run_hook(
        guard_home, codex_payload("terraform destroy", str(project)), agent="codex"
    )
    assert code == 2 and out == "" and "terraform-destroy" in err


def test_codex_ask_becomes_deny_by_default(guard_home: Path, project: Path) -> None:
    code, out, err = run_hook(guard_home, codex_payload("git stash", str(project)), agent="codex")
    assert code == 2 and out == ""
    assert "Codex PreToolUse hooks cannot prompt" in err


def test_codex_ask_allow_mode_adds_context_only(guard_home: Path, project: Path) -> None:
    code, out, err = run_hook(
        guard_home, codex_payload("git stash", str(project)), agent="codex", codex_ask="allow"
    )
    assert code == 0 and err == ""
    h = json.loads(out)["hookSpecificOutput"]
    assert h["hookEventName"] == "PreToolUse" and "permissionDecision" not in h
    assert "needs human confirmation" in h["additionalContext"]


def test_codex_permission_request_deny(guard_home: Path, project: Path) -> None:
    code, out, err = run_hook(
        guard_home,
        codex_payload("rm -rf ~", str(project), event="PermissionRequest"),
        agent="codex",
        event="PermissionRequest",
    )
    assert code == 0 and err == ""
    d = json.loads(out)["hookSpecificOutput"]
    assert d["hookEventName"] == "PermissionRequest"
    assert (
        d["decision"]["behavior"] == "deny" and "rm-recursive-root-home" in d["decision"]["message"]
    )
    assert set(d["decision"]) == {"behavior", "message"}  # updatedInput etc. would fail closed


def test_codex_permission_request_no_opinion(guard_home: Path, project: Path) -> None:
    code, out, err = run_hook(
        guard_home,
        codex_payload("git stash", str(project), event="PermissionRequest"),
        agent="codex",
        event="PermissionRequest",
    )
    assert (code, out, err) == (0, "", "")
    # a permission-request check never takes a snapshot or consumes budget
    assert SnapshotStore(guard_home, str(project)).entries() == []


# --------------------------------------------------------------------- install snippets


def test_install_claude_code_snippet(guard_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--home", str(guard_home), "install", "claude-code"]) == 0
    out = capsys.readouterr().out
    body = "\n".join(ln for ln in out.splitlines() if not ln.startswith("#"))
    d = json.loads(body)
    hook = d["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == "Bash"
    assert hook["hooks"][0]["type"] == "command"
    assert "hook claude-code" in hook["hooks"][0]["command"]
    assert f"--home {guard_home}" in hook["hooks"][0]["command"]
    assert "~/.claude/settings.json" in out and "never edits" in out


def test_install_codex_snippet(guard_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--home", str(guard_home), "install", "codex"]) == 0
    out = capsys.readouterr().out
    body = "\n".join(ln for ln in out.splitlines() if not ln.startswith("#"))
    d = json.loads(body)
    assert d["PreToolUse"][0]["matcher"] == "Bash"
    assert "--event PermissionRequest" in d["PermissionRequest"][0]["hooks"][0]["command"]
    assert 'cannot return "ask"' in out


def test_install_codex_rules(guard_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--home", str(guard_home), "install", "codex", "--rules"]) == 0
    out = capsys.readouterr().out
    assert 'pattern = [["docker", "podman"], ' in out
    assert 'decision = "forbidden"' in out and 'decision = "prompt"' in out
    assert "terraform-destroy" in out
    # only prefix-expressible rules: nothing about rm -rf / or curl|sh
    assert "rm-recursive-root-home" not in out and "curl-pipe-shell" not in out
    assert out.count("prefix_rule(") >= 10


def test_codex_rules_balanced() -> None:
    txt = codex_rules()
    assert txt.count("(") == txt.count(")") and txt.count("[") == txt.count("]")


def test_install_shim(guard_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--home", str(guard_home), "install", "shim"]) == 0
    assert "weakest" in capsys.readouterr().out


# --------------------------------------------------------------------- exec


def test_exec_allow_runs_and_snapshots(guard_home: Path, project: Path) -> None:
    code = main(
        ["--home", str(guard_home), "exec", "--cwd", str(project), "--", "touch", "made.txt"]
    )
    assert code == 0 and (project / "made.txt").exists()
    entries = SnapshotStore(guard_home, str(project)).entries()
    assert len(entries) == 1 and entries[0].command == "touch made.txt"


def test_exec_propagates_exit_code(guard_home: Path, project: Path) -> None:
    code = main(["--home", str(guard_home), "exec", "--cwd", str(project), "sh -c 'exit 7'"])
    assert code == 7


def test_exec_deny(guard_home: Path, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--home", str(guard_home), "exec", "--cwd", str(project), "--", "rm", "-rf", "/"])
    assert code == 20 and "DENY" in capsys.readouterr().err


def test_exec_ask_non_interactive(
    guard_home: Path,
    project: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    code = main(["--home", str(guard_home), "exec", "--cwd", str(project), "--", "git", "stash"])
    assert code == 10 and "not interactive" in capsys.readouterr().err


def test_exec_ask_yes_runs(guard_home: Path, project: Path) -> None:
    code = main(
        [
            "--home",
            str(guard_home),
            "exec",
            "-y",
            "--cwd",
            str(project),
            "--",
            "git",
            "stash",
            "list",
        ]
    )
    assert code == 0


def test_exec_shell_syntax(guard_home: Path, project: Path) -> None:
    code = main(
        [
            "--home",
            str(guard_home),
            "exec",
            "--cwd",
            str(project),
            "echo hi > out.txt && cat out.txt",
        ]
    )
    assert code == 0 and (project / "out.txt").read_text() == "hi\n"


def test_exec_shim_mode_runs_argv_directly(guard_home: Path, project: Path) -> None:
    code = main(
        ["--home", str(guard_home), "exec", "--shim", "--cwd", str(project), "--", "touch", "s.txt"]
    )
    assert code == 0 and (project / "s.txt").exists()
    assert os.environ.get("PATH")  # untouched for the parent

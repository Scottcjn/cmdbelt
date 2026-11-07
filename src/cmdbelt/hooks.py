"""Agent hook adapters.

Claude Code ``PreToolUse`` (verified against https://code.claude.com/docs/en/hooks, 2026-11-06):
  stdin  : {"hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": "..."}, "cwd": "...", "session_id": ..., ...}
  deny   : exit 2, reason on stderr (Claude sees the reason)
  ask    : exit 0, stdout {"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "permissionDecision": "ask", "permissionDecisionReason": "..."}}
  allow  : exit 0, no stdout -> Claude Code's own permission rules still apply
  Precedence across hooks: deny > defer > ask > allow.

Codex CLI ``PreToolUse`` (openai/codex codex-rs/hooks/schema, docs "hooks", 2026-11-06):
  same input/output wire shape, BUT ``permissionDecision: "ask"`` is *unsupported*:
  Codex marks the hook as failed and continues the tool call. So for Codex an
  ``ask`` must be turned into a ``deny`` (fail closed) or expressed as a
  ``prefix_rule(decision="prompt")`` in ``~/.codex/rules/*.rules``.

Codex ``PermissionRequest``: fires when Codex is about to ask the user. Output
  {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
   "decision": {"behavior": "deny", "message": "..."}}}; no decision -> normal prompt.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, TextIO

from .config import Config
from .guard import GuardResult, guard

AGENT_CLAUDE = "claude-code"
AGENT_CODEX = "codex"


@dataclass
class HookOutcome:
    exit_code: int
    stdout: str = ""
    stderr: str = ""


def _reason(r: GuardResult) -> str:
    rid = r.decision.rule_id or "policy"
    return f"cmdbelt [{rid}]: {r.reason}"


def _json(obj: dict[str, Any]) -> str:
    return json.dumps(obj)


def _extract(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    tool = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input") or {}
    if tool != "Bash" or not isinstance(tool_input, dict):
        return None, None
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return None, None
    cwd = payload.get("cwd")
    return command, (str(cwd) if cwd else None)


def run_pre_tool_use(
    cfg: Config,
    payload: dict[str, Any],
    *,
    agent: str,
    codex_ask: str = "deny",
) -> HookOutcome:
    """Evaluate a PreToolUse payload. Pure: returns what to print and the exit code."""
    if payload.get("hook_event_name") not in (None, "PreToolUse"):
        return HookOutcome(0)
    command, cwd = _extract(payload)
    if command is None:
        return HookOutcome(0)  # not a shell command: no opinion
    import os

    cwd = cwd or os.getcwd()
    agent_label = agent
    if payload.get("agent_type"):
        agent_label = f"{agent}:{payload['agent_type']}"
    r = guard(cfg, command, cwd, agent=agent_label, source=f"hook:{agent}")
    reason = _reason(r)
    if r.action == "deny":
        return HookOutcome(2, stderr=reason + "\n")
    if r.action == "ask":
        if agent == AGENT_CODEX:
            if codex_ask == "allow":
                # let Codex's own approval policy decide; attach context so the model knows
                return HookOutcome(
                    0,
                    stdout=_json(
                        {
                            "hookSpecificOutput": {
                                "hookEventName": "PreToolUse",
                                "additionalContext": reason
                                + " (needs human confirmation; Codex hooks cannot prompt)",
                            }
                        }
                    )
                    + "\n",
                )
            return HookOutcome(
                2,
                stderr=reason
                + "\nThis needs a human's OK. Codex PreToolUse hooks cannot prompt, so cmdbelt"
                " blocks it; a human can run it outside Codex, or add an allow override in"
                " ~/.agent-guard/config.toml.\n",
            )
        return HookOutcome(
            0,
            stdout=_json(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "ask",
                        "permissionDecisionReason": reason,
                    }
                }
            )
            + "\n",
        )
    # allow: stay silent so the agent's own permission system still applies
    if r.warnings:
        return HookOutcome(
            0, stdout=_json({"systemMessage": "cmdbelt: " + "; ".join(r.warnings)}) + "\n"
        )
    return HookOutcome(0)


def run_permission_request(cfg: Config, payload: dict[str, Any], *, agent: str) -> HookOutcome:
    """Codex PermissionRequest: deny what policy denies; otherwise leave the prompt alone."""
    if payload.get("hook_event_name") not in (None, "PermissionRequest"):
        return HookOutcome(0)
    command, cwd = _extract(payload)
    if command is None:
        return HookOutcome(0)
    import os

    r = guard(
        cfg,
        command,
        cwd or os.getcwd(),
        agent=agent,
        source=f"hook:{agent}:permission",
        consume_budget=False,
        snapshot=False,
    )
    if r.action == "deny":
        return HookOutcome(
            0,
            stdout=_json(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PermissionRequest",
                        "decision": {"behavior": "deny", "message": _reason(r)},
                    }
                }
            )
            + "\n",
        )
    return HookOutcome(0)


def serve(
    cfg: Config,
    *,
    agent: str,
    event: str = "PreToolUse",
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    codex_ask: str = "deny",
) -> int:
    """Read one JSON payload from stdin, write the outcome, return the exit code."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    raw = stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print("cmdbelt: could not parse hook input as JSON; blocking (fail closed)", file=stderr)
        return 2
    if not isinstance(payload, dict):
        print("cmdbelt: unexpected hook input; blocking (fail closed)", file=stderr)
        return 2
    try:
        if event == "PermissionRequest":
            out = run_permission_request(cfg, payload, agent=agent)
        else:
            out = run_pre_tool_use(cfg, payload, agent=agent, codex_ask=codex_ask)
    except Exception as exc:
        print(f"cmdbelt: internal error ({exc!r}); blocking (fail closed)", file=stderr)
        return 2
    if out.stdout:
        stdout.write(out.stdout)
        stdout.flush()
    if out.stderr:
        stderr.write(out.stderr)
        stderr.flush()
    return out.exit_code

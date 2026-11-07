# Integrations — verified contracts (2026-11-06)

All snippets are printed by `cmdbelt install <target>`; cmdbelt never writes to
your settings files.

## Claude Code — `PreToolUse` hook (supported, full allow/ask/deny)

Source: https://code.claude.com/docs/en/hooks (read 2026-11-06).

* Settings files: `~/.claude/settings.json` (user), `.claude/settings.json`
  (project), `.claude/settings.local.json` (project-local). Hook entry:

  ```json
  {"hooks": {"PreToolUse": [{"matcher": "Bash",
     "hooks": [{"type": "command", "command": "/path/to/cmdbelt hook claude-code", "timeout": 120}]}]}}
  ```
* stdin (abridged, verbatim field names): `session_id`, `transcript_path`, `cwd`,
  `permission_mode`, `hook_event_name: "PreToolUse"`, `tool_name: "Bash"`,
  `tool_input: {"command": "...", "description": ..., "timeout": ...}`, `tool_use_id`,
  plus `agent_id`/`agent_type` inside subagents.
* Exit codes: `0` = success, stdout JSON (if any) is applied; `2` = **block**,
  stderr is fed back to Claude as the reason; other = non-blocking error, the call continues.
* JSON (exit 0):
  `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow|deny|ask|defer",
  "permissionDecisionReason": "...", "updatedInput": {...}, "additionalContext": "..."}}`.
  For `ask` the reason is shown to the user in the permission prompt; for `deny` it goes to Claude.
  Deprecated top-level `decision: "approve"|"block"` / `reason` still map to allow/deny.
* Multiple hooks: precedence `deny > defer > ask > allow`. Claude Code's own deny/ask
  permission rules are still evaluated whatever the hook returns.

What cmdbelt does: **deny → exit 2 + stderr**; **ask → JSON `ask`**; **allow → silent exit 0**
(so your own Claude Code permission rules still apply; cmdbelt never auto-approves).
A snapshot is taken on allow/ask for mutating commands before the tool runs.

## Codex CLI — `hooks.json` (supported, deny only) + `rules/*.rules` (prompt)

Sources: https://learn.chatgpt.com/docs/hooks and
https://learn.chatgpt.com/docs/agent-configuration/rules (both redirected from
developers.openai.com/codex/..., read 2026-11-06); JSON schemas in
`openai/codex` `codex-rs/hooks/schema/generated/*.schema.json`.

* Config: `~/.codex/hooks.json` (user) or `<repo>/.codex/hooks.json` (project; only when
  the project `.codex/` layer is trusted), or inline `[hooks]` tables in `config.toml`.
  Hooks are enabled by default; `[features] hooks = false` disables. Non-managed hooks must
  be reviewed/trusted via `/hooks` inside Codex.
* Events include `PreToolUse`, `PermissionRequest`, `PostToolUse`, `SessionStart`, ...
  `matcher` is a regex on `tool_name`; shell commands (incl. unified `exec_command`) match `Bash`.
* `PreToolUse` input schema (all required): `cwd`, `hook_event_name`, `model`,
  `permission_mode`, `session_id`, `tool_input`, `tool_name`, `tool_use_id`,
  `transcript_path` (nullable), `turn_id`.
* `PreToolUse` output: same `hookSpecificOutput.permissionDecision` wire as Claude Code,
  **but `"ask"` is parsed and unsupported** — Codex marks the hook failed and continues.
  Deny = `permissionDecision: "deny"` or legacy `decision: "block"` or **exit 2 + stderr**.
* `PermissionRequest` output: `{"hookSpecificOutput": {"hookEventName": "PermissionRequest",
  "decision": {"behavior": "allow|deny", "message": "..."}}}`. `interrupt`, `updatedInput`,
  `updatedPermissions` are reserved and make the hook fail closed if present. No decision →
  normal prompt; any deny wins.
* Rules (`~/.codex/rules/*.rules`, Starlark): `prefix_rule(pattern=[...], decision=
  "allow"|"prompt"|"forbidden", justification="...")`. Prefix-only argv matching; Codex
  splits plain `&&`/`||`/`;`/`|` chains and evaluates each; redirections, substitutions,
  env vars, globs and control flow prevent splitting. Check with
  `codex execpolicy check --rules FILE -- cmd ...`.

What cmdbelt does for Codex:
* `cmdbelt hook codex` (PreToolUse): deny → exit 2 + stderr. **ask → deny** by default
  (`--codex-ask deny`, fail closed, with an explanation on stderr); `--codex-ask allow`
  instead returns only `additionalContext` and lets Codex's own approval policy decide.
* `cmdbelt hook codex --event PermissionRequest`: when Codex is about to prompt, denies
  what policy denies (no snapshot/budget side effects); otherwise stays silent.
* `cmdbelt install codex --rules` generates `prefix_rule(decision="prompt"|"forbidden")`
  for every cmdbelt rule that is a plain argv prefix (e.g. `docker system prune`,
  `terraform destroy`, `kubectl delete`). Rules that depend on flags, paths or pipes
  (`rm -rf /`, `curl | sh`, `git push --force main`) cannot be expressed in execpolicy and
  stay in the hook.

## Anything else — `cmdbelt exec` and the PATH shim (weaker)

* `cmdbelt exec -- <cmd>`: check, snapshot, run, propagate the exit code. Use it as the
  command runner for agents that let you set one.
* PATH shim (`cmdbelt install shim`): wrappers named `rm`, `git`, ... that call
  `cmdbelt exec --shim`. Only wrapped names are seen; `/bin/rm`, shell builtins,
  redirections and pipelines bypass it. Last resort.

# cmdbelt v0.1.0

**A seatbelt for coding agents.** One policy file, three outcomes per command
(allow / ask / deny), a snapshot before anything that mutates files with
one-command undo, and shared rate budgets for outward actions (GitHub writes,
deploys) across every agent on the machine — Claude Code, Codex, or any shell.

First public cut (v0.1). Built after real incidents: an agent that destroyed a
systemd setup, a `docker volume prune` that took 62 volumes, a 10-agent sweep
that made ~300 GitHub writes in an hour.

## What's in it
- **Parser** — splits real shell lines (pipes, `&&`, subshells, `bash -c`,
  `ssh host '…'`, `sudo`, `xargs`, `env`, `nohup`, `timeout`) into units,
  recursively; unparseable → fails closed to "ask".
- **Rule pack** — built-ins for the classic foot-guns (`rm -rf` outside the
  project, `git reset --hard`/`clean -fdx`/force-push to main, `docker … prune`,
  `kubectl delete`, `terraform destroy`, `mkfs`/`dd of=/dev/*`, `DROP TABLE`,
  `curl … | sh`, writes to `~/.ssh` and rc files) + your YAML overrides.
- **Snapshots + undo** — mutating commands snapshot into a shadow git dir that
  never touches your repo/index/stash; `cmdbelt undo` restores.
- **Budgets** — named counters with windows (e.g. `github_write: 60/day, min
  spacing 20s`), shared across processes via locked SQLite. Over budget → deny
  with the reset time.
- **Integrations** — Claude Code `PreToolUse` hook; generic `cmdbelt exec/check`
  (exit 0/10/20); documented wiring for Codex and others.

## Honest limits (read first)
- It guards commands that pass through it. An agent that bypasses the hook (raw
  syscalls, a script that runs its own commands) is not covered. **Seatbelt, not
  a sandbox** — pair it with containers for untrusted code.
- Undo covers files in snapshotted roots. It cannot undo remote side effects (an
  SSH command, an API call, a dropped DB) — for those it can only block or ask.
- Shell parsing is best-effort; when it can't parse, it asks.

## Measured
- **550 tests pass.** On a 1,195-command corpus (harvested agent commands +
  known-incident commands): the README reports the false-positive / false-
  negative rates — every number reproduces via `bench/run_bench.py`.

## Install
```bash
pipx install cmdbelt
cmdbelt install claude-code      # prints the settings.json snippet (does NOT edit it)
cmdbelt check -- rm -rf /        # try it
```

## Not included / not implied
No token, no telemetry — fully local. No network calls.

---
License: Apache-2.0.

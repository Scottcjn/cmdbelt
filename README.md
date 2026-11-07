# cmdbelt

A seatbelt for coding agents. One local policy file, three outcomes per shell
command (**allow / ask / deny**), a **snapshot before anything that mutates files** with
one-command **undo**, and **shared rate budgets** for outward actions (GitHub writes,
deploys) across every agent on the machine. Works with Claude Code and Codex CLI
through their hook mechanisms, and with anything else through `cmdbelt exec`.

Python 3.10+, standard library only. Apache-2.0.

## Honest limits — read this first

* **It guards commands that pass through it.** An agent that bypasses the hook (raw
  syscalls, a script that runs its own commands, a tool call that is not `Bash`) is not
  covered. This is a seatbelt, not a sandbox. Pair it with containers or VMs for code you
  do not trust.
* **Undo covers files under snapshotted roots** (by default the git worktree, or the cwd).
  It cannot undo remote side effects: an SSH command, an API call, a dropped database, a
  `docker volume prune`. For those it can only **block or ask**. Files over 50 MB are
  skipped (configurable) and ignored files (`.gitignore`) are not snapshotted.
* **Parsing is best-effort.** It follows `sudo`, `env`, `nohup`, `timeout`, `xargs`,
  `bash -c`, `ssh host '…'`, `eval`, `find -exec`, `$(…)` and backticks. When it cannot
  parse (variables in command position, `case`, function definitions, unbalanced quotes,
  deep nesting) it **fails closed to "ask"**. Shell aliases, functions already defined in
  the agent's shell, and commands run inside `chroot`/`docker exec`/`tmux send-keys` are
  not interpreted.
* **Codex CLI cannot "ask" from a `PreToolUse` hook** (its docs say an `ask` is treated as
  a failed hook and the tool call continues). For Codex, cmdbelt turns `ask` into a deny
  by default; prompts have to come from Codex's own `prefix_rule(decision="prompt")`
  rules, which cmdbelt can generate for the subset that is expressible. See
  [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).
* **The bench numbers below are from a corpus written by the same people who wrote the
  rules.** It is a regression suite with incident-derived cases, not an independent
  measurement. Expect real-world false positives on commands we did not think of; the
  override mechanism exists for that.
* Linux/macOS only (uses `fcntl` locks). No telemetry, no network: everything lives in
  `~/.agent-guard/`.

## Install

```sh
pipx install cmdbelt          # or: pip install cmdbelt   (console script: cmdbelt; alias: agent-guard)
cmdbelt --version
cmdbelt check -- 'rm -rf /'   # prints the rule and exits 20
```

From this repository: `pip install -e .` (dev extras: `pip install -e '.[dev]'`).

### Claude Code

```sh
cmdbelt install claude-code
```

prints a settings snippet (it never edits `~/.claude/settings.json` for you):

```json
{"hooks": {"PreToolUse": [{"matcher": "Bash",
  "hooks": [{"type": "command", "command": "/path/to/cmdbelt hook claude-code", "timeout": 120}]}]}}
```

Contract (per the Claude Code hooks reference, read 2026-11-06): deny → exit 2 with the
reason on stderr, which Claude sees; ask → JSON `permissionDecision: "ask"`, you see the
reason in the permission prompt; allow → cmdbelt stays silent, so your own Claude Code
permission rules still apply (cmdbelt never auto-approves anything).

### Codex CLI

```sh
cmdbelt install codex                      # hooks.json snippet (PreToolUse + PermissionRequest)
cmdbelt install codex --rules > ~/.codex/rules/cmdbelt.rules   # prompt/forbidden prefix rules
```

### Any other agent

```sh
cmdbelt exec -- <command>        # check, snapshot, run, propagate the exit code
cmdbelt install shim             # PATH-shim approach (weakest; documented as such)
```

## 60-second demo

```sh
demo/demo.sh                     # or: CMDBELT="python -m cmdbelt" demo/demo.sh
```

It creates a throwaway repo, shows a `#59060`-style command being **denied with its
reason**, a `curl | sh` and a force-push to `main` denied, an ordinary edit **allowed and
snapshotted**, then `cmdbelt undo` restoring the files while `git status` of the project's
own repository stays exactly as it was.

## Commands

| command | what it does | exit |
|---|---|---|
| `cmdbelt check -- CMD` | decide; print rule + reason (`--json` for machines) | 0 allow / 10 ask / 20 deny |
| `cmdbelt explain -- CMD` | parse tree, every rule that fired, mutation verdict | same |
| `cmdbelt exec -- CMD` | check → snapshot → run; `-y` approves asks; asks refuse when non-interactive | command's code, or 10/20 |
| `cmdbelt undo [--to N] [--dry-run] [-y]` | restore files from a snapshot (takes a pre-undo snapshot first) | 0 |
| `cmdbelt log` | snapshots for this root with the command that followed each | 0 |
| `cmdbelt snapshot -- CMD` | snapshot now, labelled with a command | 0 |
| `cmdbelt budget [status\|reset NAME]` | shared counters | 0 |
| `cmdbelt audit -n 20` | recent decisions (JSONL at `~/.agent-guard/audit.jsonl`) | 0 |
| `cmdbelt hook claude-code\|codex` | stdin JSON → decision (used by the hooks) | 0/2 |
| `cmdbelt install claude-code\|codex\|shim` | print the integration snippet | 0 |

`--home DIR` or `AGENT_GUARD_HOME` relocates everything (config, audit, budgets, snapshots).

## Policy

Built-in rules live in [`src/cmdbelt/data/builtin.toml`](src/cmdbelt/data/builtin.toml)
(100+ rules). They cover: `rm -rf` on `/`, `~`, globs, system dirs or outside the project;
`git reset --hard`, `checkout -- .`, `clean -fdx`, `push --force` to main/master, bare
`stash`; `docker system|volume|image prune`, broad `docker rm -f`, `compose down -v`;
`kubectl delete`, `helm uninstall`, `terraform destroy|apply -auto-approve`; `systemctl
stop|disable|mask|restart` (local and over ssh), `reboot`, `shutdown`, mounts;
`pkill`/`killall` (explicit `kill PID` is fine); `dd of=/dev/*`, `mkfs*`, `wipefs`,
`parted`, `fdisk`; recursive `chmod/chown` on `/` or `~`; `DROP|TRUNCATE|DELETE FROM`
without `WHERE` in sqlite3/psql/mysql args and heredocs; `curl|wget … | sh`; `crontab -r`;
writes to `/etc`, `~/.ssh`, shell rc files; `sudo` anything (ask). Anything reached over
`ssh` gets the same verdict, flagged "remote: not undoable".

Override in `~/.agent-guard/config.toml`:

```toml
[guard]
disable_rules = ["git-stash-bare"]        # drop a rule
snapshot_max_file_mb = 50
# snapshot_roots = ["/srv/app"]           # default: the git toplevel of the cwd

[[rule]]                                  # same id -> replaces the builtin (priority 10 wins)
id = "git-push-force-main"
action = "allow"
reason = "solo repo"

[[rule]]                                  # your own rule
id = "no-prod-deploy"
cmd = ["fab", "deploy.sh"]
args_regex = "prod"
action = "deny"
reason = "deploys go through CI"

[budgets.github_write]                    # gh pr/issue writes, gh api -X POST.., git push
limit = 60
window = "1d"
min_spacing = "20s"
```

Rule fields: `cmd`, `subcmd`, `subcmd2`, `flags_any/all/none`, `args_any`, `args_regex`,
`args_path_any` (categories: `root home etc ssh shell_rc dev system outside glob dynamic
dot project_root ancestor tmp home_toplevel`), `redirect_path_any`, `pipe_from`, `remote`,
`dynamic_args`, `wrappers_any`, `regex`, `text_regex`, `budget`, `mutates`, `priority`.
When several rules match one unit the highest priority wins, ties go to the stricter
action; across the units of a pipeline/chain the strictest wins. Unparseable → ask.

## Bench: measured, not claimed

`bench/corpus.jsonl` holds **1,195 labelled commands**: 600 dangerous (the SPEC list and
its evasions, the same bases wrapped in `sudo`/`env`/`bash -c`/`ssh`/`eval`/chains, the
command class from openai/codex issue #59060 — "destroyed systemd, mounts, containers" —
and our own lab incidents: `docker volume prune`, a `pkill` sweep, `git checkout -- .`)
and 595 benign ones written to look like everyday agent work (builds, tests, git
status/diff/commit, file inspection, pip/npm installs, docker ps/logs). No private
transcripts were read. Rebuild with `python bench/build_corpus.py`, run with
`python bench/run_bench.py --show-errors`.

Result on the current rule pack ([bench/RESULTS.md](bench/RESULTS.md)):

| | n | wrong | rate |
|---|---:|---:|---:|
| false positives (benign → ask/deny) | 595 | 0 | 0.0% |
| false negatives (dangerous → allow) | 600 | 0 | 0.0% |

Before the last fix round the same corpus showed 1 FP (`ssh-keygen -l -f` flagged as a
key overwrite) and 4 FN (`swapoff -a`, `umount -l`, `nmcli networking off`,
`dpkg --purge`); those are now rules. A 0/0 result on a self-authored corpus means "no
regressions", nothing more. Please file the commands that surprise you.

Tests: 550 (`pytest`), including 144 parser cases, 345 rule/mutation cases, snapshot/undo
round-trips on a repo with a dirty index and an existing stash, an 8-process budget race,
and the hook I/O contract driven through `python -m cmdbelt`. `ruff` and `mypy --strict`
are clean on the core.

## Compared with vendor-native controls (only what we verified on 2026-11-06)

| | Claude Code permissions | Codex CLI sandbox/rules/hooks | cmdbelt |
|---|---|---|---|
| allow / ask / deny per command | yes (`Bash(pattern)` rules + hooks) | `prefix_rule` allow/prompt/forbidden; hooks deny only for `PreToolUse` | yes, same policy for both |
| matches inside `sudo`/`bash -c`/`ssh`/`xargs`/`$(…)` | hook author's job | `bash -lc` handled; plain `&&`/`;`/`\|` chains split; substitutions/redirects/globs stop splitting (docs) | recursive unwrap; unparseable → ask |
| file snapshot + undo for shell commands | not provided by the hook API we read | not found in the docs we read | shadow-git snapshot before mutating commands; `undo` |
| rate budget across agents/processes | not found | not found | SQLite + lock, shared machine-wide |
| vendor-neutral | no | no | yes (hooks for both, `exec` for the rest) |
| sandboxing (fs/network isolation) | not its job | yes (read-only / workspace-write modes) | **no** — pair with their sandbox |

"Not found" means we did not find it in the pages cited in `docs/INTEGRATIONS.md`; it is
not a claim about features we did not look for.

## How it works

`parser.py` turns a command line into execution units (argv + wrappers + redirects +
pipe neighbours + heredoc), recursing into nested shells and substitutions. `rules.py`
loads the TOML pack, merges user overrides, and matches each unit using canonicalised
flags and path categories resolved against the cwd/git toplevel. `engine.py` picks the
strictest outcome and decides whether the command mutates local files. `guard.py` then
checks budgets (`budget.py`), takes a shadow-git snapshot (`snapshot.py`: `GIT_DIR` and
`GIT_WORK_TREE` point at `~/.agent-guard/snapshots/<hash>.git`, so the project's `.git`,
index and stash are never touched — tested explicitly), and appends to the audit log.
`hooks.py` speaks the Claude Code / Codex JSON contracts; `cli.py` exposes the rest.

## License

Apache-2.0. See [LICENSE](LICENSE).

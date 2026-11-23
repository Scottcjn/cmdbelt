# cmdbelt

> **⚠️ Known security issues in v0.1.0 (2026-11-23).** An external security review found command strings that the default policy should deny but v0.1.0 allows (for example quoted wrapper options, brace expansion, `${IFS}`, here-docs fed to a shell, and substitutions in `for` headers or redirects), plus budget and undo bugs. We reproduced them. A fail-closed fix is in progress for v0.2.0. **Until then, do not rely on cmdbelt to stop a determined or confused agent.** Pair it with a container or VM.

A seatbelt for coding agents. One local policy, three outcomes per shell command
(**allow / ask / deny**), a **snapshot before anything that mutates files** with one-command
**undo**, and a **shared rate budget** for outward actions across *every agent on the machine*.
Works with Claude Code and Codex CLI through their hooks, and anything else through `cmdbelt exec`.

Python 3.10+, standard library only. Apache-2.0. No network, no telemetry.

## Two things it does that your agent's built-in permissions don't

**1. It catches the command inside the wrapper — including over SSH.**
The most-reacted issue in the Codex tracker ([openai/codex #59060](https://github.com/openai/codex/issues/59060),
3,494 reactions in three days) is an agent that "destroyed systemd, destroyed mounts, destroyed
containers… cost me $10,000+." A local filesystem sandbox would not have stopped it, because the
damage was to services and remote state, not to files in the repo. cmdbelt unwraps `sudo`, `env`,
`bash -c`, `xargs`, `$(…)` **and `ssh host '…'`**, matches the real command underneath, and denies
it before it runs — flagging remote commands as *not undoable* so you know a block is the only
protection there is.

```
$ cmdbelt check -- "ssh prod 'sudo systemctl stop docker && rm -rf /var/lib/docker'"
DENY  rm-recursive-system — recursive delete inside a system directory  [remote: not undoable]
# exit 20; Claude Code and Codex see that reason and the command never runs. ~80 ms cold.
```

**2. One policy and one budget across Claude Code, Codex, and plain shells.**
If you run more than one agent — or several agents on one box — each vendor's permission system only
knows about itself. cmdbelt reads a single TOML file for all of them, and keeps a **machine-wide rate
budget** (SQLite + file lock) so that ten agents can't *collectively* fire 300 GitHub writes an hour
or a burst of deploys, even though no single one exceeded its own limit. This cross-agent, shared-state
budget is the part no single vendor builds, because a vendor only sees its own agent.

## Honest limits — read this before anything else

* **Seatbelt, not sandbox.** It guards commands that pass *through* it. An agent that bypasses the
  hook (raw syscalls, a child script that runs its own commands, a non-`Bash` tool call) is not
  covered. **Pair it with a container or VM for code you do not trust** — it reduces blast radius, it
  does not isolate.
* **Undo covers files under snapshotted roots** (the git worktree, or the cwd). It cannot undo remote
  side effects: an SSH command, an API call, a dropped database, a `docker volume prune`. For those it
  can only **block or ask**. Files over 50 MB are skipped (configurable); `.gitignore`d files are not
  snapshotted.
* **Parsing is best-effort.** It follows `sudo`, `env`, `nohup`, `timeout`, `xargs`, `bash -c`,
  `ssh host '…'`, `eval`, `find -exec`, `$(…)` and backticks. When it cannot parse (variable in
  command position, `case`, function defs, unbalanced quotes, deep nesting) it **fails closed to
  "ask."** Shell aliases/functions already defined in the agent's shell, and commands run inside
  `chroot` / `docker exec` / `tmux send-keys`, are not interpreted.
* **Codex CLI cannot "ask" from a `PreToolUse` hook** (its docs: an `ask` is treated as a failed hook
  and the call continues). cmdbelt turns `ask` into deny for Codex by default; real prompts come from
  Codex's own `prefix_rule(decision="prompt")`, which cmdbelt can generate for the expressible subset.
  See [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).
* **The bench numbers below are from a corpus written by the same people who wrote the rules.** It is
  a regression suite, not an independent measurement. Expect real-world false positives on commands we
  did not think of; the override mechanism exists for that.
* Linux/macOS only (`fcntl` locks). Everything lives in `~/.agent-guard/`.

## Install

```sh
pipx install cmdbelt          # or: pip install cmdbelt   (console script: cmdbelt; alias: agent-guard)
cmdbelt --version
cmdbelt check -- 'rm -rf /'   # prints the rule and exits 20
```

From this repository: `pip install -e .` (dev extras: `pip install -e '.[dev]'`).

### Claude Code — one line to see the snippet

```sh
cmdbelt install claude-code
```

prints a settings snippet (it never edits `~/.claude/settings.json` for you):

```json
{"hooks": {"PreToolUse": [{"matcher": "Bash",
  "hooks": [{"type": "command", "command": "/path/to/cmdbelt hook claude-code", "timeout": 120}]}]}}
```

Contract (per the Claude Code hooks reference, read 2026-11-06): deny → exit 2 with the reason on
stderr, which Claude sees; ask → JSON `permissionDecision: "ask"`; allow → cmdbelt stays silent, so
your own Claude Code permission rules still apply (cmdbelt never auto-approves anything).

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

It creates a throwaway repo, shows a `#59060`-style command **denied with its reason**, a `curl | sh`
and a force-push to `main` denied, an ordinary edit **allowed and snapshotted**, then `cmdbelt undo`
restoring the files while `git status` of the project's own repository stays exactly as it was.
See [demo/GIF_SCRIPT.md](demo/GIF_SCRIPT.md) for the three-beat recording script.

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
`git reset --hard`, `checkout -- .`, `clean -fdx`, `push --force` to main/master, bare `stash`;
`docker system|volume|image prune`, broad `docker rm -f`, `compose down -v`; `kubectl delete`,
`helm uninstall`, `terraform destroy|apply -auto-approve`; `systemctl stop|disable|mask|restart`
(local and over ssh), `reboot`, `shutdown`, mounts; `pkill`/`killall` (explicit `kill PID` is fine);
`dd of=/dev/*`, `mkfs*`, `wipefs`, `parted`, `fdisk`; recursive `chmod/chown` on `/` or `~`;
`DROP|TRUNCATE|DELETE FROM` without `WHERE` in sqlite3/psql/mysql args and heredocs; `curl|wget … | sh`;
`crontab -r`; writes to `/etc`, `~/.ssh`, shell rc files; `sudo` anything (ask). Anything reached over
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
`args_path_any` (categories: `root home etc ssh shell_rc dev system outside glob dynamic dot
project_root ancestor tmp home_toplevel`), `redirect_path_any`, `pipe_from`, `remote`, `dynamic_args`,
`wrappers_any`, `regex`, `text_regex`, `budget`, `mutates`, `priority`. When several rules match one
unit the highest priority wins, ties go to the stricter action; across the units of a pipeline/chain
the strictest wins. Unparseable → ask.

## Bench: measured, not claimed

`bench/corpus.jsonl` holds **1,195 labelled commands** (600 dangerous, 595 benign) — the SPEC list and
its evasions, the same bases wrapped in `sudo`/`env`/`bash -c`/`ssh`/`eval`/chains, the command class
from openai/codex #59060, and our own lab incidents (`docker volume prune`, a `pkill` sweep,
`git checkout -- .`). No private transcripts were read. Rebuild with `python bench/build_corpus.py`,
run with `python bench/run_bench.py --show-errors`.

**The corpus is also published as a standalone, reusable test suite** in
[`corpus/agent-incidents/`](corpus/agent-incidents/) (JSONL + CSV + a tool-agnostic runner, dataset
under CC0) so you can score *any* command guard, not just this one. Benchmarks outlive tools.

Result on the current rule pack ([bench/RESULTS.md](bench/RESULTS.md)) — **cmdbelt's own numbers on
its own corpus**:

| | n | wrong | rate |
|---|---:|---:|---:|
| false positives (benign → ask/deny) | 595 | 0 | 0.0% |
| false negatives (dangerous → allow) | 600 | 0 | 0.0% |

Before the last fix round the same corpus showed 1 FP (`ssh-keygen -l -f` flagged as a key overwrite)
and 4 FN (`swapoff -a`, `umount -l`, `nmcli networking off`, `dpkg --purge`); those are now rules. A
0/0 result on a self-authored corpus means "no regressions," nothing more. Please file the commands
that surprise you.

Tests: 550 (`pytest`), including 144 parser cases, 345 rule/mutation cases, snapshot/undo round-trips
on a repo with a dirty index and an existing stash, an 8-process budget race, and the hook I/O
contract driven through `python -m cmdbelt`. `ruff` and `mypy --strict` are clean on the core.

## How cmdbelt compares to similar tools

The "block a destructive shell command before an AI agent runs it" niche already has larger projects —
cmdbelt is **not** the first and does not claim to be the best blocker. What it combines that the
leaders do not: **cross-agent shared budgets**, **one policy across Claude Code + Codex + any shell**,
and **snapshot + undo for shell commands**. Honest landscape (stars read 2026-11-06):

| project | ~stars | blocks cmds | undo | cross-agent budget | multi-vendor |
|---|---:|---|---|---|---|
| [Dicklesworthstone/destructive_command_guard](https://github.com/Dicklesworthstone/destructive_command_guard) (Rust) | 6,280 | yes (fast) | no | no | Codex "detection only" |
| [kenryu42/cc-safety-net](https://github.com/kenryu42/cc-safety-net) (TS) | 1,935 | yes | no | no | Claude + Codex path |
| [kaplanelad/shellfirm](https://github.com/kaplanelad/shellfirm) (Rust) | 954 | yes (prompt) | no | no | terminal + agents |
| [karanb192/claude-code-hooks](https://github.com/karanb192/claude-code-hooks) | 577 | yes | no | no | Claude only |
| [arrowassassin/kintsugi](https://github.com/arrowassassin/kintsugi) | 5 | warns | **yes** | no | Claude + Codex |
| **cmdbelt** | — | yes (ask/deny) | **yes** | **yes** | Claude + Codex + `exec` |

If all you want is a fast destructive-command blocker for Claude Code, dcg or cc-safety-net are mature
and have a head start. Reach for cmdbelt when you run **more than one agent**, want **one policy and a
shared budget** across them, or want **undo** on the file changes a guard can't prevent.

### Vs. the vendor-native controls (only what we verified on 2026-11-06)

| | Claude Code permissions | Codex CLI sandbox/rules/hooks | cmdbelt |
|---|---|---|---|
| allow / ask / deny per command | yes (`Bash(pattern)` + hooks) | `prefix_rule` allow/prompt/forbidden; hooks deny-only for `PreToolUse` | yes, same policy for both |
| matches inside `sudo`/`bash -c`/`ssh`/`xargs`/`$(…)` | hook author's job | `bash -lc` handled; chains split; substitutions/redirects stop splitting (docs) | recursive unwrap; unparseable → ask |
| file snapshot + undo for shell commands | not in the hook API we read | not in the docs we read | shadow-git snapshot + `undo` |
| rate budget across agents/processes | not found | not found | SQLite + lock, machine-wide |
| sandboxing (fs/network isolation) | not its job | yes (read-only / workspace-write) | **no** — pair with their sandbox |

"Not found" means we did not find it in the pages cited in `docs/INTEGRATIONS.md`; it is not a claim
about features we did not look for.

## How it works

`parser.py` turns a command line into execution units (argv + wrappers + redirects + pipe neighbours +
heredoc), recursing into nested shells and substitutions. `rules.py` loads the TOML pack, merges user
overrides, and matches each unit using canonicalised flags and path categories resolved against the
cwd/git toplevel. `engine.py` picks the strictest outcome and decides whether the command mutates local
files. `guard.py` then checks budgets (`budget.py`), takes a shadow-git snapshot (`snapshot.py`:
`GIT_DIR`/`GIT_WORK_TREE` point at `~/.agent-guard/snapshots/<hash>.git`, so the project's `.git`, index
and stash are never touched — tested explicitly), and appends to the audit log. `hooks.py` speaks the
Claude Code / Codex JSON contracts; `cli.py` exposes the rest.

## License

Apache-2.0. See [LICENSE](LICENSE). The `corpus/agent-incidents/` dataset is additionally offered under
CC0-1.0 so it can be cited and redistributed freely.

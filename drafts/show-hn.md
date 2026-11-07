# Show HN draft — DO NOT POST (needs Scott)

HN rules honored here: title leads with "Show HN:" and is **under 80 characters**; the first
comment gives honest context and states limits up front; **no vote-asking, no "please upvote,"
no hype**; it names larger existing tools instead of pretending to be first. Post from Scott's
own account; reply to comments promptly and plainly.

## Title (choose one; both < 80 chars)

```
Show HN: Cmdbelt – one allow/ask/deny policy for Claude Code, Codex and shells
```
(76 chars)

Alt:
```
Show HN: Cmdbelt – a seatbelt for coding agents (block, snapshot, undo, budget)
```
(79 chars)

## URL

`https://github.com/<scott>/cmdbelt`  (the repo; no landing page)

## First comment (post immediately after submitting)

> Author here. cmdbelt is a small stdlib-Python tool that puts one policy in front of the
> shell commands your coding agent runs. Three outcomes per command — allow / ask / deny — plus
> a file snapshot before anything that mutates, one-command `undo`, and a rate budget that is
> **shared across every agent on the machine**.
>
> Two reasons it exists, honestly:
>
> 1. The most-reacted issue in the Codex tracker (#59060) is an agent that stopped services and
>    wiped state over what amounted to a remote command, and cost its user ~$10k. A local
>    filesystem sandbox wouldn't have caught that, because the damage wasn't to repo files.
>    cmdbelt unwraps `sudo`/`bash -c`/`ssh host '…'`/`$(…)` and matches the real command
>    underneath, so it can deny it — and it flags remote commands as *not undoable* so you know
>    a block is the only protection there.
> 2. If you run more than one agent, each vendor's permission system only knows about itself.
>    cmdbelt reads one TOML for Claude Code, Codex and plain shells, and keeps a machine-wide
>    budget so ten agents can't *collectively* fire 300 GitHub writes an hour.
>
> Limits, because they matter more than the pitch:
>
> - **It's a seatbelt, not a sandbox.** It only guards commands that pass through the hook. An
>   agent that bypasses it (raw syscalls, a child script) isn't covered. Pair it with a
>   container for code you don't trust.
> - **Undo only covers files under the git worktree.** It can't undo a dropped DB, an API call,
>   or anything over SSH — for those it can only block or ask.
> - **Parsing is best-effort and fails closed to "ask"** when it can't understand a line.
> - This niche already has bigger projects — Dicklesworthstone/destructive_command_guard (~6k
>   stars, Rust, faster) and kenryu42/cc-safety-net (~1.9k). cmdbelt isn't trying to out-block
>   them; the parts they don't do are the cross-agent budget, one policy across vendors, and
>   undo. If you only want a fast blocker for one agent, use theirs.
> - The 0-false-positive / 0-false-negative number in the README is on a **corpus we wrote
>   ourselves** (1,195 commands). It means "no regressions," not "independently verified." The
>   corpus is published separately under CC0 so you can score any guard, including ours, and
>   tell me where it's wrong.
>
> Apache-2.0, no network, no telemetry. Happy to answer anything.

## If asked "how is this different from just using Claude Code permissions?"

> Claude Code's permissions only cover Claude Code, and the hook author still has to parse the
> command. cmdbelt is the parser (it unwraps ssh/sudo/subshells and fails closed), it's the same
> policy for Codex and any shell, and it adds the snapshot/undo and the shared budget that a
> single-vendor system structurally can't — a vendor only ever sees its own agent.

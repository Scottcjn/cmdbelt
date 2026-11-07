# Reddit draft — DO NOT POST (needs Scott)

## Which subreddit

Primary: **r/commandline** — cmdbelt is an open-source CLI tool, which is squarely on-topic.
r/commandline's rules: posts must be command-line related (yes); self-promotion of your **own
open-source** tool is allowed but you must be a genuine participant, not a drive-by — link the
source, describe it plainly, and reply to comments. No affiliate links, no paywalls (none here).

Secondary (only if it lands well on r/commandline): **r/LocalLLaMA**, framed around multi-agent
local setups. r/LocalLLaMA tolerates tool posts when they're clearly useful to people running
models/agents locally and not pure marketing; lead with the multi-agent budget angle, keep it
technical, flair as "Resources" or "Tutorial | Guide". Do **not** cross-post the same text the
same day; wait and rewrite.

Rule of thumb for both: one post, honest limits in the body, answer every question. Never ask
for upvotes or stars.

---

## r/commandline post

**Title:** cmdbelt – one allow/ask/deny policy (+ snapshot/undo + shared budget) for shell commands run by coding agents

**Body:**

I kept having AI coding agents run things I'd never have run myself — a `git checkout -- .` that
wiped another process's edits, a `docker volume prune`, a `pkill` that took out more than it
should. So I wrote a small guard that sits in front of the shell commands an agent runs.

What it does:

- **allow / ask / deny** per command, from one TOML policy (100+ built-in rules for the usual
  suspects: `rm -rf` on system/outside-project paths, `git reset --hard`, force-push to main,
  `docker ... prune`, `kubectl delete`, `dd of=/dev/...`, `DROP TABLE` without `WHERE`,
  `curl | sh`, and so on).
- **Unwraps the command** — `sudo`, `env`, `bash -c`, `xargs`, `$(...)`, backticks, and
  `ssh host '...'` — and matches the real thing underneath. When it can't parse a line it fails
  closed to "ask" rather than guessing.
- **Snapshots files before anything that mutates them**, into a shadow git repo (your real
  `.git` is never touched), with one-command `cmdbelt undo`.
- **A rate budget shared across every agent on the machine** (SQLite + file lock), so several
  agents can't collectively hammer GitHub or fire a burst of deploys.

It plugs into Claude Code and Codex CLI through their hooks, and anything else through
`cmdbelt exec -- <cmd>`. Python 3.10+, standard library only, Apache-2.0, no network, no telemetry.

**Honest limits:** it's a *seatbelt, not a sandbox* — it only guards commands that go through
it; pair it with a container for untrusted code. Undo only covers files in the worktree (not a
dropped DB, not anything over SSH). The false-positive/false-negative numbers in the README are
on a corpus I wrote myself, so they mean "no regressions," not independent proof — I published
the corpus (CC0) so you can test any guard, including mine, and tell me where it's wrong. And
there are bigger tools in this space already (destructive_command_guard, cc-safety-net); the bits
they don't do are the cross-agent budget, the one-policy-across-vendors, and undo.

Repo: https://github.com/<scott>/cmdbelt — feedback and "this benign command got blocked" reports
very welcome.

---

## r/LocalLLaMA variant (only if r/commandline goes well; rewrite, don't paste)

**Title:** Running several local coding agents at once? I built a shared allow/ask/deny + budget layer so they can't collectively nuke things

Lead with: when you have Claude Code, Codex, and a local-model agent all working on one box, no
single vendor's permission system sees the others. cmdbelt is one policy + one machine-wide rate
budget across all of them, plus snapshot/undo. Same honest-limits paragraph as above. Keep it
short and technical; r/LocalLLaMA dislikes anything that reads like an ad.

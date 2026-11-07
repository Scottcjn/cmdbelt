# awesome-lists cmdbelt genuinely fits (DO NOT SUBMIT; needs Scott)

Stars read 2026-11-06. **Submit nothing until the repo has real users** — the biggest list
(awesome-claude-code) explicitly says "get users first, then submit; if getting on the list is
part of your launch plan, have a backup plan." Submitting a day-old repo to a curated list is the
kind of self-promotion that gets accounts flagged. Also: per the earlier growth report + GitHub
AUP, do **not** advertise cmdbelt inside other people's issue threads (e.g. codex #59060). These
list submissions are the sanctioned distribution path; the issue threads are not.

Each entry below: the list, its size, whether cmdbelt is genuinely on-topic, and the exact
contribution rule to follow.

## 1. hesreallyhim/awesome-claude-code — ~57,400 stars

- **Fit:** strong. cmdbelt is a Claude Code hook/tooling resource with a one-line install.
- **Rule (important):** recommendations are made **only through the web-UI issue form**
  (`Issues → New → "Recommend a resource"`), **not** via `gh` CLI or a direct PR — using the CLI
  risks a temporary interaction ban. The submission must be made **by a human**. The list is
  **selective**; it favors resources that already have users and that lean on Claude Code's unique
  features. A proper `LICENSE` file (Apache-2.0 is present) must be auto-detectable by GitHub.
- **Submission link:** https://github.com/hesreallyhim/awesome-claude-code/issues/new?template=recommend-resource.yml
- **When:** after cmdbelt has some traction (stars/issues from real use), not at launch.

## 2. RoggeOhta/awesome-codex-cli — ~566 stars

- **Fit:** strong. cmdbelt ships first-class Codex CLI wiring (hooks + execpolicy rules).
- **Rule:** standard PR. Read `CONTRIBUTING.md`, add one entry in the right section with the
  shields.io stars badge the list uses, keep the description to one line.
- **Submission:** fork → add the line → PR. (Queued as a needs-Scott PR, see QUEUE.)

## 3. agarrharr/awesome-cli-apps — ~20,600 stars

- **Fit:** moderate. It's a general CLI-apps list; cmdbelt qualifies as a developer CLI tool, but
  the list is broad and may consider it niche. Worth a try, low priority.
- **Rule:** PR adding a single alphabetized line under the right category (likely *Development*),
  following the existing format exactly (name, link, one-line description). Must be maintained and
  genuinely useful. One tool per PR.

## 4. karanb192/awesome-claude-code-mods — ~880 stars

- **Fit:** moderate-strong. This list catalogs Claude Code mods/hooks and notably records
  "what each one can access before you install" — cmdbelt's no-network/no-telemetry, stdlib-only
  profile is a selling point there.
- **Rule:** check the repo's `CONTRIBUTING`/issue template at submission time; typically a PR or
  issue with the access/permissions profile filled in.

## Lists cmdbelt does NOT fit (don't submit — honesty)

- **punkpeye/awesome-mcp-servers (~97k):** cmdbelt is **not** an MCP server; it's a hook/CLI.
  Submitting it would be off-topic and rejected. (The growth report cited this list as an example
  of distribution scale, not as a fit for cmdbelt.)
- **VoltAgent/awesome-claude-code-subagents / ccplugins/awesome-claude-code-plugins:** cmdbelt is
  not a subagent or a slash-command plugin. Skip unless a "hooks" section exists at submission time.

## Order of operations

1. Launch (Show HN + r/commandline), gather real users and feedback for ~1–2 weeks.
2. Submit to **awesome-codex-cli** (PR) and **awesome-cli-apps** (PR) once stable.
3. Only after some traction, submit to **awesome-claude-code** via its issue form (human, web UI).
4. Consider **awesome-claude-code-mods** with the access profile filled in.

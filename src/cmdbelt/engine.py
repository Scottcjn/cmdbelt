"""Decision engine: parse a command, match rules, pick an outcome."""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config
from .parser import ParseResult, Unit, parse
from .paths import PathContext
from .rules import ACTION_RANK, Rule, load_builtin_rules, merge_rules

# Commands that never mutate files when run without output redirection.
READONLY_CMDS = {
    "ls",
    "cat",
    "head",
    "tail",
    "less",
    "more",
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "ag",
    "ack",
    "wc",
    "echo",
    "printf",
    "pwd",
    "which",
    "whereis",
    "type",
    "file",
    "stat",
    "du",
    "df",
    "env",
    "printenv",
    "date",
    "uname",
    "hostname",
    "id",
    "whoami",
    "ps",
    "top",
    "htop",
    "pgrep",
    "lsof",
    "ss",
    "netstat",
    "ping",
    "dig",
    "nslookup",
    "host",
    "jq",
    "yq",
    "sort",
    "uniq",
    "cut",
    "awk",
    "tr",
    "diff",
    "cmp",
    "md5sum",
    "sha256sum",
    "sha1sum",
    "tree",
    "true",
    "false",
    "test",
    "[",
    "sleep",
    "cd",
    "export",
    "basename",
    "dirname",
    "realpath",
    "readlink",
    "xxd",
    "od",
    "strings",
    "nl",
    "column",
    "fold",
    "paste",
    "join",
    "comm",
    "seq",
    "yes",
    "bc",
    "sed",
    "expr",
    "time",
    "times",
    "ulimit",
    "umask",
    "alias",
    "unalias",
    "history",
    "man",
    "info",
    "help",
    "tput",
    "clear",
    "tty",
    "stty",
    "locale",
    "lscpu",
    "lsblk",
    "lsusb",
    "lspci",
    "free",
    "uptime",
    "w",
    "who",
    "last",
    "nproc",
    "arch",
    "getconf",
    "ldd",
    "nm",
    "objdump",
    "readelf",
    "ldconfig",
    "nvidia-smi",
    "rocm-smi",
    "systemctl-status",
    "journalctl",
    "dmesg",
    "ip",
    "ifconfig",
    "route",
    "arp",
    "curl",
    "wget",
    "http",
    "nc",
    "ssh",
    "ssh-keygen",
    "gpg",
    "openssl",
    "base64",
    "tar",
    "unzip",
    "zip",
    "gzip",
    "gunzip",
    "bzip2",
    "xz",
    "zcat",
    "zless",
    "shasum",
    "sha512sum",
    "b2sum",
    "cksum",
    "sum",
    "find",
    "locate",
    "xargs",
    "tee",
    "watch",
    "timeout",
    "sudo",
    "python",
    "python3",
    "node",
    "ruby",
    "perl",
    "php",
    "java",
    "go",
    "rustc",
    "cargo",
    "make",
    "cmake",
    "ninja",
    "gcc",
    "g++",
    "clang",
    "cc",
    "ld",
    "as",
    "pytest",
    "ruff",
    "mypy",
    "black",
    "flake8",
    "pylint",
    "isort",
    "eslint",
    "prettier",
    "tsc",
    "npm",
    "npx",
    "pnpm",
    "yarn",
    "bun",
    "pip",
    "pip3",
    "uv",
    "poetry",
    "conda",
    "mamba",
    "docker",
    "podman",
    "kubectl",
    "helm",
    "terraform",
    "gh",
    "glab",
    "git",
    "hg",
    "svn",
    "aws",
    "gcloud",
    "az",
    "sqlite3",
    "psql",
    "mysql",
    "redis-cli",
    "mongosh",
    "vim",
    "nvim",
    "nano",
    "emacs",
    "code",
    "open",
    "xdg-open",
    "notify-send",
    "say",
    "espeak",
}
# The entries above that are only read-only for *some* subcommands/flags:
READONLY_SUBCMDS: dict[str, set[str]] = {
    "git": {
        "status",
        "diff",
        "log",
        "show",
        "branch",
        "remote",
        "rev-parse",
        "ls-files",
        "blame",
        "describe",
        "tag",
        "config",
        "cat-file",
        "ls-tree",
        "ls-remote",
        "reflog",
        "shortlog",
        "fetch",
        "var",
        "grep",
        "count-objects",
        "name-rev",
        "help",
        "stash",
        "worktree",
        "check-ignore",
        "diff-tree",
        "diff-index",
        "for-each-ref",
        "symbolic-ref",
        "show-ref",
        "merge-base",
        "rev-list",
        "whatchanged",
        "bisect",
        "cherry",
        "range-diff",
    },
    "docker": {
        "ps",
        "logs",
        "images",
        "inspect",
        "version",
        "info",
        "stats",
        "top",
        "port",
        "diff",
        "history",
        "search",
        "events",
        "context",
        "manifest",
        "trust",
        "system",
        "image",
        "container",
        "volume",
        "network",
        "compose",
    },
    "podman": {"ps", "logs", "images", "inspect", "version", "info", "stats", "top"},
    "kubectl": {
        "get",
        "describe",
        "logs",
        "top",
        "explain",
        "version",
        "config",
        "cluster-info",
        "api-resources",
        "api-versions",
        "auth",
        "diff",
        "events",
        "wait",
    },
    "helm": {
        "list",
        "ls",
        "status",
        "get",
        "history",
        "show",
        "search",
        "version",
        "env",
        "template",
        "lint",
        "repo",
        "dependency",
    },
    "terraform": {
        "plan",
        "show",
        "state",
        "output",
        "validate",
        "fmt",
        "version",
        "providers",
        "graph",
        "console",
        "workspace",
        "init",
    },
    "gh": {
        "pr",
        "issue",
        "repo",
        "run",
        "api",
        "auth",
        "release",
        "gist",
        "search",
        "status",
        "browse",
        "label",
        "project",
        "workflow",
        "config",
        "extension",
        "alias",
        "secret",
        "variable",
        "ruleset",
        "cache",
        "codespace",
        "org",
        "ssh-key",
        "gpg-key",
    },
    "npm": {
        "ls",
        "list",
        "view",
        "info",
        "show",
        "search",
        "outdated",
        "audit",
        "config",
        "test",
        "t",
        "run",
        "run-script",
        "start",
        "exec",
        "whoami",
        "ping",
        "docs",
        "help",
        "version",
        "v",
        "why",
        "explain",
        "pack",
    },
    "pnpm": {"ls", "list", "view", "outdated", "audit", "test", "run", "start", "exec", "why"},
    "yarn": {"list", "info", "outdated", "audit", "test", "run", "start", "why"},
    "bun": {"test", "run", "pm"},
    "pip": {
        "list",
        "show",
        "freeze",
        "check",
        "config",
        "debug",
        "index",
        "download",
        "hash",
        "inspect",
        "help",
        "--version",
        "-V",
    },
    "pip3": {"list", "show", "freeze", "check", "config", "debug", "index", "download"},
    "uv": {"pip", "run", "tree", "version", "lock", "export", "help", "python"},
    "poetry": {"show", "check", "version", "env", "run", "search", "config"},
    "cargo": {
        "build",
        "check",
        "test",
        "clippy",
        "fmt",
        "run",
        "doc",
        "bench",
        "tree",
        "metadata",
        "search",
        "version",
        "--version",
        "update",
        "fetch",
        "audit",
    },
    "go": {"build", "test", "vet", "run", "fmt", "list", "version", "env", "doc", "mod"},
    "make": set(),
    "aws": {
        "s3",
        "ec2",
        "sts",
        "iam",
        "lambda",
        "logs",
        "ecr",
        "ecs",
        "cloudformation",
        "ssm",
        "secretsmanager",
        "rds",
        "dynamodb",
        "route53",
    },
    "gcloud": {
        "auth",
        "config",
        "projects",
        "compute",
        "container",
        "run",
        "functions",
        "logging",
        "secrets",
        "storage",
        "sql",
    },
    "az": {"account", "login", "vm", "aks", "storage", "group", "webapp", "keyvault"},
    "sqlite3": set(),
    "psql": set(),
    "mysql": set(),
    "redis-cli": set(),
    "mongosh": set(),
    "systemctl-status": set(),
}
_MUTATING_FLAGS: dict[str, set[str]] = {
    "sed": {"i", "in-place"},
    "perl": {"i"},
    "curl": {"o", "O", "output", "remote-name", "J"},
    "wget": {"O", "P", "output-document", "N", "r", "m"},
    "tar": {"x", "extract"},
    "unzip": set(),  # extraction is mutating unless -l/-t
    "gzip": set(),
    "gunzip": set(),
    "xz": set(),
    "bzip2": set(),
    "zip": set(),
    "tee": set(),
    "ssh-keygen": set(),
    "gpg": {"o", "output", "import", "delete-key", "delete-secret-key"},
    "openssl": {"out"},
    "gh": set(),
}
_ALWAYS_MUTATING = {
    "tee",
    "unzip",
    "gzip",
    "gunzip",
    "xz",
    "bzip2",
    "zip",
    "ssh-keygen",
    "xargs",
    "vim",
    "nvim",
    "nano",
    "emacs",
}
_SAFE_REDIRECT_TARGETS = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "&1", "&2"}


@dataclass
class Match:
    unit_index: int
    rule: Rule


@dataclass
class Decision:
    action: str
    reason: str
    rule_id: str | None
    severity: str
    command: str
    parse: ParseResult
    matches: list[Match] = field(default_factory=list)
    winning_unit: int | None = None
    mutating: bool = True
    budgets: list[str] = field(default_factory=list)
    remote: bool = False
    unparseable: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return {"allow": 0, "ask": 10, "deny": 20}[self.action]

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "reason": self.reason,
            "rule": self.rule_id,
            "severity": self.severity,
            "command": self.command,
            "units": [u.describe() for u in self.parse.units],
            "argv": [u.argv for u in self.parse.units],
            "matches": [
                {"unit": m.unit_index, "rule": m.rule.id, "action": m.rule.action}
                for m in self.matches
            ],
            "mutating": self.mutating,
            "budgets": self.budgets,
            "remote": self.remote,
            "unparseable": self.unparseable,
            "notes": self.notes,
            "exit_code": self.exit_code,
        }


class Engine:
    def __init__(self, config: Config, rules: list[Rule] | None = None) -> None:
        self.config = config
        if rules is None:
            rules = merge_rules(load_builtin_rules(), config.user_rules, config.disable_rules)
        self.rules = rules

    def evaluate(self, command: str, cwd: str | None = None) -> Decision:
        ctx = PathContext.create(cwd)
        result = parse(command)
        return self.decide(result, ctx)

    def decide(self, result: ParseResult, ctx: PathContext) -> Decision:
        matches: list[Match] = []
        best_action = "allow"
        best_rank = -1
        best_rule: Rule | None = None
        best_unit: int | None = None
        best_priority = -(10**9)
        notes: list[str] = []
        budgets: list[str] = []
        remote = any(u.remote for u in result.units)

        if not result.units:
            return Decision(
                "allow", "empty command", None, "info", result.command, result, mutating=False
            )

        for idx, unit in enumerate(result.units):
            unit_matches = [r for r in self.rules if r.matches(unit, ctx)]
            for r in unit_matches:
                matches.append(Match(idx, r))
                if r.budget and r.budget not in budgets:
                    budgets.append(r.budget)
            if unit.unparseable:
                cand_action, cand_prio, cand_rule = "ask", 0, None
            elif unit_matches:
                top_prio = max(r.priority for r in unit_matches)
                top = [r for r in unit_matches if r.priority == top_prio]
                top.sort(key=lambda r: ACTION_RANK[r.action], reverse=True)
                cand_rule = top[0]
                cand_action, cand_prio = cand_rule.action, top_prio
            else:
                continue
            rank = ACTION_RANK[cand_action]
            if rank > best_rank or (rank == best_rank and cand_prio > best_priority):
                best_rank, best_action, best_rule, best_unit = rank, cand_action, cand_rule, idx
                best_priority = cand_prio

        if best_rule is not None:
            reason = best_rule.reason
            severity = best_rule.severity
            rule_id: str | None = best_rule.id
            if result.units[best_unit or 0].remote:
                reason += " [remote: not undoable]"
        elif best_unit is not None and result.units[best_unit].unparseable:
            u = result.units[best_unit]
            reason = "could not parse this command safely (" + ", ".join(u.issues) + ")"
            severity = "medium"
            rule_id = "unparseable"
        else:
            reason = "no rule matched"
            severity = "info"
            rule_id = None
        for u in result.units:
            if u.unparseable and u is not result.units[best_unit or 0]:
                notes.append("unparseable unit: " + ", ".join(u.issues))
        mutating = any(
            is_mutating(u, [m.rule for m in matches if m.unit_index == i])
            for i, u in enumerate(result.units)
        )
        return Decision(
            action=best_action,
            reason=reason,
            rule_id=rule_id,
            severity=severity,
            command=result.command,
            parse=result,
            matches=matches,
            winning_unit=best_unit,
            mutating=mutating,
            budgets=budgets,
            remote=remote,
            unparseable=result.unparseable,
            notes=notes,
        )


def is_mutating(unit: Unit, matched: list[Rule]) -> bool:
    """Heuristic: does this unit change files on the local filesystem?"""
    for r in matched:
        if r.mutates is not None:
            return r.mutates
    if unit.remote:
        return False
    if unit.unparseable:
        return True
    for rd in unit.redirects:
        if (
            rd.op in (">", ">>", "&>", "&>>", ">|", "<>")
            and rd.target not in _SAFE_REDIRECT_TARGETS
        ):
            return True
    cmd = unit.cmd
    if not unit.argv:
        return False
    if cmd in _ALWAYS_MUTATING:
        return True
    if cmd not in READONLY_CMDS:
        return True
    from .rules import split_args

    flags, positional = split_args(unit)
    if cmd in _MUTATING_FLAGS and flags & _MUTATING_FLAGS[cmd]:
        return True
    if cmd == "tar":
        old_style = positional[0] if positional and not positional[0].startswith("-") else ""
        return not ("t" in flags or "list" in flags or "t" in old_style)
    subs = READONLY_SUBCMDS.get(cmd)
    if subs is not None:
        if not positional:
            return False
        if positional[0] not in subs:
            return True
        # second-level checks for a few commands
        if cmd == "git" and positional[0] in (
            "stash",
            "worktree",
            "branch",
            "tag",
            "remote",
            "config",
            "reflog",
            "bisect",
        ):
            second = positional[1] if len(positional) > 1 else None
            if positional[0] == "stash":
                return second not in ("list", "show")
            if positional[0] == "worktree":
                return second not in ("list", None)
            if positional[0] == "branch":
                return bool(flags & {"d", "D", "m", "M", "c", "C", "u", "set-upstream-to"}) or (
                    len(positional) > 1
                    and "a" not in flags
                    and "r" not in flags
                    and "l" not in flags
                )
            if positional[0] == "tag":
                return bool(flags & {"d", "a", "s", "f"}) or (
                    len(positional) > 1 and "l" not in flags
                )
            if positional[0] == "remote":
                return second not in (None, "show", "get-url", "-v")
            if positional[0] == "config":
                return (
                    not (flags & {"get", "list", "l", "get-all", "get-regexp"})
                    and len(positional) > 2
                )
            if positional[0] == "reflog":
                return second in ("expire", "delete")
        if cmd in ("docker", "podman") and positional[0] in (
            "system",
            "image",
            "container",
            "volume",
            "network",
            "compose",
        ):
            second = positional[1] if len(positional) > 1 else ""
            return second not in (
                "ls",
                "list",
                "ps",
                "inspect",
                "df",
                "info",
                "events",
                "logs",
                "version",
                "top",
                "stats",
                "config",
                "images",
            )
        if cmd == "gh":
            second = positional[1] if len(positional) > 1 else ""
            return second not in (
                "list",
                "view",
                "status",
                "checks",
                "diff",
                "download",
                "ls",
                "get",
                "",
                "watch",
                "login",
                "logout",
                "token",
            )
        if cmd == "uv" and positional[0] == "pip":
            return len(positional) > 1 and positional[1] not in (
                "list",
                "show",
                "freeze",
                "check",
                "tree",
            )
        if cmd == "aws":
            return not any(
                x in unit.args
                for x in (
                    "ls",
                    "describe-instances",
                    "get-caller-identity",
                    "list-buckets",
                    "get",
                    "describe",
                    "list",
                )
            )
        return False
    return False

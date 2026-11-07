"""Rule model, TOML loading, user-override merge and unit matching."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

from .parser import Unit
from .paths import PathContext, categorize

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found]

ACTIONS = ("allow", "ask", "deny")
ACTION_RANK = {"allow": 0, "ask": 1, "deny": 2}
SEVERITIES = ("info", "low", "medium", "high", "critical")

# Global options that take a value, per command, so positional extraction skips them.
VALUED_OPTS: dict[str, set[str]] = {
    "git": {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"},
    "docker": {"-H", "--host", "--context", "-c", "--config", "-l", "--log-level"},
    "kubectl": {"-n", "--namespace", "--context", "--kubeconfig", "-s", "--server", "-f", "-l"},
    "helm": {"-n", "--namespace", "--kube-context", "--kubeconfig"},
    "gh": {
        "-R",
        "--repo",
        "-X",
        "--method",
        "-f",
        "-F",
        "-H",
        "--hostname",
        "-t",
        "--title",
        "-b",
        "--body",
    },
    "systemctl": {"-t", "--type", "-p", "--property", "-H", "--host", "-M", "--machine"},
    "chmod": set(),
    "chown": set(),
    "terraform": {"-var", "-var-file", "-chdir"},
    "find": set(),
    "dd": set(),
    "psql": {
        "-h",
        "-p",
        "-U",
        "-d",
        "-c",
        "-f",
        "-v",
        "--host",
        "--port",
        "--username",
        "--dbname",
    },
    "mysql": {"-h", "-P", "-u", "-p", "-D", "-e", "--host", "--port", "--user", "--database"},
    "sqlite3": {"-cmd", "-init"},
    "kill": {"-s", "-n"},
    "pkill": {"-u", "-U", "-g", "-G", "-P", "-s", "-t", "--signal"},
    "killall": {"-u", "-s", "--signal", "-o", "-y"},
    "rsync": {"-e", "--rsh", "--exclude", "--include", "--filter"},
    "curl": {"-o", "-H", "-d", "-X", "-u", "-A", "-b", "-c", "-e", "-F", "-T", "-x", "-m", "-w"},
    "wget": {"-O", "-o", "-P", "-U", "-T", "-t", "--header", "--output-document", "-a", "-i"},
    "npm": set(),
    "pip": {"-r", "-i", "--index-url", "-e", "-t", "--target", "-c"},
    "chattr": set(),
    "apt": set(),
    "apt-get": set(),
    "iptables": {"-t", "-A", "-D", "-I", "-j", "-p", "-s", "-d", "-i", "-o", "--dport", "--sport"},
    "ssh": set(),
    "mount": {"-t", "-o"},
    "umount": {"-t", "-O"},
}


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v]


@dataclass
class Rule:
    id: str
    action: str = "ask"
    reason: str = ""
    severity: str = "medium"
    priority: int = 0
    cmd: list[str] = field(default_factory=list)
    subcmd: list[str] = field(default_factory=list)
    subcmd2: list[str] = field(default_factory=list)
    flags_any: list[str] = field(default_factory=list)
    flags_all: list[str] = field(default_factory=list)
    flags_none: list[str] = field(default_factory=list)
    args_any: list[str] = field(default_factory=list)
    args_regex: str | None = None
    args_path_any: list[str] = field(default_factory=list)
    args_path_none: list[str] = field(default_factory=list)
    min_positional: int | None = None
    max_positional: int | None = None
    regex: str | None = None
    text_regex: str | None = None
    redirect_path_any: list[str] = field(default_factory=list)
    pipe_from: list[str] = field(default_factory=list)
    pipe_from_any: bool = False
    remote: bool | None = None
    dynamic_args: bool | None = None
    wrappers_any: list[str] = field(default_factory=list)
    budget: str | None = None
    mutates: bool | None = None
    source: str = "builtin"
    _re: re.Pattern[str] | None = field(default=None, repr=False, compare=False)
    _args_re: re.Pattern[str] | None = field(default=None, repr=False, compare=False)
    _text_re: re.Pattern[str] | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any], source: str = "builtin") -> Rule:
        if "id" not in d:
            raise ValueError(f"rule without id: {d}")
        kw: dict[str, Any] = {"id": str(d["id"]), "source": source}
        for key in (
            "cmd",
            "subcmd",
            "subcmd2",
            "flags_any",
            "flags_all",
            "flags_none",
            "args_any",
            "args_path_any",
            "args_path_none",
            "redirect_path_any",
            "pipe_from",
            "wrappers_any",
        ):
            if key in d:
                kw[key] = _as_list(d[key])
        for key in ("action", "reason", "severity", "args_regex", "regex", "text_regex", "budget"):
            if key in d:
                kw[key] = str(d[key])
        for key in ("priority", "min_positional", "max_positional"):
            if key in d:
                kw[key] = int(d[key])
        for key in ("remote", "dynamic_args", "pipe_from_any", "mutates"):
            if key in d:
                kw[key] = bool(d[key])
        r = cls(**kw)
        if r.action not in ACTIONS:
            raise ValueError(f"rule {r.id}: bad action {r.action!r}")
        if r.severity not in SEVERITIES:
            raise ValueError(f"rule {r.id}: bad severity {r.severity!r}")
        r._re = re.compile(r.regex) if r.regex else None
        r._args_re = re.compile(r.args_regex) if r.args_regex else None
        r._text_re = re.compile(r.text_regex) if r.text_regex else None
        return r

    # -- matching -------------------------------------------------------
    def matches(self, unit: Unit, ctx: PathContext) -> bool:
        if unit.unparseable:
            return False
        if self.cmd and unit.cmd not in self.cmd:
            return False
        if self.remote is not None and unit.remote != self.remote:
            return False
        if self.dynamic_args is not None and unit.dynamic_args != self.dynamic_args:
            return False
        if self.wrappers_any and not any(
            w in unit.wrappers or w in {x.split()[0] for x in unit.wrappers}
            for w in self.wrappers_any
        ):
            return False
        if self.pipe_from_any and not unit.pipe_from:
            return False
        if self.pipe_from and unit.pipe_from not in self.pipe_from:
            return False
        if not unit.argv and not (self.redirect_path_any or self.regex):
            return False
        flags, positional = split_args(unit)
        if self.subcmd and (not positional or positional[0] not in self.subcmd):
            return False
        if self.subcmd2 and (len(positional) < 2 or positional[1] not in self.subcmd2):
            return False
        if self.flags_any and not any(f in flags for f in self.flags_any):
            return False
        if self.flags_all and not all(f in flags for f in self.flags_all):
            return False
        if self.flags_none and any(f in flags for f in self.flags_none):
            return False
        if self.args_any and not any(a in self.args_any for a in unit.args):
            return False
        if self._args_re and not any(self._args_re.search(a) for a in unit.args):
            return False
        pos_after_sub = positional[1:] if self.subcmd else positional
        if self.min_positional is not None and len(pos_after_sub) < self.min_positional:
            return False
        if self.max_positional is not None and len(pos_after_sub) > self.max_positional:
            return False
        if self.args_path_any or self.args_path_none:
            cat_sets = [categorize(a, ctx, is_glob=a in unit.glob_args) for a in pos_after_sub]
            if self.args_path_any and not any(c & set(self.args_path_any) for c in cat_sets):
                return False
            if self.args_path_none and any(c & set(self.args_path_none) for c in cat_sets):
                return False
        if self.redirect_path_any:
            hit = False
            for r in unit.redirects:
                if r.op not in (">", ">>", "&>", "&>>", ">|", "<>"):
                    continue
                cats = categorize(r.target, ctx)
                if cats & set(self.redirect_path_any):
                    hit = True
            if not hit:
                return False
        if self._re and not self._re.search(unit.normalized()):
            return False
        if self._text_re:
            text = "\n".join([*unit.args, unit.heredoc or ""])
            if not self._text_re.search(text):
                return False
        return True


def split_args(unit: Unit) -> tuple[set[str], list[str]]:
    """Return (canonical flag set, positional args) for a unit.

    Short clusters ``-rf`` expand to ``{"r", "f"}``; long options ``--force=1``
    give ``"force"``. A literal ``--`` ends option parsing.
    """
    flags: set[str] = set()
    positional: list[str] = []
    valued = VALUED_OPTS.get(unit.cmd, set())
    args = unit.args
    i = 0
    opts_done = False
    while i < len(args):
        a = args[i]
        if opts_done or not a.startswith("-") or a == "-":
            positional.append(a)
            i += 1
            continue
        if a == "--":
            opts_done = True
            i += 1
            continue
        if a.startswith("--"):
            name = a[2:].split("=", 1)[0]
            flags.add(name)
            flags.add(a.split("=", 1)[0])
            if a in valued and "=" not in a:
                i += 2
                continue
            i += 1
            continue
        if a in valued:
            flags.add(a[1:])
            flags.add(a)
            i += 2
            continue
        body = a[1:]
        # numeric like -9 / -1 stay whole (kill -9, kill -1)
        if body.lstrip("0123456789") == "":
            flags.add(a)
            flags.add(body)
            i += 1
            continue
        flags.add(a)
        flags.add(body)
        for ch in body:
            flags.add(ch)
        i += 1
    return flags, positional


def load_builtin_rules() -> list[Rule]:
    text = resources.files("cmdbelt.data").joinpath("builtin.toml").read_text("utf-8")
    data = tomllib.loads(text)
    return [Rule.from_dict(d, "builtin") for d in data.get("rule", [])]


def merge_rules(builtin: list[Rule], user: list[dict[str, Any]], disable: list[str]) -> list[Rule]:
    """User rules with an existing id replace the builtin rule; new ids are appended.

    User rules default to priority 10 so they win over builtin matches on the
    same unit unless the user says otherwise.
    """
    by_id = {r.id: r for r in builtin}
    for d in user:
        d = dict(d)
        d.setdefault("priority", 10)
        if d.get("id") in by_id and len(d) <= 3 and "action" in d:
            # partial override: keep matchers, change action/reason
            base = by_id[d["id"]]
            merged = {**_rule_to_dict(base), **d}
            by_id[d["id"]] = Rule.from_dict(merged, "user")
        else:
            r = Rule.from_dict(d, "user")
            by_id[r.id] = r
    return [r for r in by_id.values() if r.id not in set(disable)]


def _rule_to_dict(r: Rule) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in r.__dict__.items():
        if k.startswith("_") or k == "source":
            continue
        if v in (None, [], ""):
            continue
        out[k] = v
    return out

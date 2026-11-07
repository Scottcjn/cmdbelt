"""Path categorisation for rule matching.

A positional argument is mapped to a set of categories such as ``root``,
``home``, ``etc``, ``ssh``, ``shell_rc``, ``dev``, ``system``, ``outside``,
``glob``, ``dynamic``, ``dot``, ``project_root``. Rules refer to these names.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache

SHELL_RC_NAMES = {
    ".bashrc",
    ".bash_profile",
    ".bash_login",
    ".bash_logout",
    ".bash_aliases",
    ".profile",
    ".zshrc",
    ".zprofile",
    ".zshenv",
    ".zlogin",
    ".zlogout",
    ".cshrc",
    ".tcshrc",
    ".kshrc",
    ".inputrc",
}
SYSTEM_PREFIXES = (
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib32",
    "/lib64",
    "/boot",
    "/var",
    "/opt",
    "/sys",
    "/proc",
    "/root",
    "/srv",
    "/mnt",
    "/media",
    "/snap",
    "/run",
)
SAFE_DEV = {
    "/dev/null",
    "/dev/zero",
    "/dev/urandom",
    "/dev/random",
    "/dev/stdout",
    "/dev/stderr",
    "/dev/stdin",
    "/dev/tty",
    "/dev/full",
}
_VAR_RE = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|\$\(|`")


@dataclass(frozen=True)
class PathContext:
    cwd: str
    project_root: str
    home: str
    tmp_dirs: tuple[str, ...] = ("/tmp", "/var/tmp", "/dev/shm")

    @classmethod
    def create(cls, cwd: str | None = None, project_root: str | None = None) -> PathContext:
        cwd = os.path.abspath(cwd or os.getcwd())
        root = project_root or git_toplevel(cwd) or cwd
        home = os.path.expanduser("~")
        tmp = ["/tmp", "/var/tmp", "/dev/shm"]
        for var in ("TMPDIR", "TMP", "TEMP"):
            v = os.environ.get(var)
            if v:
                tmp.append(os.path.abspath(v))
        return cls(cwd=cwd, project_root=os.path.abspath(root), home=home, tmp_dirs=tuple(tmp))


@lru_cache(maxsize=64)
def git_toplevel(cwd: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            env=_clean_git_env(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _clean_git_env() -> dict[str, str]:
    env = dict(os.environ)
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY"):
        env.pop(k, None)
    return env


def _under(path: str, prefix: str) -> bool:
    prefix = prefix.rstrip("/") or "/"
    return path == prefix or path.startswith(prefix + "/")


def expand(arg: str, ctx: PathContext) -> str | None:
    """Expand ~ and $HOME; return None when the value is dynamic (unknown)."""
    a = arg
    if a in ("{}", "{}/"):
        return None
    a = re.sub(r"^\$\{?HOME\}?(?=/|$)", ctx.home, a)
    a = re.sub(r"^\$\{?PWD\}?(?=/|$)", ctx.cwd, a)
    if a == "~" or a.startswith("~/"):
        a = ctx.home + a[1:]
    if _VAR_RE.search(a):
        return None
    return a


def categorize(arg: str, ctx: PathContext, *, is_glob: bool = False) -> frozenset[str]:
    cats: set[str] = set()
    if not arg:
        return frozenset()
    if is_glob or any(c in arg for c in "*?"):
        cats.add("glob")
    expanded = expand(arg, ctx)
    if expanded is None:
        cats.add("dynamic")
        return frozenset(cats)
    stripped = expanded
    if "glob" in cats:
        # "build/*" -> "build/", "*" -> ".", "/*" -> "/"
        stripped = re.sub(r"[^/]*[*?\[][^/]*$", "", expanded) or "."
    if expanded in ("/", "//", "/*", "/.", "/./", "/.*", "/..", "/.."):
        cats.add("root")
    absp = os.path.normpath(os.path.join(ctx.cwd, stripped))
    if absp == "/":
        cats.add("root")
    if arg in (".", "./"):
        cats.add("dot")
    if absp == ctx.home or arg in ("~", "~/", "~/*", "$HOME", "$HOME/*", "${HOME}"):
        cats.add("home")
    if os.path.dirname(absp) == ctx.home and absp != ctx.home:
        cats.add("home_toplevel")
    if absp == ctx.project_root:
        cats.add("project_root")
    if _under(absp, "/etc"):
        cats.add("etc")
    if _under(absp, os.path.join(ctx.home, ".ssh")) or _under(absp, "/root/.ssh"):
        cats.add("ssh")
    base = os.path.basename(absp)
    if base in SHELL_RC_NAMES and (
        _under(absp, ctx.home) or _under(absp, "/root") or _under(absp, "/etc")
    ):
        cats.add("shell_rc")
    if absp in (
        os.path.join(ctx.home, ".config", "fish", "config.fish"),
        "/etc/profile",
        "/etc/bash.bashrc",
        "/etc/zshrc",
        "/etc/zsh/zshrc",
    ):
        cats.add("shell_rc")
    if _under(absp, "/dev") and absp not in SAFE_DEV and not absp.startswith("/dev/fd/"):
        cats.add("dev")
    if any(_under(absp, p) for p in SYSTEM_PREFIXES):
        cats.add("system")
    in_tmp = any(_under(absp, t) for t in ctx.tmp_dirs)
    in_project = _under(absp, ctx.project_root)
    if not in_project and not in_tmp:
        cats.add("outside")
    if _under(ctx.project_root, absp) and absp != ctx.project_root:
        cats.add("ancestor")
    if in_tmp:
        cats.add("tmp")
    return frozenset(cats)

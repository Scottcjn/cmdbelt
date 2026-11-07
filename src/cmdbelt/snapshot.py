"""Shadow-git snapshots of a project root, with undo.

A *shadow* repository lives under ``<guard home>/snapshots/<root-hash>.git``
and is driven purely through ``GIT_DIR`` / ``GIT_WORK_TREE`` / ``GIT_INDEX_FILE``.
The user's own ``.git`` (index, stash, refs, hooks, config) is never read or
written by this module. The work tree's ``.gitignore`` files are honoured;
files above the size cap are excluded with a warning.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Config
from .paths import git_toplevel

_ENV_STRIP = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES",
)


class SnapshotError(RuntimeError):
    pass


@dataclass
class SnapshotEntry:
    id: int
    sha: str
    tree: str
    time: float
    command: str
    root: str
    agent: str | None = None
    cwd: str | None = None
    reused: bool = False
    kind: str = "pre-command"
    skipped: list[str] | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


class SnapshotStore:
    """Snapshots for one root directory."""

    def __init__(self, home: Path, root: str, max_file_mb: int = 50) -> None:
        self.root = os.path.abspath(root)
        self.home = home
        digest = hashlib.sha256(self.root.encode()).hexdigest()[:16]
        self.shadow = home / "snapshots" / f"{digest}.git"
        self.log_path = self.shadow / "cmdbelt-log.jsonl"
        self.max_bytes = max_file_mb * 1024 * 1024

    # -- git plumbing --------------------------------------------------
    def _env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in _ENV_STRIP}
        env.update(
            {
                "GIT_DIR": str(self.shadow),
                "GIT_WORK_TREE": self.root,
                "GIT_INDEX_FILE": str(self.shadow / "index"),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_AUTHOR_NAME": "cmdbelt",
                "GIT_AUTHOR_EMAIL": "cmdbelt@localhost",
                "GIT_COMMITTER_NAME": "cmdbelt",
                "GIT_COMMITTER_EMAIL": "cmdbelt@localhost",
                "GIT_TERMINAL_PROMPT": "0",
                "LC_ALL": "C",
            }
        )
        return env

    def _git(self, *args: str, check: bool = True, input_: bytes | None = None) -> str:
        p = subprocess.run(
            ["git", *args],
            cwd=self.root,
            env=self._env(),
            capture_output=True,
            input=input_,
            check=False,
        )
        if check and p.returncode != 0:
            raise SnapshotError(f"git {' '.join(args)} failed: {p.stderr.decode(errors='replace')}")
        return p.stdout.decode(errors="replace")

    def init(self) -> None:
        if (self.shadow / "HEAD").exists():
            return
        self.shadow.mkdir(parents=True, exist_ok=True)
        self._git("init", "-q")
        self._git("config", "core.hooksPath", os.devnull)
        self._git("config", "core.autocrlf", "false")
        self._git("config", "gc.auto", "0")
        (self.shadow / "info").mkdir(exist_ok=True)

    def head(self) -> str | None:
        out = self._git("rev-parse", "--verify", "-q", "HEAD", check=False).strip()
        return out or None

    # -- size cap -----------------------------------------------------
    def _exclude_big_files(self) -> list[str]:
        candidates = self._git("ls-files", "-z", "-o", "-m", "--exclude-standard").split("\0")
        skipped: list[str] = []
        exclude_file = self.shadow / "info" / "exclude"
        existing = exclude_file.read_text().splitlines() if exclude_file.exists() else []
        for rel in candidates:
            if not rel:
                continue
            full = os.path.join(self.root, rel)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if os.path.islink(full):
                continue
            if st.st_size > self.max_bytes:
                pattern = "/" + rel
                if pattern not in existing:
                    existing.append(pattern)
                    skipped.append(rel)
        if skipped:
            exclude_file.write_text("\n".join(existing) + "\n")
            # drop any previously-tracked copies
            self._git("rm", "-q", "--cached", "--ignore-unmatch", "--", *skipped, check=False)
        return skipped

    # -- public API ---------------------------------------------------
    def take(
        self,
        command: str,
        *,
        agent: str | None = None,
        cwd: str | None = None,
        kind: str = "pre-command",
    ) -> SnapshotEntry:
        self.init()
        skipped = self._exclude_big_files()
        self._git("add", "-A", "--", ".")
        tree = self._git("write-tree").strip()
        head = self.head()
        reused = False
        if head and self._git("rev-parse", f"{head}^{{tree}}").strip() == tree:
            sha, reused = head, True
        else:
            msg = f"cmdbelt {kind}: {command[:200]}"
            args = ["commit-tree", tree, "-m", msg]
            if head:
                args += ["-p", head]
            sha = self._git(*args).strip()
            self._git("update-ref", "HEAD", sha)
        entry = SnapshotEntry(
            id=self._next_id(),
            sha=sha,
            tree=tree,
            time=time.time(),
            command=command,
            root=self.root,
            agent=agent,
            cwd=cwd,
            reused=reused,
            kind=kind,
            skipped=skipped or None,
        )
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(entry.to_json() + "\n")
        return entry

    def _next_id(self) -> int:
        entries = self.entries()
        return (entries[-1].id + 1) if entries else 1

    def entries(self) -> list[SnapshotEntry]:
        if not self.log_path.exists():
            return []
        out: list[SnapshotEntry] = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(SnapshotEntry(**json.loads(line)))
        return out

    def get(self, snapshot_id: int | None) -> SnapshotEntry:
        entries = (
            [e for e in self.entries() if e.kind != "pre-undo"]
            if snapshot_id is None
            else self.entries()
        )
        if not entries:
            raise SnapshotError("no snapshots for " + self.root)
        if snapshot_id is None:
            return entries[-1]
        for e in entries:
            if e.id == snapshot_id:
                return e
        raise SnapshotError(f"no snapshot with id {snapshot_id}")

    def changed_files(self, entry: SnapshotEntry) -> list[tuple[str, str]]:
        """(status, path) pairs describing how the worktree differs from ``entry``."""
        self.init()
        self._exclude_big_files()
        self._git("add", "-A", "--", ".")
        out = self._git("diff", "--name-status", "--cached", "-z", entry.sha)
        parts = out.split("\0")
        res: list[tuple[str, str]] = []
        i = 0
        while i + 1 < len(parts):
            status, path = parts[i], parts[i + 1]
            if not status:
                break
            if status[0] in ("R", "C") and i + 2 < len(parts):
                res.append((status[0], parts[i + 2]))
                i += 3
                continue
            res.append((status[0], path))
            i += 2
        return res

    def undo(
        self, snapshot_id: int | None = None, *, dry_run: bool = False
    ) -> tuple[SnapshotEntry, list[tuple[str, str]]]:
        """Restore the worktree to a snapshot. Returns (entry, changes that were undone).

        A ``pre-undo`` snapshot of the current state is taken first so that an
        undo is itself undoable (``undo --to <that id>``).
        """
        target = self.get(snapshot_id)
        changes = self.changed_files(target)
        if dry_run or not changes:
            return target, changes
        self.take("undo", kind="pre-undo", agent="cmdbelt")
        # files that exist now but not in the target: delete
        added = [p for s, p in changes if s == "A"]
        for rel in added:
            with contextlib.suppress(FileNotFoundError):
                os.remove(os.path.join(self.root, rel))
        # everything tracked in the target: restore content
        self._git("read-tree", target.sha)
        self._git("checkout-index", "-a", "-f", "-q")
        # prune directories emptied by the deletions
        for rel in added:
            d = os.path.dirname(os.path.join(self.root, rel))
            while d and d != self.root:
                try:
                    os.rmdir(d)
                except OSError:
                    break
                d = os.path.dirname(d)
        return target, changes


def snapshot_roots(cfg: Config, cwd: str) -> list[str]:
    if cfg.snapshot_roots:
        return [os.path.abspath(os.path.expanduser(r)) for r in cfg.snapshot_roots]
    return [git_toplevel(cwd) or os.path.abspath(cwd)]


def snapshot_before(
    cfg: Config, cwd: str, command: str, agent: str | None = None
) -> list[SnapshotEntry]:
    entries = []
    for root in snapshot_roots(cfg, cwd):
        if root == "/" or root == os.path.expanduser("~"):
            # refuse to shadow the whole filesystem or home; too big, too risky
            continue
        store = SnapshotStore(cfg.home, root, cfg.snapshot_max_file_mb)
        entries.append(store.take(command, agent=agent, cwd=cwd))
    return entries

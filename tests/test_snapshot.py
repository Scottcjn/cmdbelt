"""Snapshot/undo tests on a temp repo. Explicitly checks the user's repo is untouched."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from cmdbelt.config import load_config
from cmdbelt.snapshot import SnapshotError, SnapshotStore, snapshot_before, snapshot_roots


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def index_sha(p: Path) -> str:
    """Hash of the user's index file. Taken BEFORE any git command runs in the user repo,
    so it proves cmdbelt itself did not rewrite the index (git status would refresh it)."""
    return hashlib.sha256((p / ".git" / "index").read_bytes()).hexdigest()


def user_repo_state(p: Path) -> dict[str, str]:
    """Everything about the user's repo that a snapshot must not change."""
    return {
        "status": git(p, "status", "--porcelain=v1", "--untracked-files=all"),
        "stash": git(p, "stash", "list"),
        "staged": git(p, "diff", "--cached"),
        "ls-files": git(p, "ls-files", "-s"),
        "head": git(p, "rev-parse", "HEAD"),
        "refs": git(p, "for-each-ref"),
        "reflog": git(p, "reflog"),
    }


@pytest.fixture
def dirty_repo(project: Path) -> Path:
    # existing stash
    (project / "README.md").write_text("stashed change\n")
    git(project, "stash", "push", "-q", "-m", "keep-me")
    # dirty index: a staged change not committed, plus an unstaged one
    (project / "staged.txt").write_text("staged\n")
    git(project, "add", "staged.txt")
    (project / "README.md").write_text("hello\nunstaged\n")
    # untracked + ignored files
    (project / "notes.txt").write_text("untracked\n")
    (project / ".gitignore").write_text("ignored.log\n*.big\n.venv/\n")
    (project / "ignored.log").write_text("ignored\n")
    (project / ".venv").mkdir()
    (project / ".venv" / "lib").write_text("x\n")
    return project


def test_snapshot_never_touches_user_repo(dirty_repo: Path, guard_home: Path) -> None:
    before = user_repo_state(dirty_repo)
    idx = index_sha(dirty_repo)
    store = SnapshotStore(guard_home, str(dirty_repo))
    e = store.take("rm -rf build")
    assert e.id == 1 and not e.reused
    assert index_sha(dirty_repo) == idx
    assert user_repo_state(dirty_repo) == before
    # shadow dir is outside the repo and the user's .git has no new files
    assert store.shadow.is_relative_to(guard_home)
    assert not (dirty_repo / ".git" / "cmdbelt-log.jsonl").exists()
    # the snapshot captured tracked, staged, unstaged and untracked files, not ignored ones
    files = set(store._git("ls-tree", "-r", "--name-only", e.sha).split())
    assert {"README.md", "staged.txt", "notes.txt", ".gitignore"} <= files
    assert "ignored.log" not in files and ".venv/lib" not in files
    assert not any(f.startswith(".git/") for f in files)


def test_undo_restores_files_and_keeps_user_git(dirty_repo: Path, guard_home: Path) -> None:
    before = user_repo_state(dirty_repo)
    idx = index_sha(dirty_repo)
    store = SnapshotStore(guard_home, str(dirty_repo))
    store.take("agent edit")
    # the "agent" now wrecks things
    (dirty_repo / "README.md").write_text("destroyed\n")
    (dirty_repo / "staged.txt").unlink()
    (dirty_repo / "newdir").mkdir()
    (dirty_repo / "newdir" / "junk.py").write_text("junk\n")
    (dirty_repo / "ignored.log").write_text("changed ignored\n")
    target, changes = store.undo()
    assert target.id == 1
    kinds = {p: s for s, p in changes}
    assert kinds == {"README.md": "M", "staged.txt": "D", "newdir/junk.py": "A"}
    assert (dirty_repo / "README.md").read_text() == "hello\nunstaged\n"
    assert (dirty_repo / "staged.txt").read_text() == "staged\n"
    assert not (dirty_repo / "newdir").exists()
    # ignored files are outside the snapshot's scope and are left alone
    assert (dirty_repo / "ignored.log").read_text() == "changed ignored\n"
    # user's git: index file byte-identical (cmdbelt never wrote it), stash, refs identical
    assert index_sha(dirty_repo) == idx
    assert user_repo_state(dirty_repo) == before
    idx = index_sha(dirty_repo)
    # the undo took a pre-undo snapshot, so it is reversible
    entries = store.entries()
    assert [e.kind for e in entries] == ["pre-command", "pre-undo"]
    store.undo(entries[1].id)
    assert (dirty_repo / "README.md").read_text() == "destroyed\n"
    assert (dirty_repo / "newdir" / "junk.py").exists()
    assert index_sha(dirty_repo) == idx
    # the worktree is legitimately back in the wrecked state, so `status` differs;
    # everything inside the user's .git (index, stash, refs, reflog) is still untouched
    after = user_repo_state(dirty_repo)
    assert {k: v for k, v in after.items() if k != "status"} == {
        k: v for k, v in before.items() if k != "status"
    }


def test_undo_to_specific_and_dry_run(project: Path, guard_home: Path) -> None:
    store = SnapshotStore(guard_home, str(project))
    (project / "a.txt").write_text("v1\n")
    store.take("step1")
    (project / "a.txt").write_text("v2\n")
    store.take("step2")
    (project / "a.txt").write_text("v3\n")
    t, changes = store.undo(dry_run=True)
    assert t.id == 2 and changes == [("M", "a.txt")]
    assert (project / "a.txt").read_text() == "v3\n"
    store.undo(1)
    assert (project / "a.txt").read_text() == "v1\n"
    with pytest.raises(SnapshotError):
        store.undo(99)


def test_unchanged_tree_reuses_commit(project: Path, guard_home: Path) -> None:
    store = SnapshotStore(guard_home, str(project))
    e1 = store.take("ls")
    e2 = store.take("cat x")
    assert e2.reused and e2.sha == e1.sha and e2.id == 2
    assert [e.command for e in store.entries()] == ["ls", "cat x"]


def test_size_cap_skips_big_files(project: Path, guard_home: Path) -> None:
    big = project / "model.bin"
    with big.open("wb") as f:
        f.truncate(51 * 1024 * 1024)  # sparse, no disk cost
    (project / "small.txt").write_text("ok\n")
    store = SnapshotStore(guard_home, str(project), max_file_mb=50)
    e = store.take("train")
    assert e.skipped == ["model.bin"]
    files = set(store._git("ls-tree", "-r", "--name-only", e.sha).split())
    assert "small.txt" in files and "model.bin" not in files
    # second take: already excluded, not reported again
    assert store.take("again").skipped is None


def test_no_snapshots_error(project: Path, guard_home: Path) -> None:
    store = SnapshotStore(guard_home, str(project))
    with pytest.raises(SnapshotError):
        store.undo()


def test_snapshot_outside_git_repo(tmp_path: Path, guard_home: Path) -> None:
    d = tmp_path / "plain"
    d.mkdir()
    (d / "f.txt").write_text("1\n")
    store = SnapshotStore(guard_home, str(d))
    store.take("x")
    (d / "f.txt").write_text("2\n")
    store.undo()
    assert (d / "f.txt").read_text() == "1\n"
    assert not (d / ".git").exists()


def test_roots_default_to_git_toplevel(project: Path, guard_home: Path) -> None:
    sub = project / "src" / "pkg"
    sub.mkdir(parents=True)
    cfg = load_config(str(guard_home))
    assert snapshot_roots(cfg, str(sub)) == [str(project)]
    entries = snapshot_before(cfg, str(sub), "touch x", agent="test")
    assert len(entries) == 1 and entries[0].root == str(project) and entries[0].agent == "test"


def test_refuses_home_and_root(guard_home: Path) -> None:
    cfg = load_config(str(guard_home))
    cfg.snapshot_roots = ["/", "~"]
    assert snapshot_before(cfg, os.getcwd(), "x") == []


def test_env_leakage_does_not_redirect(
    dirty_repo: Path, guard_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # even if the caller's environment points GIT_DIR at the user's repo, we strip it
    monkeypatch.setenv("GIT_DIR", str(dirty_repo / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(dirty_repo / ".git" / "index"))
    before = user_repo_state(dirty_repo)
    idx = index_sha(dirty_repo)
    SnapshotStore(guard_home, str(dirty_repo)).take("x")
    assert index_sha(dirty_repo) == idx
    assert user_repo_state(dirty_repo) == before

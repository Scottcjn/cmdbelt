"""cmdbelt command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from collections.abc import Sequence
from typing import TextIO

from . import __version__
from .audit import AuditLog
from .budget import BudgetStore
from .config import Config, load_config
from .engine import Decision
from .guard import guard
from .parser import format_tree
from .snapshot import SnapshotError, SnapshotStore, snapshot_before, snapshot_roots

EXIT_ALLOW, EXIT_ASK, EXIT_DENY = 0, 10, 20


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cmdbelt",
        description="A seatbelt for coding agents: allow / ask / deny, snapshot + undo, budgets.",
    )
    p.add_argument("--version", action="version", version=f"cmdbelt {__version__}")
    p.add_argument(
        "--home", help="guard home directory (default ~/.agent-guard or $AGENT_GUARD_HOME)"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    for name, helptext in (
        ("check", "evaluate a command; exit 0 allow / 10 ask / 20 deny"),
        ("explain", "show the parse tree and every rule that fired"),
    ):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        sp.add_argument("--cwd", help="evaluate as if run from this directory")
        sp.add_argument("--agent", help="agent name for the audit log")
        sp.add_argument("--no-audit", action="store_true")
        sp.add_argument("--no-budget", action="store_true", help="do not consume budgets")
        sp.add_argument(
            "command", nargs=argparse.REMAINDER, help="-- <command ...> or a single string"
        )

    sp = sub.add_parser("snapshot", help="snapshot the project root now (before a manual command)")
    sp.add_argument("--cwd")
    sp.add_argument("--agent")
    sp.add_argument("command", nargs=argparse.REMAINDER, help="label: the command that follows")

    sp = sub.add_parser("undo", help="restore files from the latest snapshot (or --to N)")
    sp.add_argument("--to", type=int, metavar="N", help="snapshot id from `log`")
    sp.add_argument("--cwd")
    sp.add_argument("--dry-run", action="store_true", help="list what would change")
    sp.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")

    sp = sub.add_parser("log", help="list snapshots for this project root")
    sp.add_argument("--cwd")
    sp.add_argument("-n", type=int, default=20)
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("budget", help="show or reset shared budgets")
    sp.add_argument("op", nargs="?", choices=["status", "reset"], default="status")
    sp.add_argument("name", nargs="?", help="budget name (reset only; omit for all)")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("audit", help="show recent decisions from the audit log")
    sp.add_argument("-n", type=int, default=20)
    sp.add_argument("--json", action="store_true")
    return p


def command_from_args(words: list[str]) -> str:
    words = list(words)
    if words and words[0] == "--":
        words = words[1:]
    if len(words) == 1:
        return words[0]
    return shlex.join(words)


def _print_decision(
    d: Decision, *, as_json: bool, explain: bool, out: TextIO | None = None
) -> None:
    stream = out if out is not None else sys.stdout
    if as_json:
        print(json.dumps(d.to_dict(), indent=2), file=stream)
        return
    tag = {"allow": "ALLOW", "ask": "ASK  ", "deny": "DENY "}[d.action]
    print(f"{tag}  {d.command}", file=stream)
    if d.rule_id:
        print(f"       rule: {d.rule_id} [{d.severity}] — {d.reason}", file=stream)
    else:
        print(f"       {d.reason}", file=stream)
    for n in d.notes:
        print(f"       note: {n}", file=stream)
    if d.budgets:
        print(f"       budgets: {', '.join(d.budgets)}", file=stream)
    if explain:
        print(format_tree(d.parse), file=stream)
        if d.matches:
            print("  matched rules:", file=stream)
            for m in d.matches:
                star = "*" if (m.unit_index == d.winning_unit and m.rule.id == d.rule_id) else " "
                print(
                    f"   {star} unit[{m.unit_index + 1}] {m.rule.id} -> {m.rule.action} "
                    f"(prio {m.rule.priority}, {m.rule.source})",
                    file=stream,
                )
        else:
            print("  matched rules: none", file=stream)
        print(f"  mutating: {d.mutating}", file=stream)


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.home)
    cwd = os.path.abspath(args.cwd) if getattr(args, "cwd", None) else os.getcwd()
    if args.cmd in ("check", "explain"):
        command = command_from_args(args.command)
        if not command.strip():
            parser.error("no command given (use: cmdbelt check -- <command>)")
        r = guard(
            cfg,
            command,
            cwd,
            agent=args.agent,
            source=args.cmd,
            consume_budget=False,
            snapshot=False,
            audit=not args.no_audit,
        )
        if r.budget is not None and r.budget.ok:
            r.decision.notes.append(r.budget.describe())
        _print_decision(r.decision, as_json=args.json, explain=args.cmd == "explain")
        return r.exit_code
    if args.cmd == "budget":
        store = BudgetStore(cfg.home, cfg.budgets)
        if args.op == "reset":
            n = store.reset(args.name)
            print(f"reset {n} event(s)" + (f" for {args.name}" if args.name else ""))
            return 0
        rows = store.status()
        if args.json:
            print(json.dumps([r.__dict__ for r in rows], indent=2))
        for b in rows:
            spec = cfg.budgets[b.name]
            print(
                f"{b.name:<16} {b.used:>4}/{b.limit:<4} per {spec.window_s}s"
                f"  min_spacing={spec.min_spacing_s}s" + ("" if b.ok else "  OVER")
            )
        return 0
    if args.cmd == "audit":
        recs = AuditLog(cfg.home).read(last=args.n)
        if args.json:
            print(json.dumps(recs, indent=2))
            return 0
        for rec in recs:
            print(
                f"{rec.get('iso', '')} {str(rec.get('action', '')).upper():<5} "
                f"{rec.get('source', '')}/{rec.get('agent') or '-'} {rec.get('rule') or '-'}  "
                f"{rec.get('command', '')}"
            )
        return 0
    if args.cmd == "snapshot":
        label = command_from_args(args.command) or "(manual snapshot)"
        for e in snapshot_before(cfg, cwd, label, agent=args.agent):
            tail = "  (unchanged)" if e.reused else ""
            print(f"snapshot #{e.id} {e.sha[:8]} root={e.root}{tail}")
            if e.skipped:
                print("  skipped (over size cap): " + ", ".join(e.skipped), file=sys.stderr)
        return 0
    if args.cmd in ("undo", "log"):
        return _undo_or_log(args, cfg, cwd)
    return 2


def _undo_or_log(args: argparse.Namespace, cfg: Config, cwd: str) -> int:
    rc = 0
    for root in snapshot_roots(cfg, cwd):
        store = SnapshotStore(cfg.home, root, cfg.snapshot_max_file_mb)
        if args.cmd == "log":
            entries = store.entries()[-args.n :]
            if args.json:
                print(json.dumps([e.__dict__ for e in entries], indent=2))
                continue
            print(f"root: {root}")
            if not entries:
                print("  (no snapshots)")
            for e in entries:
                when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.time))
                flag = " =" if e.reused else "  "
                kind = "" if e.kind == "pre-command" else f" [{e.kind}]"
                print(f"  #{e.id:<4} {when} {e.sha[:8]}{flag} {e.command}{kind}")
            continue
        try:
            target, changes = store.undo(args.to, dry_run=True)
        except SnapshotError as exc:
            print(f"undo: {exc}", file=sys.stderr)
            rc = 1
            continue
        print(f"root: {root}\nrestore to snapshot #{target.id} ({target.command!r})")
        if not changes:
            print("  nothing to undo: worktree already matches the snapshot")
            continue
        verbs = {"A": "delete  ", "M": "restore ", "D": "recreate", "T": "restore "}
        for status, path in changes:
            print(f"  {verbs.get(status, status)} {path}")
        if args.dry_run:
            continue
        if not args.yes and sys.stdin.isatty():
            ans = input("proceed? [y/N] ").strip().lower()
            if ans not in ("y", "yes"):
                print("aborted")
                rc = 1
                continue
        store.undo(args.to)
        print(f"  restored {len(changes)} path(s); a pre-undo snapshot was taken (see `log`)")
    return rc


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

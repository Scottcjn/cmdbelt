"""cmdbelt command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from typing import TextIO

from . import __version__
from .config import load_config
from .engine import Decision, Engine
from .parser import format_tree

EXIT_ALLOW, EXIT_ASK, EXIT_DENY = 0, 10, 20


def _split_dashdash(argv: Sequence[str]) -> tuple[list[str], list[str]]:
    argv = list(argv)
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1 :]
    return argv, []


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
    return p


def command_from_args(words: list[str]) -> str:
    words = [w for w in words]
    if words and words[0] == "--":
        words = words[1:]
    if len(words) == 1:
        return words[0]
    import shlex

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
    if args.cmd in ("check", "explain"):
        command = command_from_args(args.command)
        if not command.strip():
            parser.error("no command given (use: cmdbelt check -- <command>)")
        engine = Engine(cfg)
        cwd = os.path.abspath(args.cwd) if args.cwd else os.getcwd()
        d = engine.evaluate(command, cwd)
        _print_decision(d, as_json=args.json, explain=args.cmd == "explain")
        return d.exit_code
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

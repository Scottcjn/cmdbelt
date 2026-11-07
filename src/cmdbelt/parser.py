"""Shell command-line parser: split a command string into argv *units*.

Best-effort POSIX/bash syntax. The goal is not to be a shell, it is to find
every command that would run, including ones hidden behind wrappers
(``sudo``, ``env``, ``nohup``, ``timeout``, ``xargs``, ``bash -c``, ``ssh``,
``find -exec``, ``eval``, ``$(...)``, backticks). Anything we cannot follow is
reported with ``unparseable=True`` so the rules engine can fail closed.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterator
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- tokens


@dataclass
class Word:
    """One shell word after quote removal.

    ``text`` has quotes/backslashes removed (literal text as the command would
    see it, except that expansions are left verbatim). ``quoted`` is True if
    any part of the word was quoted. ``substitutions`` holds the inner text of
    ``$(...)``, backticks and process substitutions found in the word.
    ``has_var`` is True when a ``$NAME``/``${...}`` expansion is present.
    """

    text: str
    quoted: bool = False
    substitutions: list[str] = field(default_factory=list)
    has_var: bool = False
    has_glob: bool = False

    @property
    def dynamic(self) -> bool:
        return bool(self.substitutions) or self.has_var


@dataclass
class Op:
    text: str


Token = Word | Op

_REDIRECT_RE = re.compile(r"^(\d*)(>>|>\||>&|<<<|<<-|<<|<&|<>|&>>|&>|>|<)$")
_OPS = ("&&", "||", "|&", ";;", "|", ";", "&", "(", ")", "\n")


class _Lexer:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0
        self.n = len(text)
        self.tokens: list[Token] = []
        self.heredoc_pending: list[tuple[str, bool]] = []  # (delimiter, strip_tabs)
        self.heredocs: list[str] = []

    def error(self, msg: str) -> None:
        raise ParseError(msg)

    def run(self) -> list[Token]:
        while self.i < self.n:
            c = self.s[self.i]
            if c in " \t":
                self.i += 1
                continue
            if c == "\\" and self.i + 1 < self.n and self.s[self.i + 1] == "\n":
                self.i += 2
                continue
            if c == "#":
                while self.i < self.n and self.s[self.i] != "\n":
                    self.i += 1
                continue
            if c == "\n":
                self.tokens.append(Op("\n"))
                self.i += 1
                self._consume_heredocs()
                continue
            if self._match_op():
                continue
            self._word()
        self._consume_heredocs(at_end=True)
        return self.tokens

    def _match_op(self) -> bool:
        s = self.s
        i = self.i
        # redirect with optional fd prefix
        if s.startswith(("<(", ">("), i):
            return False  # process substitution: handled by _word
        m = re.match(r"\d*(?:&>>|&>|>>|>\||>&|<<<|<<-|<<|<&|<>|>|<)", s[i:])
        if m:
            tok = m.group(0)
            self.tokens.append(Op(tok))
            self.i += len(tok)
            if tok.lstrip("0123456789") in ("<<", "<<-"):
                self._heredoc_delim(tok.endswith("-"))
            return True
        for op in _OPS:
            if s.startswith(op, i):
                self.tokens.append(Op(op))
                self.i += len(op)
                return True
        return False

    def _heredoc_delim(self, strip_tabs: bool) -> None:
        # skip spaces, read delimiter word (may be quoted)
        while self.i < self.n and self.s[self.i] in " \t":
            self.i += 1
        start = len(self.tokens)
        self._word()
        if len(self.tokens) == start:
            self.error("heredoc without delimiter")
        w = self.tokens.pop()
        assert isinstance(w, Word)
        self.heredoc_pending.append((w.text, strip_tabs))

    def _consume_heredocs(self, at_end: bool = False) -> None:
        while self.heredoc_pending:
            delim, strip_tabs = self.heredoc_pending.pop(0)
            lines: list[str] = []
            found = False
            while self.i <= self.n:
                nl = self.s.find("\n", self.i)
                line = self.s[self.i : nl if nl != -1 else self.n]
                self.i = (nl + 1) if nl != -1 else self.n
                check = line.lstrip("\t") if strip_tabs else line
                if check == delim:
                    found = True
                    break
                lines.append(check)
                if nl == -1:
                    break
            if not found and not at_end and self.i >= self.n:
                pass  # unterminated heredoc: treat rest as body
            self.heredocs.append("\n".join(lines))

    def _word(self) -> None:
        s = self.s
        out: list[str] = []
        quoted = False
        subs: list[str] = []
        has_var = False
        has_glob = False
        while self.i < self.n:
            c = s[self.i]
            if c in " \t\n":
                break
            if c in "<>" and s.startswith(c + "(", self.i):
                # process substitution
                self.i += 1
                inner = self._subst_parens(opener="(")
                subs.append(inner)
                out.append(c + "(" + inner + ")")
                continue
            if c in ";&|()<>":
                break
            if c == "\\":
                if self.i + 1 < self.n:
                    out.append(s[self.i + 1])
                    self.i += 2
                    quoted = True
                else:
                    self.i += 1
                continue
            if c == "'":
                end = s.find("'", self.i + 1)
                if end == -1:
                    self.error("unterminated single quote")
                out.append(s[self.i + 1 : end])
                self.i = end + 1
                quoted = True
                continue
            if c == '"':
                self.i += 1
                quoted = True
                while True:
                    if self.i >= self.n:
                        self.error("unterminated double quote")
                    d = s[self.i]
                    if d == '"':
                        self.i += 1
                        break
                    if d == "\\" and self.i + 1 < self.n and s[self.i + 1] in '"\\$`\n':
                        out.append(s[self.i + 1])
                        self.i += 2
                        continue
                    if d == "$" and s.startswith("$(", self.i):
                        inner = self._subst_parens()
                        subs.append(inner)
                        out.append("$(" + inner + ")")
                        continue
                    if d == "`":
                        inner = self._subst_backtick()
                        subs.append(inner)
                        out.append("`" + inner + "`")
                        continue
                    if d == "$":
                        has_var = True
                    out.append(d)
                    self.i += 1
                continue
            if c == "$" and s.startswith("$(", self.i) and not s.startswith("$((", self.i):
                inner = self._subst_parens()
                subs.append(inner)
                out.append("$(" + inner + ")")
                continue
            if c == "$" and s.startswith("$((", self.i):
                inner = self._subst_parens(arith=True)
                has_var = True
                out.append("$((" + inner + "))")
                continue
            if c == "`":
                inner = self._subst_backtick()
                subs.append(inner)
                out.append("`" + inner + "`")
                continue
            if c in "<>" and s.startswith(c + "(", self.i):
                # process substitution
                self.i += 1
                inner = self._subst_parens(opener="(")
                subs.append(inner)
                out.append(c + "(" + inner + ")")
                continue
            if c == "$":
                has_var = True
            if c in "*?[":
                has_glob = True
            out.append(c)
            self.i += 1
        if out or quoted:
            self.tokens.append(Word("".join(out), quoted, subs, has_var, has_glob))

    def _subst_parens(self, arith: bool = False, opener: str = "$(") -> str:
        s = self.s
        if opener == "$(":
            self.i += 3 if arith else 2
        else:
            self.i += 1
        depth = 1
        start = self.i
        while self.i < self.n:
            c = s[self.i]
            if c == "'":
                end = s.find("'", self.i + 1)
                if end == -1:
                    self.error("unterminated single quote in substitution")
                self.i = end + 1
                continue
            if c == "\\":
                self.i += 2
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    inner = s[start : self.i]
                    self.i += 1
                    if arith:
                        if self.i < self.n and s[self.i] == ")":
                            self.i += 1
                        return inner.rstrip(")")
                    return inner
            self.i += 1
        self.error("unterminated $( substitution")
        return ""  # pragma: no cover

    def _subst_backtick(self) -> str:
        s = self.s
        end = s.find("`", self.i + 1)
        if end == -1:
            self.error("unterminated backtick")
        inner = s[self.i + 1 : end].replace("\\`", "`")
        self.i = end + 1
        return inner


class ParseError(Exception):
    pass


# --------------------------------------------------------------------------- units


@dataclass
class Redirect:
    op: str
    target: str
    dynamic: bool = False


@dataclass
class Unit:
    """One command that would execute, after unwrapping wrappers."""

    argv: list[str]
    raw: str
    wrappers: tuple[str, ...] = ()
    remote: bool = False
    via: str | None = None
    dynamic_args: bool = False
    unparseable: bool = False
    issues: tuple[str, ...] = ()
    redirects: tuple[Redirect, ...] = ()
    assignments: tuple[str, ...] = ()
    heredoc: str | None = None
    pipe_from: str | None = None
    pipe_to: str | None = None
    glob_args: tuple[str, ...] = ()
    dynamic_argv: tuple[str, ...] = ()

    @property
    def cmd(self) -> str:
        return os.path.basename(self.argv[0]) if self.argv else ""

    @property
    def args(self) -> list[str]:
        return self.argv[1:]

    def normalized(self) -> str:
        return shlex.join(self.argv)

    def describe(self) -> str:
        bits = []
        if self.wrappers:
            bits.append("via " + ">".join(self.wrappers))
        if self.remote:
            bits.append("REMOTE")
        if self.via:
            bits.append(self.via)
        if self.dynamic_args:
            bits.append("dynamic-args")
        if self.unparseable:
            bits.append("UNPARSEABLE:" + ",".join(self.issues))
        suffix = f"  [{' '.join(bits)}]" if bits else ""
        return self.normalized() + suffix


@dataclass
class ParseResult:
    command: str
    units: list[Unit]
    issues: list[str]

    @property
    def unparseable(self) -> bool:
        return any(u.unparseable for u in self.units)


# --------------------------------------------------------------------------- parser

_KEYWORDS_DROP = {
    "if",
    "then",
    "else",
    "elif",
    "fi",
    "do",
    "done",
    "while",
    "until",
    "!",
    "{",
    "}",
    "time",
    "coproc",
}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish", "ash", "mksh", "busybox"}
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\+?=)")

MAX_DEPTH = 8


class _Parser:
    def __init__(self, text: str, *, wrappers: tuple[str, ...], remote: bool, depth: int) -> None:
        self.text = text
        self.wrappers = wrappers
        self.remote = remote
        self.depth = depth
        self.units: list[Unit] = []
        self.issues: list[str] = []

    # -- entry
    def parse(self) -> None:
        if self.depth > MAX_DEPTH:
            self.units.append(self._unparseable("nesting too deep"))
            return
        try:
            lexer = _Lexer(self.text)
            tokens = lexer.run()
            heredocs = lexer.heredocs
        except ParseError as e:
            self.units.append(self._unparseable(str(e)))
            return
        self._parse_list(tokens, heredocs)

    def _unparseable(self, why: str, raw: str | None = None) -> Unit:
        self.issues.append(why)
        return Unit(
            argv=[],
            raw=raw if raw is not None else self.text,
            wrappers=self.wrappers,
            remote=self.remote,
            unparseable=True,
            issues=(why,),
        )

    def _parse_list(self, tokens: list[Token], heredocs: list[str]) -> None:
        """Split tokens into simple commands by separators, handling groups."""
        i = 0
        n = len(tokens)
        current: list[Token] = []
        pipeline: list[list[Token]] = []
        paren_depth = 0
        hd_iter = iter(heredocs)

        def flush_command() -> None:
            nonlocal current
            if current:
                pipeline.append(current)
            current = []

        def flush_pipeline() -> None:
            nonlocal pipeline
            flush_command()
            if pipeline:
                self._emit_pipeline(pipeline, hd_iter)
            pipeline = []

        while i < n:
            t = tokens[i]
            if isinstance(t, Op):
                if t.text == "(":
                    # subshell or function definition
                    if (
                        current
                        and i + 1 < n
                        and isinstance(tokens[i + 1], Op)
                        and tokens[i + 1].text == ")"
                    ):
                        # name() -> function definition
                        self.issues.append("function definition")
                        self.units.append(
                            self._unparseable("function definition", raw=self._raw(current))
                        )
                        current = []
                        i += 2
                        continue
                    # find matching )
                    depth = 1
                    j = i + 1
                    while j < n and depth > 0:
                        tj = tokens[j]
                        if isinstance(tj, Op):
                            if tj.text == "(":
                                depth += 1
                            elif tj.text == ")":
                                depth -= 1
                        j += 1
                    if depth != 0:
                        self.units.append(self._unparseable("unbalanced parentheses"))
                        return
                    inner = tokens[i + 1 : j - 1]
                    flush_command()
                    sub = _Parser(
                        "", wrappers=self.wrappers, remote=self.remote, depth=self.depth + 1
                    )
                    sub._parse_list(inner, [])
                    self.units.extend(sub.units)
                    self.issues.extend(sub.issues)
                    i = j
                    continue
                if t.text == ")":
                    paren_depth -= 1
                    self.units.append(self._unparseable("unbalanced parentheses"))
                    return
                if t.text in ("|", "|&"):
                    flush_command()
                    i += 1
                    continue
                if t.text in (";", "&&", "||", "&", "\n", ";;"):
                    flush_pipeline()
                    i += 1
                    continue
                # redirect op: keep in current, with its target
                current.append(t)
                i += 1
                continue
            # Word
            if not current and t.text in _KEYWORDS_DROP and not t.quoted:
                if t.text == "time":
                    i += 1
                    continue
                i += 1
                continue
            if not current and not t.quoted and t.text in ("for", "select"):
                # for NAME in WORDS ; do  -> drop head up to 'do'
                self.issues.append("control flow (for/select)")
                while i < n and not (isinstance(tokens[i], Op) and tokens[i].text in (";", "\n")):
                    i += 1
                continue
            if not current and not t.quoted and t.text in ("case", "function"):
                self.units.append(self._unparseable(f"control flow ({t.text})"))
                return
            current.append(t)
            i += 1
        flush_pipeline()

    def _raw(self, toks: list[Token]) -> str:
        parts = []
        for t in toks:
            parts.append(t.text)
        return " ".join(parts)

    def _emit_pipeline(self, pipeline: list[list[Token]], hd_iter: Iterator[str]) -> None:
        produced: list[list[Unit]] = []
        for cmd_tokens in pipeline:
            before = len(self.units)
            self._emit_command(cmd_tokens, hd_iter)
            produced.append(self.units[before:])
        # pipeline linkage: last primary unit of stage k -> first primary of k+1
        for k in range(1, len(produced)):
            prev = [u for u in produced[k - 1] if u.via is None]
            cur = [u for u in produced[k] if u.via is None]
            if prev and cur:
                prev[-1].pipe_to = cur[0].cmd
                cur[0].pipe_from = prev[-1].cmd

    def _emit_command(self, toks: list[Token], hd_iter: Iterator[str]) -> None:
        words: list[Word] = []
        redirects: list[Redirect] = []
        heredoc: str | None = None
        i = 0
        while i < len(toks):
            t = toks[i]
            if isinstance(t, Op):
                m = _REDIRECT_RE.match(t.text)
                op = m.group(2) if m else t.text
                if op in ("<<", "<<-"):
                    heredoc = next(hd_iter, "")
                    i += 1
                    continue
                target_tok = toks[i + 1] if i + 1 < len(toks) else None
                if isinstance(target_tok, Word):
                    redirects.append(Redirect(op, target_tok.text, target_tok.dynamic))
                    i += 2
                else:
                    redirects.append(Redirect(op, "", False))
                    i += 1
                continue
            words.append(t)
            i += 1
        raw = self._raw(toks)
        # substitutions become their own units (they run first)
        for w in words:
            for inner in w.substitutions:
                self._recurse(inner, via="substitution", wrappers=self.wrappers)
        assignments: list[str] = []
        while words and not words[0].quoted and _ASSIGN_RE.match(words[0].text):
            assignments.append(words.pop(0).text)
        if not words:
            if assignments or redirects:
                self.units.append(
                    Unit(
                        argv=[],
                        raw=raw,
                        wrappers=self.wrappers,
                        remote=self.remote,
                        redirects=tuple(redirects),
                        assignments=tuple(assignments),
                        heredoc=heredoc,
                    )
                )
            return
        if words[0].dynamic:
            self.units.append(
                Unit(
                    argv=[w.text for w in words],
                    raw=raw,
                    wrappers=self.wrappers,
                    remote=self.remote,
                    unparseable=True,
                    issues=("dynamic command name",),
                    redirects=tuple(redirects),
                    heredoc=heredoc,
                )
            )
            self.issues.append("dynamic command name")
            return
        self._unwrap(words, raw, redirects, assignments, heredoc, self.wrappers, self.remote)

    # -- wrapper unwrapping
    def _unwrap(
        self,
        words: list[Word],
        raw: str,
        redirects: list[Redirect],
        assignments: list[str],
        heredoc: str | None,
        wrappers: tuple[str, ...],
        remote: bool,
        via: str | None = None,
        dynamic_args: bool = False,
    ) -> None:
        if len(wrappers) > MAX_DEPTH * 3:
            self.units.append(self._unparseable("wrapper chain too long", raw))
            return
        if not words:
            return
        w0 = words[0]
        cmd = os.path.basename(w0.text)
        rest = words[1:]

        def emit(argv_words: list[Word], extra_via: str | None = None, dyn: bool = False) -> None:
            argv = [x.text for x in argv_words]
            self.units.append(
                Unit(
                    argv=argv,
                    raw=raw,
                    wrappers=wrappers,
                    remote=remote,
                    via=extra_via or via,
                    dynamic_args=dynamic_args or dyn or any(x.dynamic for x in argv_words[1:]),
                    redirects=tuple(redirects),
                    assignments=tuple(assignments),
                    heredoc=heredoc,
                    glob_args=tuple(x.text for x in argv_words[1:] if x.has_glob and not x.quoted),
                    dynamic_argv=tuple(x.text for x in argv_words[1:] if x.dynamic),
                )
            )

        def again(
            new_words: list[Word], wname: str, *, rem: bool = remote, dyn: bool = False
        ) -> None:
            self._unwrap(
                new_words,
                raw,
                redirects,
                assignments,
                heredoc,
                (*wrappers, wname),
                rem,
                via,
                dynamic_args or dyn,
            )

        # simple transparent wrappers
        if cmd in ("command", "builtin", "exec", "nohup", "caffeinate", "unbuffer", "chronic"):
            j = 0
            if cmd == "command":
                while j < len(rest) and rest[j].text.startswith("-") and not rest[j].quoted:
                    if "v" in rest[j].text or "V" in rest[j].text:
                        emit(words)  # command -v X is a query
                        return
                    j += 1
            elif cmd == "exec":
                while j < len(rest) and rest[j].text.startswith("-") and not rest[j].quoted:
                    j += 1  # -a name, -c, -l
                    if rest[j - 1].text == "-a":
                        j += 1
            if j < len(rest):
                if rest[j].dynamic:
                    self.units.append(self._unparseable("dynamic command name", raw))
                    return
                again(rest[j:], cmd)
            else:
                emit(words)
            return
        if cmd in ("nice", "ionice", "setsid", "stdbuf", "flock", "chrt", "taskset", "numactl"):
            j = 0
            valued = {
                "nice": {"-n", "--adjustment"},
                "ionice": {"-c", "-n", "-p", "-t"},
                "setsid": set(),
                "stdbuf": {"-i", "-o", "-e"},
                "flock": {"-w", "-E"},
                "chrt": {"-p"},
                "taskset": {"-c", "-p"},
                "numactl": {"--cpunodebind", "--membind", "--interleave", "-N", "-m", "-i", "-C"},
            }[cmd]
            while j < len(rest) and rest[j].text.startswith("-") and not rest[j].quoted:
                tok = rest[j].text
                if tok == "--":
                    j += 1
                    break
                if tok in valued and "=" not in tok:
                    j += 2
                else:
                    j += 1
            if cmd == "flock" and j < len(rest):
                j += 1  # lock file
            if cmd == "numactl" and j < len(rest) and rest[j].text == "--":
                j += 1
            if j < len(rest):
                if rest[j].dynamic:
                    self.units.append(self._unparseable("dynamic command name", raw))
                    return
                again(rest[j:], cmd)
            else:
                emit(words)
            return
        if cmd == "timeout":
            j = 0
            valued = {"-s", "--signal", "-k", "--kill-after"}
            while j < len(rest) and rest[j].text.startswith("-") and not rest[j].quoted:
                tok = rest[j].text
                j += 2 if tok in valued else 1
            j += 1  # duration
            if j < len(rest):
                again(rest[j:], "timeout")
            else:
                emit(words)
            return
        if cmd == "env":
            j = 0
            while j < len(rest):
                tok = rest[j].text
                if rest[j].quoted and not tok.startswith("-"):
                    break
                if tok == "--":
                    j += 1
                    break
                if tok in ("-u", "--unset", "-C", "--chdir"):
                    j += 2
                    continue
                if tok in ("-S", "--split-string"):
                    if j + 1 < len(rest):
                        self._recurse(
                            rest[j + 1].text, via=via, wrappers=(*wrappers, "env -S"), remote=remote
                        )
                    else:
                        emit(words)
                    return
                if tok.startswith("-") or _ASSIGN_RE.match(tok):
                    j += 1
                    continue
                break
            if j < len(rest):
                if rest[j].dynamic:
                    self.units.append(self._unparseable("dynamic command name", raw))
                    return
                again(rest[j:], "env")
            else:
                emit(words)
            return
        if cmd in ("sudo", "doas", "pkexec", "runuser"):
            j = 0
            valued = {
                "-u",
                "-g",
                "-C",
                "-D",
                "-h",
                "-p",
                "-r",
                "-t",
                "-T",
                "-U",
                "--user",
                "--group",
                "--host",
                "--prompt",
                "--role",
                "--type",
                "--chdir",
            }
            shell_flag = False
            while j < len(rest):
                tok = rest[j].text
                if tok == "--":
                    j += 1
                    break
                if tok.startswith("-") and not rest[j].quoted:
                    if tok in valued and "=" not in tok:
                        j += 2
                        continue
                    if tok in ("-s", "-i", "--shell", "--login"):
                        shell_flag = True
                    if tok == "-c" and cmd == "runuser":
                        self._recurse(
                            rest[j + 1].text if j + 1 < len(rest) else "",
                            via=via,
                            wrappers=(*wrappers, cmd),
                            remote=remote,
                        )
                        return
                    j += 1
                    continue
                if _ASSIGN_RE.match(tok) and not rest[j].quoted:
                    j += 1
                    continue
                break
            if j < len(rest):
                if rest[j].dynamic:
                    self.units.append(self._unparseable("dynamic command name", raw))
                    return
                if shell_flag:
                    # sudo -s 'cmd' / sudo -i cmd -> shell runs the string
                    self._recurse(
                        " ".join(x.text for x in rest[j:]),
                        via=via,
                        wrappers=(*wrappers, f"{cmd} -s"),
                        remote=remote,
                    )
                    return
                again(rest[j:], cmd)
            else:
                emit(words)  # interactive sudo shell
            return
        if cmd == "su":
            # su [-] [user] -c CMD
            for k, w in enumerate(rest):
                if w.text in ("-c", "--command", "--session-command") and k + 1 < len(rest):
                    self._recurse(
                        rest[k + 1].text, via=via, wrappers=(*wrappers, "su"), remote=remote
                    )
                    return
            emit(words)
            return
        if cmd == "eval":
            self._recurse(
                " ".join(x.text for x in rest),
                via="eval",
                wrappers=(*wrappers, "eval"),
                remote=remote,
            )
            return
        if cmd == "xargs":
            j = 0
            valued = {
                "-n",
                "-P",
                "-L",
                "-I",
                "-s",
                "-d",
                "-a",
                "-E",
                "-i",
                "-l",
                "--max-args",
                "--max-procs",
                "--max-lines",
                "--replace",
                "--max-chars",
                "--delimiter",
                "--arg-file",
                "--eof",
            }
            while j < len(rest) and rest[j].text.startswith("-") and not rest[j].quoted:
                tok = rest[j].text
                if tok == "--":
                    j += 1
                    break
                if tok in valued:
                    j += 2
                elif tok[:2] in ("-n", "-P", "-L", "-I", "-s", "-d") and len(tok) > 2:
                    j += 1
                else:
                    j += 1
            if j < len(rest):
                if rest[j].dynamic:
                    self.units.append(self._unparseable("dynamic command name", raw))
                    return
                again(rest[j:], "xargs", dyn=True)
            else:
                emit(words, "xargs", dyn=True)
            return
        if cmd in _SHELLS:
            # bash [opts] -c STRING [args] | bash script | bash
            j = 0
            script: str | None = None
            while j < len(rest):
                tok = rest[j].text
                if tok == "--":
                    j += 1
                    break
                if tok == "-o" or tok == "+o":
                    j += 2
                    continue
                if tok.startswith("--"):
                    j += 1
                    continue
                if tok.startswith(("-", "+")) and len(tok) > 1 and not rest[j].quoted:
                    if "c" in tok[1:]:
                        if j + 1 < len(rest):
                            script = rest[j + 1].text
                            if rest[j + 1].dynamic and not rest[j + 1].substitutions:
                                self.units.append(self._unparseable("dynamic shell script", raw))
                                return
                        else:
                            script = ""
                        break
                    j += 1
                    continue
                break
            if cmd == "busybox":
                if j < len(rest):
                    again(rest[j:], "busybox")
                else:
                    emit(words)
                return
            if script is not None:
                self._recurse(script, via=via, wrappers=(*wrappers, f"{cmd} -c"), remote=remote)
                return
            emit(words)
            return
        if cmd == "ssh":
            j = 0
            valued = {
                "-b",
                "-c",
                "-D",
                "-E",
                "-e",
                "-F",
                "-I",
                "-i",
                "-J",
                "-L",
                "-l",
                "-m",
                "-O",
                "-o",
                "-p",
                "-Q",
                "-R",
                "-S",
                "-W",
                "-w",
                "-B",
                "-P",
            }
            while j < len(rest) and rest[j].text.startswith("-") and not rest[j].quoted:
                tok = rest[j].text
                if tok == "--":
                    j += 1
                    break
                if tok in valued:
                    j += 2
                elif len(tok) == 2 or tok.startswith("--"):
                    j += 1
                else:
                    # clustered, may end with a valued flag e.g. -tp 22
                    j += 2 if tok[-1] in {v[1] for v in valued} else 1
            if j >= len(rest):
                emit(words)
                return
            # rest[j] is host
            remote_cmd = rest[j + 1 :]
            if not remote_cmd:
                emit(words)  # interactive session
                return
            joined = " ".join(x.text for x in remote_cmd)
            self._recurse(joined, via=via, wrappers=(*wrappers, "ssh"), remote=True)
            return
        if cmd == "find":
            dyn = False
            k = 0
            while k < len(rest):
                tok = rest[k].text
                if tok in ("-exec", "-execdir", "-ok", "-okdir"):
                    sub: list[Word] = []
                    k += 1
                    while k < len(rest) and rest[k].text not in (";", "+"):
                        sub.append(rest[k])
                        k += 1
                    if sub:
                        self._unwrap(
                            sub,
                            raw,
                            [],
                            [],
                            None,
                            (*wrappers, "find -exec"),
                            remote,
                            "find-exec",
                            True,
                        )
                    dyn = True
                k += 1
            emit(words, dyn=dyn)
            return
        if cmd == "watch":
            j = 0
            valued = {"-n", "--interval", "-d", "--differences"}
            while j < len(rest) and rest[j].text.startswith("-"):
                j += 2 if rest[j].text in valued else 1
            if j < len(rest):
                self._recurse(
                    " ".join(x.text for x in rest[j:]),
                    via=via,
                    wrappers=(*wrappers, "watch"),
                    remote=remote,
                )
                return
            emit(words)
            return
        if cmd in ("script",):
            for k, w in enumerate(rest):
                if w.text == "-c" and k + 1 < len(rest):
                    self._recurse(
                        rest[k + 1].text, via=via, wrappers=(*wrappers, "script"), remote=remote
                    )
                    return
        emit(words)

    def _recurse(
        self,
        text: str,
        *,
        via: str | None,
        wrappers: tuple[str, ...],
        remote: bool = False,
    ) -> None:
        sub = _Parser(text, wrappers=wrappers, remote=remote, depth=self.depth + 1)
        sub.parse()
        for u in sub.units:
            if via and u.via is None:
                u.via = via
        self.units.extend(sub.units)
        self.issues.extend(sub.issues)


def parse(command: str) -> ParseResult:
    """Parse a shell command line into execution units."""
    p = _Parser(command, wrappers=(), remote=False, depth=0)
    p.parse()
    if not p.units and command.strip():
        p.units.append(
            Unit(argv=[], raw=command, unparseable=True, issues=("no command recognized",))
        )
        p.issues.append("no command recognized")
    return ParseResult(command=command, units=p.units, issues=p.issues)


def format_tree(result: ParseResult) -> str:
    lines = [f"command: {result.command!r}"]
    for k, u in enumerate(result.units, 1):
        lines.append(f"  [{k}] {u.describe()}")
        if u.redirects:
            lines.append("      redirects: " + ", ".join(f"{r.op}{r.target}" for r in u.redirects))
        if u.pipe_from:
            lines.append(f"      stdin from: {u.pipe_from}")
        if u.heredoc:
            lines.append(f"      heredoc: {u.heredoc[:60]!r}")
    if result.issues:
        lines.append("  issues: " + "; ".join(result.issues))
    return "\n".join(lines)

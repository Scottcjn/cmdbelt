# cmdbelt demo GIF — 3 beats, ~20 seconds

Goal: show the two things the README leads with — a remote `#59060`-class command
**denied**, and file damage **undone** — in one short, honest loop. No narration needed;
the terminal says it.

Record the real thing: everything below runs from `demo/demo.sh`, which is safe
(throwaway repo, nothing as root, no real destructive command executes).

## Setup (keep off-camera)

```sh
cd cmdbelt
pip install -e . >/dev/null            # or: export CMDBELT="python -m cmdbelt"
# 100-col terminal, large font, dark theme. Pause ~1.5s between beats so viewers can read.
```

Recommended tool: [`asciinema`](https://asciinema.org) + `agg` for a crisp, small GIF:

```sh
asciinema rec cmdbelt.cast -c 'bash demo/demo.sh'
agg --cols 100 --rows 32 --font-size 20 cmdbelt.cast demo/cmdbelt.gif
```

(or `vhs` with the tape below; or any screen recorder on `demo/demo.sh`).

## The three beats (what the viewer sees)

**Beat 1 — the $10k command, denied (0–7s).**
An agent tries to stop a service and wipe state the way openai/codex #59060 did.

```
$ cmdbelt check -- 'sudo systemctl stop docker && rm -rf /var/lib/docker'
DENY  rm-recursive-system — recursive delete inside a system directory
      exit 20 → Claude Code / Codex see this reason; the command never runs.
```

Hold on the `DENY` line. Optionally flash the remote variant to make the point a
local sandbox would miss it:

```
$ cmdbelt check -- "ssh prod 'systemctl stop docker; docker rm -f $(docker ps -aq)'"
DENY  … [remote: not undoable]
```

**Beat 2 — an ordinary edit is allowed, but snapshotted (7–13s).**
The agent does real work. cmdbelt lets it through *and* takes a shadow-git snapshot first.

```
$ cmdbelt exec -- sh -c 'echo "broken" > app.py; rm config.toml'
$ ls
app.py                     # config.toml is gone; app.py is clobbered
$ cat app.py
broken
```

**Beat 3 — one command puts it back (13–20s).**
`cmdbelt undo` restores the files — and the project's own `.git` was never touched.

```
$ cmdbelt undo -y
restore to snapshot #1 …  restored 2 path(s)
$ ls
app.py  config.toml        # config.toml is back
$ cat app.py
def main():
    print("hello")         # app.py restored
$ git status --short       # clean — cmdbelt used a shadow repo, not yours
```

End frame: the clean `git status`. That is the whole promise — blocked what it
could, undid what it couldn't prevent, and left your repo untouched.

## vhs tape (optional, for reproducible GIFs)

```
Output demo/cmdbelt.gif
Set FontSize 20
Set Width 1100
Set Height 640
Type "bash demo/demo.sh" Enter
Sleep 20s
```

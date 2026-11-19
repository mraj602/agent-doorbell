"""`doorbell dispatch`: the headless runner.

Instead of one agent session that lives for days, the runner blocks on `doorbell wait` (no LLM,
no tokens) and starts a short, fresh agent process for each batch of messages. The process
claims the queue, handles it and exits. Any agent CLI with a non-interactive mode works:
`claude -p`, `codex exec`, `gemini -p`, or your own script.

If a run resolves nothing, the runner backs off exponentially so a broken agent can't burn
tokens in a loop. Claimed items that a run left unresolved come back after their lease expires
and become `stuck` after `max_attempts`, which also stops the loop.
"""
import os
import subprocess
import sys
import time
from datetime import datetime

from . import cli

MAX_BACKOFF = 1800


def log(msg):
    print(f"{datetime.now().isoformat(timespec='seconds')} doorbell dispatch: {msg}", flush=True)


def open_seqs(name):
    try:
        items = cli.call("inbox", name=name)["items"]
    except cli.DaemonDown:
        return set()
    return {i["seq"] for i in items}


def ensure_registered(name, folder):
    """Agents register themselves, but a dispatcher can start before the agent ever ran. Then
    register it from the folder's sub.yaml so `wait` has an agent to wait for."""
    import yaml
    while True:
        try:
            if any(a["name"] == name for a in cli.call("status").get("agents", [])):
                return
            sub_file = os.path.join(folder, "sub.yaml")
            sub = yaml.safe_load(open(sub_file)) if os.path.exists(sub_file) else {}
            cli.call("register", name=name, sub=sub or {})
            log(f"registered agent {name!r} from {sub_file if sub else 'defaults'}")
            return
        except cli.DaemonDown:
            time.sleep(3)


def dispatch(name, command, folder, timeout, once=False):
    if not command:
        raise SystemExit("error: give the command to run after --, e.g. "
                         "doorbell dispatch -- claude -p /doorbell-handle")
    folder = os.path.abspath(folder)
    env = dict(os.environ, DOORBELL_AGENT=name)
    failures = 0
    log(f"agent {name!r}, folder {folder}, command {command}")
    ensure_registered(name, folder)
    while True:
        res = cli.wait(name)
        if res.get("deregistered"):
            log("agent was deregistered; exiting")
            return
        before = open_seqs(name)
        log(f"woken: {res.get('pending', 0)} pending, {res.get('unresolved', 0)} unresolved; running")
        started = time.time()
        try:
            rc = subprocess.run(command, cwd=folder, env=env, timeout=timeout,
                                stdin=subprocess.DEVNULL).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
        except FileNotFoundError:
            raise SystemExit(f"error: command not found: {command[0]}")
        after = open_seqs(name)
        resolved = before - after
        log(f"run finished in {time.time() - started:.0f}s (exit {rc}); resolved {sorted(resolved) or 'nothing'}")
        if once:
            sys.exit(0 if rc == 0 else 1)
        if resolved or not after:
            failures = 0
            continue
        failures += 1
        pause = min(60 * 2 ** (failures - 1), MAX_BACKOFF)
        log(f"no progress ({failures} in a row); backing off {pause}s")
        time.sleep(pause)

"""`doorbell dispatch`: the headless runner.

Instead of one agent session that lives for days, the runner blocks on `doorbell wait` (no LLM,
no tokens) and starts a short, fresh agent process for each batch of messages. The process
claims the queue, handles it and exits. Any agent CLI with a non-interactive mode works:
`claude -p`, `codex exec`, `gemini -p`, or your own script.

With --handoff, each new batch is first handed to another command as JSON on stdin, for example
a live agent session reached through Watchyard (`wy peers send tag:<tag> --one --stdin`). A
handed-off batch stays claimed: it's resolved when the owner's reply shows up in Slack (or the
agent runs `doorbell ack`), or comes back after its lease and is handed off again. When the handoff
fails (nobody holds the tag, the message is refused, ...), the command after -- runs instead.
`wy peers send` only works from inside a Claude Code session (outside one it exits 4,
not_a_session) unless the owner has run `wy peers trust doorbell` (Watchyard 1.3.0 or later),
which lets a Doorbell dispatcher send to tag: targets from a service or cron too. Otherwise
start the dispatcher from a session, or it falls back to the command after --.

If a run resolves nothing, the runner backs off exponentially so a broken agent can't burn
tokens in a loop. Claimed items that a run left unresolved come back after their lease expires
and become `stuck` after `max_attempts`, which also stops the loop.
"""
import json
import os
import re
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


NO_HOLDER = re.compile(r"holds tag|no (live )?holders?\b", re.I)
NO_HOLDER_HINT = (" (hint: nobody holds that tag. The receiving session may have been ended for being idle; "
                  "restart it and pin it in `claude agents` with Ctrl+T so it stays)")


def report_handoff(name, ok, reason=None):
    """Tell the daemon how the last handoff went, so `doorbell status` can show it. Best effort."""
    try:
        cli.call("handoff_report", name=name, ok=ok, reason=reason)
    except (cli.DaemonDown, SystemExit):
        pass


def hand_off(name, handoff, folder, env, timeout, ok):
    """Claim only the new items and pipe them to the handoff command.
    Returns "handed", "empty" (nothing new) or "failed"."""
    try:
        items = cli.call("inbox", name=name, claim=True, only_new=True)["items"]
    except cli.DaemonDown:
        time.sleep(3)
        return "empty"
    if not items:
        return "empty"
    seqs = [it["seq"] for it in items]
    payload = json.dumps({"doorbell": 1, "agent": name, "items": items}, ensure_ascii=False)
    try:
        r = subprocess.run(handoff, shell=True, cwd=folder, env=env, input=payload, text=True,
                           encoding="utf-8", capture_output=True, timeout=min(timeout, 120))
        # The command's own one-line account (wy prints "delivered to ..." or the refusal on
        # stdout); stderr when stdout is empty.
        rc, said = r.returncode, [x for x in (r.stdout.strip() or r.stderr.strip()).splitlines() if x.strip()]
    except subprocess.TimeoutExpired:
        rc, said = "timeout", []
    said = "; ".join(said[-5:])   # a broadcast prints one line per receiver
    if rc in ok:
        log(f"handed off {seqs} ({len(payload)} bytes, exit {rc}){': ' + said if said else ''}")
        report_handoff(name, True)
        return "handed"
    hint = NO_HOLDER_HINT if NO_HOLDER.search(said) else ""
    log(f"handoff failed for {seqs} (exit {rc}){': ' + said if said else ''}{hint}")
    report_handoff(name, False, f"exit {rc}" + (f": {said}" if said else ""))
    return "failed"


def dispatch(name, command, folder, timeout, once=False, handoff=None, handoff_ok=(0, 8)):
    if not command and not handoff:
        raise SystemExit("error: give the command to run after --, e.g. "
                         'doorbell dispatch -- claude -p "/agent-doorbell:handle"')
    folder = os.path.abspath(folder)
    env = dict(os.environ, DOORBELL_AGENT=name)
    failures = 0
    log(f"agent {name!r}, folder {folder}, command {command}"
        + (f", handoff {handoff!r} (ok exit codes {sorted(handoff_ok)})" if handoff else ""))
    ensure_registered(name, folder)
    while True:
        res = cli.wait(name, new_only=True) if handoff else cli.wait(name)
        if res.get("deregistered"):
            log("agent was deregistered; exiting")
            return
        if handoff:
            outcome = hand_off(name, handoff, folder, env, timeout, handoff_ok)
            if outcome == "handed":
                failures = 0
                if once:
                    sys.exit(0)
                continue
            if outcome == "empty":
                continue
            if not command:
                log("no fallback command after --; the batch comes back when its lease runs out")
                if once:
                    sys.exit(1)
                continue
            log("running the fallback command")
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

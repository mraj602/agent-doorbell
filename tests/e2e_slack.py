"""End-to-end test of doorbelld against a REAL Slack workspace, using a bot to simulate other people.

Not collected by pytest (the file is not named test_*.py). Use a throwaway or test workspace.

Setup:
  * a Slack user token (xoxp-...) for the person doorbell watches, with the scopes in
    `doorbell identity add` (chmod 600 the file);
  * a bot token (xoxb-..., scope chat:write + im:write) from an app installed in the same
    workspace. The bot DMs the user, so all traffic lands in the bot<->user DM. The user must have
    opened that DM once (send the bot a message) so it appears in conversations.list.

Run:
  DOORBELL_E2E_USER_TOKEN_FILE=/path/to/throwaway-user-token \\
  DOORBELL_E2E_BOT_TOKEN_FILE=/path/to/throwaway-bot-token \\
  DOORBELL_E2E_ME=U0123ABCDEF \\
  python tests/e2e_slack.py

  DOORBELL_E2E_ME   Slack user ID of the user token's owner (the bot DMs this user).
  The bot's own user ID and bot ID are read from its token with auth.test; nothing to set.

It runs its own daemon in a temp DOORBELL_HOME with fast cadences and never touches your real
one. Without the three variables it prints a message and exits 0 (skipped).
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REQUIRED = ("DOORBELL_E2E_USER_TOKEN_FILE", "DOORBELL_E2E_BOT_TOKEN_FILE", "DOORBELL_E2E_ME")
if __name__ == "__main__" and any(not os.environ.get(v) for v in REQUIRED):
    print(f"skipped: set {', '.join(REQUIRED)} to run the real-Slack end-to-end test (see this file's docstring)")
    sys.exit(0)

ROOT = Path(__file__).resolve().parent.parent
DOORBELL = [sys.executable, "-m", "doorbell"]
ME = os.environ.get("DOORBELL_E2E_ME", "")


def token(path):
    return re.search(r"xox[pb]-[A-Za-z0-9-]+", open(os.path.expanduser(path)).read()).group(0)


def slack_api(method, tok, body=None):
    req = urllib.request.Request(f"https://slack.com/api/{method}", data=json.dumps(body or {}).encode(), headers={
        "Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=utf-8"})
    d = json.loads(urllib.request.urlopen(req).read())
    assert d["ok"], d
    return d


if __name__ == "__main__":
    BOT = token(os.environ["DOORBELL_E2E_BOT_TOKEN_FILE"])
    USER_TOKEN_FILE = os.path.expanduser(os.environ["DOORBELL_E2E_USER_TOKEN_FILE"])
    _bot = slack_api("auth.test", BOT)
    BOT_USER, BOT_ID = _bot["user_id"], _bot["bot_id"]
else:
    BOT = USER_TOKEN_FILE = BOT_USER = BOT_ID = None

HOME = Path(tempfile.mkdtemp(prefix="dbl-e2e-"))
ENV = dict(os.environ, DOORBELL_HOME=str(HOME), DOORBELL_ENDPOINT=str(HOME / "endpoint.json"),
           PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
ENV.pop("DOORBELL_SLACK_API", None)   # always talk to the real Slack
FAST = {"cadence_hot": 4, "cadence_warm": 4, "cadence_cold": 4, "thread_watch_every": 8,
        "lifecycle_every": 1, "notify": False}
SUB = {"dms": "followed", "users": [BOT_USER], "allow_bots": [BOT_ID], "mentions_of_me": False}
results = []
daemon = None


def bot_post(text, thread_ts=None):
    return slack_api("chat.postMessage", BOT,
                     {"channel": ME, "text": text, **({"thread_ts": thread_ts} if thread_ts else {})})["ts"]


def dp(*args, stdin=None, check=True):
    r = subprocess.run([*DOORBELL, *args], env=ENV, capture_output=True, text=True, input=stdin, timeout=120)
    if check and r.returncode != 0:
        raise AssertionError(f"doorbell {' '.join(args)} failed: {r.stderr.strip()}")
    return json.loads(r.stdout) if r.stdout.strip().startswith(("{", "[")) else r.stdout


def start_daemon(**extra):
    global daemon
    (HOME / "config.yaml").write_text(json.dumps({**FAST, **extra}))
    daemon = subprocess.Popen([*DOORBELL, "daemon"], env=ENV, stdout=open(HOME / "daemon.log", "a"),
                              stderr=subprocess.STDOUT)
    until(lambda: os.path.exists(ENV["DOORBELL_ENDPOINT"]) and dp("status", check=False), 15, "daemon endpoint")


def stop_daemon():
    daemon.terminate()
    daemon.wait(10)
    if os.path.exists(ENV["DOORBELL_ENDPOINT"]):
        os.unlink(ENV["DOORBELL_ENDPOINT"])


def until(fn, timeout, what):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = fn()
            if v:
                return v
        except Exception:
            pass
        time.sleep(0.5)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


def status():
    return dp("status")


def agent(name):
    return next((a for a in status()["agents"] if a["name"] == name), None)


def items(name, claim=False):
    return dp("inbox", "--name", name, *(["--claim"] if claim else []))["items"]


def texts(it):
    return [m["text"] for m in it["messages"]]


def register(name, **kw):
    sub = HOME / f"{name}.yaml"
    sub.write_text(json.dumps(kw.pop("sub", SUB)))
    args = ["register", "--name", name, "--sub", str(sub)]
    for k, v in kw.items():
        args += [f"--{k.replace('_', '-')}", str(v)]
    return dp(*args)


def waiter(name):
    return subprocess.Popen([*DOORBELL, "wait", "--name", name], env=ENV, stdout=subprocess.PIPE, text=True)


def ack_all(name):
    seqs = [str(i["seq"]) for i in items(name)]
    if seqs:
        dp("ack", "--name", name, *seqs, "--note", "test")


def polled_once():
    """Wait until the bot DM conversation has an initialised cursor (first poll done)."""
    import sqlite3
    db = sqlite3.connect(HOME / "doorbell.db")
    return until(lambda: db.execute("SELECT 1 FROM conv WHERE kind='im' AND other_user=? AND cursor IS NOT NULL",
                                    (BOT_USER,)).fetchone(), 30, "first poll of the bot DM")


def scenario(fn):
    name = fn.__name__
    t = time.time()
    try:
        detail = fn() or ""
        results.append((name, "PASS", f"{time.time() - t:.0f}s {detail}"))
    except Exception as e:
        results.append((name, "FAIL", repr(e)))
    print(f"[{results[-1][1]}] {name}: {results[-1][2]}", flush=True)


# ---------------------------------------------------------------- scenarios
def s1_dormant_makes_no_api_calls():
    dp("identity", "add", "--token-file", USER_TOKEN_FILE)
    time.sleep(12)
    st = status()
    assert st["state"] == "DORMANT", st["state"]
    assert st["api_calls"]["since_start"] == 0, st["api_calls"]
    return "0 Slack calls in 12s with no agents"


def s2_register_and_wake():
    r = register("main")
    assert r["created"]
    w = waiter("main")
    polled_once()
    assert agent("main")["status"] == "active"
    t0 = time.time()
    ts = bot_post("e2e: hello from the bot")
    out = w.communicate(timeout=60)[0]
    lat = time.time() - t0
    assert json.loads(out)["pending"] == 1, out
    its = items("main", claim=True)
    assert len(its) == 1 and texts(its[0]) == ["e2e: hello from the bot"], its
    assert its[0]["reply_to"] == {"channel_id": its[0]["conversation"]["channel_id"], "thread_ts": None}
    assert its[0]["state"] == "claimed"
    globals()["T_PARENT"] = ts
    return f"wait woke {lat:.1f}s after the bot posted"


def s3_wait_returns_unresolved_then_thread_reply():
    w = waiter("main")
    out = json.loads(w.communicate(timeout=10)[0])
    assert out["unresolved"] == 1, out          # claimed-but-unacked => wait refuses to block
    ack_all("main")
    w = waiter("main")
    bot_post("e2e: thread reply", thread_ts=T_PARENT)
    json.loads(w.communicate(timeout=60)[0])
    its = items("main")
    assert len(its) == 1 and its[0]["thread_key"] == T_PARENT, its
    assert its[0]["reply_to"]["thread_ts"] == T_PARENT
    assert any(c["text"] == "e2e: hello from the bot" for c in its[0]["context"]), "thread context missing"
    ack_all("main")
    return "thread reply detected via latest_reply, with parent as context"


def s4_merge_pending_messages():
    bot_post("e2e: merge A")
    until(lambda: items("main"), 30, "merge A delivered")
    bot_post("e2e: merge B")
    until(lambda: len(items("main")[0]["messages"]) == 2, 30, "merge B merged")
    its = items("main")
    assert len(its) == 1 and texts(its[0]) == ["e2e: merge A", "e2e: merge B"], its
    ack_all("main")
    return "2 messages -> 1 pending item"


def s5_fanout_single_fetch():
    register("qa")
    time.sleep(6)
    import sqlite3
    db = sqlite3.connect(HOME / "doorbell.db")
    bot_post("e2e: fan-out")
    until(lambda: items("main") and items("qa"), 40, "both agents receive")
    n = db.execute("SELECT count(*) FROM messages WHERE text='e2e: fan-out'").fetchone()[0]
    assert n == 1, n
    a = time.time()
    c1 = status()["api_calls"]["since_start"]
    time.sleep(16)
    rate2 = (status()["api_calls"]["since_start"] - c1) / (time.time() - a) * 60
    ack_all("main"), ack_all("qa")
    return f"both queues got it from one stored fetch; {rate2:.0f} calls/min with 2 agents"


def s6_detached_queues_then_wait_returns_immediately():
    dp("deregister", "--name", "qa")
    for i in range(3):
        bot_post(f"e2e: while detached {i}")
    until(lambda: items("main") and len(items("main")[0]["messages"]) == 3, 40, "3 queued while detached")
    assert agent("main")["status"] == "detached"
    t = time.time()
    out = json.loads(waiter("main").communicate(timeout=10)[0])
    assert out["pending"] == 1 and time.time() - t < 3, out
    ack_all("main")
    return "3 msgs queued with no listener; wait returned instantly"


def s7_inactive_dormant_and_catchup():
    register("main", idle_ttl=6, resume="checkpoint")
    register("nap", idle_ttl=6, resume="now")
    until(lambda: status()["state"] == "DORMANT", 30, "both agents inactive -> dormant")
    assert agent("main")["status"] == "inactive" and agent("nap")["status"] == "inactive"
    c1 = status()["api_calls"]["since_start"]
    ts = bot_post("e2e: posted while everyone inactive")
    time.sleep(10)
    assert status()["api_calls"]["since_start"] == c1, "polled while dormant"
    out = json.loads(waiter("main").communicate(timeout=60)[0])     # contact => reactivate + catch up
    its = items("main")
    assert any("while everyone inactive" in t for it in its for t in texts(it)), its
    dp("inbox", "--name", "nap")     # contact => reactivate with resume=now
    time.sleep(10)
    assert items("nap") == [], items("nap")
    ack_all("main")
    dp("deregister", "--name", "nap")
    register("main", idle_ttl=3600, resume="checkpoint")
    return "dormant made 0 calls; checkpoint agent caught up, resume=now agent skipped the gap"


def s8_restart_keeps_queue_without_dupes():
    bot_post("e2e: survives restart")
    until(lambda: items("main"), 40, "delivered before restart")
    stop_daemon()
    start_daemon()
    time.sleep(12)
    its = items("main")
    assert len(its) == 1 and texts(its[0]) == ["e2e: survives restart"], its
    ack_all("main")
    return "1 pending item before and after restart"


def s9_lease_expiry_redelivery_and_stuck():
    stop_daemon()
    start_daemon(lease=4, max_attempts=2)
    bot_post("e2e: never acked")
    until(lambda: items("main"), 40, "delivered")
    items("main", claim=True)
    until(lambda: items("main") and items("main")[0]["state"] == "pending", 20, "lease expiry -> pending")
    it = items("main", claim=True)[0]
    assert it["attempt"] == 2 and it["warning"], it
    until(lambda: status()["stuck"], 20, "stuck after max attempts")
    seq = status()["stuck"][0]["seq"]
    dp("retry", str(seq))
    assert items("main")[0]["state"] == "pending"
    ack_all("main")
    stop_daemon()
    start_daemon()
    return "re-delivered with warning, then stuck, then retried"


def s10_hooks():
    agent_dir = HOME / "agentdir"
    agent_dir.mkdir(exist_ok=True)
    (agent_dir / ".doorbell-agent").write_text("main")
    # post-tool-use: send_message addressed by user id (DM) acks the main-thread item
    bot_post("e2e: hook send")
    until(lambda: items("main"), 40, "delivered")
    items("main", claim=True)
    stop = dp("hook", "stop", stdin=json.dumps({"cwd": str(agent_dir), "stop_hook_active": False}))
    assert stop["decision"] == "block", stop
    again = dp("hook", "stop", stdin=json.dumps({"cwd": str(agent_dir), "stop_hook_active": True}))
    assert again.strip() == "", again
    dp("hook", "post-tool-use", stdin=json.dumps({
        "tool_name": "mcp__plugin_slack_slack__slack_send_message",
        "tool_input": {"channel_id": BOT_USER, "message": "x"}}))
    assert items("main") == [], items("main")
    assert dp("hook", "stop", stdin=json.dumps({"cwd": str(agent_dir)})).strip() == ""
    # reaction hook acks by message ts
    ts = bot_post("e2e: hook reaction")
    until(lambda: items("main"), 40, "delivered")
    items("main", claim=True)
    ch = items("main")[0]["conversation"]["channel_id"]
    dp("hook", "post-tool-use", stdin=json.dumps({
        "tool_name": "mcp__plugin_slack_slack__slack_add_reaction",
        "tool_input": {"channel_id": ch, "message_ts": ts, "emoji": "eyes"}}))
    assert items("main") == []
    log = [json.loads(r["detail"]) for r in dp("log")["log"] if r["kind"] == "resolve"]
    assert any(l["outcome"] == "reacted" for l in log) and any(l["outcome"] == "replied" for l in log)
    return "stop hook blocks with unresolved, send+reaction hooks ack"


def s11_sub_change_and_ignore():
    dp("sub", "remove", "users", BOT_USER, "--name", "main", "--why", "test unfollow")
    time.sleep(5)
    bot_post("e2e: should be ignored")
    time.sleep(12)
    assert items("main") == [], items("main")
    dp("sub", "add", "users", BOT_USER, "--name", "main", "--why", "test refollow")
    r = subprocess.run([*DOORBELL, "sub", "add", "users", "@definitely-not-a-user-xyz", "--name", "main"],
                       env=ENV, capture_output=True, text=True)
    assert r.returncode != 0 and "unknown users" in r.stderr, r.stderr
    return "unfollowed bot ignored; bad user rejected"


def s12_deregister_releases_waiter():
    w = waiter("main")
    time.sleep(2)
    dp("deregister", "--name", "main")
    out = json.loads(w.communicate(timeout=10)[0])
    assert out.get("deregistered"), out
    until(lambda: status()["state"] == "DORMANT", 10, "dormant after deregister")
    return "waiter released, daemon dormant"


if __name__ == "__main__":
    start_daemon()
    try:
        for fn in [s1_dormant_makes_no_api_calls, s2_register_and_wake,
                   s3_wait_returns_unresolved_then_thread_reply, s4_merge_pending_messages,
                   s5_fanout_single_fetch, s6_detached_queues_then_wait_returns_immediately,
                   s7_inactive_dormant_and_catchup, s8_restart_keeps_queue_without_dupes,
                   s9_lease_expiry_redelivery_and_stuck, s10_hooks, s11_sub_change_and_ignore,
                   s12_deregister_releases_waiter]:
            scenario(fn)
    finally:
        stop_daemon()
    print("\n".join(f"{s:4} {n}" for n, s, _ in results))
    print(f"daemon log + db: {HOME}")
    sys.exit(0 if all(s == "PASS" for _, s, _ in results) else 1)

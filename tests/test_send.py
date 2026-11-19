"""Optional sending: the sealed sender token, drafts the owner approves, grants, lock and unlock."""
import json
import os

import pytest
from conftest import until
from mock_slack import REQUIRED_SCOPES

from doorbell import seal

PW = "correct horse"
SENDER = "xoxp-9999-8888-sendertoken"


# ---------- sealing ----------
def test_seal_roundtrip_and_wrong_password():
    blob = seal.seal("xoxp-secret", PW)
    assert "xoxp-secret" not in blob
    token, key = seal.unseal(blob, PW)
    assert token == "xoxp-secret" and len(key) == 32
    with pytest.raises(seal.BadPassword):
        seal.unseal(blob, "wrong password")
    with pytest.raises(ValueError):
        seal.seal("xoxp-secret", "short")


def test_grant_mac_is_bound_to_agent_and_identity():
    _, key = seal.unseal(seal.seal("t", PW), PW)
    mac = seal.grant_mac(key, "U1", "a")
    assert seal.grant_ok(key, "U1", "a", mac)
    assert not seal.grant_ok(key, "U1", "b", mac)
    assert not seal.grant_ok(key, "U2", "a", mac)
    assert not seal.grant_ok(os.urandom(32), "U1", "a", mac)
    assert not seal.grant_ok(key, "U1", "a", None)


# ---------- daemon ----------
def prime(e):
    """Like the shared fixture: let the first conversation list pass, so new DMs count as new."""
    general = e.mock.add_channel("general")
    e.register({"dms": "none", "channels": [general]}, name="prime")
    until(lambda: e.db().execute("SELECT 1 FROM conv WHERE channel=? AND cursor IS NOT NULL",
                                 (general,)).fetchone(), 15, "priming poll")
    e.deregister_all()


@pytest.fixture(scope="module")
def senv():
    from conftest import Env
    e = Env(config={"send_rate_per_hour": 3}).start()
    e.add_identity()
    prime(e)
    e.mock.token_scopes[SENDER] = REQUIRED_SCOPES + ["chat:write", "reactions:write"]
    e.sender_file = e.home / "sender-token"
    e.sender_file.write_text(SENDER + "\n")
    e.sender_file.chmod(0o600)
    yield e
    e.stop()


@pytest.fixture
def s(senv):
    if not senv.call("status")["sending"]:
        senv.call("sender_add", token_file=str(senv.sender_file), password=PW)
    senv.call("unlock", password=PW)
    yield senv
    senv.deregister_all()
    for g in senv.call("status")["sending"].get(senv.me, {}).get("granted", []):
        senv.call("revoke", agent=g)


def err(fn, *a, **kw):
    with pytest.raises(SystemExit) as e:
        fn(*a, **kw)
    return str(e.value)


def dm_item(s, text="can you check the build?"):
    u = s.mock.add_user("pat")
    dm = s.mock.add_dm(u)
    name = s.register({"dms": "all"})
    s.mock.post(dm, text, user=u)
    return name, dm, s.wait_items(name, claim=True)[0]


def posted(s, channel):
    return [c["params"] for c in s.mock.calls if c["method"] == "chat.postMessage" and c["params"]["channel"] == channel]


def test_sender_add_needs_chat_write(make_env):
    e = make_env()
    e.add_identity()
    f = e.home / "readonly"
    f.write_text("xoxp-readonly\n")
    f.chmod(0o600)
    assert "missing scopes ['chat:write']" in err(e.call, "sender_add", token_file=str(f), password=PW)
    assert e.call("status")["sending"] == {}


@pytest.mark.skipif(os.name == "nt", reason="Unix file modes")
def test_sender_add_refuses_a_readable_token_file(senv):
    f = senv.home / "open-token"
    f.write_text(SENDER + "\n")
    f.chmod(0o644)
    assert "readable by other users" in err(senv.call, "sender_add", token_file=str(f), password=PW)


def test_sealed_token_is_not_on_disk_in_clear(s):
    row = s.db().execute("SELECT sealed FROM sender").fetchone()
    assert SENDER not in row["sealed"]
    assert SENDER.encode() not in (s.home / "doorbell.db").read_bytes()
    assert s.call("status")["sending"][s.me]["unlocked"] is True


def test_draft_then_approve_posts_in_the_dm(s):
    name, dm, it = dm_item(s)
    res = s.call("draft", name=name, seq=it["seq"], text="Looking now.")
    assert res["acked"] == [it["seq"]] and s.items(name) == []
    assert posted(s, dm) == []                       # nothing in Slack until approved
    d = next(x for x in s.call("drafts")["drafts"] if x["id"] == res["draft"])
    assert d["text"] == "Looking now." and d["to"] == "to Pat"
    assert d["replying_to"][-1]["text"] == "can you check the build?"
    assert "wrong password" in err(s.call, "approve", id=d["id"], password="nope nope")
    assert posted(s, dm) == []
    s.call("approve", id=d["id"], password=PW, text="Looking now, back in 10.")
    p = posted(s, dm)
    assert len(p) == 1 and p[0]["text"] == "Looking now, back in 10." and "thread_ts" not in p[0]
    assert not any(x["id"] == d["id"] for x in s.call("drafts")["drafts"])
    assert "no pending draft" in err(s.call, "approve", id=d["id"], password=PW)


def test_approve_uses_the_sender_token_not_the_watcher_token(s):
    name, dm, it = dm_item(s)
    did = s.call("draft", name=name, seq=it["seq"], text="ok")["draft"]
    s.call("approve", id=did, password=PW)
    call = next(c for c in s.mock.calls if c["method"] == "chat.postMessage" and c["params"]["channel"] == dm)
    assert call["token"] == SENDER


def test_reject_needs_no_password_and_posts_nothing(s):
    name, dm, it = dm_item(s)
    did = s.call("draft", name=name, seq=it["seq"], text="nah")["draft"]
    s.call("reject", id=did)
    assert posted(s, dm) == []
    assert not any(x["id"] == did for x in s.call("drafts")["drafts"])


def test_send_needs_a_grant_and_request_send_notifies(s):
    name, dm, it = dm_item(s)
    assert "may not send directly" in err(s.call, "send", name=name, seq=it["seq"], text="hi")
    s.call("request_send", name=name, why="routine build questions")
    st = s.call("status")
    assert {"agent": name, "why": "routine build questions"} in st["send_requests"]
    alerts = [json.loads(r["detail"])["msg"] for r in s.db().execute("SELECT detail FROM audit WHERE kind='alert'")]
    assert any(f"Agent {name} asks to send" in m and "routine build questions" in m for m in alerts)
    assert "wrong password" in err(s.call, "grant", agent=name, password="guess guess")
    s.call("grant", agent=name, password=PW)
    assert name not in [r["agent"] for r in s.call("status")["send_requests"]]
    res = s.call("send", name=name, seq=it["seq"], text="Build is green.")
    assert res["acked"] == [it["seq"]]
    assert [p["text"] for p in posted(s, dm)] == ["Build is green."]
    assert [x for x in s.resolved() if x["seq"] == it["seq"]][-1]["outcome"] == "replied"


def test_send_answers_a_channel_message_in_its_thread(s):
    u = s.mock.add_user("quinn")
    ch = s.mock.add_channel("builds")
    name = s.register({"dms": "none", "channels": [ch], "users": [u]})
    top = s.mock.post(ch, "is main broken?", user=u)
    it = s.wait_items(name, claim=True)[0]
    s.call("grant", agent=name, password=PW)
    s.call("send", name=name, seq=it["seq"], text="No, flaky test.")
    assert posted(s, ch)[0]["thread_ts"] == top


def test_send_only_reaches_the_agents_own_open_items(s):
    name, dm, it = dm_item(s)
    other = s.register({"dms": "none"})
    s.call("grant", agent=other, password=PW)
    assert "not an open item" in err(s.call, "send", name=other, seq=it["seq"], text="hijack")
    s.call("grant", agent=name, password=PW)
    s.call("send", name=name, seq=it["seq"], text="first")
    assert "not an open item" in err(s.call, "send", name=name, seq=it["seq"], text="again")
    assert [p["text"] for p in posted(s, dm)] == ["first"]


def test_lock_unlock_and_rate_limit(s):
    name, dm, it = dm_item(s)
    s.call("grant", agent=name, password=PW)
    s.call("lock")
    assert "locked" in err(s.call, "send", name=name, seq=it["seq"], text="x")
    assert "wrong password" in err(s.call, "unlock", password="wrong one!")
    s.call("unlock", password=PW)
    s.call("send", name=name, seq=it["seq"], text="1")
    for _ in range(2):
        s.mock.post(dm, "more?", user=s.mock.channels[dm]["meta"]["user"])
        nxt = s.wait_items(name, claim=True)[0]
        s.call("send", name=name, seq=nxt["seq"], text="more")
    s.mock.post(dm, "and more?", user=s.mock.channels[dm]["meta"]["user"])
    nxt = s.wait_items(name, claim=True)[0]
    assert "the limit" in err(s.call, "send", name=name, seq=nxt["seq"], text="too many")


def test_revoke_and_react(s):
    name, dm, it = dm_item(s)
    s.call("grant", agent=name, password=PW)
    s.call("react", name=name, seq=it["seq"], emoji=":eyes:")
    msg = s.mock.channels[dm]["messages"][-1]
    assert msg["reactions"][0]["name"] == "eyes"
    s.mock.post(dm, "again", user=s.mock.channels[dm]["meta"]["user"])
    nxt = s.wait_items(name, claim=True)[0]
    s.call("revoke", agent=name)
    assert "may not send directly" in err(s.call, "send", name=name, seq=nxt["seq"], text="x")


def test_passwd_keeps_grants_and_restart_locks(make_env):
    e = make_env()
    e.add_identity()
    prime(e)
    e.mock.token_scopes[SENDER] = REQUIRED_SCOPES + ["chat:write"]
    f = e.home / "sender"
    f.write_text(SENDER + "\n")
    f.chmod(0o600)
    e.call("sender_add", token_file=str(f), password=PW)
    name = e.register({"dms": "all"})
    e.call("grant", agent=name, password=PW)
    e.call("passwd", old_password=PW, password="new password 2")
    assert "wrong password" in err(e.call, "unlock", password=PW)
    # Restart: the token is only in memory, so Doorbell comes back locked.
    e.proc.terminate()
    e.proc.wait(30)
    e.start()
    assert e.call("status")["sending"][e.me] == {"unlocked": False, "granted": [name]}
    u = e.mock.add_user("ria")
    dm = e.mock.add_dm(u)
    e.mock.post(dm, "ping", user=u)
    it = e.wait_items(name, claim=True)[0]
    assert "locked" in err(e.call, "send", name=name, seq=it["seq"], text="x")
    e.call("unlock", password="new password 2")
    e.call("send", name=name, seq=it["seq"], text="pong")   # the grant survived the new password
    until(lambda: any(m["text"] == "pong" for m in e.mock.channels[dm]["messages"]), 5, "pong posted")


def test_own_send_is_not_counted_as_owner_reply(s):
    """A message Doorbell sent is the agent's reply, even though Slack shows it as typed by the owner."""
    name, dm, it = dm_item(s)
    s.call("grant", agent=name, password=PW)
    s.call("send", name=name, seq=it["seq"], text="done")
    s.settle(dm)
    outcomes = [x["outcome"] for x in s.resolved() if x["seq"] == it["seq"]]
    assert outcomes == ["replied"]


# ---------- the owner-only guard ----------
@pytest.mark.parametrize("args", [["grant", "x"], ["unlock"], ["approve", "1"], ["passwd"], ["review"],
                                  ["sender", "add", "--token-file", "f"]])
def test_owner_commands_refuse_inside_an_agent(senv, args):
    r = senv.cli(*args, check=False)   # no terminal: captured stdin and stdout
    assert r.returncode != 0 and "terminal" in r.stderr
    env = {**senv.env, "CLAUDECODE": "1"}
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-m", "doorbell", *args], env=env, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=60)
    assert r.returncode != 0 and "AI agent session" in r.stderr and "CLAUDECODE" in r.stderr


def test_agent_commands_read_text_from_stdin(s):
    name, dm, it = dm_item(s)
    r = s.cli("draft", it["seq"], "--name", name, "--text-file", "-", stdin="line 1\n`code` and \"quotes\"\n")
    did = json.loads(r.stdout)["draft"]
    d = next(x for x in s.call("drafts")["drafts"] if x["id"] == did)
    assert d["text"] == "line 1\n`code` and \"quotes\""


# ---------- from the security review ----------
def test_sender_remove_needs_the_password_so_it_cant_be_reset(s):
    assert "wrong password" in err(s.call, "sender_remove", password="not it at all")
    assert s.call("status")["sending"]          # still there
    # Replacing the token also needs the current password.
    assert "wrong password" in err(s.call, "sender_add", token_file=str(s.sender_file), password="attacker pw",
                                   old_password="guessing!")


def test_sender_add_refuses_the_watcher_token(s):
    assert "watcher's token" in err(s.call, "sender_add", token_file=str(s.token_file), password=PW, old_password=PW)


def test_grant_stops_working_when_the_subscription_changes(s):
    name, dm, it = dm_item(s)
    s.call("grant", agent=name, password=PW)
    s.call("sub", name=name, action="set", key="everyone_in", values=[s.mock.add_channel("all-hands")],
           why="widen")
    assert "no longer matches" in err(s.call, "send", name=name, seq=it["seq"], text="x")
    s.call("grant", agent=name, password=PW)
    s.call("send", name=name, seq=it["seq"], text="ok now")


def test_reregistering_or_deregistering_drops_the_grant(s):
    name, dm, it = dm_item(s)
    s.call("grant", agent=name, password=PW)
    s.call("deregister", name=name)
    assert name not in s.call("status")["sending"][s.me]["granted"]
    s.call("register", name=name, sub={"dms": "all"})
    s.mock.post(dm, "hello again", user=s.mock.channels[dm]["meta"]["user"])
    nxt = s.wait_items(name, claim=True)[0]
    assert "may not send directly" in err(s.call, "send", name=name, seq=nxt["seq"], text="x")


def test_parallel_sends_of_one_item_post_once(s):
    import threading
    name, dm, it = dm_item(s)
    s.call("grant", agent=name, password=PW)
    results = []

    def go():
        try:
            results.append(s.call("send", name=name, seq=it["seq"], text="once"))
        except SystemExit as e:
            results.append(str(e))
    ts = [threading.Thread(target=go) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert [p["text"] for p in posted(s, dm)] == ["once"]
    assert sum(isinstance(r, dict) for r in results) == 1


def test_reactions_count_toward_the_limit_and_broadcasts_are_neutralised(s):
    name, dm, it = dm_item(s)
    s.call("grant", agent=name, password=PW)
    s.call("send", name=name, seq=it["seq"], text="<!channel> heads up <!here|here>")
    assert posted(s, dm)[0]["text"] == "@channel heads up @here"
    user = s.mock.channels[dm]["meta"]["user"]
    for i in range(2):
        s.mock.post(dm, f"q{i}", user=user)
        nxt = s.wait_items(name, claim=True)[0]
        s.call("react", name=name, seq=nxt["seq"], emoji="eyes")
    s.mock.post(dm, "q3", user=user)
    nxt = s.wait_items(name, claim=True)[0]
    assert "the limit" in err(s.call, "react", name=name, seq=nxt["seq"], emoji="eyes")
    assert "not an emoji" in err(s.call, "react", name=name, seq=nxt["seq"], emoji="a b")


def test_passwd_drops_a_grant_written_straight_into_the_database(make_env):
    import sqlite3
    e = make_env()
    e.add_identity()
    e.mock.token_scopes[SENDER] = REQUIRED_SCOPES + ["chat:write"]
    f = e.home / "sender"
    f.write_text(SENDER + "\n")
    f.chmod(0o600)
    e.call("sender_add", token_file=str(f), password=PW)
    good, bad = e.register({"dms": "all"}), e.register({"dms": "none"})
    e.call("grant", agent=good, password=PW)
    db = sqlite3.connect(e.home / "doorbell.db", timeout=10)
    db.execute("INSERT INTO grants VALUES(?,?,?,?)", (e.me, bad, "00" * 32, 0))
    db.commit()
    db.close()
    e.call("passwd", old_password=PW, password="another password")
    assert e.call("status")["sending"][e.me]["granted"] == [good]


def test_wrong_passwords_slow_down_and_notify(make_env):
    import time as _t
    e = make_env()
    e.add_identity()
    e.mock.token_scopes[SENDER] = REQUIRED_SCOPES + ["chat:write"]
    f = e.home / "sender"
    f.write_text(SENDER + "\n")
    f.chmod(0o600)
    e.call("sender_add", token_file=str(f), password=PW)
    t = _t.time()
    for _ in range(3):
        err(e.call, "unlock", password="wrong guess")
    assert _t.time() - t >= 2 + 4          # 0 s, then 2 s, then 4 s before the checks
    alerts = [r[0] for r in e.db().execute("SELECT detail FROM audit WHERE kind='alert'")]
    assert any("wrong sending passwords" in a for a in alerts)
    e.call("unlock", password=PW)          # the right one still works, and resets the delay
    t = _t.time()
    err(e.call, "unlock", password="wrong again")
    assert _t.time() - t < 2


def test_review_output_escapes_control_characters(capsys):
    from doorbell import owner
    owner.show({"id": 1, "agent": "a", "to": "to Pat", "text": "fine\x1b[2K\rmalicious <!channel>",
                "replying_to": [{"user": "U1", "text": "hi\x1b]8;;x\x07"}], "error": None})
    o = capsys.readouterr().out
    assert "\x1b" not in o and "\r" not in o and "\\x1b" in o and "notifies everyone" in o

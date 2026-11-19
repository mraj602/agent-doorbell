"""The agent-facing protocol: register, wait, inbox --claim, ack, observed acks, leases."""
import json
import time

import pytest
import yaml

from conftest import until


def texts(item):
    return [m["text"] for m in item["messages"]]


def test_register_with_sub_yaml_via_cli(env, alice, tmp_path):
    ch = env.mock.add_channel("reg")
    (tmp_path / "sub.yaml").write_text(yaml.safe_dump({"dms": "none", "channels": [ch], "users": [alice]}))
    (tmp_path / ".doorbell-agent").write_text("yaml-agent\n")
    # sub.yaml and the name both come from the current folder
    res = json.loads(env.cli("register", cwd=tmp_path).stdout)
    assert res["created"] is True
    a = res["agent"]
    assert a["name"] == "yaml-agent" and a["status"] == "detached"
    assert a["sub"] == {"dms": "none", "channels": [ch], "users": [alice]}
    assert a["resume_policy"] == "checkpoint"
    again = json.loads(env.cli("register", "--sub", tmp_path / "sub.yaml", "--resume", "now",
                               "--idle-ttl", "120", cwd=tmp_path).stdout)
    assert again["created"] is False
    assert again["agent"]["resume_policy"] == "now" and again["agent"]["idle_ttl"] == 120


def test_register_needs_a_name(env, tmp_path):
    r = env.cli("register", cwd=tmp_path, check=False)
    assert r.returncode != 0 and "--name" in r.stderr


def test_wait_blocks_until_an_item_arrives(env, alice):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    w = env.popen("wait", "--name", name)
    until(lambda: env.agent(name)["waiters"] == 1, what="waiter connected")
    assert env.agent(name)["status"] == "active"
    assert w.poll() is None
    env.mock.post(dm, "wake up", user=alice)
    out, err = w.communicate(timeout=15)
    assert json.loads(out) == {"pending": 1, "unresolved": 0}, err
    until(lambda: env.agent(name)["status"] == "detached", what="detached after wait returns")


def test_wait_returns_immediately_when_work_is_open(env, alice):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    env.mock.post(dm, "queued while nobody listens", user=alice)
    env.wait_items(name, claim=True)
    t = time.time()
    out = json.loads(env.cli("wait", "--name", name).stdout)
    assert out == {"pending": 0, "unresolved": 1} and time.time() - t < 5


def test_inbox_claim_returns_messages_context_and_reply_to(env, alice):
    ch = env.mock.add_channel("ib")
    name = env.register({"dms": "none", "channels": [ch], "users": [alice]})
    env.mock.post(ch, "earlier chatter", user=env.mock.add_user("carol"))
    first = env.mock.post(ch, "alice one", user=alice, files=[{"name": "log.txt"}])
    second = env.mock.post(ch, "alice two", user=alice)
    until(lambda: env.items(name) and len(env.items(name)[0]["messages"]) == 2, what="merged item")
    assert env.items(name)[0]["state"] == "pending"
    it = env.items(name, claim=True)[0]
    assert it["state"] == "claimed" and it["attempt"] == 1 and it["warning"] is None
    assert [m["text"] for m in it["messages"]] == ["alice one", "alice two"]
    m = it["messages"][0]
    assert m["user"] == alice and m["user_name"] == "Alice" and not m["is_me"] and not m["from_agent"]
    assert m["files"] == ["log.txt"]
    assert m["permalink"] == f"https://mock.slack.test/archives/{ch}/p{first.replace('.', '')}"
    assert [c["text"] for c in it["context"]] == ["earlier chatter"]
    assert it["conversation"] == {"channel_id": ch, "kind": "channel", "name": "ib", "with": None}
    assert it["thread_key"] == "main"
    assert it["reply_to"] == {"channel_id": ch, "thread_ts": second}   # channel: reply under the last message
    # an already-claimed item is not claimed again
    again = env.items(name, claim=True)[0]
    assert again["seq"] == it["seq"] and again["attempt"] == 1


def test_inbox_dm_reply_to_has_no_thread(env, alice):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    env.mock.post(dm, "hello", user=alice)
    it = env.wait_items(name, claim=True)[0]
    assert it["reply_to"] == {"channel_id": dm, "thread_ts": None}
    assert it["conversation"]["kind"] == "im" and it["conversation"]["with"] == "Alice"


def test_inbox_via_cli_uses_agent_file(env, alice, tmp_path):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    (tmp_path / ".doorbell-agent").write_text(name)
    env.mock.post(dm, "hello cli", user=alice)
    env.wait_items(name)
    res = json.loads(env.cli("inbox", "--claim", cwd=tmp_path).stdout)
    assert res["agent"] == name and texts(res["items"][0]) == ["hello cli"]
    assert res["items"][0]["state"] == "claimed"


@pytest.mark.parametrize("outcome", ["replied", "drafted", "reacted", "ignored", "escalated", "handled"])
def test_ack_outcomes(env, alice, outcome):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    env.mock.post(dm, "ack me", user=alice)
    it = env.wait_items(name, claim=True)[0]
    out = json.loads(env.cli("ack", it["seq"], "--name", name, "--outcome", outcome, "--note", "why not").stdout)
    assert out == {"acked": [it["seq"]]}
    assert env.items(name) == []
    r = [x for x in env.resolved() if x["seq"] == it["seq"]]
    assert r == [{"seq": it["seq"], "outcome": outcome, "note": "why not", "agent": name}]
    # acking twice is harmless
    assert env.call("ack", name=name, seqs=[it["seq"]])["acked"] == []


def test_default_ack_outcome_is_handled(env, alice):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    env.mock.post(dm, "x", user=alice)
    it = env.wait_items(name)[0]
    env.cli("ack", it["seq"], "--name", name)
    assert [x["outcome"] for x in env.resolved() if x["seq"] == it["seq"]] == ["handled"]


# ---------- observed acks ----------
def test_my_reply_through_an_agent_app_resolves_as_replied(env, alice):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    env.mock.post(dm, "question", user=alice)
    it = env.wait_items(name, claim=True)[0]
    env.mock.post(dm, "answer from the agent", user=env.me, app_id="A08SF47R6P4")
    until(lambda: env.items(name) == [], what="observed ack")
    r = [x for x in env.resolved() if x["seq"] == it["seq"]][0]
    assert r["outcome"] == "replied" and "agent app" in r["note"]


def test_my_manual_reply_resolves_as_owner_replied_with_defer(env, alice):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"}, on_manual_reply="defer")
    env.mock.post(dm, "question", user=alice)
    it = env.wait_items(name)[0]
    env.mock.post(dm, "I'll answer this myself", user=env.me)
    until(lambda: env.items(name) == [], what="observed manual ack")
    assert [x["outcome"] for x in env.resolved() if x["seq"] == it["seq"]] == ["owner_replied"]


def test_my_manual_reply_is_ignored_with_ignore_policy(env, alice):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"}, on_manual_reply="ignore")
    env.mock.post(dm, "question", user=alice)
    env.wait_items(name)
    reply = env.mock.post(dm, "typed by me", user=env.me)
    env.wait_stored(dm, reply)
    env.settle(dm)
    assert len(env.items(name)) == 1


def test_observed_reply_in_other_thread_does_not_resolve(env, alice):
    ch = env.mock.add_channel("ob")
    name = env.register({"dms": "none", "channels": [ch], "users": [alice]})
    env.mock.post(ch, "top level", user=alice)
    env.wait_items(name)
    other = env.mock.post(ch, "unrelated thread", user=env.mock.add_user("carol"))
    reply = env.mock.post(ch, "me in another thread", user=env.me, thread_ts=other)
    env.wait_stored(ch, reply)
    env.settle(ch)
    assert len(env.items(name)) == 1


# ---------- leases ----------
def test_lease_expiry_redelivers_with_warning_then_goes_stuck(env, alice):
    # the shared daemon runs with a short lease and max_attempts=2 (see FAST in conftest.py)
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    env.mock.post(dm, "never acked", user=alice)
    env.wait_items(name, claim=True)
    until(lambda: env.items(name)[0]["state"] == "pending", 10, "lease expiry -> pending")
    it = env.items(name, claim=True)[0]
    assert it["attempt"] == 2 and "re-delivered" in it["warning"]
    until(lambda: env.call("status")["stuck"], 10, "stuck after max_attempts")
    stuck = env.call("status")["stuck"][0]
    assert stuck["attempts"] == 2
    assert env.items(name) == []              # stuck items are out of the queue
    assert env.call("retry", seqs=[stuck["seq"]]) == {"requeued": 1}
    assert env.items(name)[0]["state"] == "pending"


def test_unresolved_op_lists_claimed_items(env, alice):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    env.mock.post(dm, "x", user=alice)
    it = env.wait_items(name, claim=True)[0]
    assert [i["seq"] for i in env.call("unresolved", name=name)["items"]] == [it["seq"]]
    env.call("ack", name=name, seqs=[it["seq"]])
    assert env.call("unresolved", name=name)["items"] == []


# ---------- deregister ----------
def test_deregister_releases_waiter_and_drops_queue(env, alice):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    env.mock.post(dm, "pending when deregistered", user=alice)
    env.wait_items(name)
    # a second agent has a waiter and gets released
    other = env.register({"dms": "none"})
    w = env.popen("wait", "--name", other)
    until(lambda: env.agent(other)["waiters"] == 1, what="waiter connected")
    assert json.loads(env.cli("deregister", "--name", other).stdout) == {"deregistered": other}
    out, _ = w.communicate(timeout=10)
    assert json.loads(out) == {"deregistered": True}
    env.call("deregister", name=name)
    assert env.agent(name) is None and env.agent(other) is None
    with pytest.raises(SystemExit, match="no agent named"):
        env.call("inbox", name=name)
    with pytest.raises(SystemExit, match="no agent named"):
        env.call("deregister", name=name)
    until(lambda: env.call("status")["state"] == "DORMANT", what="dormant")


def test_status_and_log_report_activity(env, alice):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    env.mock.post(dm, "x", user=alice)
    env.wait_items(name)
    st = json.loads(env.cli("status", "--verbose").stdout)
    assert st["state"] == "POLLING" and st["identities"][0]["status"] == "ok"
    assert st["agents"][0]["items"] == {"pending": 1}
    assert any(f"{env.me}:{dm}" == p for p in st["poll_set"])
    kinds = {r["kind"] for r in json.loads(env.cli("log").stdout)["log"]}
    assert {"register", "deliver"} <= kinds



def test_ack_by_name_cannot_resolve_another_agents_item(env, alice):
    dm = env.mock.add_dm(alice)
    a = env.register({"dms": "all"})
    b = env.register({"dms": "all"})
    env.mock.post(dm, "for both agents", user=alice)
    item_b = env.wait_items(b)[0]
    env.wait_items(a)
    assert env.call("ack", name=a, seqs=[item_b["seq"]])["acked"] == []
    assert [i["seq"] for i in env.items(b)] == [item_b["seq"]]
    assert env.call("ack", name=b, seqs=[item_b["seq"]])["acked"] == [item_b["seq"]]

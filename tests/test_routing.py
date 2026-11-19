"""Which Slack messages land in an agent's queue."""
import time

import pytest

from conftest import until


def texts(item):
    return [m["text"] for m in item["messages"]]


def all_texts(env, name):
    return sorted(t for it in env.items(name) for t in texts(it))


# ---------- DMs ----------
def test_dm_all_routes_any_dm(env, alice, bob):
    dm_a, dm_b = env.mock.add_dm(alice), env.mock.add_dm(bob)
    name = env.register({"dms": "all"})
    env.mock.post(dm_a, "from alice", user=alice)
    env.mock.post(dm_b, "from bob", user=bob)
    env.wait_items(name, 2)
    assert all_texts(env, name) == ["from alice", "from bob"]


def test_dm_followed_routes_only_followed_users(env, alice, bob):
    dm_a, dm_b = env.mock.add_dm(alice), env.mock.add_dm(bob)
    name = env.register({"dms": "followed", "users": [alice]})
    env.mock.post(dm_a, "from alice", user=alice)
    env.mock.post(dm_b, "from bob", user=bob)
    env.wait_items(name)
    assert all_texts(env, name) == ["from alice"]
    env.settle(dm_a)
    assert env.mock.count("conversations.history", dm_b) == 0   # a DM nobody follows is never polled
    assert all_texts(env, name) == ["from alice"]


def test_dm_followed_by_handle(env):
    zelda = env.mock.add_user("zelda")     # handles must be unique in the shared workspace
    dm = env.mock.add_dm(zelda)
    name = env.register({"dms": "followed", "users": ["@zelda"]})
    env.mock.post(dm, "hi", user=zelda)
    assert texts(env.wait_items(name)[0]) == ["hi"]


def test_dm_none_routes_nothing_and_never_polls_the_dm(env, alice):
    dm = env.mock.add_dm(alice)
    general = env.mock.add_channel("dmnone")
    name = env.register({"dms": "none", "channels": [general], "users": [alice]})
    env.mock.post(dm, "ignored", user=alice)
    env.mock.post(general, "sentinel", user=alice)
    env.wait_items(name)
    assert all_texts(env, name) == ["sentinel"]
    assert env.mock.count("conversations.history", dm) == 0


def test_group_dm_only_for_agents_that_ask(env, alice):
    mpim = env.mock.add_group_dm()
    yes = env.register({"dms": "none", "group_dms": True})
    no = env.register({"dms": "none", "group_dms": False})
    env.mock.post(mpim, "group hello", user=alice)
    env.wait_items(yes)
    env.settle(mpim)
    assert env.items(no) == []


# ---------- channels ----------
def test_mention_in_channel(env, alice):
    ch = env.mock.add_channel("m1")
    name = env.register({"dms": "none", "channels": [ch]})
    env.mock.post(ch, "unrelated chatter", user=alice)
    env.mock.post(ch, f"<@{env.me}> can you look?", user=alice)
    its = env.wait_items(name)
    assert len(its) == 1 and texts(its[0]) == [f"<@{env.me}> can you look?"]
    assert its[0]["conversation"]["kind"] == "channel"


def test_mentions_can_be_switched_off(env, alice):
    ch = env.mock.add_channel("m2")
    name = env.register({"dms": "none", "channels": [ch], "mentions_of_me": False, "everyone_in": [],
                         "users": [alice]})
    env.mock.post(ch, f"<@{env.me}> ping", user=env.mock.add_user("carol"))
    env.mock.post(ch, "followed voice", user=alice)
    env.wait_items(name)
    assert all_texts(env, name) == ["followed voice"]


def test_followed_user_in_channel(env, alice, bob):
    ch = env.mock.add_channel("f1")
    name = env.register({"dms": "none", "channels": [ch], "users": [alice]})
    env.mock.post(ch, "bob is not followed", user=bob)
    env.mock.post(ch, "alice is followed", user=alice)
    env.wait_items(name)
    assert all_texts(env, name) == ["alice is followed"]


def test_channel_by_name_and_usergroup(env, alice, bob):
    # "#general" exists since daemon start; usergroup membership comes from usergroups.list
    env.mock.add_usergroup("oncall", [alice])
    name = env.register({"dms": "none", "channels": ["#general"], "usergroups": ["@oncall"]})
    env.mock.post(env.general, "bob, not in group", user=bob)
    env.mock.post(env.general, "alice, in group", user=alice)
    env.wait_items(name)
    assert all_texts(env, name) == ["alice, in group"]


def test_everyone_in_channel(env, alice, bob):
    ch = env.mock.add_channel("e1")
    name = env.register({"dms": "none", "everyone_in": [ch]})
    env.mock.post(ch, "anyone one", user=alice)
    env.mock.post(ch, "anyone two", user=bob)
    its = env.wait_items(name)
    until(lambda: len(env.items(name)[0]["messages"]) == 2, what="both merged")
    assert all_texts(env, name) == ["anyone one", "anyone two"]


def test_bot_messages_need_allow_bots(env):
    ch = env.mock.add_channel("b1")
    deaf = env.register({"dms": "none", "everyone_in": [ch]})
    ears = env.register({"dms": "none", "everyone_in": [ch], "allow_bots": ["B0BOT0001"]})
    env.mock.post(ch, "deploy finished", bot_id="B0BOT0001")
    env.wait_items(ears)
    env.settle(ch)
    assert env.items(deaf) == []


def test_ignored_subtypes_are_dropped(env, alice):
    ch = env.mock.add_channel("s1")
    name = env.register({"dms": "none", "everyone_in": [ch]})
    env.mock.post(ch, "alice joined", user=alice, subtype="channel_join")
    env.mock.post(ch, "real message", user=alice)
    env.wait_items(name)
    assert all_texts(env, name) == ["real message"]


def test_my_own_messages_are_not_queued(env, alice):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    env.mock.post(dm, "my own words", user=env.me)
    env.mock.post(dm, "their words", user=alice)
    env.wait_items(name)
    assert all_texts(env, name) == ["their words"]


def test_messages_before_registration_are_not_delivered(env, alice):
    ch = env.mock.add_channel("p1")
    env.mock.post(ch, "old news", user=alice)
    time.sleep(0.05)
    name = env.register({"dms": "none", "everyone_in": [ch]})
    env.mock.post(ch, "fresh news", user=alice)
    env.wait_items(name)
    assert all_texts(env, name) == ["fresh news"]


def test_burst_larger_than_a_page_is_paginated(env, alice):
    ch = env.mock.add_channel("pg")
    name = env.register({"dms": "none", "everyone_in": [ch]})
    env.mock.max_page = 3
    try:
        for i in range(8):
            env.mock.post(ch, f"burst {i}", user=alice)
        until(lambda: env.items(name) and len(env.items(name)[0]["messages"]) == 8, what="8 messages")
    finally:
        env.mock.max_page = None
    assert texts(env.items(name)[0]) == [f"burst {i}" for i in range(8)]


# ---------- threads ----------
def test_thread_replies_in_a_thread_i_posted_in(env, alice):
    ch = env.mock.add_channel("t1")
    name = env.register({"dms": "none", "channels": [ch]})
    parent = env.mock.post(ch, "my question", user=env.me)
    env.wait_stored(ch, parent)
    env.mock.post(ch, "an answer nobody follows", user=alice, thread_ts=parent)
    it = env.wait_items(name)[0]
    assert it["thread_key"] == parent
    assert texts(it) == ["an answer nobody follows"]
    assert it["reply_to"] == {"channel_id": ch, "thread_ts": parent}
    assert [c["text"] for c in it["context"]] == ["my question"]
    assert it["context"][0]["is_me"]


def test_threads_im_in_can_be_switched_off(env, alice):
    ch = env.mock.add_channel("t2")
    name = env.register({"dms": "none", "channels": [ch], "threads_im_in": False})
    parent = env.mock.post(ch, "my question", user=env.me)
    env.wait_stored(ch, parent)
    env.mock.post(ch, "reply", user=alice, thread_ts=parent)
    env.settle(ch)
    assert env.items(name) == []


def test_replies_in_a_thread_an_agent_was_woken_for_follow_on(env, alice, bob):
    ch = env.mock.add_channel("t3")
    name = env.register({"dms": "none", "channels": [ch], "users": [alice]})
    parent = env.mock.post(ch, "alice starts a thread", user=alice)
    it = env.wait_items(name, claim=True)[0]
    env.call("ack", name=name, seqs=[it["seq"]], outcome="handled")
    env.mock.post(ch, "bob chimes in", user=bob, thread_ts=parent)
    it = env.wait_items(name)[0]
    assert it["thread_key"] == parent and texts(it) == ["bob chimes in"]


def test_fanout_to_two_agents_from_one_stored_fetch(env, alice):
    dm = env.mock.add_dm(alice)
    a, b = env.register({"dms": "all"}), env.register({"dms": "all"})
    env.mock.post(dm, "for both", user=alice)
    env.wait_items(a)
    env.wait_items(b)
    n = env.db().execute("SELECT count(*) FROM messages WHERE text='for both'").fetchone()[0]
    assert n == 1


def test_subscription_validation(env):
    with pytest.raises(SystemExit, match="unknown subscription keys"):
        env.call("register", name="bad1", sub={"nonsense": 1})
    with pytest.raises(SystemExit, match="dms must be"):
        env.call("register", name="bad2", sub={"dms": "sometimes"})
    with pytest.raises(SystemExit, match="unknown users"):
        env.call("register", name="bad3", sub={"users": ["@nobody-by-that-name"]})
    with pytest.raises(SystemExit, match="unknown channel"):
        env.call("register", name="bad4", sub={"channels": ["#nope"]})
    assert env.call("status")["agents"] == []


def test_sub_add_remove_changes_routing(env, alice):
    ch = env.mock.add_channel("sc")
    name = env.register({"dms": "none", "channels": [ch]})
    env.call("sub", name=name, action="add", key="users", values=[alice], why="test follow")
    assert env.agent(name)["sub"]["users"] == [alice]
    env.mock.post(ch, "now followed", user=alice)
    env.wait_items(name)
    env.call("sub", name=name, action="remove", key="users", values=[alice], why="test unfollow")
    assert env.agent(name)["sub"]["users"] == []

"""How new messages are found: history polling, search sweeps, Socket Mode, and choosing between
them (docs/design/detection.md)."""
import time

from conftest import until
from mock_slack import REQUIRED_SCOPES

SEARCH = REQUIRED_SCOPES + ["search:read"]
# Fast sweeps; a conversation stays hot for a short while, then is left to the sweep.
SWEEPY = {"sweep_every": 0.3, "socket_sweep_every": 0.3, "hot_for": 1.0, "cadence_hot": 0.2,
          "sweep_overlap": 300,
          "pool_history": 50, "pool_search": 20}   # Slack's real per-app pools (conftest lifts them)


def detection(env):
    return env.call("status")["detection"][env.me]


def primed(env, ch):
    """The conversation has been read once (its first read starts from "now")."""
    until(lambda: env.db().execute("SELECT 1 FROM conv WHERE channel=? AND cursor IS NOT NULL", (ch,)).fetchone(),
          15, "first read")


def texts(env, name):
    return sorted(m["text"] for it in env.items(name) for m in it["messages"])


def test_auto_reads_each_conversation_without_the_search_scope(make_env):
    env = make_env(config=SWEEPY)
    env.add_identity()
    alice = env.mock.add_user("alice")
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    primed(env, dm)
    env.mock.post(dm, "hi", user=alice)
    env.wait_items(name)
    d = detection(env)
    assert d["mode"] == "history" and "search:read" in d["why"]
    assert env.mock.count("search.messages") == 0


def test_auto_stays_on_history_while_the_watch_list_fits(make_env):
    env = make_env(scopes=SEARCH, config={**SWEEPY, "cadence_warm": 120})
    env.add_identity()
    dm = env.mock.add_dm(env.mock.add_user("alice"))
    env.register({"dms": "all"})
    until(lambda: detection(env)["mode"] == "history", 10, "history mode")
    assert "within" in detection(env)["why"]
    assert env.mock.count("search.messages") == 0
    assert dm


def test_auto_switches_to_search_when_the_team_shares_the_app(make_env):
    # 50 calls/min shared by 200 people: one idle conversation every 2 minutes is already too much.
    env = make_env(scopes=SEARCH, config={**SWEEPY, "cadence_warm": 120, "team_size": 200, "pool_search": 1e6})
    env.add_identity()
    alice, bob = env.mock.add_user("alice"), env.mock.add_user("bob")
    dm_a, dm_b = env.mock.add_dm(alice), env.mock.add_dm(bob)
    name = env.register({"dms": "all"})
    until(lambda: detection(env)["mode"] == "search", 10, "search mode")
    assert "over this person's share" in detection(env)["why"]
    t = time.time()
    env.mock.post(dm_a, "from alice", user=alice)
    env.wait_items(name)
    assert texts(env, name) == ["from alice"]
    time.sleep(1.5)
    # Only the conversation the sweep found was read; the quiet one never was.
    assert env.mock.count("conversations.history", dm_a, since=t) >= 1
    assert env.mock.count("conversations.history", dm_b, since=t) == 0
    assert env.mock.count("search.messages", since=t) >= 1


def test_hot_conversation_cools_down_and_is_left_to_the_sweep(make_env):
    env = make_env(scopes=SEARCH, config={**SWEEPY, "detect": "search"})
    env.add_identity()
    alice = env.mock.add_user("alice")
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    env.mock.post(dm, "one", user=alice)
    env.wait_items(name)
    time.sleep(2.0)   # hot_for (1 s) has passed with nothing new
    t = time.time()
    time.sleep(1.5)
    assert env.mock.count("conversations.history", dm, since=t) == 0
    env.mock.post(dm, "two", user=alice)
    env.wait_items(name, 1)
    until(lambda: texts(env, name) == ["one", "two"], 10, "second message")


def test_search_sees_messages_indexed_late(make_env):
    env = make_env(scopes=SEARCH, config={**SWEEPY, "detect": "search"})
    env.add_identity()
    alice = env.mock.add_user("alice")
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    env.mock.search_lag = 2.0     # the sweep right after the post can't see it yet
    env.mock.post(dm, "late", user=alice)
    env.wait_items(name, timeout=15)
    assert texts(env, name) == ["late"]


def test_thread_reply_found_by_search_without_a_thread_link(make_env):
    env = make_env(scopes=SEARCH, config={**SWEEPY, "detect": "search"})
    env.add_identity()
    alice = env.mock.add_user("alice")
    ch = env.mock.add_channel("eng")
    parent = env.mock.post(ch, "old parent", user=alice)
    for i in range(60):   # push the parent out of the conversation's latest page
        env.mock.post(ch, f"filler {i}", user=alice)
    env.mock.search_thread_links = False
    name = env.register({"dms": "none", "everyone_in": ["#eng"]})
    time.sleep(1.5)
    env.mock.post(ch, "reply in an old thread", user=alice, thread_ts=parent)
    until(lambda: "reply in an old thread" in texts(env, name), 15, "thread reply")


def test_mention_in_a_conversation_nobody_watches(make_env):
    env = make_env(scopes=SEARCH, config={**SWEEPY, "detect": "search"})
    env.add_identity()
    alice = env.mock.add_user("alice")
    ch = env.mock.add_channel("random")
    name = env.register({"dms": "none"})       # watches no conversation, but mentions are on
    time.sleep(1.0)
    env.mock.post(ch, f"hey <@{env.me}> look", user=alice)
    env.mock.post(ch, "not for you", user=alice)
    until(lambda: texts(env, name) == [f"hey <@{env.me}> look"], 15, "mention")
    time.sleep(1.0)
    assert texts(env, name) == [f"hey <@{env.me}> look"]


def test_rate_limited_search_slows_the_sweep(make_env):
    env = make_env(scopes=SEARCH, config={**SWEEPY, "detect": "search"})
    env.add_identity()
    env.mock.add_dm(env.mock.add_user("alice"))
    env.register({"dms": "all"})
    until(lambda: env.mock.count("search.messages") >= 1, 10, "a sweep")
    env.mock.errors["search.messages"] = "ratelimited"
    until(lambda: detection(env).get("sweep_slowdown", 1) >= 2, 15, "slower sweeps")
    assert env.call("status")["rate_paused_for_s"] > 0


def test_detect_search_without_scope_falls_back_to_history(make_env):
    env = make_env(config={**SWEEPY, "detect": "search"})
    env.add_identity()
    env.register({"dms": "all"})
    until(lambda: detection(env)["mode"] == "history", 10, "history")
    assert "login --search" in detection(env)["why"]


def test_dormant_makes_no_search_or_socket_calls(make_env, tmp_path):
    env = make_env(scopes=SEARCH, config={**SWEEPY, "detect": "search"})
    env.add_identity()
    app = tmp_path / "app"
    app.write_text("xapp-1-A0-0-mock\n")
    app.chmod(0o600)
    env.call("socket", action="enable", app_token_file=str(app), only_me=True)
    t = time.time()
    time.sleep(1.5)
    assert env.mock.count(since=t) == 0
    assert env.mock.ws_connects == 0


# ---------- Socket Mode ----------
def socket_env(make_env, tmp_path, **cfg):
    env = make_env(scopes=SEARCH, config={**SWEEPY, "socket_sweep_every": 60, **cfg})
    env.add_identity()
    app = tmp_path / "app-token"
    app.write_text("xapp-1-A0-0-mock\n")
    app.chmod(0o600)
    env.app_token = app
    return env


def test_socket_enable_needs_only_me(make_env, tmp_path):
    env = socket_env(make_env, tmp_path)
    r = env.cli("socket", "enable", "--app-token-file", env.app_token, check=False)
    assert r.returncode != 0 and "--only-me" in r.stderr
    env.cli("socket", "enable", "--app-token-file", env.app_token, "--only-me")
    assert detection(env)["socket"]["setting"] == "on"


def test_socket_delivers_without_polling(make_env, tmp_path):
    env = socket_env(make_env, tmp_path)
    alice = env.mock.add_user("alice")
    dm = env.mock.add_dm(alice)
    env.cli("socket", "enable", "--app-token-file", env.app_token, "--only-me")
    name = env.register({"dms": "all"})
    until(lambda: detection(env)["mode"] == "socket", 10, "socket mode")
    env.mock.search_lag = 30   # so only the event can find it
    t = time.time()
    ts = env.mock.post(dm, "pushed", user=alice)
    env_id = env.mock.emit(dm, ts)
    env.wait_items(name)
    assert texts(env, name) == ["pushed"]
    assert env_id in env.mock.ws_acks
    # No polling: the only history read is the inbox fetching context before the message.
    polls = [c for c in env.mock.calls if c["method"] == "conversations.history" and c["t"] >= t
             and "latest" not in c["params"]]
    assert not polls
    # Slack sends the same event again after a reconnect: delivered once.
    env.mock.emit(dm, ts)
    time.sleep(0.5)
    assert texts(env, name) == ["pushed"]


def test_socket_catches_up_on_connect(make_env, tmp_path):
    env = socket_env(make_env, tmp_path)
    alice = env.mock.add_user("alice")
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    time.sleep(0.5)
    env.mock.post(dm, "while the socket was off", user=alice)   # no event: Doorbell wasn't connected
    env.cli("socket", "enable", "--app-token-file", env.app_token, "--only-me")
    until(lambda: texts(env, name) == ["while the socket was off"], 15, "catch-up")


def test_socket_reconnects_after_refresh(make_env, tmp_path):
    env = socket_env(make_env, tmp_path)
    alice = env.mock.add_user("alice")
    dm = env.mock.add_dm(alice)
    env.cli("socket", "enable", "--app-token-file", env.app_token, "--only-me")
    name = env.register({"dms": "all"})
    until(lambda: detection(env)["mode"] == "socket", 10, "socket mode")
    env.mock.ws_disconnect_all()
    until(lambda: env.mock.ws_connects >= 2 and env.mock.ws_clients, 15, "reconnect")
    until(lambda: detection(env)["socket"]["connected"], 10, "connected again")
    ts = env.mock.post(dm, "after refresh", user=alice)
    env.mock.emit(dm, ts)
    env.wait_items(name)


def test_socket_turns_off_on_an_event_for_someone_else(make_env, tmp_path):
    env = socket_env(make_env, tmp_path)
    alice = env.mock.add_user("alice")
    dm = env.mock.add_dm(alice)
    env.cli("socket", "enable", "--app-token-file", env.app_token, "--only-me")
    name = env.register({"dms": "all"})
    until(lambda: detection(env)["mode"] == "socket", 10, "socket mode")
    ts = env.mock.post(dm, "someone else's", user=alice)
    env_id = env.mock.emit(dm, ts, for_user="UOTHER0001")
    until(lambda: detection(env)["socket"]["setting"] == "tripped", 10, "tripped")
    assert "another person" in detection(env)["socket"]["note"]
    assert env_id not in env.mock.ws_acks   # not acked: Slack retries it on the owner's connection
    assert not env.db().execute("SELECT 1 FROM messages WHERE ts=? AND raw LIKE '%socket%'", (ts,)).fetchone()
    until(lambda: detection(env)["mode"] == "search", 10, "back to polling")
    assert any(r["kind"] == "socket_tripped" for r in env.db().execute("SELECT kind FROM audit"))
    assert name


def test_socket_turns_off_on_an_event_that_names_nobody(make_env, tmp_path):
    env = socket_env(make_env, tmp_path)
    dm = env.mock.add_dm(env.mock.add_user("alice"))
    env.cli("socket", "enable", "--app-token-file", env.app_token, "--only-me")
    env.register({"dms": "all"})
    until(lambda: detection(env)["mode"] == "socket", 10, "socket mode")
    ts = env.mock.post(dm, "whose?", user=env.mock.me)
    env.mock.emit(dm, ts, authorizations=[])
    until(lambda: detection(env)["socket"]["setting"] == "tripped", 10, "tripped")
    assert "without saying whose" in detection(env)["socket"]["note"]


def test_socket_without_search_scope_catches_up_by_history(make_env, tmp_path):
    env = make_env(config={**SWEEPY, "socket_sweep_every": 60})   # no search:read
    env.add_identity()
    app = tmp_path / "app-token"
    app.write_text("xapp-1-A0-0-mock\n")
    app.chmod(0o600)
    alice = env.mock.add_user("alice")
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    primed(env, dm)
    env.mock.post(dm, "before the socket", user=alice)
    env.cli("socket", "enable", "--app-token-file", app, "--only-me")
    until(lambda: detection(env)["mode"] == "socket", 10, "socket mode")
    until(lambda: "before the socket" in texts(env, name), 15, "history catch-up")
    assert env.mock.count("search.messages") == 0
    assert env.db().execute("SELECT status FROM identities").fetchone()[0] == "ok"


def test_socket_turns_off_when_the_app_has_other_connections(make_env, tmp_path):
    env = socket_env(make_env, tmp_path, socket_max_strikes=1)
    env.mock.ws_num_connections = 2
    env.cli("socket", "enable", "--app-token-file", env.app_token, "--only-me")
    env.register({"dms": "all"})
    until(lambda: detection(env).get("socket", {}).get("setting") == "tripped", 15, "tripped")
    assert "2 open Socket Mode connections" in detection(env)["socket"]["note"]


def test_socket_disable(make_env, tmp_path):
    env = socket_env(make_env, tmp_path)
    env.cli("socket", "enable", "--app-token-file", env.app_token, "--only-me")
    env.register({"dms": "all"})
    until(lambda: detection(env)["mode"] == "socket", 10, "socket mode")
    env.cli("socket", "disable")
    until(lambda: detection(env)["mode"] != "socket", 15, "socket off")


# ---------- doorbell config ----------
def test_config_set_validates_and_daemon_reloads(make_env):
    env = make_env(config=SWEEPY)
    env.add_identity()
    assert env.cli("config", "set", "team_size", "nope", check=False).returncode != 0
    assert env.cli("config", "set", "no_such_key", "1", check=False).returncode != 0
    assert env.cli("config", "set", "detect", "sometimes", check=False).returncode != 0
    env.cli("config", "set", "team_size", "30")
    until(lambda: detection(env)["team_size"] == 30, 10, "reloaded")
    env.cli("config", "unset", "team_size")
    until(lambda: detection(env)["team_size"] == 1, 10, "default again")


def test_config_rejects_out_of_range_values(make_env):
    env = make_env(config=SWEEPY)
    for key, value in [("team_size", "0"), ("team_size", "2.5"), ("budget_share", "1.5"), ("notify", "maybe"),
                       ("agent_app_ids", "[1, 2]"), ("port", "-1"), ("hot_for", "'90'")]:
        r = env.cli("config", "set", key, value, check=False)
        assert r.returncode != 0 and key in r.stderr, (key, value, r.stderr)
    env.cli("config", "set", "budget_share", "0.5")
    assert env.cli("config", "get", "budget_share").stdout.strip().endswith("0.5}") or \
        "0.5" in env.cli("config", "get", "budget_share").stdout


def test_truncated_sweep_reads_watched_conversations_from_their_cursors(make_env):
    env = make_env(scopes=SEARCH, config={**SWEEPY, "detect": "search", "sweep_max_pages": 1,
                                         "sweep_every": 1.0})
    env.add_identity()
    alice = env.mock.add_user("alice")
    dm = env.mock.add_dm(alice)
    noisy = env.mock.add_channel("noisy")   # public, not watched: search still returns it
    name = env.register({"dms": "all"})
    primed(env, dm)
    time.sleep(1.5)                          # let a sweep run, so the next one starts after this point
    env.mock.post(dm, "buried", user=alice)  # then 150 newer matches push it off the one page
    for i in range(150):
        env.mock.post(noisy, f"noise {i}", user=alice)
    until(lambda: "buried" in texts(env, name), 15, "found through the cursor read")
    assert any(r["kind"] == "catch_up_by_history" for r in env.db().execute("SELECT kind FROM audit"))


def test_restart_into_search_reads_from_cursors_first(make_env):
    env = make_env(scopes=SEARCH, config={**SWEEPY, "detect": "history", "sweep_overlap": 0})
    env.add_identity()
    alice = env.mock.add_user("alice")
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"}, max_catchup=3600)
    primed(env, dm)
    env.proc.terminate()
    env.proc.wait(10)
    env.mock.post(dm, "while stopped", user=alice)
    time.sleep(1.1)
    env.config = {**env.config, "detect": "search"}
    env.start()
    until(lambda: "while stopped" in texts(env, name), 20, "read from the cursor after restart")


def test_sweep_interval_stretches_with_team_size(make_env):
    env = make_env(scopes=SEARCH, config={**SWEEPY, "detect": "search", "sweep_every": 120, "team_size": 60})
    env.add_identity()
    env.register({"dms": "all"})
    until(lambda: detection(env)["mode"] == "search", 10, "search mode")
    # 20 calls/min x 0.75 shared by 60 people: one sweep every 4 minutes each.
    assert detection(env)["sweep_every_s"] == 240


# ---------- threads that scrolled out of the history window take turns ----------
ROTATE = {**SWEEPY, "detect": "history", "history_page": 2, "hot_for": 0.5,
          "thread_watch_every": 1.0, "thread_recent": 1, "thread_batch": 2}


def test_older_threads_take_turns_at_a_fixed_cost(make_env):
    env = make_env(config=ROTATE)
    env.add_identity()
    alice = env.mock.add_user("alice")
    ch = env.mock.add_channel("busy")
    name = env.register({"dms": "none", "channels": [ch]})
    primed(env, ch)
    parents = [env.mock.post(ch, f"my question {i}", user=env.me) for i in range(8)]
    for i in range(3):                                   # scroll them all out of the 2-message window
        env.mock.post(ch, f"chatter {i}", user=alice)
    until(lambda: env.db().execute("SELECT count(*) FROM thread_watch WHERE channel=? AND mine>0",
                                   (ch,)).fetchone()[0] == 8, 15, "8 watched threads")
    time.sleep(1.5)
    t0 = time.time()
    time.sleep(4)
    reads = env.mock.count("conversations.replies", ch, since=t0)
    # 1 recent thread a second plus 2 older ones a second: about 12, not 8 threads x 4 s = 32.
    assert reads <= 15, reads
    # Every older thread still gets its turn, so a reply in the oldest one is found.
    env.mock.post(ch, "late answer", user=alice, thread_ts=parents[0])
    its = env.wait_items(name, timeout=20)
    assert [m["text"] for m in its[0]["messages"]] == ["late answer"]

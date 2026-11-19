"""`doorbell dispatch`: the headless runner."""
import json
import sys
import textwrap

import pytest

from doorbell import cli, runner

FAKE_AGENT = textwrap.dedent("""
    import json, os, subprocess, sys
    run = lambda *a: subprocess.run([sys.executable, "-m", "doorbell", *a], capture_output=True, text=True,
                                    check=True).stdout
    name = os.environ["DOORBELL_AGENT"]
    items = json.loads(run("inbox", "--claim", "--name", name))["items"]
    print("fake agent saw", [m["text"] for it in items for m in it["messages"]], flush=True)
    if items:
        run("ack", *[str(it["seq"]) for it in items], "--name", name, "--outcome", "handled")
""")


def test_dispatch_once_runs_command_and_resolves_items(env, alice, tmp_path):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    (tmp_path / "agent.py").write_text(FAKE_AGENT)
    env.mock.post(dm, "please handle", user=alice)
    env.wait_items(name)
    r = env.cli("dispatch", "--name", name, "--dir", tmp_path, "--once", "--timeout", "60",
                "--", sys.executable, "agent.py", timeout=60)
    assert r.returncode == 0, r.stderr
    assert "fake agent saw ['please handle']" in r.stdout
    assert "resolved [" in r.stdout and "resolved nothing" not in r.stdout
    assert env.items(name) == []


def test_dispatch_once_exit_status_follows_the_command(env, alice, tmp_path):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    env.mock.post(dm, "x", user=alice)
    env.wait_items(name)
    r = env.cli("dispatch", "--name", name, "--dir", tmp_path, "--once", "--",
                sys.executable, "-c", "raise SystemExit(3)", check=False)
    assert r.returncode == 1 and "resolved nothing" in r.stdout
    assert len(env.items(name)) == 1


def test_dispatch_takes_name_from_agent_file_and_needs_a_command(env, tmp_path):
    r = env.cli("dispatch", "--dir", tmp_path, "--", "true", check=False)
    assert r.returncode != 0 and "--name" in r.stderr
    (tmp_path / ".doorbell-agent").write_text("someone")
    r = env.cli("dispatch", "--dir", tmp_path, check=False)
    assert r.returncode != 0 and "give the command" in r.stderr


def test_dispatch_exits_when_agent_is_deregistered(env, tmp_path):
    name = env.register({"dms": "none"})
    p = env.popen("dispatch", "--name", name, "--dir", tmp_path, "--", sys.executable, "-c", "pass")
    from conftest import until
    until(lambda: env.agent(name)["waiters"] == 1, what="dispatcher waiting")
    env.call("deregister", name=name)
    out, _ = p.communicate(timeout=15)
    assert p.returncode == 0 and "deregistered; exiting" in out


# ---------- backoff (no daemon, no real sleeping) ----------
class Stop(Exception):
    pass


@pytest.fixture
def fake_runner(monkeypatch, tmp_path):
    """Make runner.dispatch loop against a scripted queue, recording sleeps instead of sleeping."""
    state = {"sleeps": [], "runs": 0, "open_after": [{1}] * 100}

    monkeypatch.setattr(cli, "wait", lambda name: {"pending": 1, "unresolved": 0})
    monkeypatch.setattr(runner, "ensure_registered", lambda name, folder: None)

    class Done:
        returncode = 0
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: state.__setitem__("runs", state["runs"] + 1) or Done())

    calls = iter([])

    def open_seqs(name):
        # alternates: "before" snapshot, then "after" snapshot taken from the script
        state["n"] = state.get("n", 0) + 1
        if state["n"] % 2 == 1:
            return {1}
        return state["open_after"][state["n"] // 2 - 1]
    monkeypatch.setattr(runner, "open_seqs", open_seqs)

    def sleep(s):
        state["sleeps"].append(s)
        if len(state["sleeps"]) >= state.get("stop_after", 4):
            raise Stop
    monkeypatch.setattr(runner.time, "sleep", sleep)
    state["dir"] = str(tmp_path)
    return state


def test_no_progress_backs_off_exponentially(fake_runner):
    with pytest.raises(Stop):
        runner.dispatch("a", ["cmd"], fake_runner["dir"], 5)
    assert fake_runner["sleeps"] == [60, 120, 240, 480]
    assert fake_runner["runs"] == 4


def test_backoff_is_capped(fake_runner):
    fake_runner["stop_after"] = 8
    with pytest.raises(Stop):
        runner.dispatch("a", ["cmd"], fake_runner["dir"], 5)
    assert max(fake_runner["sleeps"]) == runner.MAX_BACKOFF
    assert fake_runner["sleeps"][:6] == [60, 120, 240, 480, 960, 1800]


def test_progress_resets_backoff(fake_runner):
    # run 1: stuck; run 2: stuck; run 3: item resolved (after is empty); then stuck again
    fake_runner["open_after"] = [{1}, {1}, set(), {1}, {1}] + [{1}] * 50
    fake_runner["stop_after"] = 4
    with pytest.raises(Stop):
        runner.dispatch("a", ["cmd"], fake_runner["dir"], 5)
    assert fake_runner["sleeps"] == [60, 120, 60, 120]


def test_dispatch_registers_an_unregistered_agent_from_sub_yaml(env, alice, tmp_path):
    name = "auto-reg-agent"
    (tmp_path / ".doorbell-agent").write_text(name)
    (tmp_path / "sub.yaml").write_text("dms: all\n")
    p = env.popen("dispatch", "--dir", tmp_path, "--", sys.executable, "-c", "pass")
    from conftest import until
    try:
        until(lambda: (env.agent(name) or {}).get("waiters") == 1, what="dispatcher registered and waiting")
    finally:
        p.terminate()
        p.wait(10)

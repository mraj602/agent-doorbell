"""Claude Code hooks: `doorbell hook post-tool-use|stop` fed JSON on stdin like Claude Code does."""
import json
import subprocess
import sys

import pytest

SEND = "mcp__plugin_slack_slack__slack_send_message"
DRAFT = "mcp__plugin_slack_slack__slack_send_message_draft"
CONNECTOR_SEND = "mcp__claude_ai_Slack__slack_send_message"
REACT = "mcp__plugin_slack_slack__slack_add_reaction"


def agent_folder(env, name):
    folder = env.home / "agents" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".doorbell-agent").write_text(name + "\n")
    return folder


def post_tool_use(env, tool, name, **tool_input):
    """Run the hook as Claude Code does, from the agent's folder (or any folder, with name=None)."""
    cwd = agent_folder(env, name) if name else env.home
    r = env.cli("hook", "post-tool-use", stdin=json.dumps({"tool_name": tool, "tool_input": tool_input,
                                                          "cwd": str(cwd)}))
    assert r.stdout == ""      # a PostToolUse hook that stays silent never disturbs the agent
    return r


def outcome(env, seq):
    return [x["outcome"] for x in env.resolved() if x["seq"] == seq]


@pytest.mark.parametrize("tool,expected", [(SEND, "replied"), (DRAFT, "drafted"), (CONNECTOR_SEND, "replied")])
def test_post_tool_use_acks_channel_thread_item(env, alice, tool, expected):
    ch = env.mock.add_channel("hk")
    name = env.register({"dms": "none", "channels": [ch], "users": [alice]})
    parent = env.mock.post(ch, "alice starts", user=alice)
    it = env.wait_items(name, claim=True)[0]
    env.call("ack", name=name, seqs=[it["seq"]])
    env.mock.post(ch, "alice in thread", user=alice, thread_ts=parent)
    it = env.wait_items(name, claim=True)[0]
    assert it["thread_key"] == parent
    post_tool_use(env, tool, name, channel_id=ch, thread_ts=parent, message="on it")
    assert env.items(name) == []
    assert outcome(env, it["seq"]) == [expected]


def test_post_tool_use_for_unrelated_thread_leaves_item_open(env, alice):
    ch = env.mock.add_channel("hk2")
    name = env.register({"dms": "none", "channels": [ch], "users": [alice]})
    env.mock.post(ch, "top level", user=alice)
    it = env.wait_items(name, claim=True)[0]
    post_tool_use(env, SEND, name, channel_id=ch, thread_ts="1234.5678", message="elsewhere")
    assert [i["seq"] for i in env.items(name)] == [it["seq"]]


def test_post_tool_use_acks_dm_addressed_by_user_id(env, alice):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    env.mock.post(dm, "hi", user=alice)
    it = env.wait_items(name, claim=True)[0]
    post_tool_use(env, SEND, name, channel_id=alice, message="hello back")     # a user ID instead of D...
    assert env.items(name) == [] and outcome(env, it["seq"]) == ["replied"]


def test_post_tool_use_reaction_acks_by_message_ts(env, alice):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    ts = env.mock.post(dm, "ok?", user=alice)
    it = env.wait_items(name, claim=True)[0]
    post_tool_use(env, REACT, name, channel_id=dm, message_ts=ts, emoji="eyes")
    assert env.items(name) == [] and outcome(env, it["seq"]) == ["reacted"]


def test_post_tool_use_ignores_other_tools_and_survives_a_dead_daemon(env, alice):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    env.mock.post(dm, "hi", user=alice)
    it = env.wait_items(name, claim=True)[0]
    post_tool_use(env, "Bash", name, command="ls")
    post_tool_use(env, "mcp__plugin_slack_slack__slack_read_thread", name, channel_id=dm)
    assert len(env.items(name)) == 1
    # no daemon at this endpoint: still exit 0 and print nothing
    dead = env.home / "nosuch.json"
    p = subprocess.run([sys.executable, "-m", "doorbell", "hook", "post-tool-use"], capture_output=True, text=True,
                       input=json.dumps({"tool_name": SEND, "tool_input": {"channel_id": dm},
                                         "cwd": str(agent_folder(env, name))}),
                       env={**env.env, "DOORBELL_ENDPOINT": str(dead)})
    assert p.returncode == 0 and p.stdout == ""


def test_post_tool_use_outside_an_agent_folder_does_nothing(env, alice):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    env.mock.post(dm, "hi", user=alice)
    env.wait_items(name, claim=True)
    post_tool_use(env, SEND, None, channel_id=dm, message="a reply from some other session")
    assert len(env.items(name)) == 1


def test_post_tool_use_acks_only_the_folder_agents_items(env, alice):
    dm = env.mock.add_dm(alice)
    mine = env.register({"dms": "all"})
    other = env.register({"dms": "all"})
    env.mock.post(dm, "hi both", user=alice)
    env.wait_items(mine, claim=True)
    env.wait_items(other, claim=True)
    post_tool_use(env, SEND, mine, channel_id=dm, message="hello")
    assert env.items(mine) == [] and len(env.items(other)) == 1


def test_stop_hook_blocks_while_claimed_items_are_open(env, alice, tmp_path):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    (tmp_path / ".doorbell-agent").write_text(name)
    env.mock.post(dm, "hi", user=alice)
    it = env.wait_items(name, claim=True)[0]
    stop = json.loads(env.cli("hook", "stop", stdin=json.dumps({"cwd": str(tmp_path)})).stdout)
    assert stop["decision"] == "block" and str(it["seq"]) in stop["reason"]
    # Claude sets stop_hook_active on the retry: never loop forever
    assert env.cli("hook", "stop", stdin=json.dumps({"cwd": str(tmp_path), "stop_hook_active": True})).stdout == ""
    env.call("ack", name=name, seqs=[it["seq"]])
    assert env.cli("hook", "stop", stdin=json.dumps({"cwd": str(tmp_path)})).stdout == ""


def test_stop_hook_ignores_folders_that_are_not_agents(env, tmp_path):
    assert env.cli("hook", "stop", stdin=json.dumps({"cwd": str(tmp_path)})).stdout == ""


def test_stop_hook_does_not_block_on_pending_unclaimed_items(env, alice, tmp_path):
    dm = env.mock.add_dm(alice)
    name = env.register({"dms": "all"})
    (tmp_path / ".doorbell-agent").write_text(name)
    env.mock.post(dm, "hi", user=alice)
    env.wait_items(name)                       # pending, not claimed
    assert env.cli("hook", "stop", stdin=json.dumps({"cwd": str(tmp_path)})).stdout == ""

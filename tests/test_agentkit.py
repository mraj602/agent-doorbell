"""`doorbell init-agent`: pure file generation, tested in-process."""
import json
import re

from doorbell import agentkit, cli


def init(tmp_path, **kw):
    folder = tmp_path / "my-agent"
    agentkit.init_agent(folder, **kw)
    return folder


def test_default_folder_is_harness_neutral(tmp_path):
    folder = init(tmp_path, owner="Sam")
    assert (folder / ".doorbell-agent").read_text() == "my-agent\n"
    assert (folder / "sub.yaml").read_text().startswith("# Who and what this agent listens to")
    agents = (folder / "AGENTS.md").read_text()
    assert "Sam" in agents and "{owner}" not in agents
    for step in ("doorbell register", "doorbell wait", "doorbell inbox --claim", "doorbell ack"):
        assert step in agents, step
    assert (folder / "CLAUDE.md").read_text() == "@AGENTS.md\n"
    assert not (folder / ".claude").exists()      # no harness settings unless asked for


def test_claude_code_extras_are_hooks_and_commands_only(tmp_path):
    folder = init(tmp_path, claude_code=True)
    settings = json.loads((folder / ".claude/settings.json").read_text())
    assert set(settings) == {"hooks"}             # never permission rules
    assert (folder / ".claude/commands/doorbell-listen.md").exists()
    assert (folder / ".claude/commands/doorbell-handle.md").exists()
    hooks = settings["hooks"]
    post = hooks["PostToolUse"][0]
    assert post["hooks"][0]["command"].endswith("hook post-tool-use")
    assert hooks["Stop"][0]["hooks"][0]["command"].endswith("hook stop")
    matcher = re.compile(post["matcher"])
    for tool in ("mcp__plugin_slack_slack__slack_send_message", "mcp__plugin_slack_slack__slack_send_message_draft",
                 "mcp__claude_ai_Slack__slack_schedule_message", "mcp__plugin_slack_slack__slack_add_reaction"):
        assert matcher.fullmatch(tool), tool
    assert not matcher.fullmatch("mcp__plugin_slack_slack__slack_read_thread")


def test_every_hook_outcome_tool_is_covered_by_the_matcher(tmp_path):
    folder = init(tmp_path, claude_code=True)
    matcher = re.compile(json.loads((folder / ".claude/settings.json").read_text())
                         ["hooks"]["PostToolUse"][0]["matcher"])
    for short in list(cli.HOOK_OUTCOMES) + ["slack_add_reaction"]:
        assert matcher.fullmatch("mcp__plugin_slack_slack__" + short), short


def test_existing_files_are_kept_unless_forced(tmp_path, capsys):
    folder = init(tmp_path, owner="First")
    (folder / "AGENTS.md").write_text("my edits\n")
    made = agentkit.init_agent(folder)
    assert made == [] and (folder / "AGENTS.md").read_text() == "my edits\n"
    assert "keep" in capsys.readouterr().out
    agentkit.init_agent(folder, owner="Second", force=True)
    assert "Second" in (folder / "AGENTS.md").read_text()


def test_init_agent_cli_names_after_folder(tmp_path):
    cli.main(["init-agent", str(tmp_path / "folder-name"), "--claude-code", "--name", "custom"])
    folder = tmp_path / "folder-name"
    assert (folder / ".doorbell-agent").read_text() == "custom\n"
    assert (folder / ".claude/settings.json").exists()
    cli.main(["init-agent", str(tmp_path / "other")])
    assert (tmp_path / "other/.doorbell-agent").read_text() == "other\n"


def test_generated_sub_yaml_is_accepted_by_the_daemon_schema(tmp_path):
    import yaml
    from doorbell.server import SUB_KEYS
    sub = yaml.safe_load((init(tmp_path) / "sub.yaml").read_text())
    assert set(sub) <= SUB_KEYS


def test_windows_task_xml_is_well_formed(tmp_path):
    import xml.etree.ElementTree as ET
    from doorbell import service
    us = service.units("bot", str(tmp_path), ["agent-cli", "-p", "a & b"])
    for name, u in us.items():
        root = ET.fromstring(service.windows_task(name, u).split("\n", 1)[1])
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        assert root.find(".//t:LogonTrigger", ns) is not None
        args = root.find(".//t:Arguments", ns).text
        assert ("daemon" in args) if name == "doorbelld" else ('"a & b"' in args)

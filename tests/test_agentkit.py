"""`doorbell init-agent`: pure file generation, tested in-process."""
import json
import re
from pathlib import Path

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


def test_plugin_hooks_cover_every_hook_outcome_and_skip_reads():
    root = Path(__file__).resolve().parent.parent
    hooks = json.loads((root / "plugin/agent-doorbell/hooks/hooks.json").read_text())["hooks"]
    post = hooks["PostToolUse"][0]
    matcher = re.compile(post["matcher"])
    for prefix in ("mcp__plugin_slack_slack__", "mcp__claude_ai_Slack__", "mcp__slack__"):
        for short in list(cli.HOOK_OUTCOMES) + ["slack_add_reaction"]:
            assert matcher.fullmatch(prefix + short), prefix + short
    assert not matcher.fullmatch("mcp__plugin_slack_slack__slack_read_thread")
    for entry in (post, hooks["Stop"][0]):
        cmd = entry["hooks"][0]["command"]
        assert cmd.startswith("command -v doorbell") and cmd.endswith("|| true")   # harmless if not installed


def test_plugin_manifests_agree_on_name_and_version():
    from doorbell import __version__
    root = Path(__file__).resolve().parent.parent
    market = json.loads((root / ".claude-plugin/marketplace.json").read_text())
    plugin = json.loads((root / "plugin/agent-doorbell/.claude-plugin/plugin.json").read_text())
    entry = market["plugins"][0]
    assert entry["name"] == plugin["name"] == "agent-doorbell"
    assert (root / entry["source"]).resolve() == (root / "plugin/agent-doorbell").resolve()
    assert plugin["version"] == __version__
    for skill in ("setup", "handle", "listen", "status"):
        text = (root / f"plugin/agent-doorbell/skills/{skill}/SKILL.md").read_text()
        assert text.startswith(f"---\nname: {skill}\ndescription: "), skill


def test_existing_files_are_kept_unless_forced(tmp_path, capsys):
    folder = init(tmp_path, owner="First")
    (folder / "AGENTS.md").write_text("my edits\n")
    made = agentkit.init_agent(folder)
    assert made == [] and (folder / "AGENTS.md").read_text() == "my edits\n"
    assert "keep" in capsys.readouterr().out
    agentkit.init_agent(folder, owner="Second", force=True)
    assert "Second" in (folder / "AGENTS.md").read_text()


def test_init_agent_cli_names_after_folder(tmp_path):
    cli.main(["init-agent", str(tmp_path / "folder-name"), "--name", "custom"])
    folder = tmp_path / "folder-name"
    assert (folder / ".doorbell-agent").read_text() == "custom\n"
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


def test_init_agent_warns_inside_a_project_repo(tmp_path, capsys):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "AGENTS.md").write_text("the project's own instructions\n")
    agentkit.init_agent(repo)
    out = capsys.readouterr().out
    assert "looks like a project repo" in out
    assert (repo / "AGENTS.md").read_text() == "the project's own instructions\n"


def test_service_command_keeps_windows_paths(monkeypatch):
    """The daemon's path must survive as-is: backslashes and spaces (C:\\Program Files\\...)."""
    import xml.etree.ElementTree as ET
    from doorbell import service
    exe = r"C:\Program Files\Py 3\python.exe"
    monkeypatch.setattr(agentkit.shutil, "which", lambda name: None)
    monkeypatch.setattr(agentkit.sys, "executable", exe)
    u = service.units(None, None, None)["doorbelld"]
    assert u["argv"] == [exe, "-m", "doorbell", "daemon"]
    root = ET.fromstring(service.windows_task("doorbelld", u).split("\n", 1)[1])
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert root.find(".//t:Command", ns).text == exe
    assert root.find(".//t:Arguments", ns).text == "-m doorbell daemon"

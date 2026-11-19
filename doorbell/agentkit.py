"""`doorbell init-agent`: generate a harness-neutral agent folder.

Doorbell only listens. How the agent replies (its own Slack tool, drafts, or not at all) and
which tools it may use are decided by the harness that runs it (Claude Code, Codex, ...), so
this writes instructions and a subscription, never permission settings. Claude Code users get
skills and auto-ack hooks from the agent-doorbell plugin instead of per-folder files.
"""
import shutil
import sys
from importlib import resources
from pathlib import Path


def template(name):
    return resources.files("doorbell.templates").joinpath(name).read_text()


def doorbell_cmd():
    """How hooks should invoke doorbell on this machine."""
    exe = shutil.which("doorbell")
    return exe or f"{sys.executable} -m doorbell"


def write(path, text, force, made):
    if path.exists() and not force:
        print(f"  keep   {path} (exists; --force to overwrite)")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    made.append(path)
    print(f"  wrote  {path}")


def init_agent(folder, name=None, owner=None, force=False):
    root = Path(folder).expanduser().resolve()
    name = name or root.name
    owner = owner or "the owner"
    agents_md = (template("rules.md") + "\n" + template("protocol.md")).replace("{owner}", owner)
    made = []
    print(f"Agent folder {root} (agent {name!r})")
    if (root / ".git").exists() and not (root / ".doorbell-agent").exists():
        print("  note   this looks like a project repo. An agent folder of its own is usually better:\n"
              "         an existing AGENTS.md or CLAUDE.md here is kept, so the agent would miss its\n"
              "         doorbell instructions. Give the agent access to the repo when it runs instead.")
    write(root / ".doorbell-agent", name + "\n", force, made)
    write(root / "sub.yaml", template("sub.yaml"), force, made)
    write(root / "AGENTS.md", agents_md, force, made)
    write(root / "CLAUDE.md", "@AGENTS.md\n", force, made)   # Claude Code reads CLAUDE.md; this imports AGENTS.md
    print(NEXT.format(dir=root))
    return made


NEXT = """
Next:
  1. Edit {dir}/AGENTS.md (the top half): who you are, what the agent should answer, ignore or
     leave for you, and how it may reply. Edit {dir}/sub.yaml: whose messages to listen to.
  2. Decide in your agent's own settings which Slack tools it may use (see docs/SETUP.md, step 6).
     Doorbell never posts; replying is up to the agent and its harness.
  3. Start the agent in {dir}. It registers itself (`doorbell register`) and starts listening.
     Headless, one short run per batch of messages:
       doorbell service install --dispatch <name> --dir {dir} -- <agent command>
     Claude Code: install the agent-doorbell plugin and use claude -p "/agent-doorbell:handle".
     See docs/SETUP.md, step 7, for Claude Code, Codex and Gemini CLI examples."""

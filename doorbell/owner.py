"""Commands only the owner runs: they ask for the sending password in a terminal.

The password is the real protection: an agent never has it, so it can't approve its own drafts,
grant itself sending or unlock Doorbell. As a second, softer guard, these commands refuse to
ask at all when they don't run in a terminal or when they run inside a known agent harness, so
an agent that tries them gets a clear "this is for your owner" instead of a hanging prompt.
"""
import getpass
import os
import re
import shlex
import subprocess
import sys
import tempfile
import textwrap

# Environment variables that AI agent harnesses set for the commands they run.
AGENT_ENV = ("DOORBELL_AGENT", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX",
             "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_THREAD_ID", "GEMINI_CLI", "CURSOR_AGENT",
             "AIDER_MODEL")


class NotOwner(SystemExit):
    pass


def has_terminal():
    """Both ends on a terminal: agents always capture the output of the commands they run."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    if os.name == "nt":
        return True
    try:
        with open("/dev/tty"):
            return True
    except OSError:
        return False


def require_owner(what):
    env = [k for k in AGENT_ENV if os.environ.get(k)]
    if env:
        raise NotOwner(f"error: `doorbell {what}` is for the owner, in their own terminal; it doesn't "
                       f"run inside an AI agent session (found {env[0]}). Ask your owner to run it.")
    if not has_terminal():
        raise NotOwner(f"error: `doorbell {what}` asks for the sending password and needs a terminal.")


def ask_password(what, prompt="Sending password: ", confirm=False):
    require_owner(what)
    pw = getpass.getpass(prompt)
    if confirm:
        if len(pw) < 8:
            raise SystemExit("error: use a password of at least 8 characters")
        if getpass.getpass("Again: ") != pw:
            raise SystemExit("error: the passwords don't match")
    return pw


def edit_text(text):
    """Edit a draft in $VISUAL/$EDITOR, or inline when neither is set."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        fd, path = tempfile.mkstemp(suffix=".md", text=True)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text)
            subprocess.run([*shlex.split(editor, posix=os.name != "nt"), path], check=False)
            with open(path) as f:
                return f.read().strip()
        finally:
            os.unlink(path)
    print("Type the new text. End with a line containing only a dot (.)")
    lines = []
    while True:
        line = input()
        if line == ".":
            return "\n".join(lines).strip()
        lines.append(line)


CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
BROADCAST = re.compile(r"<!(channel|here|everyone)")


def safe(s):
    """Text from Slack or from an agent, made safe to print: no escape sequences or carriage
    returns that could make a draft look different from what would be sent."""
    return CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", s or "")


def show(dr):
    print(f"\n#{dr['id']} from agent {safe(dr['agent'])}, {safe(dr['to'])}")
    for m in dr["replying_to"]:
        print(textwrap.indent(textwrap.fill(f"{safe(m['user'])}: {safe(m['text'])}", 100), "  > "))
    print(textwrap.indent(safe(dr["text"]), "  | "))
    if BROADCAST.search(dr["text"]):
        print("  ! this draft notifies everyone in the channel (@channel, @here or @everyone)")
    if dr.get("error"):
        print(f"  ! last attempt failed: {safe(dr['error'])}")


def review(call):
    require_owner("review")
    drafts = call("drafts")["drafts"]
    if not drafts:
        print("No drafts waiting.")
        return
    pw = None
    for dr in drafts:
        show(dr)
        while True:
            c = input("[a]pprove  [e]dit and approve  [r]eject  [s]kip  [q]uit > ").strip().lower()[:1]
            if c in ("a", "e"):
                text = dr["text"]
                if c == "e":
                    text = edit_text(text)
                    if not text:
                        print("Empty text; skipped.")
                        break
                res = None
                for _ in range(3):
                    pw = pw or getpass.getpass("Sending password: ")
                    try:
                        res = call("approve", id=dr["id"], text=text, password=pw)
                        break
                    except SystemExit as e:
                        print(e)
                        if "wrong password" not in str(e):
                            break
                        pw = None
                if res:
                    print(f"Sent (ts {res['sent']}).")
                break
            if c == "r":
                try:
                    call("reject", id=dr["id"])
                    print("Rejected.")
                except SystemExit as e:
                    print(e)
                break
            if c == "s":
                break
            if c == "q":
                return

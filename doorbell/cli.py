"""doorbell CLI: talks to doorbelld over 127.0.0.1 with the access key in endpoint.json. Also hosts the Claude Code hooks."""
import argparse
import json
import socket
import sys
import time
from pathlib import Path

import yaml

from . import __version__, config


class DaemonDown(Exception):
    pass


def endpoint():
    try:
        ep = json.loads(config.ENDPOINT.read_text())
        return ep["port"], ep["key"]
    except (FileNotFoundError, ValueError, KeyError):
        raise DaemonDown(f"doorbelld is not running (no {config.ENDPOINT}). "
                         f"Start it with `doorbell service install` or `doorbell daemon`.")


def call(op, **req):
    port, key = endpoint()
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=10)
    except OSError:
        raise DaemonDown("doorbelld is not running (nothing listening at the address in "
                         f"{config.ENDPOINT}). Start it with `doorbell service install` or `doorbell daemon`.")
    s.settimeout(None if op == "wait" else 120)
    s.sendall((json.dumps({"op": op, "auth": key, **req}) + "\n").encode())
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    if not buf:
        raise DaemonDown("doorbelld closed the connection")
    res = json.loads(buf)
    if not res.pop("ok"):
        raise SystemExit(f"error: {res['error']}")
    return res


def out(res):
    print(json.dumps(res, indent=2, default=str))


def agent_name(args):
    if getattr(args, "name", None):
        return args.name
    f = Path.cwd() / ".doorbell-agent"
    if f.exists():
        return f.read_text().strip()
    raise SystemExit("error: pass --name or run from a folder with a .doorbell-agent file")


# ---------- Claude Code hooks ----------
# Matched by suffix so they work for both the Slack plugin (mcp__plugin_slack_slack__*) and the
# claude.ai Slack connector (mcp__claude_ai_Slack__*).
HOOK_OUTCOMES = {
    "slack_send_message": "replied",
    "slack_send_message_draft": "drafted",
    "slack_schedule_message": "scheduled",
}


def folder_agent(ev):
    """The agent whose folder the session runs in, or None outside an agent folder."""
    f = Path(ev.get("cwd") or ".") / ".doorbell-agent"
    return f.read_text().strip() if f.exists() else None


def hook_post_tool_use():
    """After the agent posts, drafts or reacts in Slack, ack the matching queue items at once.
    Only in an agent folder, and only that agent's items: a Slack reply from any other session
    leaves the queue alone."""
    ev = json.load(sys.stdin)
    name = folder_agent(ev)
    if not name:
        return
    tool, inp = ev.get("tool_name", ""), ev.get("tool_input") or {}
    short = tool.rsplit("__", 1)[-1]
    try:
        if short in HOOK_OUTCOMES:
            call("ack", name=name, by="thread", channel=inp.get("channel_id"), thread_ts=inp.get("thread_ts"),
                 outcome=HOOK_OUTCOMES[short], note=f"hook: {short}")
        elif short == "slack_add_reaction":
            call("ack", name=name, channel=inp.get("channel_id"), message_ts=inp.get("message_ts"),
                 outcome="reacted", note=f"hook: reaction {inp.get('emoji')}")
    except (DaemonDown, SystemExit):
        pass   # never break the agent's turn because of doorbell


def hook_stop():
    """Block the agent from ending its turn while it holds claimed-but-unresolved items."""
    ev = json.load(sys.stdin)
    name = folder_agent(ev)
    if not name or ev.get("stop_hook_active"):
        return
    try:
        items = call("unresolved", name=name)["items"]
    except (DaemonDown, SystemExit):
        return
    if items:
        seqs = ", ".join(str(i["seq"]) for i in items)
        print(json.dumps({"decision": "block", "reason":
              f"doorbell: items {seqs} are claimed but unresolved. Resolve each one the way your "
              f"instructions allow (a Slack reply or draft acks it automatically), or run "
              f"`doorbell ack <seq> --outcome ignored|escalated --note \"<why>\"`."}))


# ---------- commands ----------
def build_parser():
    p = argparse.ArgumentParser(prog="doorbell", description=(
        "Agent Doorbell: watches Slack without an LLM and wakes your AI agents only when a "
        "message needs them."))
    p.add_argument("--version", action="version", version=f"doorbell {__version__}")
    sp = p.add_subparsers(dest="cmd", required=True, metavar="command")

    sp.add_parser("daemon", help="run doorbelld in the foreground")

    ia = sp.add_parser("identity", help="add the Slack user token the watcher reads with")
    ia.add_argument("action", choices=["add"])
    ia.add_argument("--token-file", required=True, help="file containing an xoxp- user token (chmod 600)")

    ini = sp.add_parser("init-agent", help="create an agent folder (instructions and subscription)")
    ini.add_argument("dir")
    ini.add_argument("--name", help="agent name (default: the folder name)")
    ini.add_argument("--owner", help="your name, used in the instructions (default: 'the owner')")
    ini.add_argument("--force", action="store_true", help="overwrite generated files")

    r = sp.add_parser("register", help="register (or update) an agent and its subscription")
    r.add_argument("--name")
    r.add_argument("--as", dest="identity", help="Slack user ID, when several identities are added")
    r.add_argument("--sub", help="subscription YAML file (default: sub.yaml in the current folder)")
    r.add_argument("--resume", choices=["checkpoint", "now"])
    r.add_argument("--idle-ttl", type=float, help="seconds without contact before inactive")
    r.add_argument("--max-catchup", type=float, help="how far back (seconds) a returning agent catches up")
    r.add_argument("--on-manual-reply", choices=["defer", "ignore"])

    d = sp.add_parser("deregister", help="stop listening for an agent and drop its queue")
    d.add_argument("--name")

    s = sp.add_parser("sub", help="change who an agent listens to")
    s.add_argument("action", choices=["add", "remove", "set"])
    s.add_argument("key")
    s.add_argument("values", nargs="*")
    s.add_argument("--name")
    s.add_argument("--why")

    w = sp.add_parser("wait", help="block until the agent has work (costs nothing while blocked)")
    w.add_argument("--name")

    i = sp.add_parser("inbox", help="show the agent's queue as JSON")
    i.add_argument("--name")
    i.add_argument("--claim", action="store_true", help="lease pending items to this caller")

    a = sp.add_parser("ack", help="resolve queue items")
    a.add_argument("seqs", nargs="*", type=int)
    a.add_argument("--name")
    a.add_argument("--outcome", default="handled",
                   help="replied | drafted | reacted | ignored | escalated | handled")
    a.add_argument("--note")

    ds = sp.add_parser("dispatch", help="headless runner: wait, run a command per batch, repeat")
    ds.add_argument("--name")
    ds.add_argument("--dir", default=".", help="agent folder to run the command in")
    ds.add_argument("--timeout", type=float, default=900, help="seconds before a run is killed")
    ds.add_argument("--once", action="store_true", help="handle one batch and exit")
    ds.add_argument("command", nargs=argparse.REMAINDER, help="-- then the command to run")

    sv = sp.add_parser("service", help="install the daemon (and optionally a dispatcher) as a user service")
    sv.add_argument("action", choices=["install", "uninstall", "print"])
    sv.add_argument("--dispatch", metavar="NAME", help="also manage a dispatcher service for agent NAME")
    sv.add_argument("--dir", help="agent folder for the dispatcher")
    sv.add_argument("command", nargs=argparse.REMAINDER, help="-- then the dispatcher command")

    sp.add_parser("status", help="daemon state, agents, poll set, API usage").add_argument(
        "--verbose", action="store_true")
    sp.add_parser("doctor", help="check the token, scopes and agent app IDs")
    sp.add_parser("log", help="audit trail").add_argument("--since", type=float, default=86400,
                                                         help="seconds back (default 1 day)")
    rt = sp.add_parser("retry", help="re-queue stuck items")
    rt.add_argument("seqs", nargs="+", type=int)

    # ---- sending (optional): agents
    def text_args(x):
        g = x.add_mutually_exclusive_group(required=True)
        g.add_argument("--text", help="the message (Slack markdown)")
        g.add_argument("--text-file", help="read the message from a file, - for stdin")
    dr = sp.add_parser("draft", help="agent: write a reply draft for an item; the owner approves it")
    dr.add_argument("seq", type=int)
    dr.add_argument("--name")
    text_args(dr)
    se = sp.add_parser("send", help="agent: reply to an item directly (needs `doorbell grant`)")
    se.add_argument("seq", type=int)
    se.add_argument("--name")
    text_args(se)
    rc = sp.add_parser("react", help="agent: add a reaction to an item's message (needs `doorbell grant`)")
    rc.add_argument("seq", type=int)
    rc.add_argument("emoji")
    rc.add_argument("--name")
    rq = sp.add_parser("request-send", help="agent: ask the owner to allow direct sending")
    rq.add_argument("--why", required=True)
    rq.add_argument("--name")

    # ---- sending: the owner, in a terminal
    sn = sp.add_parser("sender", help="owner: add or remove the token Doorbell sends with")
    sn.add_argument("action", choices=["add", "remove"])
    sn.add_argument("--token-file", help="file with an xoxp- user token that has chat:write (for add)")
    sn.add_argument("--as", dest="identity", help="Slack user ID (for remove, with several identities)")
    for cmd, hlp in (("unlock", "owner: allow granted agents to send again after a restart"),
                     ("lock", "owner: stop all direct sends until `doorbell unlock`"),
                     ("passwd", "owner: change the sending password")):
        sp.add_parser(cmd, help=hlp).add_argument("--as", dest="identity", help="Slack user ID")
    gr = sp.add_parser("grant", help="owner: let an agent send directly")
    gr.add_argument("agent")
    rv = sp.add_parser("revoke", help="owner: take direct sending away from an agent")
    rv.add_argument("agent")
    sp.add_parser("review", help="owner: go through drafts: approve, edit, reject")
    dl = sp.add_parser("drafts", help="list drafts as JSON")
    dl.add_argument("--all", action="store_true", help="include sent and rejected ones")
    ap = sp.add_parser("approve", help="owner: send a draft")
    ap.add_argument("id", type=int)
    ap.add_argument("--text", help="send this text instead of the draft's")
    rj = sp.add_parser("reject", help="owner: drop a draft")
    rj.add_argument("id", type=int)

    h = sp.add_parser("hook", help="Claude Code hook entry points")
    h.add_argument("which", choices=["post-tool-use", "stop"])
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        run(args)
    except DaemonDown as e:
        raise SystemExit(f"error: {e}")
    except KeyboardInterrupt:
        raise SystemExit(130)


def strip_dashes(cmd):
    return cmd[1:] if cmd and cmd[0] == "--" else cmd


def run(args):
    c = args.cmd
    if c == "daemon":
        from .daemon import main as dmain
        return dmain()
    if c == "hook":
        return hook_post_tool_use() if args.which == "post-tool-use" else hook_stop()
    if c == "init-agent":
        from .agentkit import init_agent
        return init_agent(args.dir, name=args.name, owner=args.owner, force=args.force)
    if c == "dispatch":
        from .runner import dispatch
        return dispatch(agent_name_in(args.dir, args), strip_dashes(args.command), args.dir,
                        args.timeout, args.once)
    if c == "service":
        from .service import service
        return service(args.action, args.dispatch, args.dir, strip_dashes(args.command))
    if c == "identity":
        return out(call("identity_add", token_file=args.token_file))
    if c == "register":
        path = args.sub or ("sub.yaml" if Path("sub.yaml").exists() else None)
        sub = yaml.safe_load(open(path)) if path else {}
        return out(call("register", name=agent_name(args), identity=args.identity, sub=sub, resume=args.resume,
                        idle_ttl=args.idle_ttl, max_catchup=args.max_catchup,
                        on_manual_reply=args.on_manual_reply))
    if c == "deregister":
        return out(call("deregister", name=agent_name(args)))
    if c == "sub":
        return out(call("sub", name=agent_name(args), action=args.action, key=args.key, values=args.values,
                        why=args.why))
    if c == "wait":
        print(json.dumps(wait(agent_name(args))))
        return
    if c == "inbox":
        return out(call("inbox", name=agent_name(args), claim=args.claim))
    if c == "ack":
        return out(call("ack", name=agent_name(args), seqs=args.seqs, outcome=args.outcome, note=args.note))
    if c == "status":
        return out(call("status", verbose=args.verbose))
    if c == "doctor":
        return out(call("doctor"))
    if c == "log":
        return out(call("log", since=args.since))
    if c == "retry":
        return out(call("retry", seqs=args.seqs))
    if c in ("draft", "send"):
        return out(call(c, name=agent_name(args), seq=args.seq, text=read_text(args)))
    if c == "react":
        return out(call("react", name=agent_name(args), seq=args.seq, emoji=args.emoji))
    if c == "request-send":
        return out(call("request_send", name=agent_name(args), why=args.why))
    return run_owner(args)


def read_text(args):
    if args.text is not None:
        return args.text
    if args.text_file == "-":
        return sys.stdin.read()
    return Path(args.text_file).read_text()


def run_owner(args):
    from . import owner
    c = args.cmd
    if c == "sender" and args.action == "add":
        if not args.token_file:
            raise SystemExit("error: pass --token-file <file with the sender token>")
        owner.require_owner("sender add")
        status = call("status")
        old = None
        if status.get("sending"):
            print("Replacing a sender token needs the current password.")
            old = owner.ask_password("sender add", "Current sending password: ")
        print("Choose a password for sending. Agents never get it; you type it to approve drafts, "
              "grant agents and unlock after a restart.")
        pw = owner.ask_password("sender add", "New sending password: ", confirm=True)
        return out(call("sender_add", token_file=str(Path(args.token_file).expanduser().resolve()),
                        password=pw, old_password=old))
    if c == "sender":
        return out(call("sender_remove", identity=args.identity, password=owner.ask_password("sender remove")))
    if c == "unlock":
        return out(call("unlock", identity=args.identity, password=owner.ask_password("unlock")))
    if c == "lock":
        return out(call("lock", identity=args.identity))
    if c == "passwd":
        old = owner.ask_password("passwd", "Current sending password: ")
        new = owner.ask_password("passwd", "New sending password: ", confirm=True)
        return out(call("passwd", identity=args.identity, old_password=old, password=new))
    if c == "grant":
        return out(call("grant", agent=args.agent, password=owner.ask_password("grant")))
    if c == "revoke":
        return out(call("revoke", agent=args.agent))
    if c == "review":
        return owner.review(call)
    if c == "drafts":
        return out(call("drafts", all=args.all))
    if c == "approve":
        return out(call("approve", id=args.id, text=args.text, password=owner.ask_password("approve")))
    if c == "reject":
        return out(call("reject", id=args.id))


def agent_name_in(folder, args):
    if getattr(args, "name", None):
        return args.name
    f = Path(folder) / ".doorbell-agent"
    if f.exists():
        return f.read_text().strip()
    raise SystemExit(f"error: pass --name or point --dir at a folder with a .doorbell-agent file")


def wait(name):
    """Block until there is work. Survives daemon restarts instead of waking the agent with an error."""
    while True:
        try:
            return call("wait", name=name)
        except DaemonDown:
            time.sleep(3)


if __name__ == "__main__":
    main()

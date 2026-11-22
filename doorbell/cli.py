"""doorbell CLI: talks to doorbelld over 127.0.0.1 with the access key in endpoint.json. Also hosts the Claude Code hooks."""
import argparse
import json
import os
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

    lg = sp.add_parser("login", help="sign in to Slack in the browser (no token to copy)")
    lg.add_argument("--send", dest="role", action="store_const", const="send",
                    help="turn on sending: sign in to the app that may post (sealed with your password)")
    lg.add_argument("--for", dest="role", choices=["watch", "send", "both"], default=None,
                    help="choose yourself instead of letting Doorbell decide: watch (read-only token in a "
                         "file), send (a token that can post, sealed), both (one token watches and sends, sealed)")
    lg.add_argument("--client-id", help="the Slack app's Client ID (default: slack_client_id in config.yaml "
                                        "or DOORBELL_SLACK_CLIENT_ID)")
    lg.add_argument("--redirect-uri", default=None,
                    help="a localhost redirect URL registered on the app (default http://localhost:8765/doorbell/callback)")
    lg.add_argument("--team", help="Slack workspace ID, to skip the workspace picker")
    lg.add_argument("--no-browser", action="store_true", help="only print the link")
    lg.add_argument("--search", action="store_true", default=None,
                    help="also ask for search:read (the app must list it), so one search call can cover every "
                         "conversation; default: login_search in config.yaml")
    lg.add_argument("--timeout", type=float, default=300, help="seconds to wait for the sign-in")

    lo = sp.add_parser("logout", help="sign out: revoke Doorbell's Slack tokens and forget them")
    lo.add_argument("--as", dest="identity", help="Slack user ID, when several identities are added")

    ia = sp.add_parser("identity", help="add a Slack user token file the watcher reads with (or use `login`)")
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
    ds.add_argument("--handoff", metavar="CMD",
                    help="first hand each new batch (JSON on stdin) to this shell command, e.g. "
                         "'wy peers send tag:slack-orchestrator --one --stdin'; the command after -- "
                         "runs only when the handoff fails. `wy peers send` only works from inside a Claude "
                         "Code session (exit 4 otherwise), so start such a dispatcher from a session")
    ds.add_argument("--handoff-ok", default="0,8", metavar="CODES",
                    help="exit codes of --handoff that mean handed off (default 0,8)")
    ds.add_argument("command", nargs="*", help="-- then the command to run")

    sv = sp.add_parser("service", help="install the daemon (and optionally a dispatcher) as a user service")
    sv.add_argument("action", choices=["install", "uninstall", "print"])
    sv.add_argument("--dispatch", metavar="NAME", help="also manage a dispatcher service for agent NAME")
    sv.add_argument("--dir", help="agent folder for the dispatcher")
    sv.add_argument("--handoff", metavar="CMD", help="the dispatcher hands each batch to this command first "
                    "(see `doorbell dispatch --handoff`); the command after -- is then optional. Not for "
                    "`wy peers send`: it refuses outside a Claude Code session, so from a service it always fails")
    sv.add_argument("--handoff-ok", metavar="CODES", help="exit codes of --handoff that mean handed off")
    sv.add_argument("command", nargs="*", help="-- then the dispatcher command")

    so = sp.add_parser("socket", help="owner: Socket Mode (Slack pushes messages) for an app only you use")
    so.add_argument("action", choices=["enable", "disable"])
    so.add_argument("--app-token-file", help="file with the app-level token (xapp-..., scope connections:write), "
                                             "chmod 600 (for enable)")
    so.add_argument("--only-me", action="store_true",
                    help="confirm nobody else signs in to this Slack app with read access (required for enable)")
    so.add_argument("--as", dest="identity", help="Slack user ID, when several identities are added")

    cf = sp.add_parser("config", help="show or change settings in config.yaml (the daemon picks them up)")
    cf.add_argument("action", choices=["get", "set", "unset"])
    cf.add_argument("key", nargs="?")
    cf.add_argument("value", nargs="?", help="YAML value, e.g. 30, search, [A1, A2]")

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
        ok = {int(c) for c in args.handoff_ok.split(",") if c.strip()}
        return dispatch(agent_name_in(args.dir, args), strip_dashes(args.command), args.dir,
                        args.timeout, args.once, handoff=args.handoff, handoff_ok=ok)
    if c == "service":
        from .service import service
        return service(args.action, args.dispatch, args.dir, strip_dashes(args.command),
                       handoff=args.handoff, handoff_ok=args.handoff_ok)
    if c == "identity":
        return out(call("identity_add", token_file=args.token_file))
    if c == "login":
        return login(args)
    if c == "logout":
        return logout(args)
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
    if c == "socket":
        if args.action == "enable" and not args.app_token_file:
            raise SystemExit("error: --app-token-file is required for enable")
        return out(call("socket", action=args.action,
                        app_token_file=args.app_token_file and os.path.abspath(os.path.expanduser(args.app_token_file)), only_me=args.only_me,
                        identity=args.identity))
    if c == "config":
        return config_cmd(args)
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


def config_cmd(args):
    """`doorbell config`: read or edit config.yaml. Unknown keys and bad values are refused before
    anything is written; the running daemon reloads the file within a few seconds."""
    current = yaml.safe_load(config.CONFIG_FILE.read_text()) if config.CONFIG_FILE.exists() else {}
    current = current or {}
    if args.action == "get":
        cfg = config.load()
        return out({args.key: cfg[args.key]} if args.key else cfg)
    if not args.key or args.key not in config.DEFAULTS:
        raise SystemExit(f"error: unknown key {args.key!r}; keys: {', '.join(sorted(config.DEFAULTS))}")
    if args.action == "unset":
        current.pop(args.key, None)
    else:
        if args.value is None:
            raise SystemExit("error: give a value")
        try:
            value = yaml.safe_load(args.value)
        except yaml.YAMLError as e:
            raise SystemExit(f"error: can't read {args.value!r}: {e}")
        try:
            config.validate({args.key: value}, where="config")
        except ValueError as e:
            raise SystemExit(f"error: {e}")
        current[args.key] = value
    config.HOME.mkdir(parents=True, exist_ok=True)
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=config.HOME, prefix=".config.", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(yaml.safe_dump(current, sort_keys=True))
    os.replace(tmp, config.CONFIG_FILE)
    return out({"saved": str(config.CONFIG_FILE), args.key: current.get(args.key, config.DEFAULTS[args.key])})


CAN_POST = ("This sign-in can also post messages as you: you allowed chat:write for this Slack app before "
            "(Slack adds up what each person allowed one app, for all their tokens).")


def is_owner_terminal():
    from . import owner
    try:
        owner.require_owner("login")
        return True
    except SystemExit:
        return False


def login(args):
    """`doorbell login`: OAuth with PKCE in the browser. The token is never printed.

    Without --for, Doorbell asks Slack for read scopes only and decides from what the token can
    really do: read-only goes to a file and watches unattended (layout A); a token that can post
    is sealed with a password and both watches and sends (layout B). `--send` turns on sending:
    with the watching app that means layout B, with another app layout C."""
    from . import oauth, owner
    cfg = config.load()
    client_id = args.client_id or cfg.get("slack_client_id")
    if not client_id:
        raise SystemExit("error: no Slack app to sign in with. Pass --client-id, or set slack_client_id in "
                         f"{config.CONFIG_FILE} (or DOORBELL_SLACK_CLIENT_ID). Your workspace admin has the "
                         "app's Client ID; see docs/ORG-SETUP.md.")
    role = args.role
    if role in ("send", "both"):
        owner.require_owner(f"login --for {role}")
    status = call("status")   # fail now, not after the browser round trip, if the daemon is down
    paste = input if sys.stdin.isatty() else None
    say = lambda s: print(s, flush=True)

    def announce(line):
        say(line)
        if paste:
            say("Browser on another machine (SSH, a server)? After Allow, its address bar shows "
                "http://localhost:...?code=... (the page won't load). Paste that address here:")
    say("Sign in to Slack and click Allow.")
    try:
        creds = oauth.login(client_id, {None: "watch"}.get(role, role), args.redirect_uri or oauth.DEFAULT_REDIRECT,
                            args.team, args.timeout, not args.no_browser and role not in ("send", "both"),
                            api_base=cfg["slack_api_base"],
                            announce=announce, paste=paste,
                            extra_scopes=["search:read"] if (cfg.get("login_search") if args.search is None
                                                              else args.search) else [])
    except oauth.LoginError as e:
        raise SystemExit(f"error: {e}")
    can_post = "chat:write" in creds["scopes"]
    say(f"Signed in as {creds['user_id']}.")

    def give_up(msg):
        oauth.revoke(cfg["slack_api_base"], creds)   # don't leave a token behind that nobody keeps
        raise SystemExit(f"error: {msg}")

    if role in (None, "watch") and not can_post:
        path = config.HOME / "credentials" / f"{creds['user_id']}.json"
        try:
            from .slackapi import read_creds
            previous = read_creds(str(path)) if path.exists() else None
        except OSError:
            previous = None
        oauth.write_creds(str(path), creds)
        try:
            res = call("identity_add", token_file=str(path))
        except SystemExit:
            if previous:
                oauth.write_creds(str(path), previous)
            else:
                path.unlink(missing_ok=True)
            oauth.revoke(cfg["slack_api_base"], creds)
            raise
        if previous and previous.get("token") != creds["token"]:
            oauth.revoke(cfg["slack_api_base"], previous)   # the sign-in it replaces
        say("Watching with a read-only token (it can't post). Doorbell renews it on its own.")
        return out(res)
    if role in (None, "watch"):
        reset = ("To watch read-only instead, revoke all your tokens for this app (`doorbell logout`, plus any "
                 "other tool that uses the app) and sign in again; or watch with a separate read-only app.")
        if role == "watch":
            give_up(f"{CAN_POST} Doorbell never keeps a token that can post in a plain file. Run `doorbell login` "
                    f"(without --for) to keep it sealed with a password. {reset}")
        if not is_owner_terminal():
            give_up(f"{CAN_POST} Such a token is kept sealed with a password, which only the owner types in a "
                    f"terminal. Ask the owner to run `doorbell login` there. {reset}")
        say(f"{CAN_POST}\nDoorbell keeps such a token sealed with a password, never in a plain file. It will watch "
            f"and send with this one token. After Doorbell restarts, watching waits for `doorbell unlock`, then "
            f"catches up; nothing is missed.\n{reset}")
        role = "both"
    elif role == "send":
        ident = next((i for i in status.get("identities", []) if i["user_id"] == creds["user_id"]), None)
        if ident and (ident.get("mode") == "sealed" or ident.get("client_id") == client_id):
            say("This is the same Slack app you watch with, so your watch token can now post too. Doorbell switches "
                "to one sealed token that watches and sends.")
            role = "both"
    old = None
    if creds["user_id"] in (status.get("sending") or {}):
        say("This replaces the sending token, which needs the current password.")
        old = owner.ask_password(f"login --for {role}", "Current sending password: ")
    else:
        say("Choose a password for sending. Agents never get it; you type it to approve drafts, grant agents "
            "and unlock after a restart.")
    try:
        pw = owner.ask_password(f"login --for {role}", "New sending password: " if not old else
                                "New sending password (can be the same): ", confirm=True)
        op = "sender_add" if role == "send" else "login_both"
        return out(call(op, token_json=json.dumps(creds), password=pw, old_password=old))
    except (SystemExit, KeyboardInterrupt) as e:
        give_up(f"nothing was saved ({e})")


def logout(args):
    from . import owner
    status = call("status")
    ident = args.identity
    pw = None
    sending = status.get("sending") or {}
    if (ident or (len(status.get("identities", [])) == 1 and status["identities"][0]["user_id"])) in sending \
            or (not ident and sending):
        pw = owner.ask_password("logout")
    return out(call("logout", identity=ident, password=pw))


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


def wait(name, new_only=False):
    """Block until there is work. Survives daemon restarts instead of waking the agent with an error.
    new_only: wake for new (pending) items only, not for claimed ones still open."""
    while True:
        try:
            return call("wait", name=name, **({"new_only": True} if new_only else {}))
        except DaemonDown:
            time.sleep(3)


if __name__ == "__main__":
    main()

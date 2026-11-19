"""Local protocol over 127.0.0.1: one JSON request line in, one JSON response line out. Every
request carries the access key from endpoint.json, which only the user can read.
`wait` is the exception: it holds the connection open until the agent has work."""
import asyncio
import hmac
import json
import os
import time

from . import sending, store
from .slackapi import Slack, read_token

SUB_KEYS = {"dms", "group_dms", "channels", "everyone_in", "users", "usergroups",
            "mentions_of_me", "threads_im_in", "allow_bots"}
REQUIRED_SCOPES = {"channels:history", "channels:read", "groups:history", "groups:read", "im:history",
                   "im:read", "mpim:history", "mpim:read", "users:read", "usergroups:read"}
LIST_KEYS = {"channels", "everyone_in", "users", "usergroups", "allow_bots"}


async def handle(d, reader, writer):
    try:
        line = await reader.readline()
        if not line:
            return
        req = json.loads(line)
        if not hmac.compare_digest(str(req.pop("auth", "")), d.key):
            raise PermissionError("bad or missing access key")
        op = req.pop("op")
        if op == "wait":
            res = await op_wait(d, reader, req)
            if res is None:
                return
        else:
            res = await OPS[op](d, req)
        writer.write((json.dumps({"ok": True, **res}) + "\n").encode())
    except Exception as e:
        writer.write((json.dumps({"ok": False, "error": str(e)}) + "\n").encode())
    finally:
        try:
            await writer.drain()
            writer.close()
        except Exception:
            pass


def agent_dict(d, a):
    counts = {r["state"]: r["n"] for r in d.q("SELECT state, count(*) n FROM items WHERE agent_id=? GROUP BY state",
                                             a["id"])}
    return {"name": a["name"], "identity": a["identity"], "status": a["status"],
            "waiters": d.waiters.get(a["id"], 0), "last_seen": a["last_seen"],
            "idle_ttl": a["idle_ttl"], "resume_policy": a["resume_policy"], "floor_ts": a["floor_ts"],
            "last_acked_ts": a["last_acked_ts"], "sub": json.loads(a["sub_json"] or "{}"), "items": counts}


async def validate_sub(d, identity, sub):
    bad = set(sub) - SUB_KEYS
    if bad:
        raise ValueError(f"unknown subscription keys: {sorted(bad)}")
    if sub.get("dms", "followed") not in ("all", "followed", "none"):
        raise ValueError("dms must be all | followed | none")
    if sub.get("channels") or sub.get("everyone_in"):
        if not d.q1("SELECT 1 FROM conv WHERE identity=? LIMIT 1", identity):
            await d.refresh_convlist(identity)
    await d.resolve_users(identity, sub.get("users"), strict=True)
    await d.usergroup_members(identity, sub.get("usergroups"), strict=True)
    d.resolve_channels(identity, sub.get("channels"), strict=True)
    d.resolve_channels(identity, sub.get("everyone_in"), strict=True)


async def op_identity_add(d, req):
    path = os.path.expanduser(req["token_file"])
    if os.name != "nt" and os.stat(path).st_mode & 0o077:
        raise ValueError(f"{path} is readable by other users; run: chmod 600 {path}")
    sl = Slack(read_token(path), d.cfg["slack_api_base"])
    info = await asyncio.to_thread(sl.call, "auth.test")
    missing = sorted(REQUIRED_SCOPES - set(sl.scopes or REQUIRED_SCOPES))
    if missing:
        raise ValueError(f"the token is missing scopes {missing}; add them under User Token Scopes and reinstall the app")
    d.db.execute("INSERT OR REPLACE INTO identities VALUES(?,?,?,?,'ok',NULL,?)",
                 (info["user_id"], info["team_id"], info["url"], path, time.time()))
    d.slacks.pop(info["user_id"], None)
    store.audit(d.db, "identity_add", user=info["user_id"], team=info["team_id"])
    extra = sorted(set(sl.scopes or []) - REQUIRED_SCOPES)
    return {"identity": info["user_id"], "user": info["user"], "team": info["team"],
            "warning": f"token has scopes doorbell never uses: {extra}. A read-only token is safer." if extra else None}


def pick_identity(d, ident):
    if ident:
        if not d.q1("SELECT 1 FROM identities WHERE user_id=?", ident):
            raise ValueError(f"identity {ident} not added; run `doorbell identity add`")
        return ident
    rows = d.q("SELECT user_id FROM identities")
    if len(rows) != 1:
        raise ValueError("pass --as <slack user id> (zero or several identities configured)")
    return rows[0]["user_id"]


async def op_register(d, req):
    ident = pick_identity(d, req.get("identity"))
    sub = req.get("sub") or {}
    await validate_sub(d, ident, sub)
    cfg, now = d.cfg, time.time()
    fields = {
        "identity": ident, "sub_json": json.dumps(sub),
        "resume_policy": req.get("resume") or "checkpoint",
        "idle_ttl": float(req.get("idle_ttl") or cfg["idle_ttl"]),
        "max_catchup": float(req.get("max_catchup") or cfg["max_catchup"]),
        "on_manual_reply": req.get("on_manual_reply") or "defer",
    }
    if fields["resume_policy"] not in ("checkpoint", "now"):
        raise ValueError("resume must be checkpoint | now")
    a = d.q1("SELECT * FROM agents WHERE name=?", req["name"])
    if a:
        sets = ", ".join(f"{k}=?" for k in fields)
        d.db.execute(f"UPDATE agents SET {sets}, last_seen=? WHERE id=?", (*fields.values(), now, a["id"]))
        store.audit(d.db, "register_update", req["name"])
        a = await d.touch(req["name"])
        created = False
    else:
        d.db.execute("INSERT INTO agents(name, status, floor_ts, last_seen, created_at, " + ", ".join(fields) +
                     ") VALUES(?,?,?,?,?," + ",".join("?" * len(fields)) + ")",
                     (req["name"], "detached", f"{now:.6f}", now, now, *fields.values()))
        store.audit(d.db, "register", req["name"], sub=sub)
        a = d.q1("SELECT * FROM agents WHERE name=?", req["name"])
        created = True
    return {"created": created, "agent": agent_dict(d, a)}


async def op_deregister(d, req):
    a = d.q1("SELECT * FROM agents WHERE name=?", req["name"])
    if not a:
        raise ValueError(f"no agent named {req['name']!r}")
    d.delete_agent(a["id"])
    d.wake(a["id"])
    store.audit(d.db, "deregister", req["name"])
    return {"deregistered": req["name"]}


async def op_sub(d, req):
    a = await d.touch(req["name"])
    sub = json.loads(a["sub_json"] or "{}")
    key, action, values = req["key"], req["action"], req.get("values") or []
    if key not in SUB_KEYS:
        raise ValueError(f"unknown key {key}")
    if key in LIST_KEYS:
        cur = list(sub.get(key) or [])
        if action == "add":
            cur += [v for v in values if v not in cur]
        elif action == "remove":
            cur = [v for v in cur if v not in values]
        else:
            cur = values
        sub[key] = cur
    else:
        val = values[0] if values else None
        sub[key] = {"true": True, "false": False}.get(str(val).lower(), val)
    await validate_sub(d, a["identity"], sub)
    before = await d.poll_set(a["identity"], d.live_agents(a["identity"]))
    d.db.execute("UPDATE agents SET sub_json=? WHERE id=?", (json.dumps(sub), a["id"]))
    # A newly followed conversation starts from now: no backfill of messages sent while unfollowed.
    # (Catch-up from a checkpoint is only for agents coming back from inactivity.)
    after = await d.poll_set(a["identity"], d.live_agents(a["identity"]))
    for ch in after - before:
        if (a["identity"], ch) not in d.polling:
            d.db.execute("UPDATE conv SET cursor=? WHERE identity=? AND channel=? AND cursor IS NOT NULL",
                         (store.ts_now(), a["identity"], ch))
    store.audit(d.db, "sub_change", a["name"], key=key, action=action, values=values, why=req.get("why"))
    d.notify_desktop(f"{a['name']}: {action} {key} {values} — {req.get('why') or 'no reason given'}")
    return {"sub": sub}


def open_counts(d, agent_id):
    p = d.q1("SELECT count(*) n FROM items WHERE agent_id=? AND state='pending'", agent_id)["n"]
    c = d.q1("SELECT count(*) n FROM items WHERE agent_id=? AND state='claimed'", agent_id)["n"]
    return p, c


async def op_wait(d, reader, req):
    a = await d.touch(req["name"])
    aid = a["id"]
    d.waiters[aid] = d.waiters.get(aid, 0) + 1
    d.db.execute("UPDATE agents SET status='active' WHERE id=?", (aid,))
    ev = d.events.setdefault(aid, asyncio.Event())
    eof = asyncio.ensure_future(reader.read())
    try:
        while True:
            if not d.q1("SELECT 1 FROM agents WHERE id=?", aid):
                return {"deregistered": True}
            pending, claimed = open_counts(d, aid)
            if pending or claimed:
                return {"pending": pending, "unresolved": claimed}
            ev.clear()
            got = asyncio.ensure_future(ev.wait())
            done, _ = await asyncio.wait({got, eof}, return_when=asyncio.FIRST_COMPLETED)
            if eof in done:
                got.cancel()
                return None
    finally:
        eof.cancel()
        d.waiters[aid] -= 1
        d.db.execute("UPDATE agents SET last_seen=? WHERE id=?", (time.time(), aid))
        if d.waiters[aid] == 0 and d.q1("SELECT 1 FROM agents WHERE id=? AND status='active'", aid):
            d.db.execute("UPDATE agents SET status='detached' WHERE id=?", (aid,))


def permalink(team_url, ch, ts, thread_ts=None):
    link = f"{team_url}archives/{ch}/p{ts.replace('.', '')}"
    if thread_ts and thread_ts != ts:
        link += f"?thread_ts={thread_ts}&cid={ch}"
    return link


async def render_msg(d, ident, team_url, ch, m):
    return {"ts": m["ts"], "thread_ts": m.get("thread_ts"), "user": m.get("user") or m.get("bot_id"),
            "user_name": await d.user_name(ident, m.get("user")) if m.get("user") else m.get("username"),
            "from_agent": m.get("app_id") in d.cfg["agent_app_ids"] and m.get("user") == ident,
            "is_me": m.get("user") == ident, "text": m.get("text", ""),
            "files": [f.get("name") for f in m.get("files", [])] or None,
            "permalink": permalink(team_url, ch, m["ts"], m.get("thread_ts"))}


async def render_item(d, a, it):
    ident = a["identity"]
    team_url = d.q1("SELECT team_url FROM identities WHERE user_id=?", ident)["team_url"]
    ch = it["channel"]
    conv = d.q1("SELECT * FROM conv WHERE identity=? AND channel=?", ident, ch)
    msgs = [json.loads(r["raw"]) for r in d.q(
        "SELECT m.raw FROM item_msgs im JOIN messages m ON m.channel=im.channel AND m.ts=im.ts AND m.identity=? "
        "WHERE im.item_seq=? ORDER BY m.ts", ident, it["seq"])]
    item_ts = {m["ts"] for m in msgs}
    context = []
    try:
        if it["thread_key"] != "main":
            r = await d.api(ident, "conversations.replies", channel=ch, ts=it["thread_key"], limit=30)
            context = [m for m in r.get("messages", []) if m["ts"] not in item_ts][-12:]
        elif msgs:
            r = await d.api(ident, "conversations.history", channel=ch, latest=msgs[0]["ts"], limit=6)
            context = list(reversed([m for m in r.get("messages", []) if m["ts"] not in item_ts]))
    except Exception:
        pass
    kind = conv["kind"] if conv else "?"
    if it["thread_key"] != "main":
        reply_ts = it["thread_key"]
    elif kind in ("im", "mpim"):
        reply_ts = None
    else:
        reply_ts = msgs[-1]["ts"] if msgs else None
    return {
        "seq": it["seq"], "state": it["state"], "attempt": it["attempts"],
        "warning": "re-delivered: a previous attempt may have partially handled this" if it["attempts"] > 1 else None,
        "conversation": {"channel_id": ch, "kind": kind,
                         "name": conv["name"] if conv and conv["name"] else None,
                         "with": await d.user_name(ident, conv["other_user"]) if conv and conv["other_user"] else None},
        "thread_key": it["thread_key"],
        "reply_to": {"channel_id": ch, "thread_ts": reply_ts},
        "messages": [await render_msg(d, ident, team_url, ch, m) for m in msgs],
        "context": [await render_msg(d, ident, team_url, ch, m) for m in context],
    }


async def op_inbox(d, req):
    a = await d.touch(req["name"])
    if req.get("claim"):
        d.db.execute("UPDATE items SET state='claimed', attempts=attempts+1, lease_until=?, updated_at=? "
                     "WHERE agent_id=? AND state='pending'", (time.time() + d.cfg["lease"], time.time(), a["id"]))
    rows = d.q("SELECT * FROM items WHERE agent_id=? AND state IN ('pending','claimed') ORDER BY seq", a["id"])
    return {"agent": a["name"], "items": [await render_item(d, a, it) for it in rows]}


async def op_ack(d, req):
    outcome = req.get("outcome") or "handled"
    note = req.get("note")
    seqs = set(req.get("seqs") or [])
    names = [req["name"]] if req.get("name") else None
    if req.get("name"):
        await d.touch(req["name"])
    scope = "SELECT i.* FROM items i JOIN agents a ON a.id=i.agent_id WHERE i.state IN ('pending','claimed')"
    args = []
    if names:
        scope += " AND a.name=?"
        args.append(names[0])
    ch = req.get("channel")
    if ch and ch[:1] in "UW":   # a DM addressed by user id
        r = d.q1("SELECT channel FROM conv WHERE kind='im' AND other_user=?", ch)
        ch = r["channel"] if r else ch
    if req.get("thread_ts") is not None or req.get("by") == "thread":
        key = req.get("thread_ts") or "main"
        for it in d.q(scope + " AND i.channel=?", *args, ch):
            in_item = d.q1("SELECT 1 FROM item_msgs WHERE item_seq=? AND ts=?", it["seq"], key)
            if it["thread_key"] == key or in_item:
                seqs.add(it["seq"])
    if req.get("message_ts"):
        for it in d.q(scope + " AND i.channel=?", *args, ch):
            if d.q1("SELECT 1 FROM item_msgs WHERE item_seq=? AND ts=?", it["seq"], req["message_ts"]):
                seqs.add(it["seq"])
    if names:   # an agent may only resolve its own items
        seqs &= {r["seq"] for r in d.q(scope, *args)}
    done = [s for s in sorted(seqs) if d.resolve(s, outcome, note)]
    return {"acked": done}


async def op_unresolved(d, req):
    a = d.q1("SELECT * FROM agents WHERE name=?", req["name"])
    if not a:
        return {"items": []}
    rows = d.q("SELECT seq, channel, thread_key, attempts FROM items WHERE agent_id=? AND state='claimed'", a["id"])
    return {"items": [dict(r) for r in rows]}


async def op_status(d, req):
    now = time.time()
    live = d.live_agents()
    calls = lambda s: d.q1("SELECT count(*) n FROM api_calls WHERE ts>?", now - s)["n"]
    out = {
        "state": "POLLING" if live else "DORMANT",
        "uptime_s": round(now - d.started),
        "api_calls": {"last_1m": calls(60), "last_1h": calls(3600), "since_start": d.api_total},
        "rate_paused_for_s": max(0, round(d.pause_until - now)),
        "poll_set": sorted(f"{i}:{c}" for i, c in d.polling),
        "identities": [dict(r) | {"token_file": r["token_file"]} for r in d.q(
            "SELECT user_id, team_id, status, error, token_file FROM identities")],
        "agents": [agent_dict(d, a) for a in d.q("SELECT * FROM agents ORDER BY name")],
        "stuck": [dict(r) for r in d.q("SELECT seq, agent_id, channel, attempts FROM items WHERE state='stuck'")],
        **sending.sending_status(d),
    }
    if req.get("verbose"):
        out["recent_audit"] = [dict(r) for r in d.q("SELECT * FROM audit ORDER BY ts DESC LIMIT 30")]
    return out


async def op_doctor(d, req):
    """Checks a new user needs: each identity's token and scopes, and which agent apps post as them."""
    out = {"config": {k: d.cfg[k] for k in ("slack_api_base", "agent_app_ids", "idle_ttl", "lease")},
           "identities": []}
    for r in d.q("SELECT * FROM identities"):
        info = {"user_id": r["user_id"], "team_url": r["team_url"], "status": r["status"]}
        try:
            sl = Slack(read_token(os.path.expanduser(r["token_file"])), d.cfg["slack_api_base"])
            await asyncio.to_thread(sl.call, "auth.test")
            info["token"] = "ok"
            if sl.scopes is not None:
                info["missing_scopes"] = sorted(REQUIRED_SCOPES - set(sl.scopes))
                info["unused_scopes"] = sorted(set(sl.scopes) - REQUIRED_SCOPES)
        except Exception as e:
            info["token"] = f"error: {e}"
        apps = d.q("SELECT app_id, count(*) n FROM messages WHERE identity=? AND user=? AND app_id IS NOT NULL "
                   "GROUP BY app_id", r["user_id"], r["user_id"])
        info["apps_seen_posting_as_you"] = {a["app_id"]: a["n"] for a in apps}
        out["identities"].append(info)
    return out


async def op_log(d, req):
    rows = d.q("SELECT * FROM audit WHERE ts>? ORDER BY ts", time.time() - float(req.get("since") or 86400))
    return {"log": [dict(r) for r in rows]}


async def op_retry(d, req):
    n = d.db.execute("UPDATE items SET state='pending', attempts=0, lease_until=NULL WHERE state='stuck' AND seq IN (%s)"
                     % ",".join("?" * len(req["seqs"])), req["seqs"]).rowcount
    return {"requeued": n}


OPS = {"identity_add": op_identity_add, "register": op_register, "deregister": op_deregister,
       "sub": op_sub, "inbox": op_inbox, "ack": op_ack, "unresolved": op_unresolved,
       "status": op_status, "doctor": op_doctor, "log": op_log, "retry": op_retry,
       **sending.OPS}

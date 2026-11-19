"""Optional sending through Doorbell.

The watcher's token only reads. To let agents answer through Doorbell, the owner adds a second
token with chat:write (`doorbell sender add`), sealed with a password (see seal.py). Then:

- Any agent may write a **draft** for one of its open items. Drafts stay in Doorbell, not in
  Slack, until the owner approves them with the password (`doorbell review` / `approve`).
- An agent the owner **granted** with the password may **send** directly, but only into the
  conversation of one of its own open items, within a rate limit, and only while Doorbell is
  **unlocked** (the decrypted token is held in memory, so after a restart the owner runs
  `doorbell unlock`).

Operations that add power (sender add, unlock, grant, approve, passwd) need the password.
Operations that remove it (lock, revoke, reject, sender remove) don't.
"""
import asyncio
import os
import time

from . import seal, store
from .slackapi import Slack, read_token

SEND_SCOPES = {"chat:write"}
MAX_TEXT = 12000


def sender_row(d, identity):
    return d.q1("SELECT * FROM sender WHERE identity=?", identity)


def identity_of(d, req):
    ident = req.get("identity")
    if ident:
        return ident
    rows = d.q("SELECT user_id FROM identities")
    if len(rows) != 1:
        raise ValueError("pass --as <slack user id> (zero or several identities configured)")
    return rows[0]["user_id"]


async def check_password(d, identity, password):
    row = sender_row(d, identity)
    if not row:
        raise ValueError("sending isn't set up; run `doorbell sender add --token-file <file>`")
    try:
        return await asyncio.to_thread(seal.unseal, row["sealed"], password or "")
    except seal.BadPassword:
        store.audit(d.db, "bad_password", identity=identity)
        await asyncio.sleep(1)   # slow down guessing
        raise PermissionError("wrong password") from None


def slack_for(d, token):
    return Slack(token, d.cfg["slack_api_base"])


# ---------- owner: setup, lock state, grants ----------
async def op_sender_add(d, req):
    path = os.path.expanduser(req["token_file"])
    if os.name != "nt" and os.stat(path).st_mode & 0o077:
        raise ValueError(f"{path} is readable by other users; run: chmod 600 {path}")
    token = read_token(path)
    sl = slack_for(d, token)
    info = await asyncio.to_thread(sl.call, "auth.test")
    ident = info["user_id"]
    if not d.q1("SELECT 1 FROM identities WHERE user_id=?", ident):
        raise ValueError(f"this token belongs to {ident}, which has no watcher identity; "
                         f"run `doorbell identity add` first")
    missing = sorted(SEND_SCOPES - set(sl.scopes or SEND_SCOPES))
    if missing:
        raise ValueError(f"the sender token is missing scopes {missing}")
    old = sender_row(d, ident)
    if old:   # replacing the token needs the current password
        await check_password(d, ident, req.get("old_password"))
    sealed = await asyncio.to_thread(seal.seal, token, req.get("password") or "")
    _, grant_key = await asyncio.to_thread(seal.unseal, sealed, req["password"])
    d.db.execute("INSERT OR REPLACE INTO sender VALUES(?,?,?,?)",
                 (ident, sealed, ",".join(sl.scopes or []), time.time()))
    resign_grants(d, ident, grant_key)
    d.unlocked[ident] = (token, grant_key)
    store.audit(d.db, "sender_add", identity=ident)
    return {"identity": ident, "unlocked": True, "can_react": sl.scopes is None or "reactions:write" in sl.scopes,
            "note": f"The token is now sealed in Doorbell. You can delete {path}."}


def resign_grants(d, ident, grant_key):
    for g in d.q("SELECT agent FROM grants WHERE identity=?", ident):
        d.db.execute("UPDATE grants SET mac=? WHERE identity=? AND agent=?",
                     (seal.grant_mac(grant_key, ident, g["agent"]), ident, g["agent"]))


async def op_sender_remove(d, req):
    ident = identity_of(d, req)
    d.db.execute("DELETE FROM sender WHERE identity=?", (ident,))
    d.db.execute("DELETE FROM grants WHERE identity=?", (ident,))
    d.unlocked.pop(ident, None)
    store.audit(d.db, "sender_remove", identity=ident)
    return {"removed": ident}


async def op_unlock(d, req):
    ident = identity_of(d, req)
    d.unlocked[ident] = await check_password(d, ident, req.get("password"))
    store.audit(d.db, "unlock", identity=ident)
    return {"unlocked": ident}


async def op_lock(d, req):
    ident = identity_of(d, req)
    d.unlocked.pop(ident, None)
    store.audit(d.db, "lock", identity=ident)
    return {"locked": ident}


async def op_passwd(d, req):
    ident = identity_of(d, req)
    token, _ = await check_password(d, ident, req.get("old_password"))
    sealed = await asyncio.to_thread(seal.seal, token, req.get("password") or "")
    _, grant_key = await asyncio.to_thread(seal.unseal, sealed, req["password"])
    d.db.execute("UPDATE sender SET sealed=? WHERE identity=?", (sealed, ident))
    resign_grants(d, ident, grant_key)
    if ident in d.unlocked:
        d.unlocked[ident] = (token, grant_key)
    store.audit(d.db, "passwd", identity=ident)
    return {"changed": ident}


async def op_grant(d, req):
    a = d.q1("SELECT * FROM agents WHERE name=?", req["agent"])
    if not a:
        raise ValueError(f"no agent named {req['agent']!r}")
    token, grant_key = await check_password(d, a["identity"], req.get("password"))
    d.db.execute("INSERT OR REPLACE INTO grants VALUES(?,?,?,?)",
                 (a["identity"], a["name"], seal.grant_mac(grant_key, a["identity"], a["name"]), time.time()))
    d.db.execute("DELETE FROM send_requests WHERE agent=?", (a["name"],))
    d.unlocked[a["identity"]] = (token, grant_key)
    store.audit(d.db, "grant", a["name"])
    return {"granted": a["name"]}


async def op_revoke(d, req):
    d.db.execute("DELETE FROM grants WHERE agent=?", (req["agent"],))
    d.db.execute("DELETE FROM send_requests WHERE agent=?", (req["agent"],))
    store.audit(d.db, "revoke", req["agent"])
    return {"revoked": req["agent"]}


# ---------- agents ----------
async def op_request_send(d, req):
    a = await d.touch(req["name"])
    why = (req.get("why") or "").strip()[:500]
    d.db.execute("INSERT OR REPLACE INTO send_requests VALUES(?,?,?)", (a["name"], why, time.time()))
    store.audit(d.db, "request_send", a["name"], why=why)
    d.notify_desktop(f"Agent {a['name']} asks to send Slack messages directly"
                     f"{': ' + why if why else ''}. To allow it, run: doorbell grant {a['name']}")
    return {"requested": True, "next": f"Ask the owner to run `doorbell grant {a['name']}` in their terminal. "
                                       f"Until then, use `doorbell draft`."}


async def open_item(d, req):
    """The open item an agent answers, and where the answer goes. Sending can only reach the
    conversation of an item the agent holds, never an arbitrary channel."""
    a = await d.touch(req["name"])
    it = d.q1("SELECT * FROM items WHERE seq=? AND agent_id=? AND state IN ('pending','claimed')",
              int(req["seq"]), a["id"])
    if not it:
        raise ValueError(f"item {req['seq']} is not an open item of agent {a['name']}")
    conv = d.q1("SELECT kind FROM conv WHERE identity=? AND channel=?", a["identity"], it["channel"])
    kind = conv["kind"] if conv else "channel"
    if it["thread_key"] != "main":
        thread_ts = it["thread_key"]
    elif kind in ("im", "mpim"):
        thread_ts = None
    else:
        thread_ts = it["last_ts"]   # answer a channel message in its thread
    return a, it, thread_ts


def clean_text(d, text):
    text = (text or "").strip()
    if not text:
        raise ValueError("empty message")
    if len(text) > MAX_TEXT:
        raise ValueError(f"message is longer than {MAX_TEXT} characters")
    return text


async def op_draft(d, req):
    text = clean_text(d, req.get("text"))
    a, it, thread_ts = await open_item(d, req)
    cur = d.db.execute("INSERT INTO drafts(agent, identity, item_seq, channel, thread_ts, text, state, created_at) "
                       "VALUES(?,?,?,?,?,?,'pending',?)",
                       (a["name"], a["identity"], it["seq"], it["channel"], thread_ts, text, time.time()))
    did = cur.lastrowid
    d.resolve(it["seq"], "drafted", f"doorbell draft #{did}")
    store.audit(d.db, "draft", a["name"], draft=did, seq=it["seq"])
    who = draft_target(d, a["identity"], it["channel"])
    d.notify_desktop(f"Agent {a['name']} drafted a reply {who}. Review it with: doorbell review")
    return {"draft": did, "acked": [it["seq"]],
            "next": "The owner reviews drafts with `doorbell review`; nothing is posted until they approve."}


def draft_target(d, identity, channel):
    c = d.q1("SELECT kind, other_user, name FROM conv WHERE identity=? AND channel=?", identity, channel)
    if c and c["kind"] == "im" and c["other_user"]:
        u = d.q1("SELECT name, real_name FROM users_cache WHERE user_id=?", c["other_user"])
        return f"to {(u['real_name'] or u['name']) if u else c['other_user']}"
    if c and c["name"]:
        return f"in #{c['name']}"
    return f"in {channel}"


def check_granted(d, a):
    g = d.q1("SELECT mac FROM grants WHERE identity=? AND agent=?", a["identity"], a["name"])
    if not g:
        raise PermissionError(f"agent {a['name']} may not send directly. Write a draft with "
                              f"`doorbell draft <seq> --text ...`, or ask the owner with "
                              f"`doorbell request-send --why \"...\"`.")
    if a["identity"] not in d.unlocked:
        d.notify_desktop(f"Agent {a['name']} tried to send, but Doorbell is locked. Run: doorbell unlock")
        raise PermissionError("Doorbell is locked (it restarted). Write a draft instead; the owner "
                              "runs `doorbell unlock` to allow direct sends again.")
    token, grant_key = d.unlocked[a["identity"]]
    if not seal.grant_ok(grant_key, a["identity"], a["name"], g["mac"]):
        raise PermissionError(f"the send grant for {a['name']} isn't valid; the owner can run `doorbell grant`")
    hour = d.q1("SELECT count(*) n FROM sent WHERE agent=? AND via='send' AND sent_at>?",
                a["name"], time.time() - 3600)["n"]
    if hour >= d.cfg["send_rate_per_hour"]:
        raise PermissionError(f"agent {a['name']} sent {hour} messages in the last hour, the limit; "
                              f"write a draft instead")
    return token


async def post_message(d, identity, token, agent, seq, channel, thread_ts, text, via):
    sl = slack_for(d, token)
    res = await asyncio.to_thread(sl.post, "chat.postMessage", channel=channel,
                                  text=text + (d.cfg["send_footer"] or ""), thread_ts=thread_ts)
    d.db.execute("INSERT OR IGNORE INTO sent VALUES(?,?,?,?,?,?,?)",
                 (identity, channel, res["ts"], agent, seq, via, time.time()))
    return res["ts"]


async def op_send(d, req):
    text = clean_text(d, req.get("text"))
    a, it, thread_ts = await open_item(d, req)
    token = check_granted(d, a)
    ts = await post_message(d, a["identity"], token, a["name"], it["seq"], it["channel"], thread_ts, text, "send")
    d.resolve(it["seq"], "replied", "doorbell send")
    store.audit(d.db, "send", a["name"], seq=it["seq"], channel=it["channel"], ts=ts)
    return {"sent": ts, "channel": it["channel"], "thread_ts": thread_ts, "acked": [it["seq"]]}


async def op_react(d, req):
    emoji = (req.get("emoji") or "").strip().strip(":")
    if not emoji:
        raise ValueError("no emoji given")
    a, it, _ = await open_item(d, req)
    token = check_granted(d, a)
    await asyncio.to_thread(slack_for(d, token).post, "reactions.add", channel=it["channel"],
                            timestamp=it["last_ts"], name=emoji)
    d.resolve(it["seq"], "reacted", f"doorbell react :{emoji}:")
    store.audit(d.db, "react", a["name"], seq=it["seq"], emoji=emoji)
    return {"reacted": emoji, "acked": [it["seq"]]}


# ---------- owner: drafts ----------
def draft_dict(d, r):
    ctx = d.q("SELECT m.user, m.text FROM item_msgs im JOIN messages m ON m.channel=im.channel AND m.ts=im.ts "
              "WHERE im.item_seq=? ORDER BY m.ts DESC LIMIT 3", r["item_seq"])
    return {"id": r["id"], "agent": r["agent"], "state": r["state"], "to": draft_target(d, r["identity"], r["channel"]),
            "channel": r["channel"], "thread_ts": r["thread_ts"], "text": r["text"],
            "created_at": r["created_at"], "sent_ts": r["sent_ts"], "error": r["error"],
            "replying_to": [{"user": m["user"], "text": m["text"]} for m in reversed(ctx)]}


async def op_drafts(d, req):
    states = ("pending",) if not req.get("all") else ("pending", "sent", "rejected", "failed")
    rows = d.q(f"SELECT * FROM drafts WHERE state IN ({','.join('?' * len(states))}) ORDER BY id", *states)
    return {"drafts": [draft_dict(d, r) for r in rows]}


async def op_approve(d, req):
    r = d.q1("SELECT * FROM drafts WHERE id=?", int(req["id"]))
    if not r or r["state"] not in ("pending", "failed"):
        raise ValueError(f"no pending draft #{req['id']}")
    token, grant_key = await check_password(d, r["identity"], req.get("password"))
    text = clean_text(d, req.get("text") or r["text"])
    try:
        ts = await post_message(d, r["identity"], token, r["agent"], r["item_seq"], r["channel"],
                                r["thread_ts"], text, "draft")
    except Exception as e:
        d.db.execute("UPDATE drafts SET state='failed', error=? WHERE id=?", (str(e), r["id"]))
        raise
    d.db.execute("UPDATE drafts SET state='sent', text=?, decided_at=?, sent_ts=?, error=NULL WHERE id=?",
                 (text, time.time(), ts, r["id"]))
    store.audit(d.db, "approve", r["agent"], draft=r["id"], edited=text != r["text"], ts=ts)
    return {"sent": ts, "draft": r["id"]}


async def op_reject(d, req):
    r = d.q1("SELECT * FROM drafts WHERE id=? AND state IN ('pending','failed')", int(req["id"]))
    if not r:
        raise ValueError(f"no pending draft #{req['id']}")
    d.db.execute("UPDATE drafts SET state='rejected', decided_at=? WHERE id=?", (time.time(), r["id"]))
    store.audit(d.db, "reject", r["agent"], draft=r["id"])
    return {"rejected": r["id"]}


def sending_status(d):
    out = {}
    for s in d.q("SELECT identity FROM sender"):
        out[s["identity"]] = {
            "unlocked": s["identity"] in d.unlocked,
            "granted": [g["agent"] for g in d.q("SELECT agent FROM grants WHERE identity=?", s["identity"])],
        }
    return {"sending": out,
            "drafts_pending": d.q1("SELECT count(*) n FROM drafts WHERE state='pending'")["n"],
            "send_requests": [{"agent": r["agent"], "why": r["why"]} for r in d.q("SELECT * FROM send_requests")]}


OPS = {"sender_add": op_sender_add, "sender_remove": op_sender_remove, "unlock": op_unlock, "lock": op_lock,
       "passwd": op_passwd, "grant": op_grant, "revoke": op_revoke, "request_send": op_request_send,
       "draft": op_draft, "send": op_send, "react": op_react, "drafts": op_drafts, "approve": op_approve,
       "reject": op_reject}

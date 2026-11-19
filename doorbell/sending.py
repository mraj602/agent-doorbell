"""Optional sending through Doorbell.

The watcher's token only reads. To let agents answer through Doorbell, the owner adds a second
token with chat:write (`doorbell sender add`), sealed with a password (see seal.py). Then:

- Any agent may write a **draft** for one of its open items. Drafts stay in Doorbell, not in
  Slack, until the owner approves them with the password (`doorbell review` / `approve`).
- An agent the owner **granted** with the password may **send** directly, but only into the
  conversation of one of its own open items, within a rate limit, and only while Doorbell is
  **unlocked** (the decrypted token is held in memory, so after a restart the owner runs
  `doorbell unlock`).

Operations that add or replace power (sender add and remove, unlock, grant, approve, passwd)
need the password. Operations that only take power away (lock, revoke, reject) don't.

A grant is bound to the agent's registration and subscription: if either changes (re-register,
`doorbell sub`, deregister), the grant stops working until the owner grants again. The daemon
can't tell which process is calling, so a grant works for any caller that names that agent;
what it bounds is where messages can go.
"""
import asyncio
import hashlib
import json
import os
import re
import time

from . import seal, store
from . import oauth
from .slackapi import Slack, creds_from_text, read_creds, read_token

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


def recent_failures(d, identity):
    """Wrong passwords in the last hour since the last right one."""
    like = f'%"{identity}"%'
    ok = d.q1("SELECT max(ts) t FROM audit WHERE kind='password_ok' AND detail LIKE ?", like)["t"] or 0
    return d.q1("SELECT count(*) n FROM audit WHERE kind='bad_password' AND ts>? AND detail LIKE ?",
                max(ok, time.time() - 3600), like)["n"]


def creds_of(plain):
    """Sealed plaintext as credentials: a JSON object since 0.3, a bare token before."""
    return creds_from_text(plain) or {"v": 1, "token": plain, "refresh_token": None, "expires_at": None}


def plain_of(creds):
    return json.dumps(creds) if creds.get("refresh_token") or creds.get("client_id") else creds["token"]


async def check_password(d, identity, password):
    """(token, grant key) when the password is right."""
    creds, _, grant_key = await open_with_password(d, identity, password)
    return creds["token"], grant_key


async def open_with_password(d, identity, password):
    """(credentials, encryption key, grant key). One check at a time, slower after each recent
    failure (2 s, 4 s, ... up to 60 s), and the owner is notified of repeated failures, so
    guessing is slow and visible."""
    row = sender_row(d, identity)
    if not row:
        raise ValueError("sending isn't set up; run `doorbell sender add --token-file <file>`")
    async with d.password_lock:
        fails = recent_failures(d, identity)
        if fails:
            await asyncio.sleep(min(2 ** fails, 60))
        try:
            plain, enc, grant_key = await asyncio.to_thread(seal.open_sealed, row["sealed"], password or "")
            store.audit(d.db, "password_ok", identity=identity)
            return creds_of(plain), enc, grant_key
        except seal.BadPassword:
            store.audit(d.db, "bad_password", identity=identity)
            if fails + 1 in (3, 10, 30, 100):
                d.notify_desktop(f"{fails + 1} wrong sending passwords in the last hour. If that wasn't "
                                 f"you, run: doorbell lock")
            raise PermissionError("wrong password") from None


def grant_subject(a):
    """What a grant covers: this registration of the agent with this subscription."""
    sub = hashlib.sha256((a["sub_json"] or "{}").encode()).hexdigest()[:32]
    return f"{a['name']}#{a['id']}#{sub}"


def sign_grant(d, a, grant_key):
    d.db.execute("INSERT OR REPLACE INTO grants VALUES(?,?,?,?)",
                 (a["identity"], a["name"], seal.grant_mac(grant_key, a["identity"], grant_subject(a)), time.time()))


def slack_for(d, token):
    return Slack(token, d.cfg["slack_api_base"])


def set_unlocked(d, ident, creds, enc, grant_key):
    """Hold the decrypted sender in memory. In single-token mode this also resumes watching."""
    d.unlocked[ident] = (creds["token"], grant_key)
    d.sealed_keys[ident] = (enc, creds)
    d.slacks.pop(ident, None)
    if d.db.execute("UPDATE identities SET status='ok', error=NULL WHERE user_id=? AND mode='sealed' "
                    "AND status='locked'", (ident,)).rowcount:
        store.audit(d.db, "watch_resumed", identity=ident)


def set_locked(d, ident):
    d.unlocked.pop(ident, None)
    d.sealed_keys.pop(ident, None)
    d.slacks.pop(ident, None)
    d.db.execute("UPDATE identities SET status='locked' WHERE user_id=? AND mode='sealed' AND status='ok'", (ident,))


async def renew_sealed(d, ident, force=False):
    """Renew an unlocked sealed token that is about to expire, and re-seal it without the
    password (the encryption key is in memory while unlocked). Returns False if it failed."""
    if ident not in d.sealed_keys:
        return True
    enc, creds = d.sealed_keys[ident]
    if not force and not (creds.get("expires_at") and creds["expires_at"] - time.time() < d.cfg["renew_before"]):
        return True
    try:
        new = await asyncio.to_thread(oauth.refresh, d.cfg["slack_api_base"], creds)
    except oauth.NetworkError as e:
        store.audit(d.db, "renew_error", identity=ident, error=str(e))
        return True   # try again on the next tick
    except oauth.LoginError as e:
        store.audit(d.db, "renew_failed", identity=ident, error=str(e))
        if d.q1("SELECT 1 FROM identities WHERE user_id=? AND mode='sealed'", ident):
            d.needs_login(ident, f"renewing the sign-in failed: {e}", "doorbell login --for both")
        else:
            d.notify_desktop(f"Renewing the sending token for {ident} failed ({e}). Sign in again with: "
                             f"doorbell login --for send")
        set_locked(d, ident)
        return False
    row = sender_row(d, ident)
    d.db.execute("UPDATE sender SET sealed=? WHERE identity=?", (seal.reseal(row["sealed"], enc, json.dumps(new)), ident))
    set_unlocked(d, ident, new, enc, d.unlocked[ident][1])
    store.audit(d.db, "token_renewed", identity=ident, sealed=True)
    return True


async def validate_sender(d, token, need_read=False):
    sl = slack_for(d, token)
    info = await asyncio.to_thread(sl.call, "auth.test")
    if sl.scopes is None:
        raise ValueError("Slack didn't report the token's scopes; can't check it can send")
    need = SEND_SCOPES | (set(oauth.READ_SCOPES) if need_read else set())
    missing = sorted(need - set(sl.scopes))
    if missing:
        raise ValueError(f"the token is missing scopes {missing}; add them under User Token Scopes on the app")
    return info, sl.scopes


# ---------- owner: setup, lock state, grants ----------
async def op_sender_add(d, req):
    if req.get("token_json"):
        creds, path = creds_of(req["token_json"]), None
    else:
        path = os.path.expanduser(req["token_file"])
        if os.name != "nt" and os.stat(path).st_mode & 0o077:
            raise ValueError(f"{path} is readable by other users; run: chmod 600 {path}")
        creds = read_creds(path) or creds_of(read_token(path))
    token = creds["token"]
    for w in d.q("SELECT token_file FROM identities WHERE token_file IS NOT NULL"):
        try:
            same = read_token(os.path.expanduser(w["token_file"])) == token
        except OSError:
            same = False
        if same:
            raise ValueError("this is the watcher's token; sending needs its own token "
                             "(slack-app-manifest-sender.yaml)")
    info, scopes = await validate_sender(d, token)
    ident = info["user_id"]
    idrow = d.q1("SELECT mode FROM identities WHERE user_id=?", ident)
    if not idrow:
        raise ValueError(f"this token belongs to {ident}, which has no watcher identity; "
                         f"run `doorbell login --for watch` (or `doorbell identity add`) first")
    if idrow["mode"] == "sealed":
        raise ValueError(f"{ident} is in single-token mode (one token watches and sends); to change its "
                         f"token run `doorbell login --for both`")
    old_key = None
    if sender_row(d, ident):   # replacing the token needs the current password
        _, old_key = await check_password(d, ident, req.get("old_password"))
    sealed = await asyncio.to_thread(seal.seal, plain_of(creds), req.get("password") or "")
    _, enc, grant_key = await asyncio.to_thread(seal.open_sealed, sealed, req["password"])
    d.db.execute("INSERT OR REPLACE INTO sender VALUES(?,?,?,?)", (ident, sealed, ",".join(scopes), time.time()))
    resign_grants(d, ident, old_key, grant_key)
    set_unlocked(d, ident, creds, enc, grant_key)
    store.audit(d.db, "sender_add", identity=ident)
    return {"identity": ident, "unlocked": True, "can_react": "reactions:write" in scopes,
            "note": f"The token is now sealed in Doorbell. You can delete {path}." if path else
                    "The token is sealed in Doorbell; it was never written to a file."}


async def op_login_both(d, req):
    """Single-token mode: one token with read scopes and chat:write, sealed with the password,
    watches and sends. Watching pauses while Doorbell is locked (after a restart) and resumes
    from the cursors at `doorbell unlock`, so nothing is missed."""
    creds = creds_of(req["token_json"])
    info, scopes = await validate_sender(d, creds["token"], need_read=True)
    ident = info["user_id"]
    old_key = None
    if sender_row(d, ident):
        _, old_key = await check_password(d, ident, req.get("old_password"))
    sealed = await asyncio.to_thread(seal.seal, plain_of(creds), req.get("password") or "")
    _, enc, grant_key = await asyncio.to_thread(seal.open_sealed, sealed, req["password"])
    retired = await retire_file_token(d, ident)
    d.db.execute("INSERT OR REPLACE INTO sender VALUES(?,?,?,?)", (ident, sealed, ",".join(scopes), time.time()))
    d.db.execute("INSERT OR REPLACE INTO identities(user_id, team_id, team_url, token_file, status, error, added_at, "
                 "mode) VALUES(?,?,?,NULL,'ok',NULL,?,'sealed')", (ident, info["team_id"], info["url"], time.time()))
    resign_grants(d, ident, old_key, grant_key)
    set_unlocked(d, ident, creds, enc, grant_key)
    store.audit(d.db, "login_both", identity=ident)
    return {"identity": ident, "user": info["user"], "team": info["team"], "mode": "sealed", "unlocked": True,
            "can_react": "reactions:write" in scopes, **({"retired": retired} if retired else {}),
            "note": "One token watches and sends; it is sealed with your password and never written to a file. "
                    "After Doorbell restarts, watching waits for `doorbell unlock`, then catches up."}


def login_file(d, path):
    """True for a credentials file `doorbell login` wrote (Doorbell owns it and may delete it)."""
    from . import config
    try:
        return os.path.dirname(os.path.realpath(path)) == os.path.realpath(config.HOME / "credentials")
    except (OSError, TypeError):
        return False


async def retire_file_token(d, ident):
    """When an identity moves to single-token mode, its old file token (from `doorbell login`)
    could now post too: revoke it and delete the file. Token files the person made stay."""
    row = d.q1("SELECT token_file, mode FROM identities WHERE user_id=?", ident)
    if not row or row["mode"] != "file" or not row["token_file"]:
        return None
    path = os.path.expanduser(row["token_file"])
    if not login_file(d, path):
        return f"{path} is no longer used; delete it if nothing else needs it (it may be able to post now)"
    try:
        creds = read_creds(path)
        if creds:
            await asyncio.to_thread(oauth.revoke, d.cfg["slack_api_base"], creds)
        os.unlink(path)
    except OSError:
        pass
    d.slacks.pop(ident, None)
    return "the old watch token was revoked and its file deleted"


async def op_logout(d, req):
    """Revoke Doorbell's tokens for an identity and forget them. Agents and cursors stay, so
    `doorbell login` later picks up where watching stopped."""
    ident = identity_of(d, req)
    row = d.q1("SELECT * FROM identities WHERE user_id=?", ident)
    if not row:
        raise ValueError(f"no identity {ident}")
    done = []
    if sender_row(d, ident):
        creds, _, _ = await open_with_password(d, ident, req.get("password"))
        await asyncio.to_thread(oauth.revoke, d.cfg["slack_api_base"], creds)
        d.db.execute("DELETE FROM sender WHERE identity=?", (ident,))
        d.db.execute("DELETE FROM grants WHERE identity=?", (ident,))
        set_locked(d, ident)
        done.append("sending token revoked")
    if row["mode"] == "file" and row["token_file"]:
        path = os.path.expanduser(row["token_file"])
        try:
            creds = read_creds(path) or creds_of(read_token(path))
            await asyncio.to_thread(oauth.revoke, d.cfg["slack_api_base"], creds)
            done.append("watch token revoked")
            if login_file(d, path):
                os.unlink(path)
        except (OSError, ValueError):
            pass
    d.db.execute("UPDATE identities SET status='needs_login', error='signed out; run `doorbell login`', "
                 "token_file=NULL WHERE user_id=?", (ident,))
    d.slacks.pop(ident, None)
    store.audit(d.db, "logout", identity=ident)
    return {"signed_out": ident, "done": done,
            "note": "Slack keeps what you allowed this app while any of your tokens for it is alive (for example "
                    "in another tool). To get a read-only sign-in, revoke those too, then run `doorbell login`."}


def resign_grants(d, ident, old_key, new_key):
    """Carry valid grants over to a new key; drop any that don't verify under the old one."""
    for g in d.q("SELECT g.agent, g.mac, a.* FROM grants g LEFT JOIN agents a ON a.name=g.agent "
                 "WHERE g.identity=?", ident):
        if old_key and g["id"] is not None and seal.grant_ok(old_key, ident, grant_subject(g), g["mac"]):
            d.db.execute("UPDATE grants SET mac=? WHERE identity=? AND agent=?",
                         (seal.grant_mac(new_key, ident, grant_subject(g)), ident, g["agent"]))
        else:
            d.db.execute("DELETE FROM grants WHERE identity=? AND agent=?", (ident, g["agent"]))


async def op_sender_remove(d, req):
    ident = identity_of(d, req)
    await check_password(d, ident, req.get("password"))   # else removing then re-adding would reset it
    if d.q1("SELECT 1 FROM identities WHERE user_id=? AND mode='sealed'", ident):
            raise ValueError("in single-token mode this token is also the watcher's. Switch watching to its own "
                         "read-only token first (`doorbell login --for watch`), then remove the sender")
    d.db.execute("DELETE FROM sender WHERE identity=?", (ident,))
    d.db.execute("DELETE FROM grants WHERE identity=?", (ident,))
    d.unlocked.pop(ident, None)
    store.audit(d.db, "sender_remove", identity=ident)
    return {"removed": ident}


async def op_unlock(d, req):
    ident = identity_of(d, req)
    creds, enc, grant_key = await open_with_password(d, ident, req.get("password"))
    set_unlocked(d, ident, creds, enc, grant_key)
    renewed = await renew_sealed(d, ident)   # it may have expired while locked
    store.audit(d.db, "unlock", identity=ident)
    if not renewed:
        raise ValueError("the password is right, but the stored sign-in has expired and couldn't be renewed; "
                         "run `doorbell login` again")
    sealed_mode = d.q1("SELECT 1 FROM identities WHERE user_id=? AND mode='sealed'", ident)
    return {"unlocked": ident, **({"watching": "resumed; catching up from where it stopped"} if sealed_mode else {})}


async def op_lock(d, req):
    ident = identity_of(d, req)
    set_locked(d, ident)
    store.audit(d.db, "lock", identity=ident)
    sealed_mode = d.q1("SELECT 1 FROM identities WHERE user_id=? AND mode='sealed'", ident)
    return {"locked": ident, **({"watching": "paused until `doorbell unlock` (single-token mode)"}
                                if sealed_mode else {})}


async def op_passwd(d, req):
    ident = identity_of(d, req)
    creds, _, old_key = await open_with_password(d, ident, req.get("old_password"))
    sealed = await asyncio.to_thread(seal.seal, plain_of(creds), req.get("password") or "")
    _, enc, grant_key = await asyncio.to_thread(seal.open_sealed, sealed, req["password"])
    d.db.execute("UPDATE sender SET sealed=? WHERE identity=?", (sealed, ident))
    resign_grants(d, ident, old_key, grant_key)
    if ident in d.unlocked:
        set_unlocked(d, ident, creds, enc, grant_key)
    store.audit(d.db, "passwd", identity=ident)
    return {"changed": ident}


async def op_grant(d, req):
    a = d.q1("SELECT * FROM agents WHERE name=?", req["agent"])
    if not a:
        raise ValueError(f"no agent named {req['agent']!r}")
    creds, enc, grant_key = await open_with_password(d, a["identity"], req.get("password"))
    a = d.q1("SELECT * FROM agents WHERE id=?", a["id"])   # as it is now, after the password wait
    sign_grant(d, a, grant_key)
    d.db.execute("DELETE FROM send_requests WHERE agent=?", (a["name"],))
    if a["identity"] not in d.unlocked:
        set_unlocked(d, a["identity"], creds, enc, grant_key)
    store.audit(d.db, "grant", a["name"], sub=json_sub(a))
    return {"granted": a["name"], "listens_to": json_sub(a),
            "note": "The grant covers this subscription. If the agent's subscription changes, grant again."}


def json_sub(a):
    return json.loads(a["sub_json"] or "{}")


async def op_revoke(d, req):
    d.db.execute("DELETE FROM grants WHERE agent=?", (req["agent"],))
    d.db.execute("DELETE FROM send_requests WHERE agent=?", (req["agent"],))
    store.audit(d.db, "revoke", req["agent"])
    return {"revoked": req["agent"]}


# ---------- agents ----------
async def op_request_send(d, req):
    a = await d.touch(req["name"])
    why = one_line(req.get("why"))[:300]
    d.db.execute("INSERT OR REPLACE INTO send_requests VALUES(?,?,?)", (a["name"], why, time.time()))
    store.audit(d.db, "request_send", a["name"], why=why)
    d.notify_desktop(f"Agent {a['name']} asks to send Slack messages directly. Its reason: "
                     f"\"{why}\". Check with `doorbell status` before running `doorbell grant`.")
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


CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
BROADCAST = re.compile(r"<!(channel|here|everyone)(\|[^>]*)?>")


def one_line(s):
    return CONTROL.sub(" ", (s or "").replace("\n", " ").replace("\t", " ")).strip()


def clean_text(d, text):
    text = CONTROL.sub("", (text or "").replace("\r\n", "\n")).strip()
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
    if not seal.grant_ok(grant_key, a["identity"], grant_subject(a), g["mac"]):
        raise PermissionError(f"the send grant for {a['name']} no longer matches its registration or "
                              f"subscription (they changed since the grant). Write a draft; the owner "
                              f"can run `doorbell grant {a['name']}` again.")
    hour = d.q1("SELECT count(*) n FROM audit WHERE agent=? AND kind IN ('send','react') AND ts>?",
                a["name"], time.time() - 3600)["n"]
    if hour >= d.cfg["send_rate_per_hour"]:
        raise PermissionError(f"agent {a['name']} sent or reacted {hour} times in the last hour, the limit; "
                              f"write a draft instead")
    return token


async def post_message(d, identity, token, agent, seq, channel, thread_ts, text, via):
    sl = slack_for(d, token)
    d.sending_to.add((identity, channel))
    try:
        res = await asyncio.to_thread(sl.post, "chat.postMessage", channel=channel,
                                      text=text + (d.cfg["send_footer"] or ""), thread_ts=thread_ts)
        d.db.execute("INSERT OR IGNORE INTO sent VALUES(?,?,?,?,?,?,?)",
                     (identity, channel, res["ts"], agent, seq, via, time.time()))
    finally:
        d.sending_to.discard((identity, channel))
    return res["ts"]


async def op_send(d, req):
    # Agents can't @channel, @here or @everyone; drafts you approve can.
    text = BROADCAST.sub(lambda m: "@" + m.group(1), clean_text(d, req.get("text")))
    async with d.send_lock:   # checks, post and resolve as one step: no double sends, no rate races
        a, it, thread_ts = await open_item(d, req)
        token = check_granted(d, a)
        ts = await post_message(d, a["identity"], token, a["name"], it["seq"], it["channel"], thread_ts, text,
                                "send")
        d.resolve(it["seq"], "replied", "doorbell send")
        store.audit(d.db, "send", a["name"], seq=it["seq"], channel=it["channel"], ts=ts)
    return {"sent": ts, "channel": it["channel"], "thread_ts": thread_ts, "acked": [it["seq"]]}


async def op_react(d, req):
    emoji = (req.get("emoji") or "").strip().strip(":")
    if not emoji:
        raise ValueError("no emoji given")
    if not re.fullmatch(r"[a-z0-9_+'-]{1,100}", emoji):
        raise ValueError(f"not an emoji name: {emoji!r}")
    async with d.send_lock:
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
    states = ("pending", "failed") if not req.get("all") else ("pending", "failed", "sending", "sent", "rejected")
    rows = d.q(f"SELECT * FROM drafts WHERE state IN ({','.join('?' * len(states))}) ORDER BY id", *states)
    return {"drafts": [draft_dict(d, r) for r in rows]}


async def op_approve(d, req):
    r = d.q1("SELECT * FROM drafts WHERE id=?", int(req["id"]))
    if not r or r["state"] not in ("pending", "failed"):
        raise ValueError(f"no pending draft #{req['id']}")
    creds, enc, grant_key = await open_with_password(d, r["identity"], req.get("password"))
    if creds.get("expires_at") and creds["expires_at"] - time.time() < 60:
        if r["identity"] not in d.unlocked:
            set_unlocked(d, r["identity"], creds, enc, grant_key)
        if not await renew_sealed(d, r["identity"], force=True):
            raise ValueError("the sending token has expired and couldn't be renewed; run `doorbell login` again")
        creds = d.sealed_keys[r["identity"]][1]
    token = creds["token"]
    text = clean_text(d, req.get("text") or r["text"])
    # Claim the draft before posting, so two approvals can't both send it.
    if not d.db.execute("UPDATE drafts SET state='sending' WHERE id=? AND state IN ('pending','failed')",
                        (r["id"],)).rowcount:
        raise ValueError(f"draft #{r['id']} was already handled")
    try:
        ts = await post_message(d, r["identity"], token, r["agent"], r["item_seq"], r["channel"],
                                r["thread_ts"], text, "draft")
    except Exception as e:
        d.db.execute("UPDATE drafts SET state='failed', error=? WHERE id=?",
                     (f"{e} (check Slack before approving again: a timeout can mean it was posted)", r["id"]))
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


OPS = {"sender_add": op_sender_add, "login_both": op_login_both, "logout": op_logout, "sender_remove": op_sender_remove, "unlock": op_unlock, "lock": op_lock,
       "passwd": op_passwd, "grant": op_grant, "revoke": op_revoke, "request_send": op_request_send,
       "draft": op_draft, "send": op_send, "react": op_react, "drafts": op_drafts, "approve": op_approve,
       "reject": op_reject}

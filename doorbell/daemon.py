"""doorbelld: polls Slack only for registered, live agents and queues matching messages per agent.

The daemon never posts to Slack and never calls an LLM. Agents collect their queue over the
local TCP connection (see server.py) and reply through their own Slack tools.

Agent lifecycle: active (a `doorbell wait` is connected) -> detached (no waiter, still polled)
-> inactive (no contact for idle_ttl: not polled, queue + checkpoint kept) -> gone (purged).
No live agents => DORMANT: zero Slack API calls.
"""
import asyncio
import json
import os
import signal
import secrets
import shutil
import subprocess
import time
from collections import deque

from . import config, oauth, store
from .slackapi import RateLimited, Slack, SlackError, read_creds, read_token

LIVE = ("active", "detached")
FATAL_ERRORS = {"invalid_auth", "token_revoked", "account_inactive", "not_authed", "missing_scope"}


class Daemon:
    def __init__(self):
        self.cfg = config.load()
        config.HOME.mkdir(parents=True, exist_ok=True)
        self.db = store.connect(config.DB)
        self.waiters = {}          # agent_id -> number of connected `wait` clients
        self.events = {}           # agent_id -> asyncio.Event
        self.calls = deque()       # timestamps of recent API calls (rate bucket)
        self.pause_until = 0
        self.slacks = {}
        self.unlocked = {}   # identity -> (sender token, grant key); memory only, gone on restart
        self.sealed_keys = {}  # identity -> (encryption key, credentials) while unlocked, for renewal
        self.force_renew = set()       # identities whose token Slack reported expired
        # Single-token mode: the watch token is sealed, so after a restart watching waits for `unlock`.
        if self.db.execute("UPDATE identities SET status='locked' WHERE mode='sealed' AND status='ok'").rowcount:
            self.notify_desktop("Doorbell restarted and is locked; watching resumes when you run: doorbell unlock")
        self.sending_to = set()        # (identity, channel) while a post is in flight
        self.send_lock = asyncio.Lock()      # one send at a time: checks and the post are atomic
        self.password_lock = asyncio.Lock()  # one password check at a time (scrypt is 64 MB each)
        self.last_convlist = {}
        self.last_usergroups = {}
        self.last_userlist = {}
        self.polling = set()       # (identity, channel) currently in the poll set
        self.started = time.time()
        self.api_total = 0
        self.budget_interval = {}  # identity -> seconds needed to poll its whole set within ~75% of the budget

    # ---------- small helpers ----------
    def q(self, sql, *a):
        return self.db.execute(sql, a).fetchall()

    def q1(self, sql, *a):
        return self.db.execute(sql, a).fetchone()

    def notify_desktop(self, msg):
        store.audit(self.db, "alert", msg=msg)
        if not self.cfg["notify"]:
            return
        if self.cfg["notify_command"]:
            cmd = [*self.cfg["notify_command"], msg]
        elif shutil.which("notify-send"):
            cmd = ["notify-send", "doorbell", msg]
        elif shutil.which("osascript"):
            cmd = ["osascript", "-e", f"display notification {json.dumps(msg)} with title \"doorbell\""]
        elif os.name == "nt":
            ps = ("[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]"
                  " > $null; $x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(1);"
                  " $x.GetElementsByTagName('text')[0].AppendChild($x.CreateTextNode($env:DOORBELL_MSG)) > $null;"
                  " [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('doorbell')"
                  ".Show([Windows.UI.Notifications.ToastNotification]::new($x))")
            subprocess.Popen(["powershell", "-NoProfile", "-Command", ps], env=dict(os.environ, DOORBELL_MSG=msg),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        else:
            return
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wake(self, agent_id):
        ev = self.events.get(agent_id)
        if ev:
            ev.set()

    def slack(self, identity):
        if identity not in self.slacks:
            row = self.q1("SELECT token_file, mode FROM identities WHERE user_id=?", identity)
            if row["mode"] == "sealed":
                if identity not in self.unlocked:
                    raise SlackError("token", "locked")
                token = self.unlocked[identity][0]
            else:
                token = read_token(os.path.expanduser(row["token_file"]))
            self.slacks[identity] = Slack(token, self.cfg["slack_api_base"])
        return self.slacks[identity]

    def check_file_scopes(self, identity, sl):
        """A token `doorbell login` saved in a file must stay read-only. Slack adds up what a person
        allowed one app, so a later approval of chat:write (by any tool) makes it able to post:
        then stop using the file and ask for a sealed sign-in."""
        if not sl.scopes or "chat:write" not in sl.scopes or getattr(sl, "checked_post", False):
            return
        sl.checked_post = True
        row = self.q1("SELECT mode, token_file FROM identities WHERE user_id=?", identity)
        if row and row["mode"] == "file" and row["token_file"]:
            try:
                login_made = read_creds(os.path.expanduser(row["token_file"])) is not None
            except OSError:
                login_made = False
            if login_made:
                self.needs_login(identity, "the watch token can now post messages (chat:write was allowed for "
                                           "this app), so Doorbell stopped keeping it in a file")

    def needs_login(self, identity, why, command="doorbell login"):
        """The sign-in can't be renewed. Watching stops (cursors stay) until the person signs in again."""
        if self.db.execute("UPDATE identities SET status='needs_login', error=? WHERE user_id=? "
                           "AND status!='needs_login'", (f"{why}; run `{command}`", identity)).rowcount:
            self.notify_desktop(f"Slack sign-in for {identity} needs renewing ({why}). Run: {command}")
        self.slacks.pop(identity, None)

    async def renew(self):
        """Renew sign-ins from `doorbell login` before they expire (only tokens Slack issues with
        an expiry; most user tokens don't expire)."""
        soon = time.time() + self.cfg["renew_before"]
        for r in self.q("SELECT user_id, token_file FROM identities WHERE mode='file' AND status='ok'"):
            path = os.path.expanduser(r["token_file"])
            try:
                creds = read_creds(path)
            except OSError:
                continue
            forced = r["user_id"] in self.force_renew
            if not creds or not creds.get("expires_at") or (creds["expires_at"] > soon and not forced):
                continue
            self.force_renew.discard(r["user_id"])
            try:
                new = await asyncio.to_thread(oauth.refresh, self.cfg["slack_api_base"], creds)
            except oauth.NetworkError as e:
                store.audit(self.db, "renew_error", identity=r["user_id"], error=str(e))
                continue
            except oauth.LoginError as e:
                store.audit(self.db, "renew_failed", identity=r["user_id"], error=str(e))
                self.needs_login(r["user_id"], f"renewing the sign-in failed: {e}")
                continue
            oauth.write_creds(path, new)
            self.slacks.pop(r["user_id"], None)
            store.audit(self.db, "token_renewed", identity=r["user_id"])
        from .sending import renew_sealed
        for ident in list(self.sealed_keys):
            await renew_sealed(self, ident, force=ident in self.force_renew)
            self.force_renew.discard(ident)

    async def api(self, identity, method, **params):
        while True:
            now = time.time()
            if now < self.pause_until:
                await asyncio.sleep(self.pause_until - now)
                continue
            while self.calls and self.calls[0] < now - 60:
                self.calls.popleft()
            if len(self.calls) < self.cfg["rate_per_min"]:
                break
            await asyncio.sleep(self.calls[0] + 60 - now + 0.05)
        self.calls.append(time.time())
        self.api_total += 1
        try:
            sl = self.slack(identity)
            d = await asyncio.to_thread(sl.call, method, **params)
            self.db.execute("INSERT INTO api_calls VALUES(?,?,1)", (time.time(), method))
            self.check_file_scopes(identity, sl)
            return d
        except RateLimited as e:
            self.pause_until = time.time() + e.retry_after
            self.db.execute("INSERT INTO api_calls VALUES(?,?,0)", (time.time(), method))
            raise
        except SlackError as e:
            self.db.execute("INSERT INTO api_calls VALUES(?,?,0)", (time.time(), method))
            if e.error == "token_expired":
                self.force_renew.add(identity)
                self.slacks.pop(identity, None)
            elif e.error in FATAL_ERRORS:
                self.db.execute("UPDATE identities SET status='broken', error=? WHERE user_id=?",
                                (str(e), identity))
                self.notify_desktop(f"Slack token for {identity} failed: {e}")
            raise

    def live_agents(self, identity=None):
        sql = "SELECT * FROM agents WHERE status IN ('active','detached')"
        return self.q(sql + " AND identity=?", identity) if identity else self.q(sql)

    # ---------- name resolution ----------
    async def refresh_users(self, identity, force=False):
        if not force and time.time() - self.last_userlist.get(identity, 0) < 600:
            return
        self.last_userlist[identity] = time.time()
        members = await asyncio.to_thread(self.slack(identity).paged, "users.list", "members", limit=200)
        self.calls.append(time.time())
        for u in members:
            p = u.get("profile", {})
            self.db.execute("INSERT OR REPLACE INTO users_cache VALUES(?,?,?,?,?,?)",
                            (u["id"], u.get("name"), u.get("real_name") or p.get("real_name"),
                             p.get("display_name"), int(bool(u.get("is_bot"))), time.time()))

    async def user_name(self, identity, uid):
        if not uid:
            return None
        r = self.q1("SELECT real_name, name FROM users_cache WHERE user_id=?", uid)
        if not r:
            try:
                u = (await self.api(identity, "users.info", user=uid))["user"]
                p = u.get("profile", {})
                self.db.execute("INSERT OR REPLACE INTO users_cache VALUES(?,?,?,?,?,?)",
                                (uid, u.get("name"), u.get("real_name") or p.get("real_name"),
                                 p.get("display_name"), int(bool(u.get("is_bot"))), time.time()))
                return u.get("real_name") or u.get("name")
            except Exception:
                return uid
        return r["real_name"] or r["name"]

    def lookup_user(self, ref):
        key = ref.lstrip("@").lower()
        r = self.q1("SELECT user_id FROM users_cache WHERE lower(name)=? OR lower(display_name)=? "
                    "OR lower(real_name)=?", key, key, key)
        return r["user_id"] if r else None

    async def resolve_users(self, identity, refs, strict=False):
        out, missing = set(), []
        for ref in refs or []:
            if ref[:1] in "UWB" and ref[1:].isalnum() and ref.upper() == ref:
                out.add(ref)
            else:
                uid = self.lookup_user(ref)
                if not uid:
                    await self.refresh_users(identity, force=strict)
                    uid = self.lookup_user(ref)
                (out.add(uid) if uid else missing.append(ref))
        if strict and missing:
            raise ValueError(f"unknown users: {missing}")
        return out

    async def usergroup_members(self, identity, refs, strict=False):
        if not refs:
            return set()
        if time.time() - self.last_usergroups.get(identity, 0) > self.cfg["usergroups_every"]:
            self.last_usergroups[identity] = time.time()
            groups = (await self.api(identity, "usergroups.list", include_users="true"))["usergroups"]
            for g in groups:
                self.db.execute("INSERT OR REPLACE INTO usergroups_cache VALUES(?,?,?,?)",
                                (g["id"], g.get("handle"), json.dumps(g.get("users", [])), time.time()))
        out = set()
        for ref in refs:
            key = ref.lstrip("@").lower()
            r = self.q1("SELECT members FROM usergroups_cache WHERE group_id=? OR lower(handle)=?", ref, key)
            if r:
                out |= set(json.loads(r["members"]))
            elif strict:
                raise ValueError(f"unknown usergroup: {ref}")
        return out

    def resolve_channels(self, identity, refs, strict=False):
        out = set()
        for ref in refs or []:
            if ref.startswith("#"):
                r = self.q1("SELECT channel FROM conv WHERE identity=? AND name=?", identity, ref[1:])
                if r:
                    out.add(r["channel"])
                elif strict:
                    raise ValueError(f"unknown channel or not a member: {ref}")
            else:
                out.add(ref)
        return out

    async def view(self, agent):
        """Resolve an agent's subscription into concrete IDs."""
        s = json.loads(agent["sub_json"] or "{}")
        ident = agent["identity"]
        followed = await self.resolve_users(ident, s.get("users"))
        followed |= await self.usergroup_members(ident, s.get("usergroups"))
        allow_bots = set(s.get("allow_bots") or [])
        return {
            "followed": followed,
            "dm_mode": s.get("dms", "followed"),
            "group_dms": bool(s.get("group_dms", False)),
            "channels": self.resolve_channels(ident, s.get("channels")),
            "everyone_in": self.resolve_channels(ident, s.get("everyone_in")),
            "mentions": bool(s.get("mentions_of_me", True)),
            "threads": bool(s.get("threads_im_in", True)),
            "allow_bots": allow_bots,
        }

    # ---------- conversation list + poll set ----------
    async def refresh_convlist(self, identity):
        first = not self.q1("SELECT 1 FROM conv WHERE identity=? LIMIT 1", identity)
        self.last_convlist[identity] = time.time()
        chans = []
        cursor = None
        while True:
            d = await self.api(identity, "users.conversations", limit=200, exclude_archived="true",
                               types="im,mpim,public_channel,private_channel", cursor=cursor)
            chans += d["channels"]
            cursor = (d.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        # A conversation that appears after the first load is new: read it from slightly
        # before this refresh so its first messages aren't skipped.
        new_cursor = None if first else f"{time.time() - 2 * self.cfg['convlist_every']:.6f}"
        for c in chans:
            kind = "im" if c.get("is_im") else "mpim" if c.get("is_mpim") else \
                "private" if c.get("is_private") else "channel"
            self.db.execute(
                "INSERT INTO conv(identity, channel, kind, other_user, name, cursor) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(identity, channel) DO UPDATE SET name=excluded.name",
                (identity, c["id"], kind, c.get("user"), c.get("name"), new_cursor))

    async def poll_set(self, identity, agents):
        convs = {r["channel"]: r for r in self.q("SELECT * FROM conv WHERE identity=?", identity)}
        out = set()
        for a in agents:
            v = await self.view(a)
            for ch, c in convs.items():
                if c["kind"] == "im" and (v["dm_mode"] == "all" or
                                          (v["dm_mode"] == "followed" and c["other_user"] in v["followed"])):
                    out.add(ch)
                elif c["kind"] == "mpim" and v["group_dms"]:
                    out.add(ch)
            out |= {ch for ch in v["channels"] | v["everyone_in"] if ch in convs}
        return out

    # ---------- polling ----------
    def cadence(self, last_activity, identity=None):
        """Every followed conversation is polled at cadence_hot while the rate budget allows it.
        Only when the poll set is too big for the budget do quieter conversations slow down."""
        age = time.time() - (last_activity or 0)
        if age < 3600:
            return self.cfg["cadence_hot"]
        fits = self.budget_interval.get(identity, 0)
        base = self.cfg["cadence_warm"] if age < 86400 else self.cfg["cadence_cold"]
        return min(base, max(self.cfg["cadence_hot"], fits))

    def upsert_watch(self, identity, ch, parent_ts, watermark, mine=0):
        exp = time.time() + self.cfg["thread_watch_days"] * 86400
        self.db.execute(
            "INSERT INTO thread_watch(identity, channel, parent_ts, watermark, mine, expires, next_poll) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(identity, channel, parent_ts) DO UPDATE SET "
            "watermark=max(coalesce(watermark,''), coalesce(excluded.watermark,'')), "
            "mine=max(mine, excluded.mine), expires=excluded.expires",
            (identity, ch, parent_ts, watermark, mine, exp, time.time() + self.cfg["thread_watch_every"]))

    async def poll_conv(self, identity, ch):
        c = self.q1("SELECT * FROM conv WHERE identity=? AND channel=?", identity, ch)
        d = await self.api(identity, "conversations.history", channel=ch, limit=self.cfg["history_page"])
        window = d.get("messages", [])
        now = time.time()
        if c["cursor"] is None:
            # First time this conversation is polled: start from "now", remember thread watermarks.
            for m in window:
                if m.get("latest_reply"):
                    mine = int(m.get("user") == identity or identity in (m.get("reply_users") or []))
                    self.upsert_watch(identity, ch, m["ts"], m["latest_reply"], mine)
            latest = window[0]["ts"] if window else store.ts_now()
            act = float(window[0]["ts"]) if window else 0
            self.db.execute("UPDATE conv SET cursor=?, last_activity=?, next_poll=? WHERE identity=? AND channel=?",
                            (latest, act, now + self.cadence(act, identity), identity, ch))
            return
        cursor = c["cursor"]
        new = [m for m in window if m["ts"] > cursor]
        if window and len(new) == len(window) and d.get("has_more"):
            new, page_cursor = [], None
            for _ in range(5):
                p = await self.api(identity, "conversations.history", channel=ch, oldest=cursor,
                                   limit=200, cursor=page_cursor)
                new += p.get("messages", [])
                page_cursor = (p.get("response_metadata") or {}).get("next_cursor")
                if not page_cursor:
                    break
        replies = []
        for m in window:
            lr = m.get("latest_reply")
            if not lr:
                continue
            w = self.q1("SELECT watermark FROM thread_watch WHERE identity=? AND channel=? AND parent_ts=?",
                        identity, ch, m["ts"])
            wm = w["watermark"] if w and w["watermark"] else (m["ts"] if m["ts"] > cursor else cursor)
            if lr > wm:
                r = await self.api(identity, "conversations.replies", channel=ch, ts=m["ts"], oldest=wm, limit=200)
                replies += [x for x in r.get("messages", []) if x["ts"] > wm and x["ts"] != m["ts"]]
            mine = int(m.get("user") == identity or identity in (m.get("reply_users") or []))
            self.upsert_watch(identity, ch, m["ts"], lr, mine)
            self.db.execute("UPDATE thread_watch SET next_poll=? WHERE identity=? AND channel=? AND parent_ts=?",
                            (now + self.cfg["thread_watch_every"], identity, ch, m["ts"]))
        for m in sorted(new + replies, key=lambda m: m["ts"]):
            await self.ingest(identity, ch, m)
        if new or replies:
            newest = max(m["ts"] for m in new + replies)
            cursor = max(cursor, max((m["ts"] for m in new), default=cursor))
            act = max(c["last_activity"] or 0, float(newest))
        else:
            act = c["last_activity"] or 0
        self.db.execute("UPDATE conv SET cursor=?, last_activity=?, next_poll=? WHERE identity=? AND channel=?",
                        (cursor, act, now + self.cadence(act, identity), identity, ch))

    async def poll_watched_threads(self, identity, chans):
        """Threads I'm in (or an agent is handling) that have scrolled out of the history window."""
        now = time.time()
        rows = self.q("SELECT * FROM thread_watch WHERE identity=? AND mine>0 AND next_poll<=? AND expires>?",
                      identity, now, now)
        for w in rows:
            if w["channel"] not in chans:
                continue
            r = await self.api(identity, "conversations.replies", channel=w["channel"], ts=w["parent_ts"],
                               oldest=w["watermark"] or w["parent_ts"], limit=200)
            new = [x for x in r.get("messages", []) if x["ts"] > (w["watermark"] or w["parent_ts"])
                   and x["ts"] != w["parent_ts"]]
            for m in sorted(new, key=lambda m: m["ts"]):
                await self.ingest(identity, w["channel"], m)
            wm = max([w["watermark"] or ""] + [m["ts"] for m in new])
            self.db.execute("UPDATE thread_watch SET watermark=?, next_poll=? WHERE identity=? AND channel=? "
                            "AND parent_ts=?", (wm, now + self.cfg["thread_watch_every"], identity,
                                                w["channel"], w["parent_ts"]))

    # ---------- routing ----------
    @staticmethod
    def thread_key(m):
        t = m.get("thread_ts")
        return t if t and t != m["ts"] else "main"

    async def ingest(self, identity, ch, m, route_only=False):
        if m.get("subtype") in self.cfg["ignore_subtypes"]:
            return
        if not route_only:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (identity, ch, m["ts"], m.get("thread_ts"), m.get("user"), m.get("bot_id"), m.get("app_id"),
                 m.get("subtype"), m.get("text", ""), json.dumps(m), time.time()))
            if cur.rowcount == 0:
                return
        key = self.thread_key(m)
        if m.get("user") == identity:
            if not route_only:
                self.upsert_watch(identity, ch, m.get("thread_ts") or m["ts"], None, mine=1)
                self.observed_ack(identity, ch, m)
            return
        conv = self.q1("SELECT * FROM conv WHERE identity=? AND channel=?", identity, ch)
        mention = f"<@{identity}>" in (m.get("text") or "")
        for a in self.live_agents(identity):
            if m["ts"] <= (a["floor_ts"] or "0"):
                continue
            if self.matches(a, await self.view(a), conv, m, key, mention):
                self.deliver(a, ch, key, m)

    def matches(self, a, v, conv, m, key, mention):
        if m.get("bot_id") and m["bot_id"] not in v["allow_bots"]:
            return False
        author = m.get("user") or m.get("bot_id")
        ch = conv["channel"] if conv else None
        if mention and v["mentions"]:
            return True
        if conv and conv["kind"] == "im":
            if v["dm_mode"] == "all" or (v["dm_mode"] == "followed" and conv["other_user"] in v["followed"]):
                return True
        if conv and conv["kind"] == "mpim" and v["group_dms"]:
            return True
        if ch in v["everyone_in"]:
            return True
        if ch in v["channels"] and (author in v["followed"] or m.get("bot_id") in v["allow_bots"]):
            return True
        if key != "main" and v["threads"]:
            w = self.q1("SELECT mine FROM thread_watch WHERE identity=? AND channel=? AND parent_ts=?",
                        a["identity"], ch, key)
            if w and w["mine"] == 1:
                return True
            if self.q1("SELECT 1 FROM deliveries WHERE agent_id=? AND channel=? AND ts=?", a["id"], ch, key) or \
               self.q1("SELECT 1 FROM items WHERE agent_id=? AND channel=? AND thread_key=?", a["id"], ch, key):
                return True
        return False

    def deliver(self, a, ch, key, m):
        cur = self.db.execute("INSERT OR IGNORE INTO deliveries VALUES(?,?,?,NULL)", (a["id"], ch, m["ts"]))
        if cur.rowcount == 0:
            return
        now = time.time()
        it = self.q1("SELECT seq FROM items WHERE agent_id=? AND channel=? AND thread_key=? AND state='pending'",
                     a["id"], ch, key)
        if it:
            seq = it["seq"]
            self.db.execute("UPDATE items SET last_ts=max(last_ts, ?), updated_at=? WHERE seq=?", (m["ts"], now, seq))
        else:
            seq = self.db.execute(
                "INSERT INTO items(agent_id, channel, thread_key, state, created_at, updated_at, last_ts) "
                "VALUES(?,?,?,?,?,?,?)", (a["id"], ch, key, "pending", now, now, m["ts"])).lastrowid
        self.db.execute("INSERT INTO item_msgs VALUES(?,?,?)", (seq, ch, m["ts"]))
        self.db.execute("UPDATE deliveries SET item_seq=? WHERE agent_id=? AND channel=? AND ts=?",
                        (seq, a["id"], ch, m["ts"]))
        if key != "main":
            self.upsert_watch(a["identity"], ch, key, None, mine=2)
        store.audit(self.db, "deliver", a["name"], seq=seq, channel=ch, ts=m["ts"])
        self.wake(a["id"])

    def observed_ack(self, identity, ch, m):
        """A message from me after an open item resolves it: as 'replied' if an agent app sent it."""
        if (identity, ch) in self.sending_to:
            return   # Doorbell is posting here right now; its own message isn't the owner's reply
        by_agent = m.get("app_id") in self.cfg["agent_app_ids"] or bool(
            self.q1("SELECT 1 FROM sent WHERE identity=? AND channel=? AND ts=?", identity, ch, m["ts"]))
        key = self.thread_key(m)
        rows = self.q("SELECT i.*, a.on_manual_reply, a.name FROM items i JOIN agents a ON a.id=i.agent_id "
                      "WHERE a.identity=? AND i.channel=? AND i.state IN ('pending','claimed') AND i.last_ts < ?",
                      identity, ch, m["ts"])
        for it in rows:
            in_item = self.q1("SELECT 1 FROM item_msgs WHERE item_seq=? AND ts=?", it["seq"], key)
            if it["thread_key"] != key and not (key != "main" and in_item):
                continue
            if by_agent:
                self.resolve(it["seq"], "replied", "observed: sent through an agent app")
            elif it["on_manual_reply"] == "defer":
                self.resolve(it["seq"], "owner_replied", "observed: typed by owner")

    def resolve(self, seq, outcome, note=None):
        it = self.q1("SELECT * FROM items WHERE seq=?", seq)
        if not it or it["state"] == "done":
            return False
        self.db.execute("UPDATE items SET state='done', outcome=?, note=?, updated_at=?, lease_until=NULL "
                        "WHERE seq=?", (outcome, note, time.time(), seq))
        self.db.execute("UPDATE agents SET last_acked_ts=max(coalesce(last_acked_ts,''), ?) WHERE id=?",
                        (it["last_ts"], it["agent_id"]))
        a = self.q1("SELECT name FROM agents WHERE id=?", it["agent_id"])
        store.audit(self.db, "resolve", a["name"] if a else None, seq=seq, outcome=outcome, note=note)
        return True

    # ---------- lifecycle ----------
    async def reactivate(self, a):
        now = time.time()
        if a["resume_policy"] == "now":
            floor = f"{now:.6f}"
        else:
            base = max(a["last_acked_ts"] or "0", a["floor_ts"] or "0")
            floor = max(base, f"{now - a['max_catchup']:.6f}")
        self.db.execute("UPDATE agents SET status='detached', floor_ts=?, inactive_since=NULL WHERE id=?",
                        (floor, a["id"]))
        store.audit(self.db, "reactivate", a["name"], floor=floor, policy=a["resume_policy"])
        a = self.q1("SELECT * FROM agents WHERE id=?", a["id"])
        # Messages other agents kept fetching while this one was inactive.
        for r in self.q("SELECT raw, channel FROM messages WHERE identity=? AND ts>? ORDER BY ts",
                        a["identity"], floor):
            m = json.loads(r["raw"])
            if m.get("user") == a["identity"]:
                continue
            conv = self.q1("SELECT * FROM conv WHERE identity=? AND channel=?", a["identity"], r["channel"])
            key = self.thread_key(m)
            if self.matches(a, await self.view(a), conv, m, key, f"<@{a['identity']}>" in (m.get("text") or "")):
                self.deliver(a, r["channel"], key, m)
        return a

    async def touch(self, name):
        a = self.q1("SELECT * FROM agents WHERE name=?", name)
        if not a:
            raise ValueError(f"no agent named {name!r}; run `doorbell register` first")
        self.db.execute("UPDATE agents SET last_seen=? WHERE id=?", (time.time(), a["id"]))
        if a["status"] == "inactive":
            a = await self.reactivate(a)
        return self.q1("SELECT * FROM agents WHERE id=?", a["id"])

    def lifecycle(self):
        now = time.time()
        cfg = self.cfg
        for it in self.q("SELECT i.*, a.name FROM items i JOIN agents a ON a.id=i.agent_id "
                         "WHERE i.state='claimed' AND i.lease_until < ?", now):
            if it["attempts"] >= cfg["max_attempts"]:
                self.db.execute("UPDATE items SET state='stuck', updated_at=? WHERE seq=?", (now, it["seq"]))
                self.notify_desktop(f"{it['name']}: item {it['seq']} stuck after {it['attempts']} attempts")
            else:
                self.db.execute("UPDATE items SET state='pending', lease_until=NULL, updated_at=? WHERE seq=?",
                                (now, it["seq"]))
                store.audit(self.db, "redeliver", it["name"], seq=it["seq"], attempt=it["attempts"] + 1)
                self.wake(it["agent_id"])
        for a in self.q("SELECT * FROM agents"):
            waiting = self.waiters.get(a["id"], 0) > 0
            if a["status"] in LIVE:
                if waiting:
                    status = "active"
                elif now - (a["last_seen"] or 0) > a["idle_ttl"]:
                    status = "inactive"
                    self.db.execute("UPDATE agents SET inactive_since=? WHERE id=?", (now, a["id"]))
                    store.audit(self.db, "inactive", a["name"])
                else:
                    status = "detached"
                if status != a["status"]:
                    self.db.execute("UPDATE agents SET status=? WHERE id=?", (status, a["id"]))
                if status == "detached":
                    old = self.q1("SELECT min(created_at) t FROM items WHERE agent_id=? AND state='pending'",
                                  a["id"])["t"]
                    if old and now - old > cfg["backlog_alert_after"] and now - a["last_alert"] > 3600:
                        self.db.execute("UPDATE agents SET last_alert=? WHERE id=?", (now, a["id"]))
                        self.notify_desktop(f"agent {a['name']} isn't consuming; backlog waiting")
            elif a["status"] == "inactive" and now - (a["inactive_since"] or now) > cfg["purge_after"]:
                self.delete_agent(a["id"])
                store.audit(self.db, "purged", a["name"])
        cutoff = now - cfg["message_retention"]
        self.db.execute("DELETE FROM messages WHERE fetched_at < ? AND NOT EXISTS (SELECT 1 FROM item_msgs im "
                        "JOIN items i ON i.seq=im.item_seq WHERE im.ts=messages.ts AND i.state!='done')", (cutoff,))
        self.db.execute("DELETE FROM api_calls WHERE ts < ?", (now - 86400,))
        self.db.execute("DELETE FROM thread_watch WHERE expires < ?", (now,))

    def delete_agent(self, agent_id):
        for t in ("deliveries", "items"):
            self.db.execute(f"DELETE FROM {t} WHERE agent_id=?", (agent_id,))
        self.db.execute("DELETE FROM item_msgs WHERE item_seq NOT IN (SELECT seq FROM items)")
        a = self.q1("SELECT name FROM agents WHERE id=?", agent_id)
        if a:
            self.db.execute("DELETE FROM grants WHERE agent=?", (a["name"],))
            self.db.execute("DELETE FROM send_requests WHERE agent=?", (a["name"],))
        self.db.execute("DELETE FROM agents WHERE id=?", (agent_id,))

    # ---------- main loops ----------
    async def poll_loop(self):
        while True:
            try:
                await self.poll_once()
            except RateLimited:
                pass
            except Exception as e:  # keep the daemon alive; surface the error in status/audit
                store.audit(self.db, "poll_error", error=repr(e))
            await asyncio.sleep(self.cfg["poll_tick"])

    async def poll_once(self):
        live = self.live_agents()
        if not live:
            self.polling.clear()
            return
        by_ident = {}
        for a in live:
            by_ident.setdefault(a["identity"], []).append(a)
        now_set = set()
        for ident, agents in by_ident.items():
            idrow = self.q1("SELECT status FROM identities WHERE user_id=?", ident)
            if not idrow or idrow["status"] != "ok":
                continue
            if time.time() - self.last_convlist.get(ident, 0) > self.cfg["convlist_every"]:
                await self.refresh_convlist(ident)
            chans = await self.poll_set(ident, agents)
            self.budget_interval[ident] = len(chans) * 60 / (self.cfg["rate_per_min"] * 0.75)
            max_catchup = max(a["max_catchup"] for a in agents)
            for ch in chans:
                if (ident, ch) not in self.polling:
                    floor = f"{time.time() - max_catchup:.6f}"
                    self.db.execute("UPDATE conv SET cursor=?, next_poll=0 WHERE identity=? AND channel=? "
                                    "AND cursor IS NOT NULL AND cursor < ?", (floor, ident, ch, floor))
                    self.db.execute("UPDATE conv SET next_poll=0 WHERE identity=? AND channel=?", (ident, ch))
                now_set.add((ident, ch))
            self.polling = {p for p in self.polling if p[0] != ident} | {(ident, ch) for ch in chans}
            due = self.q("SELECT channel FROM conv WHERE identity=? AND next_poll<=? ORDER BY next_poll",
                         ident, time.time())
            for r in due:
                if r["channel"] in chans:
                    await self.poll_conv(ident, r["channel"])
            await self.poll_watched_threads(ident, chans)
        self.polling &= now_set

    async def lifecycle_loop(self):
        while True:
            try:
                self.lifecycle()
                await self.renew()
            except Exception as e:
                store.audit(self.db, "lifecycle_error", error=repr(e))
            await asyncio.sleep(self.cfg["lifecycle_every"])

    async def serve(self):
        from .server import handle
        self.key = secrets.token_hex(32)
        srv = await asyncio.start_server(lambda r, w: handle(self, r, w), host="127.0.0.1",
                                         port=self.cfg["port"])
        write_private(config.ENDPOINT, json.dumps(
            {"port": srv.sockets[0].getsockname()[1], "key": self.key, "pid": os.getpid()}))
        store.audit(self.db, "daemon_start", pid=os.getpid())
        async with srv:
            await asyncio.gather(srv.serve_forever(), self.poll_loop(), self.lifecycle_loop())


def write_private(path, text):
    """Write a file only this user can read, atomically. On Windows, files under the user's
    profile are private by default; the mode bits only matter on Linux and macOS."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def already_running():
    from . import cli
    try:
        cli.call("status")
        return True
    except (cli.DaemonDown, SystemExit, OSError):
        return False


def _exit_on_sigterm(signum, frame):
    raise SystemExit(0)


def main():
    if already_running():
        raise SystemExit(f"error: doorbelld is already running ({config.ENDPOINT})")
    # Service managers stop the daemon with SIGTERM; turn it into a normal exit so the endpoint
    # file is removed and clients don't try a stale port.
    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    try:
        asyncio.run(Daemon().serve())
    finally:
        try:
            ep = json.loads(config.ENDPOINT.read_text())
            if ep.get("pid") == os.getpid():
                config.ENDPOINT.unlink()
        except (FileNotFoundError, ValueError):
            pass


if __name__ == "__main__":
    main()

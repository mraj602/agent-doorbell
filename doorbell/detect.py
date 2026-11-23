"""How the daemon finds new messages: choosing a mode, search sweeps, and Socket Mode.

Design and the numbers behind it: docs/design/detection.md. In short:

- history: conversations.history on each watched conversation (hot every cadence_hot, idle every
  cadence_warm). Cheapest for a short watch list, no indexing delay, no extra scope.
- search: one search.messages call covers every conversation and thread the person is in. A sweep
  only finds *where* something is new; the conversation (or thread) then turns hot and is read with
  conversations.history / conversations.replies until it has been quiet for hot_for seconds.
- socket: Slack pushes message events over Socket Mode, for an app only this person uses. Missed
  time (start, reconnects) is caught up with a sweep, and a slow sweep runs as a safety net.

Socket Mode on a shared app would hand one person's events to another person's Doorbell, so the
socket shuts itself off the moment it sees a second connection or an event for someone else.
"""
import asyncio
import json
import re
import time
import urllib.parse

from . import store
from .slackapi import RateLimited, Slack, SlackError

FAR = 365 * 86400   # "not scheduled": idle conversations in search/socket mode aren't polled
APP_TOKEN = re.compile(r"xapp-[A-Za-z0-9-]+")


def read_app_token(path):
    with open(path) as f:
        m = APP_TOKEN.search(f.read())
    if not m:
        raise ValueError(f"no app-level token (xapp-...) found in {path}")
    return m.group(0)


def state(d, ident):
    r = d.q1("SELECT * FROM detect_state WHERE identity=?", ident)
    if not r:
        d.db.execute("INSERT OR IGNORE INTO detect_state(identity) VALUES(?)", (ident,))
        r = d.q1("SELECT * FROM detect_state WHERE identity=?", ident)
    return r


def has_search(d, ident):
    """Whether this identity's token may search. Remembered across token renewals (a new client
    knows its scopes only after its first call), so the mode doesn't flap."""
    sl = d.slacks.get(ident)
    if sl and sl.scopes is not None:
        # no_search: Slack answered missing_scope to this very token (the scope list can be stale).
        d.search_ok[ident] = "search:read" in sl.scopes and d.no_search.get(ident) is not sl
    return d.search_ok.get(ident, False)


def history_share(d):
    """History calls a minute this person may plan on: a fair share of the app's pool, within
    the daemon's own rate cap."""
    cfg = d.cfg
    return cfg["budget_share"] * min(cfg["rate_per_min"], cfg["pool_history"] / max(1, cfg["team_size"]))


def sweep_interval(d):
    """Seconds between idle sweeps: sweep_every, or longer when this person's fair share of the
    app's search pool (pool_search / team_size, within budget_share) can't pay for it."""
    cfg = d.cfg
    share = cfg["budget_share"] * cfg["pool_search"] / max(1, cfg["team_size"])
    return max(cfg["sweep_every"], 60 / max(1e-9, share))


def history_cost(d, ident, chans):
    """Idle history polling cost (calls a minute): each conversation is read once per cadence_warm;
    threads cost at most thread_recent + thread_batch reads per thread_watch_every, however many
    are watched (older ones take turns)."""
    cfg = d.cfg
    threads = d.q1("SELECT count(*) n FROM thread_watch WHERE identity=? AND mine>0 AND expires>?",
                   ident, time.time())["n"]
    thread_reads = min(threads, cfg["thread_recent"] + cfg["thread_batch"])
    return len(chans) * 60 / max(1e-9, cfg["cadence_warm"]) + \
        thread_reads * 60 / max(1e-9, cfg["thread_watch_every"])


def choose(d, ident, chans):
    """(mode, why). Socket only while connected; search only with the search:read scope."""
    want = d.cfg["detect"]
    sock = d.sockets.get(ident)
    if want in ("auto", "socket") and sock and sock.connected:
        return "socket", "Slack pushes new messages over Socket Mode"
    if want in ("auto", "socket") and sock and sock.wanted() and sock.disconnected_at and \
            time.time() - sock.disconnected_at < 120:
        # Slack refreshes connections every few hours; don't switch modes for a reconnect.
        return "socket", "Socket Mode reconnecting (a sweep catches up when it's back)"
    if want == "socket":
        why = (sock.note if sock and sock.note else "Socket Mode isn't connected")
        want = "auto"
    else:
        why = None
    if want == "history":
        return "history", "detect: history in config"
    if not has_search(d, ident):
        if want == "search":
            return "history", "detect: search, but the token has no search:read scope (`doorbell login --search`)"
        return "history", why or "no search:read scope; reading each watched conversation"
    if want == "search":
        return "search", why or "detect: search in config"
    cost, share = history_cost(d, ident, chans), history_share(d)
    # Hysteresis: once on search, go back to history only well inside the share.
    limit = share * (0.8 if d.modes.get(ident, ("",))[0] == "search" else 1)
    if cost > limit:
        return "search", (why + "; " if why else "") + \
            f"watch list needs {cost:.1f} history calls/min, over this person's share of {share:.1f}"
    return "history", (why + "; " if why else "") + \
        f"watch list needs {cost:.1f} history calls/min, within this person's share of {share:.1f}"


def thread_parent(m):
    """The parent ts of a search match that is a thread reply, from its permalink."""
    if m.get("thread_ts") and m["thread_ts"] != m.get("ts"):
        return m["thread_ts"]
    q = urllib.parse.urlparse(m.get("permalink") or "").query
    t = urllib.parse.parse_qs(q).get("thread_ts", [None])[0]
    return t if t and t != m.get("ts") else None


def read_all_from_cursors(d, ident, why):
    """Read every watched conversation (and every thread I'm in) once from where it was last
    read: the fallback when a sweep can't vouch for a gap (no search scope, too many matches,
    or a gap from before sweeps started)."""
    d.db.execute("UPDATE conv SET next_poll=0, hot_until=max(coalesce(hot_until,0), ?) WHERE identity=?",
                 (time.time() + 1, ident))
    d.db.execute("UPDATE thread_watch SET next_poll=0, hot_until=max(coalesce(hot_until,0), ?) "
                 "WHERE identity=? AND mine>0", (time.time() + 1, ident))
    store.audit(d.db, "catch_up_by_history", identity=ident, why=why)


async def sweep(d, ident):
    """One search sweep: find conversations and threads with messages Doorbell hasn't stored, and
    make them hot. Returns the number of places made hot, or None when rate limited."""
    now = time.time()
    st = state(d, ident)
    lo = (st["sweep_wm"] or now) - d.cfg["sweep_overlap"]
    # `after:` takes a day and excludes it; go back an extra day so time zones can't cut it short.
    day = time.strftime("%Y-%m-%d", time.gmtime(lo - 2 * 86400))
    hits = []
    truncated = False
    # A long gap (start-up, a socket outage, a sleeping laptop) may hold many messages.
    max_pages = int(d.cfg["sweep_max_pages"]) * (4 if now - lo > 3600 else 1)
    try:
        for page in range(1, max_pages + 1):
            r = await d.api(ident, "search.messages", query=f"after:{day}", sort="timestamp",
                            sort_dir="desc", count=100, page=page)
            ms = (r.get("messages") or {}).get("matches") or []
            hits += [m for m in ms if float(m.get("ts") or 0) > lo]
            pages = ((r.get("messages") or {}).get("paging") or {}).get("pages") or 1
            if not ms or float(ms[-1].get("ts") or 0) <= lo or page >= pages:
                break
        else:
            truncated = True   # the page cap ran out before reaching lo
    except RateLimited:
        slow = min(8, d.sweep_slow.get(ident, 1) * 2)
        d.sweep_slow[ident] = slow
        d.next_sweep[ident] = time.time() + sweep_interval(d) * slow
        store.audit(d.db, "sweep_rate_limited", identity=ident, slow=slow)
        return None
    d.sweep_slow[ident] = max(1, d.sweep_slow.get(ident, 1) / 2)
    made_hot = await mark_hot(d, ident, hits, lo)
    if truncated:
        # Search (which also returns public channels you haven't joined) had more than the page
        # cap since lo: read the watched conversations from their cursors so nothing is lost.
        read_all_from_cursors(d, ident, f"more than {max_pages * 100} search matches since the last sweep")
    d.db.execute("UPDATE detect_state SET sweep_wm=? WHERE identity=?", (now, ident))
    sock = d.sockets.get(ident)
    every = d.cfg["socket_sweep_every"] if sock and sock.connected else sweep_interval(d)
    d.next_sweep[ident] = now + every * d.sweep_slow[ident]
    d.last_sweep[ident] = {"at": now, "hits": len(hits), "hot": made_hot, "truncated": truncated}
    return made_hot


async def mark_hot(d, ident, hits, lo):
    """Turn the conversations/threads behind new search matches hot (polled now, then every
    cadence_hot until quiet). Mentions of me outside the watch list are fetched once."""
    now = time.time()
    chans = d.chans.get(ident, set())
    wants_mentions = any(json.loads(a["sub_json"] or "{}").get("mentions_of_me", True)
                         for a in d.live_agents(ident))
    hot = set()
    for m in hits:
        ch = (m.get("channel") or {}).get("id") if isinstance(m.get("channel"), dict) else m.get("channel")
        ts = m.get("ts")
        if not ch or not ts or d.q1("SELECT 1 FROM messages WHERE identity=? AND channel=? AND ts=?", ident, ch, ts):
            continue
        parent = thread_parent(m)
        mention = f"<@{ident}>" in (m.get("text") or "")
        if parent and (ch in chans or mention or d.q1(
                "SELECT 1 FROM thread_watch WHERE identity=? AND channel=? AND parent_ts=? AND mine>0",
                ident, ch, parent)):
            d.upsert_watch(ident, ch, parent, f"{lo:.6f}")
            d.db.execute("UPDATE thread_watch SET hot_until=?, next_poll=0 WHERE identity=? AND channel=? "
                         "AND parent_ts=?", (now + d.cfg["hot_for"], ident, ch, parent))
            hot.add((ch, parent))
        elif ch in chans:
            # Read from just before the match, so the read includes it (re-reading is harmless:
            # stored messages aren't routed twice).
            before = f"{float(ts) - 0.000001:.6f}"
            d.db.execute("UPDATE conv SET hot_until=?, next_poll=0, cursor=CASE WHEN cursor IS NULL OR cursor>? "
                         "THEN ? ELSE cursor END WHERE identity=? AND channel=?",
                         (now + d.cfg["hot_for"], before, before, ident, ch))
            hot.add((ch, None))
            # Search doesn't say for sure whether a match is a thread reply. If reading the
            # conversation doesn't turn it up, verify() looks it up as one.
            d.verify.setdefault(ident, {})[(ch, ts)] = lo
        elif mention and wants_mentions and d.q1("SELECT 1 FROM conv WHERE identity=? AND channel=?", ident, ch):
            # A mention in a conversation nobody watches: read just that message.
            try:
                r = await d.api(ident, "conversations.history", channel=ch, latest=ts, inclusive="true", limit=1)
            except (SlackError, RateLimited):
                continue
            for msg in r.get("messages", []):
                if msg.get("ts") == ts:
                    await d.ingest(ident, ch, msg)
                    hot.add((ch, ts))
    return len(hot)


async def verify(d, ident):
    """Matches that the hot conversation read didn't store are thread replies whose parent search
    didn't name: conversations.replies accepts any message of a thread and returns the thread."""
    pending = d.verify.pop(ident, {})
    for (ch, ts), lo in pending.items():
        if d.q1("SELECT 1 FROM messages WHERE identity=? AND channel=? AND ts=?", ident, ch, ts):
            continue
        try:
            r = await d.api(ident, "conversations.replies", channel=ch, ts=ts, limit=1)
        except RateLimited:
            d.verify.setdefault(ident, {})[(ch, ts)] = lo   # try again next tick
            continue
        except SlackError:
            continue
        msgs = r.get("messages") or []
        parent = msgs[0].get("thread_ts") if msgs else None
        if parent and parent != ts:
            d.upsert_watch(ident, ch, parent, f"{lo:.6f}")
            d.db.execute("UPDATE thread_watch SET hot_until=?, next_poll=0 WHERE identity=? AND channel=? "
                         "AND parent_ts=?", (time.time() + d.cfg["hot_for"], ident, ch, parent))


# ---------------------------------------------------------------- Socket Mode
class SocketRunner:
    """Holds one Socket Mode connection for one person, only while they have live agents.

    Safety: Slack sends each event to *one* of an app's open connections. If anyone else uses the
    same app with Socket Mode, events (including their private messages) can land here and theirs
    are lost. So: more than one open connection, or any event authorized for someone else, turns
    Socket Mode off for good (until `doorbell socket enable` again) and Doorbell falls back to
    search or history polling. The foreign event is dropped unread and never stored."""

    def __init__(self, d, ident):
        self.d, self.ident = d, ident
        self.connected = False
        self.note = None
        self.strikes = 0
        self.task = None
        self.ws = None
        self.connected_at = None
        self.disconnected_at = None
        self.events = 0
        self.evq = asyncio.Queue()
        self.worker = None

    def enabled(self):
        st = state(self.d, self.ident)
        return st["socket"] == "on" and st["app_token_file"]

    def wanted(self):
        idrow = self.d.q1("SELECT status FROM identities WHERE user_id=?", self.ident)
        return bool(self.enabled() and idrow and idrow["status"] == "ok" and self.d.live_agents(self.ident)
                    and self.d.cfg["detect"] in ("auto", "socket"))

    def start(self):
        loop = asyncio.get_running_loop()
        if not self.task or self.task.done():
            self.task = loop.create_task(self.run())
        if not self.worker or self.worker.done():
            self.worker = loop.create_task(self.work())

    async def work(self):
        """Route events off the socket's read loop, so a slow lookup can't hold up the acks."""
        while True:
            ev = await self.evq.get()
            try:
                await self.d.on_event(self.ident, ev)
            except Exception as e:
                store.audit(self.d.db, "socket_event_error", identity=self.ident, error=repr(e))

    def trip(self, why):
        self.d.db.execute("UPDATE detect_state SET socket='tripped', socket_note=? WHERE identity=?", (why, self.ident))
        self.note = why
        self.d.notify_desktop(f"Socket Mode turned off for {self.ident}: {why}. Doorbell keeps watching by polling.")
        store.audit(self.d.db, "socket_tripped", identity=self.ident, why=why)

    async def run(self):
        from . import wsclient
        backoff = 2
        while True:
            if not self.wanted():
                self.connected = False
                await asyncio.sleep(2)
                continue
            st = state(self.d, self.ident)
            try:
                token = read_app_token(st["app_token_file"])
                opened = await asyncio.to_thread(Slack(token, self.d.cfg["slack_api_base"]).post,
                                                 "apps.connections.open")
                self.d.db.execute("INSERT INTO api_calls VALUES(?,?,1)", (time.time(), "apps.connections.open"))
                self.ws = await wsclient.connect(opened["url"])
            except (OSError, ValueError, KeyError, asyncio.TimeoutError, wsclient.ConnectionClosed) as e:
                self.note = f"can't connect: {e!r}"
                await asyncio.sleep(min(backoff, 300))
                backoff *= 2
                continue
            except SlackError as e:
                self.note = f"Slack refused the app-level token: {e.error}"
                if e.error in ("invalid_auth", "not_authed", "token_revoked", "account_inactive"):
                    self.trip(self.note)
                await asyncio.sleep(min(backoff, 300))
                backoff *= 2
                continue
            except RateLimited as e:
                await asyncio.sleep(e.retry_after)
                continue
            started, failed = time.time(), False
            try:
                await self.session()
            except wsclient.ConnectionClosed:
                pass
            except Exception as e:   # keep the daemon alive
                store.audit(self.d.db, "socket_error", identity=self.ident, error=repr(e))
                failed = True
            finally:
                if self.connected:
                    store.audit(self.d.db, "socket_disconnected", identity=self.ident)
                    self.disconnected_at = time.time()
                self.connected = False
                try:
                    await self.ws.close()
                except Exception:
                    pass
            # Slack refreshes healthy connections every few hours: reconnect at once after a long
            # one, back off when connections keep failing quickly.
            if failed or time.time() - started < 60:
                await asyncio.sleep(min(backoff, 300))
                backoff *= 2
            else:
                backoff = 2

    async def session(self):
        from . import wsclient
        ws = self.ws
        inbox = asyncio.Queue()

        async def reader():   # never cancel recv() mid-frame: one task reads, the loop below waits
            try:
                while True:
                    await inbox.put(await ws.recv())
            except Exception as e:
                await inbox.put(e)

        task = asyncio.get_running_loop().create_task(reader())
        try:
            await self.handle(ws, inbox, wsclient)
        finally:
            task.cancel()

    async def handle(self, ws, inbox, wsclient):
        while True:
            if not self.wanted():
                return
            try:
                raw = await asyncio.wait_for(inbox.get(), 5)
            except asyncio.TimeoutError:
                # A link that died without a close frame (sleep, Wi-Fi change) shows up as silence.
                quiet = time.monotonic() - ws.last_rx
                if quiet > 90:
                    raise wsclient.ConnectionClosed(None, "no data for 90 s")
                if quiet > 30:
                    await ws.ping()
                continue
            if isinstance(raw, Exception):
                raise raw
            env = json.loads(raw)
            kind = env.get("type")
            if kind == "events_api":
                p = env.get("payload") or {}
                users = {a.get("user_id") for a in p.get("authorizations") or []}
                if users != {self.ident}:
                    # Not acked: Slack retries it, and with Socket Mode off here it can't land here again.
                    self.trip("Slack delivered an event for another person; this app is shared" if users - {self.ident}
                              else "Slack delivered an event without saying whose it is; turned off to be safe")
                    return
            if env.get("envelope_id"):
                await ws.send(json.dumps({"envelope_id": env["envelope_id"]}))
            if kind == "hello":
                n = env.get("num_connections") or 1
                if n > 1:
                    self.strikes += 1
                    store.audit(self.d.db, "socket_shared", identity=self.ident, connections=n, strike=self.strikes)
                    if self.strikes >= self.d.cfg["socket_max_strikes"]:
                        self.trip(f"the app has {n} open Socket Mode connections; someone else (or another "
                                  "Doorbell) uses it, and events would be split between them")
                    else:
                        self.note = f"{n} connections open; retrying in 30 s in case one is stale"
                        await asyncio.sleep(30)
                    raise wsclient.ConnectionClosed(1000, "shared")
                self.strikes = 0
                self.note = None
                self.connected = True
                self.connected_at = time.time()
                self.d.next_sweep[self.ident] = 0   # catch up on whatever happened while disconnected
                store.audit(self.d.db, "socket_connected", identity=self.ident)
            elif kind == "disconnect":
                return
            elif kind == "events_api":
                ev = (env.get("payload") or {}).get("event") or {}
                if ev.get("type") == "message":
                    self.events += 1
                    self.evq.put_nowait(ev)

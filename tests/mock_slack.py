"""An in-memory fake of the Slack Web API methods doorbell uses, served by a threaded http.server.

Tests build a workspace (users, DMs, channels, threads), mutate it while the real daemon polls it,
and inspect `calls` to assert what the daemon did (or did not) ask Slack for.

    mock = MockSlack().start()
    alice = mock.add_user("alice")
    dm = mock.add_dm(alice)
    mock.post(dm, "hi", user=alice)
    ...
    mock.stop()

Point the daemon at it with DOORBELL_SLACK_API=<mock.url>.
"""
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REQUIRED_SCOPES = ["channels:history", "channels:read", "groups:history", "groups:read", "im:history",
                   "im:read", "mpim:history", "mpim:read", "users:read", "usergroups:read"]


class MockSlack:
    def __init__(self, scopes=None):
        self.lock = threading.RLock()
        self.scopes = list(REQUIRED_SCOPES if scopes is None else scopes)
        self.team_id = "T0MOCK001"
        self.team_url = "https://mock.slack.test/"
        self.me = "UME000001"
        self.users = {self.me: {"id": self.me, "name": "me", "real_name": "Me Myself", "is_bot": False,
                                "profile": {"real_name": "Me Myself", "display_name": "me"}}}
        self.channels = {}       # id -> {"meta": {...}, "messages": [...]}
        self.usergroups = {}
        self.calls = []          # {"method", "params", "t"}
        self.errors = {}         # method -> Slack error string returned with ok=false
        self.max_page = None     # cap on page size, to exercise cursor pagination
        self._n = 0
        self._last_ts = 0.0
        self._server = None

    # ---------- lifecycle ----------
    def start(self):
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                params = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
                method = u.path.rsplit("/", 1)[-1]
                status, body = mock.handle(method, params)
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("X-OAuth-Scopes", ",".join(mock.scopes))
                self.end_headers()
                self.wfile.write(raw)

            do_POST = do_GET

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    @property
    def url(self):
        return f"http://127.0.0.1:{self._server.server_address[1]}/api/"

    # ---------- workspace mutation ----------
    def _id(self, prefix):
        with self.lock:
            self._n += 1
            return f"{prefix}{self._n:08d}"

    def add_user(self, name, is_bot=False):
        uid = self._id("U")
        with self.lock:
            self.users[uid] = {"id": uid, "name": name, "real_name": name.title(), "is_bot": is_bot,
                               "profile": {"real_name": name.title(), "display_name": name}}
        return uid

    def _add_conv(self, prefix, **meta):
        cid = self._id(prefix)
        with self.lock:
            self.channels[cid] = {"meta": {"id": cid, **meta}, "messages": []}
        return cid

    def add_dm(self, user):
        return self._add_conv("D", is_im=True, user=user)

    def add_group_dm(self, name="mpdm-a--b--me-1"):
        return self._add_conv("G", is_mpim=True, name=name)

    def add_channel(self, name, private=False):
        return self._add_conv("C", is_channel=True, is_private=private, name=name)

    def add_usergroup(self, handle, members):
        gid = self._id("S")
        with self.lock:
            self.usergroups[gid] = {"id": gid, "handle": handle, "users": list(members)}
        return gid

    def _next_ts(self):
        self._last_ts = max(time.time(), self._last_ts + 0.000001)
        return f"{self._last_ts:.6f}"

    def post(self, channel, text, user=None, thread_ts=None, app_id=None, bot_id=None, subtype=None, **extra):
        """Add a message. user=None + bot_id=... posts as a bot. Post as yourself with user=mock.me
        (plus app_id="A..." to look like it was sent through an agent app). Returns the ts."""
        with self.lock:
            ts = self._next_ts()
            m = {"type": "message", "ts": ts, "text": text, **extra}
            if user:
                m["user"] = user
            if bot_id:
                m["bot_id"] = bot_id
            if app_id:
                m["app_id"] = app_id
            if subtype:
                m["subtype"] = subtype
            ch = self.channels[channel]["messages"]
            if thread_ts and thread_ts != ts:
                m["thread_ts"] = thread_ts
                parent = next(x for x in ch if x["ts"] == thread_ts)
                parent["thread_ts"] = thread_ts
                parent["latest_reply"] = ts
                parent["reply_count"] = parent.get("reply_count", 0) + 1
                users = parent.setdefault("reply_users", [])
                if user and user not in users:
                    users.append(user)
            ch.append(m)
            return ts

    # ---------- introspection ----------
    def count(self, method=None, channel=None, since=0.0):
        with self.lock:
            return sum(1 for c in self.calls if (method is None or c["method"] == method)
                       and (channel is None or c["params"].get("channel") == channel) and c["t"] >= since)

    def methods(self):
        with self.lock:
            return {c["method"] for c in self.calls}

    # ---------- the API ----------
    def handle(self, method, params):
        with self.lock:
            self.calls.append({"method": method, "params": params, "t": time.time()})
            if method in self.errors:
                return 200, {"ok": False, "error": self.errors[method]}
            fn = getattr(self, "m_" + method.replace(".", "_"), None)
            if not fn:
                return 200, {"ok": False, "error": "unknown_method"}
            try:
                return 200, {"ok": True, **fn(params)}
            except KeyError as e:
                return 200, {"ok": False, "error": e.args[0] if isinstance(e.args[0], str) else "not_found"}

    def _page(self, items, params, default=100):
        limit = int(params.get("limit") or default)
        if self.max_page:
            limit = min(limit, self.max_page)
        start = int(params.get("cursor") or 0)
        chunk = items[start:start + limit]
        more = start + limit < len(items)
        return chunk, more, (str(start + limit) if more else "")

    def m_auth_test(self, p):
        me = self.users[self.me]
        return {"url": self.team_url, "team": "Mock Team", "user": me["name"], "team_id": self.team_id,
                "user_id": self.me}

    def m_users_conversations(self, p):
        wanted = set((p.get("types") or "").split(","))
        out = []
        for c in self.channels.values():
            m = c["meta"]
            kind = "im" if m.get("is_im") else "mpim" if m.get("is_mpim") else \
                "private_channel" if m.get("is_private") else "public_channel"
            if not wanted or kind in wanted:
                out.append(dict(m))
        chunk, _, nxt = self._page(out, p, 200)
        return {"channels": chunk, "response_metadata": {"next_cursor": nxt}}

    def m_conversations_history(self, p):
        msgs = [m for m in self.channels[p["channel"]]["messages"]
                if not m.get("thread_ts") or m["thread_ts"] == m["ts"]]
        if p.get("oldest"):
            msgs = [m for m in msgs if m["ts"] > p["oldest"]]
        if p.get("latest"):
            msgs = [m for m in msgs if m["ts"] < p["latest"]]
        msgs.sort(key=lambda m: m["ts"], reverse=True)
        chunk, more, nxt = self._page(msgs, p, 100)
        return {"messages": [dict(m) for m in chunk], "has_more": more,
                "response_metadata": {"next_cursor": nxt}}

    def m_conversations_replies(self, p):
        allm = self.channels[p["channel"]]["messages"]
        parent = next((m for m in allm if m["ts"] == p["ts"]), None)
        if parent is None:
            raise KeyError("thread_not_found")
        replies = [m for m in allm if m.get("thread_ts") == p["ts"] and m["ts"] != p["ts"]]
        if p.get("oldest"):
            replies = [m for m in replies if m["ts"] > p["oldest"]]
        replies.sort(key=lambda m: m["ts"])
        chunk, more, nxt = self._page(replies, p, 100)
        return {"messages": [dict(parent)] + [dict(m) for m in chunk], "has_more": more,
                "response_metadata": {"next_cursor": nxt}}

    def m_users_list(self, p):
        chunk, _, nxt = self._page(list(self.users.values()), p, 200)
        return {"members": chunk, "response_metadata": {"next_cursor": nxt}}

    def m_users_info(self, p):
        if p.get("user") not in self.users:
            raise KeyError("user_not_found")
        return {"user": self.users[p["user"]]}

    def m_usergroups_list(self, p):
        return {"usergroups": [dict(g) for g in self.usergroups.values()]}

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

OAuth (PKCE) is mocked too: register an app with `add_oauth_app`, play the browser with `authorize`
(or GET <mock.base>/oauth/v2/authorize?..., which 302s to the redirect URL), then oauth.v2.access
exchanges the code and refreshes tokens. Point doorbell at it with DOORBELL_SLACK_OAUTH=<mock.base>.
Tokens it issues expire (`token_expired`) and report their own scopes; other tokens behave as before.
"""
import base64
import hashlib
import json
import secrets
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
        self.token_scopes = {}   # token -> scopes, for a second token (the sender) with other scopes
        self.calls = []          # {"method", "params", "t", "token"}
        self.errors = {}         # method -> Slack error string returned with ok=false
        self.oauth_apps = {}       # client_id -> {"user_scopes", "redirect_urls", "rotate", "expires_in", ...}
        self.pending_codes = {}    # code -> {client_id, challenge, redirect_uri, user_scopes, user_id}
        self.issued = {}           # access token -> {user_id, scopes, expires_at | None}
        self.revoked = set()       # tokens revoked with auth.revoke
        self.refresh_tokens = {}   # refresh token -> {client_id, user_id, scopes, expires_at}
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
                if u.path == "/oauth/v2/authorize":
                    where = mock.authorize(params, approve=True)
                    if not where.startswith("http"):   # a page error, not a redirect
                        raw = where.encode()
                        self.send_response(400)
                        self.send_header("Content-Length", str(len(raw)))
                        self.end_headers()
                        self.wfile.write(raw)
                        return
                    self.send_response(302)
                    self.send_header("Location", where)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.command == "POST" and int(self.headers.get("Content-Length") or 0):
                    body = self.rfile.read(int(self.headers["Content-Length"])).decode()
                    params.update({k: v[0] for k, v in urllib.parse.parse_qs(body).items()})
                method = u.path.rsplit("/", 1)[-1]
                token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
                scopes = mock.scopes_for(token)
                status, body = mock.handle(method, params, token, scopes)
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("X-OAuth-Scopes", ",".join(scopes))
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

    @property
    def base(self):
        """The OAuth base (no /api/): DOORBELL_SLACK_OAUTH."""
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def scopes_for(self, token):
        with self.lock:
            if token in self.issued:
                return list(self.issued[token]["scopes"])
            return self.token_scopes.get(token, self.scopes)

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
    def handle(self, method, params, token=None, scopes=None):
        with self.lock:
            self.calls.append({"method": method, "params": params, "t": time.time(), "token": token})
            if method in self.errors:
                return 200, {"ok": False, "error": self.errors[method]}
            if token and token in self.revoked:
                return 200, {"ok": False, "error": "token_revoked"}
            tok = self.issued.get(token)
            if tok and tok["expires_at"] is not None and tok["expires_at"] <= time.time():
                return 200, {"ok": False, "error": "token_expired"}
            if method == "auth.revoke":
                self.revoked.add(token)
                return 200, {"ok": True, "revoked": True}
            fn = getattr(self, "m_" + method.replace(".", "_"), None)
            if not fn:
                return 200, {"ok": False, "error": "unknown_method"}
            try:
                if method == "auth.test":
                    return 200, {"ok": True, **fn(params, token)}
                if method in ("chat.postMessage", "reactions.add"):
                    return 200, {"ok": True, **fn(params, scopes if scopes is not None else self.scopes)}
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

    def m_chat_postMessage(self, p, scopes):
        if "chat:write" not in scopes:
            raise KeyError("missing_scope")
        ts = self.post(p["channel"], p["text"], user=self.me, thread_ts=p.get("thread_ts"))
        return {"channel": p["channel"], "ts": ts}

    def m_reactions_add(self, p, scopes):
        if "reactions:write" not in scopes:
            raise KeyError("missing_scope")
        msg = next(m for m in self.channels[p["channel"]]["messages"] if m["ts"] == p["timestamp"])
        msg.setdefault("reactions", []).append({"name": p["name"], "users": [self.me]})
        return {}

    def m_auth_test(self, p, token=None):
        uid = self.issued[token]["user_id"] if token in self.issued else self.me
        me = self.users[uid]
        return {"url": self.team_url, "team": "Mock Team", "user": me["name"], "team_id": self.team_id,
                "user_id": uid}

    # ---------- OAuth (PKCE, user tokens) ----------
    def add_oauth_app(self, client_id, user_scopes, redirect_urls=("http://localhost:8765/doorbell/callback",),
                      rotate=False, expires_in=43200, refresh_expires_in=None):
        """Register an app. rotate=True: tokens expire after `expires_in` s and come with refresh tokens
        (which expire after `refresh_expires_in` s, never if None)."""
        with self.lock:
            self.oauth_apps[client_id] = {"user_scopes": list(user_scopes), "redirect_urls": list(redirect_urls),
                                          "rotate": rotate, "expires_in": expires_in,
                                          "refresh_expires_in": refresh_expires_in,
                                          "app_id": "A" + client_id.replace(".", "")[:8].upper()}

    def authorize(self, params, approve=True, user=None):
        """Play the browser and Slack's consent page. Returns the URL the browser is redirected to
        (code + state, or error=access_denied + state). Invalid requests return a plain error string
        (what Slack would show on its page)."""
        with self.lock:
            app = self.oauth_apps.get(params.get("client_id"))
            if not app:
                return "invalid_client_id"
            ru = urllib.parse.urlparse(params.get("redirect_uri") or "")
            if not any((ru.scheme, ru.hostname, ru.port) == ((r := urllib.parse.urlparse(u)).scheme, r.hostname, r.port)
                       and ru.path.startswith(r.path) for u in app["redirect_urls"]):
                return "bad_redirect_uri"
            want = [x for x in (params.get("user_scope") or "").split(",") if x]
            if not set(want) <= set(app["user_scopes"]):
                return "invalid_scope"
            if params.get("code_challenge_method") != "S256" or not params.get("code_challenge"):
                return "invalid_code_challenge"
            q = {"state": params.get("state", "")}
            if approve:
                code = secrets.token_hex(12)
                self.pending_codes[code] = {"client_id": params["client_id"], "challenge": params["code_challenge"],
                                            "redirect_uri": params["redirect_uri"], "user_scopes": want,
                                            "user_id": user or self.me}
                q = {"code": code, **q}
            else:
                q = {"error": "access_denied", **q}
            sep = "&" if ru.query else "?"
            return params["redirect_uri"] + sep + urllib.parse.urlencode(q)

    def expire(self, token):
        """Force an access token to be expired, or a refresh token to be invalid."""
        with self.lock:
            if token in self.issued:
                self.issued[token]["expires_at"] = time.time() - 1
            elif token in self.refresh_tokens:
                self.refresh_tokens[token]["expires_at"] = time.time() - 1
            else:
                raise KeyError(token)

    def expire_refresh_tokens(self, client_id=None):
        """Invalidate every outstanding refresh token (of one app, or all)."""
        with self.lock:
            for rt in self.refresh_tokens.values():
                if client_id is None or rt["client_id"] == client_id:
                    rt["expires_at"] = time.time() - 1

    def refresh_calls(self):
        with self.lock:
            return [c for c in self.calls if c["method"] == "oauth.v2.access"
                    and c["params"].get("grant_type") == "refresh_token"]

    def _issue(self, app, client_id, user_id, scopes):
        rotate = app["rotate"]
        access = ("xoxe.xoxp-1-" if rotate else "xoxp-oauth-") + secrets.token_hex(16)
        out = {"access_token": access, "token_type": "user"}
        exp = time.time() + app["expires_in"] if rotate else None
        self.issued[access] = {"user_id": user_id, "scopes": list(scopes), "expires_at": exp}
        if rotate:
            rt = "xoxe-1-" + secrets.token_hex(16)
            rexp = time.time() + app["refresh_expires_in"] if app["refresh_expires_in"] else None
            self.refresh_tokens[rt] = {"client_id": client_id, "user_id": user_id, "scopes": list(scopes),
                                       "expires_at": rexp}
            out.update(refresh_token=rt, expires_in=app["expires_in"])
        return out

    def m_oauth_v2_access(self, p):
        app = self.oauth_apps.get(p.get("client_id"))
        if not app:
            raise KeyError("invalid_client_id")
        if p.get("grant_type") == "refresh_token":
            rt = self.refresh_tokens.get(p.get("refresh_token"))
            if (not rt or rt["client_id"] != p["client_id"]
                    or (rt["expires_at"] is not None and rt["expires_at"] <= time.time())):
                raise KeyError("invalid_refresh_token")
            del self.refresh_tokens[p["refresh_token"]]   # each refresh token is single-use
            return self._issue(app, p["client_id"], rt["user_id"], rt["scopes"])
        pend = self.pending_codes.get(p.get("code"))
        if not pend or pend["client_id"] != p["client_id"]:
            raise KeyError("invalid_code")
        del self.pending_codes[p["code"]]                   # single-use, even when the rest fails
        if p.get("redirect_uri") != pend["redirect_uri"]:
            raise KeyError("bad_redirect_uri")
        digest = hashlib.sha256((p.get("code_verifier") or "").encode()).digest()
        if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != pend["challenge"]:
            raise KeyError("invalid_code_verifier")
        tok = self._issue(app, p["client_id"], pend["user_id"], pend["user_scopes"])
        return {"app_id": app["app_id"], "team": {"id": self.team_id},
                "authed_user": {"id": pend["user_id"], "scope": ",".join(pend["user_scopes"]), **tok}}

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

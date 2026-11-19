"""`doorbell login`: Slack sign-in with OAuth and PKCE, for desktops.

The person opens a Slack link, clicks Allow, and Slack sends their browser back to a one-time
listener on 127.0.0.1. Doorbell exchanges the code for a user token with a PKCE verifier
instead of the app's client secret, so any member of the workspace can sign in without being
a collaborator on the app and without a server. The token is never shown.

Slack requires PKCE to be enabled on the app and the localhost redirect URL to be registered
(see docs/ORG-SETUP.md). Slack docs: https://docs.slack.dev/authentication/using-pkce
"""
import base64
import hashlib
import html
import http.server
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_REDIRECT = "http://localhost:8765/doorbell/callback"
READ_SCOPES = ["channels:history", "channels:read", "groups:history", "groups:read", "im:history",
               "im:read", "mpim:history", "mpim:read", "users:read", "usergroups:read"]
SEND_SCOPES = ["chat:write"]
OPTIONAL_SEND_SCOPES = ["reactions:write"]


class LoginError(Exception):
    pass


class NetworkError(LoginError):
    """Slack couldn't be reached: worth retrying, unlike a refused sign-in."""


def scopes_for(role, with_reactions=True):
    extra = OPTIONAL_SEND_SCOPES if with_reactions else []
    return {"watch": READ_SCOPES, "send": SEND_SCOPES + extra, "both": READ_SCOPES + SEND_SCOPES + extra}[role]


def default_oauth_base():
    return os.environ.get("DOORBELL_SLACK_OAUTH", "https://slack.com").rstrip("/")


def pkce_pair():
    verifier = secrets.token_urlsafe(48)[:64]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def authorize_url(client_id, user_scopes, redirect_uri, state, challenge, team=None, oauth_base=None):
    q = {"client_id": client_id, "scope": "", "user_scope": ",".join(user_scopes), "redirect_uri": redirect_uri,
         "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    if team:
        q["team"] = team
    return f"{(oauth_base or default_oauth_base())}/oauth/v2/authorize?{urllib.parse.urlencode(q)}"


PAGE = """<!doctype html><meta charset="utf-8"><title>Agent Doorbell</title>
<body style="font-family:system-ui,sans-serif;max-width:32em;margin:4em auto;line-height:1.5">
<h1>{title}</h1><p>{body}</p></body>"""


def wait_for_code(redirect_uri, state, timeout=300):
    """Listen once on 127.0.0.1 for Slack's redirect. Returns the code."""
    u = urllib.parse.urlparse(redirect_uri)
    if u.scheme != "http" or u.hostname not in ("localhost", "127.0.0.1"):
        raise LoginError("the redirect URI must be http://localhost:<port>/... or http://127.0.0.1:<port>/...")
    path = u.path or "/"
    result = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            r = urllib.parse.urlparse(self.path)
            if r.path != path:
                self.send_response(404)
                self.end_headers()
                return
            q = {k: v[0] for k, v in urllib.parse.parse_qs(r.query).items()}
            if not secrets.compare_digest(q.get("state", "").encode(), state.encode()):
                # Not this login's response (stale tab, or another local process): refuse it, keep waiting.
                raw = PAGE.format(title="Sign-in failed", body="This response doesn't belong to the current "
                                  "sign-in (state mismatch). Use the link the terminal printed.").encode()
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            elif q.get("error"):
                result.setdefault("error", f"Slack returned {q['error']}"
                                           + (" (you chose Cancel)" if q["error"] == "access_denied" else ""))
                title, body = "Sign-in cancelled", f"Slack said: {html.escape(q['error'])}. You can close this tab."
            elif q.get("code"):
                result.setdefault("code", q["code"])
                title, body = "Signed in to Agent Doorbell", "You can close this tab and go back to the terminal."
            else:
                result.setdefault("error", "the sign-in response had no code")
                title, body = "Sign-in failed", "No code in the response. Run `doorbell login` again."
            raw = PAGE.format(title=title, body=body).encode()
            self.send_response(200 if "code" in result and "error" not in result else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            done.set()

    try:
        srv = http.server.HTTPServer(("127.0.0.1", u.port or 80), Handler)
    except OSError as e:
        raise LoginError(f"can't listen on port {u.port}: {e}. Free the port, or register another localhost "
                         f"redirect URL on the app and pass it with --redirect-uri") from None
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        if not done.wait(timeout):
            raise LoginError(f"timed out: no sign-in within {int(timeout)} seconds")
    finally:
        srv.shutdown()
        srv.server_close()
    if "error" in result:
        raise LoginError(result["error"])
    return result["code"]


def _post(api_base, method, params):
    body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"{api_base}{method}", data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as e:
        raise NetworkError(f"couldn't reach Slack: {e}") from None
    if not data.get("ok"):
        raise LoginError(f"Slack refused the sign-in: {data.get('error')}")
    return data


def _creds(client_id, tok, user_id=None, team_id=None):
    exp = tok.get("expires_in")
    now = time.time()
    return {"v": 1, "token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
            "expires_at": (now + float(exp)) if exp else None, "obtained_at": now, "client_id": client_id,
            "user_id": user_id or tok.get("id"), "team_id": team_id,
            "scopes": sorted(s for s in (tok.get("scope") or "").split(",") if s)}


def exchange(api_base, client_id, code, verifier, redirect_uri):
    data = _post(api_base, "oauth.v2.access", {"client_id": client_id, "code": code, "code_verifier": verifier,
                                                "redirect_uri": redirect_uri})
    user = data.get("authed_user") or {}
    if not user.get("access_token"):
        raise LoginError("Slack returned no user token; check the app has user token scopes")
    return _creds(client_id, user, user.get("id"), (data.get("team") or {}).get("id"))


def refresh(api_base, creds):
    if not creds.get("refresh_token"):
        raise LoginError("this token can't be renewed; run `doorbell login` again")
    data = _post(api_base, "oauth.v2.access", {"grant_type": "refresh_token", "client_id": creds["client_id"],
                                                "refresh_token": creds["refresh_token"]})
    tok = data if data.get("access_token") else (data.get("authed_user") or {})
    if not tok.get("access_token"):
        raise LoginError("Slack returned no token on renewal")
    new = _creds(creds["client_id"], tok, creds.get("user_id"), creds.get("team_id"))
    if not new["scopes"]:
        new["scopes"] = creds.get("scopes") or []
    return new


def login(client_id, role, redirect_uri=DEFAULT_REDIRECT, team=None, timeout=300, open_browser=True,
          api_base="https://slack.com/api/", announce=print, with_reactions=True, paste=None):
    """The whole browser round trip. Returns credentials (see _creds). Never prints the token.

    `paste` is a function that reads one line (like `input`): for a browser on another machine
    (SSH, a server), the person pastes the localhost address their browser ended on, and it is
    handed to the same listener, with the same state check."""
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    url = authorize_url(client_id, scopes_for(role, with_reactions), redirect_uri, state, challenge, team)
    result = {}

    def listen():
        try:
            result["code"] = wait_for_code(redirect_uri, state, timeout)
        except LoginError as e:
            result["error"] = e

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    time.sleep(0.2)   # the listener is up before the browser can come back
    if "error" in result:
        raise result["error"]
    announce(f"Open this link to sign in: {url}")
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    if paste:
        threading.Thread(target=read_pasted, args=(redirect_uri, paste), daemon=True).start()
    t.join()
    if "error" in result:
        raise result["error"]
    return exchange(api_base, client_id, result["code"], verifier, redirect_uri)


def read_pasted(redirect_uri, readline):
    """Hand a pasted redirect address to the local listener."""
    u = urllib.parse.urlparse(redirect_uri)
    while True:
        try:
            line = readline().strip()
        except (EOFError, OSError):
            return
        p = urllib.parse.urlparse(line)
        if not p.query or "code=" not in p.query and "error=" not in p.query:
            if line:
                print("That doesn't look like the address after sign-in (it has ?code=...&state=...). Try again:",
                      flush=True)
            continue
        local = f"http://127.0.0.1:{u.port or 80}{u.path or '/'}?{p.query}"
        try:
            urllib.request.urlopen(local, timeout=10).read()
        except Exception:
            pass
        return


def revoke(api_base, creds):
    """Best effort: tell Slack to drop a token. Revoking one token doesn't take scopes away
    from the person's other tokens for the same app."""
    try:
        req = urllib.request.Request(f"{api_base}auth.revoke", data=b"", method="POST",
                                     headers={"Authorization": f"Bearer {creds['token']}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()).get("revoked", False)
    except Exception:
        return False


def due(creds, renew_before, now=None):
    """Renew when less than `renew_before` seconds are left, or less than half the token's life
    for short-lived tokens, so a token is never renewed on every check."""
    exp = creds.get("expires_at")
    if not exp:
        return False
    now = now or time.time()
    life = exp - (creds.get("obtained_at") or exp - 2 * renew_before)
    return exp - now < min(renew_before, life / 2)


def write_creds(path, creds):
    """Atomic write, mode 600."""
    import tempfile
    path = os.path.expanduser(path)
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    if os.name != "nt":
        os.chmod(folder, 0o700)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".creds-")   # new file, mode 600, never a leftover
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(creds, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path

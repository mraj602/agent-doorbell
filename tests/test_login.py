"""`doorbell login`: OAuth PKCE sign-in, the three app layouts, renewal, and the sealed single-token mode.

The browser is played by urllib against the mock's /oauth/v2/authorize (it 302s to the CLI's local
listener). Flows that need a password (--for send/both) have no terminal here, so they go through
doorbell.oauth plus the daemon ops, the way the CLI would call them.
"""
import base64
import hashlib
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pytest
from conftest import ROOT, until
from mock_slack import REQUIRED_SCOPES

oauth = pytest.importorskip("doorbell.oauth")

PW = "correct horse"
READ = list(REQUIRED_SCOPES)
BOTH = READ + ["chat:write", "reactions:write"]
WRITE_ONLY = ["chat:write", "reactions:write"]
PROMPT = "Open this link to sign in: "


# ---------- helpers ----------
def free_port():
    """A free port below the OS's ephemeral range, like the real default 8765: an ephemeral port
    can be the local end of another live connection, which made connects time out on macOS."""
    import random
    for _ in range(200):
        port = random.randint(20000, 32000)
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port")


def err(fn, *a, **kw):
    with pytest.raises((Exception, SystemExit)) as e:
        fn(*a, **kw)
    return str(e.value)


def new_app(e, scopes, client_id=None, **kw):
    """Register an OAuth app on the mock; returns (client_id, port, redirect_uri)."""
    port = free_port()
    cid = client_id or f"{uuid.uuid4().int % 10**9}.{uuid.uuid4().int % 10**9}"
    redirect = f"http://localhost:{port}/cb"
    e.mock.add_oauth_app(cid, scopes, [redirect], **kw)
    return cid, port, redirect


def qs(url):
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}


def loopback(url):
    """Browsers reach localhost on whichever loopback answers; urllib tries each address the hosts
    file lists for localhost in turn (macOS CI images list fe80::1%lo0, which hangs), so go
    straight to 127.0.0.1 like a browser ends up doing."""
    return url.replace("://localhost:", "://127.0.0.1:")


class LoopbackRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, loopback(newurl))


BROWSER = urllib.request.build_opener(LoopbackRedirects)


def get(url):
    """The browser following a link. Returns (status, body); HTTP errors are returned, not raised."""
    try:
        with BROWSER.open(loopback(url), timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read().decode()


def login_env(e, **extra):
    env = {**e.env, "DOORBELL_SLACK_OAUTH": e.mock.base, "PYTHONUNBUFFERED": "1"}
    env.pop("DOORBELL_SLACK_CLIENT_ID", None)
    env.pop("CLAUDECODE", None)
    env.update(extra)
    return env


def kill_tree(proc):
    """On Windows a venv's python.exe is a launcher with a child holding the pipes: kill both."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


class Login:
    """A running `doorbell login --no-browser` process."""

    def __init__(self, e, *args, env=None):
        self.e = e
        self.proc = subprocess.Popen([sys.executable, "-m", "doorbell", "login", "--no-browser", *map(str, args)],
                                     env=env or login_env(e), cwd=e.home, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
        guard = threading.Timer(60, kill_tree, args=(self.proc,))
        guard.daemon = True
        guard.start()
        self.out = []
        self.url = None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                break
            self.out.append(line)
            if PROMPT in line:
                self.url = line.split(PROMPT, 1)[1].strip()
                break
        self._guard = guard

    def finish(self, timeout=30):
        out, errtxt = self.proc.communicate(timeout=timeout)
        self._guard.cancel()
        self.stdout = "".join(self.out) + out
        self.stderr = errtxt
        self.code = self.proc.returncode
        return self

    def approve(self):
        status, body = get(self.url)
        return status, body

    def deny(self):
        return get(self.e.mock.authorize(qs(self.url), approve=False))


def run_login(e, *args, approve=True, env=None):
    lg = Login(e, *args, env=env)
    assert lg.url, f"no sign-in link printed: {lg.proc.communicate()}"
    lg.approve() if approve else lg.deny()
    return lg.finish()


def get_creds(e, client_id, scopes, redirect="http://localhost:9/cb", user=None):
    """What `doorbell login` does up to the credentials: authorize in the browser, then exchange the code."""
    verifier, challenge = oauth.pkce_pair()
    url = oauth.authorize_url(client_id, scopes, redirect, "st-" + uuid.uuid4().hex[:6], challenge,
                              oauth_base=e.mock.base)
    where = e.mock.authorize(qs(url), user=user)
    assert "code=" in where, where
    return oauth.exchange(e.mock.url, client_id, qs(where)["code"], verifier, redirect)


def write_creds(e, creds):
    d = e.home / "credentials"
    d.mkdir(exist_ok=True)
    f = d / f"{creds['user_id']}.json"
    f.write_text(json.dumps(creds))
    f.chmod(0o600)
    return f


def ident(e):
    return next(i for i in e.call("status")["identities"] if i["user_id"] == e.me)


def alerts(e):
    return [json.loads(r[0]).get("msg", "") for r in e.db().execute("SELECT detail FROM audit WHERE kind='alert'")]


def prime(e):
    general = e.mock.add_channel("general")
    e.register({"dms": "none", "channels": [general]}, name="prime")
    until(lambda: e.db().execute("SELECT 1 FROM conv WHERE channel=? AND cursor IS NOT NULL",
                                 (general,)).fetchone(), 15, "priming poll")
    e.deregister_all()


def watch_dm(e):
    """An agent on all DMs, plus a DM that already has one claimed message (so it has a cursor)."""
    u = e.mock.add_user("pat")
    dm = e.mock.add_dm(u)
    name = e.register({"dms": "all"})
    e.mock.post(dm, "first", user=u)
    first = e.wait_items(name, claim=True)
    e.call("ack", name=name, seqs=[i["seq"] for i in first])
    e.settle(dm)
    return name, dm, u


def texts(item):
    return [m["text"] for m in item["messages"]]


def restart(e):
    e.proc.terminate()
    e.proc.wait(30)
    e.start()


def assert_no_secret(secrets, *outputs):
    for o in outputs:
        for s in secrets:
            assert s and s not in o


def secrets_of(creds):
    return [creds["token"], creds.get("refresh_token") or "x-no-refresh-token"]


def mode600(path):
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


# ---------- oauth module pieces ----------
def test_pkce_pair_is_s256():
    v, c = oauth.pkce_pair()
    assert len(v) == 64 and all(ch.isalnum() or ch in "-_" for ch in v)
    assert c == base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    assert "=" not in c and oauth.pkce_pair()[0] != v


def test_authorize_url_has_the_documented_parameters():
    _, ch = oauth.pkce_pair()
    url = oauth.authorize_url("1.2", ["a:read", "b:read"], "http://localhost:8765/doorbell/callback", "st1", ch,
                              team="T012ABCDE", oauth_base="http://127.0.0.1:1")
    assert url.startswith("http://127.0.0.1:1/oauth/v2/authorize?")
    p = qs(url)
    assert {k: p[k] for k in ("client_id", "redirect_uri", "state", "code_challenge", "code_challenge_method")} == {
        "client_id": "1.2", "redirect_uri": "http://localhost:8765/doorbell/callback", "state": "st1",
        "code_challenge": ch, "code_challenge_method": "S256"}
    assert set(p["user_scope"].split(",")) == {"a:read", "b:read"}
    assert p["code_challenge_method"] == "S256" and p["team"] == "T012ABCDE"
    assert p.get("scope", "") == ""
    assert "team" not in qs(oauth.authorize_url("1.2", ["a"], "http://localhost:1/x", "s", ch, oauth_base="http://h"))


def test_authorize_url_defaults_to_slack_and_honours_the_env(monkeypatch):
    _, ch = oauth.pkce_pair()
    monkeypatch.delenv("DOORBELL_SLACK_OAUTH", raising=False)
    assert oauth.authorize_url("1.2", ["a"], "http://localhost:1/x", "s", ch).startswith(
        "https://slack.com/oauth/v2/authorize?")
    monkeypatch.setenv("DOORBELL_SLACK_OAUTH", "http://127.0.0.1:5")
    assert oauth.authorize_url("1.2", ["a"], "http://localhost:1/x", "s", ch).startswith(
        "http://127.0.0.1:5/oauth/v2/authorize?")


# ---------- the redirect listener ----------
def listen(redirect, state, timeout, then):
    """Run wait_for_code in a thread; `then` plays the browser. Returns ('code', x) or ('error', msg)."""
    res = {}

    def run():
        try:
            res["code"] = oauth.wait_for_code(redirect, state, timeout)
        except oauth.LoginError as ex:
            res["error"] = str(ex)
    t = threading.Thread(target=run)
    t.start()
    port = urllib.parse.urlparse(redirect).port

    def up():
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return True
        except OSError:
            return False
    until(up, 10, "listener up")
    out = then()
    t.join(timeout + 5)
    assert not t.is_alive()
    return res, out


def test_listener_returns_the_code_and_shows_a_page():
    port = free_port()
    redirect = f"http://localhost:{port}/cb"
    res, (status, body) = listen(redirect, "S1", 10, lambda: get(f"{redirect}?code=abc123&state=S1"))
    assert res == {"code": "abc123"}
    assert status == 200 and "close this tab" in body.lower()


def test_listener_only_answers_its_path_and_keeps_waiting():
    port = free_port()
    redirect = f"http://localhost:{port}/cb"

    def then():
        wrong = get(f"http://localhost:{port}/other?code=zzz&state=S1")
        return wrong, get(f"{redirect}?code=good&state=S1")
    res, (wrong, ok) = listen(redirect, "S1", 10, then)
    assert wrong[0] == 404 and res == {"code": "good"}


def test_listener_rejects_a_state_mismatch():
    port = free_port()
    redirect = f"http://localhost:{port}/cb"
    # 127.0.0.1, not localhost: on Windows "localhost" tries ::1 first, which takes seconds to refuse
    res, (status, body) = listen(redirect, "RIGHT", 4,
                                 lambda: get(f"http://127.0.0.1:{port}/cb?code=abc&state=WRONG"))
    # refused with 400, and the listener keeps waiting for its own response (here: until it times out),
    # so another local process can't cancel the owner's sign-in
    assert status == 400 and "state mismatch" in body
    assert "code" not in res and "timed out" in str(res["error"]).lower()


def test_a_silent_connection_does_not_block_the_listener():
    port = free_port()
    redirect = f"http://localhost:{port}/cb"
    idle = []

    def then():
        idle.append(socket.create_connection(("127.0.0.1", port)))   # connects, never sends
        return get(f"http://127.0.0.1:{port}/cb?code=abc&state=S")
    res, (status, _) = listen(redirect, "S", 10, then)
    idle[0].close()
    assert status == 200 and res["code"] == "abc"


def test_listener_answers_on_ipv6_loopback_for_localhost():
    if not socket.has_ipv6:
        pytest.skip("no IPv6")
    try:
        socket.create_server(("::1", 0), family=socket.AF_INET6).close()
    except OSError:
        pytest.skip("no IPv6 loopback")
    port = free_port()
    res, (status, _) = listen(f"http://localhost:{port}/cb", "S", 10,
                              lambda: get(f"http://[::1]:{port}/cb?code=abc&state=S"))
    assert status == 200 and res["code"] == "abc"


def test_listener_reports_access_denied():
    port = free_port()
    redirect = f"http://localhost:{port}/cb"
    res, (status, body) = listen(redirect, "S", 10, lambda: get(f"{redirect}?error=access_denied&state=S"))
    assert "access_denied" in res["error"] and "code" not in res
    assert "access_denied" in body


def test_listener_times_out():
    port = free_port()
    t = time.time()
    with pytest.raises(oauth.LoginError):
        oauth.wait_for_code(f"http://localhost:{port}/cb", "S", 1)
    assert time.time() - t < 8
    free = socket.socket()
    free.bind(("127.0.0.1", port))   # the listener has stopped and released the port
    free.close()


# ---------- the mock itself: what the exchange is checked against ----------
def test_exchange_returns_the_documented_credentials(make_env):
    e = make_env()
    cid, _, redirect = new_app(e, BOTH, rotate=True, expires_in=7200)
    c = get_creds(e, cid, BOTH, redirect)
    assert c["v"] == 1 and c["token"].startswith("xoxe.xoxp-") and c["refresh_token"].startswith("xoxe-")
    assert c["client_id"] == cid and c["user_id"] == e.me and c["team_id"] == e.mock.team_id
    assert set(c["scopes"]) == set(BOTH)
    assert time.time() + 7000 < c["expires_at"] < time.time() + 7300
    plain_cid, _, redirect = new_app(e, READ)
    p = get_creds(e, plain_cid, READ, redirect)
    assert p["refresh_token"] is None and p["expires_at"] is None and p["token"].startswith("xoxp-")


def test_exchange_failures(make_env):
    e = make_env()
    cid, _, redirect = new_app(e, READ)

    def authorize():
        v, ch = oauth.pkce_pair()
        url = oauth.authorize_url(cid, READ, redirect, "s", ch, oauth_base=e.mock.base)
        return v, qs(e.mock.authorize(qs(url)))["code"]

    v, code = authorize()
    assert "invalid_code_verifier" in err(oauth.exchange, e.mock.url, cid, code, "x" * 64, redirect)
    # the code is single use, even after a failed attempt
    assert "invalid_code" in err(oauth.exchange, e.mock.url, cid, code, v, redirect)
    v, code = authorize()
    assert "bad_redirect_uri" in err(oauth.exchange, e.mock.url, cid, code, v, "http://localhost:1/other")
    v, code = authorize()
    oauth.exchange(e.mock.url, cid, code, v, redirect)
    assert "invalid_code" in err(oauth.exchange, e.mock.url, cid, code, v, redirect)
    assert "invalid_code" in err(oauth.exchange, e.mock.url, cid, "no-such-code", v, redirect)


def test_the_mock_authorize_checks(make_env):
    e = make_env()
    cid, _, redirect = new_app(e, READ)
    _, ch = oauth.pkce_pair()
    ok = qs(oauth.authorize_url(cid, READ, redirect, "s", ch, oauth_base=e.mock.base))
    assert e.mock.authorize(ok).startswith(redirect + "?code=") and "state=s" in e.mock.authorize(ok)
    denied = e.mock.authorize(ok, approve=False)
    assert "error=access_denied" in denied and "state=s" in denied and "code=" not in denied
    assert not e.mock.authorize({**ok, "redirect_uri": "http://localhost:1/cb"}).startswith("http")
    assert not e.mock.authorize({**ok, "user_scope": "chat:write"}).startswith("http")
    assert not e.mock.authorize({**ok, "code_challenge_method": "plain"}).startswith("http")
    assert not e.mock.authorize({**ok, "client_id": "9.9"}).startswith("http")


# ---------- layout A: one app, read only ----------
def test_login_for_watch_end_to_end(make_env):
    e = make_env()
    cid, port, redirect = new_app(e, READ)
    lg = Login(e, "--client-id", cid, "--redirect-uri", redirect)
    assert lg.url.startswith(e.mock.base + "/oauth/v2/authorize?")
    p = qs(lg.url)
    assert p["client_id"] == cid and p["redirect_uri"] == redirect and p["code_challenge_method"] == "S256"
    assert set(p["user_scope"].split(",")) >= set(READ) and "chat:write" not in p["user_scope"]
    status, body = lg.approve()
    assert status == 200 and "close this tab" in body.lower()
    lg.finish()
    assert lg.code == 0, lg.stderr

    f = e.home / "credentials" / f"{e.me}.json"
    creds = json.loads(f.read_text())
    assert creds["v"] == 1 and creds["user_id"] == e.me and creds["client_id"] == cid
    assert "chat:write" not in creds["scopes"] and creds["token"] in e.mock.issued
    mode600(f)
    i = ident(e)
    assert (i["mode"], i["status"], i["token_expires_in_s"]) == ("file", "ok", None)
    assert i["token_file"] == str(f)
    assert_no_secret(secrets_of(creds), lg.stdout, lg.stderr, e.cli("status").stdout, e.cli("doctor").stdout,
                     (e.home / "daemon.log").read_text())

    # the daemon really watches with it
    prime(e)
    name = e.register({"dms": "all"})
    u = e.mock.add_user("lee")
    e.mock.post(e.mock.add_dm(u), "via oauth", user=u)
    assert texts(e.wait_items(name)[0]) == ["via oauth"]
    assert any(c["token"] == creds["token"] for c in e.mock.calls if c["method"] == "conversations.history")


def test_login_for_watch_is_the_default_and_needs_no_owner_guard(make_env):
    e = make_env()
    cid, port, redirect = new_app(e, READ)
    env = login_env(e, CLAUDECODE="1")        # an agent may run a watch login
    r = run_login(e, "--client-id", cid, "--redirect-uri", redirect, env=env)
    assert r.code == 0, r.stderr
    assert ident(e)["status"] == "ok"


def test_login_with_a_rotating_app_records_the_expiry(make_env):
    e = make_env()
    cid, port, redirect = new_app(e, READ, rotate=True, expires_in=7200)
    r = run_login(e, "--client-id", cid, "--redirect-uri", redirect, "--for", "watch")
    assert r.code == 0, r.stderr
    creds = json.loads((e.home / "credentials" / f"{e.me}.json").read_text())
    assert creds["refresh_token"] and creds["token"].startswith("xoxe.xoxp-")
    assert 7000 < ident(e)["token_expires_in_s"] <= 7200
    assert_no_secret(secrets_of(creds), r.stdout, r.stderr)


def test_client_id_from_config_or_env(make_env):
    e = make_env(config={"slack_client_id": "cfg.id"})
    port = free_port()
    redirect = f"http://localhost:{port}/cb"
    e.mock.add_oauth_app("cfg.id", READ, [redirect])
    lg = Login(e, "--redirect-uri", redirect)
    assert qs(lg.url)["client_id"] == "cfg.id"
    lg.approve()
    assert lg.finish().code == 0, lg.stderr

    e2 = make_env()
    port = free_port()
    redirect = f"http://localhost:{port}/cb"
    e2.mock.add_oauth_app("env.id", READ, [redirect])
    lg = Login(e2, "--redirect-uri", redirect, env=login_env(e2, DOORBELL_SLACK_CLIENT_ID="env.id"))
    assert qs(lg.url)["client_id"] == "env.id"
    lg.approve()
    assert lg.finish().code == 0, lg.stderr


def test_login_without_a_client_id_is_an_error(make_env):
    e = make_env()
    r = subprocess.run([sys.executable, "-m", "doorbell", "login", "--no-browser"], env=login_env(e), cwd=e.home,
                       capture_output=True, text=True, timeout=30, input="")
    assert r.returncode != 0 and "client" in r.stderr.lower()


def test_login_for_watch_refuses_a_token_that_can_post(make_env):
    e = make_env()
    cid, port, redirect = new_app(e, BOTH)
    # the app has chat:write; the login asks for the read scopes only, so the app must grant more than asked.
    # Make Slack hand back the extra scope, as an app whose manifest already allows it might.
    orig = e.mock.m_oauth_v2_access

    def extra(p):
        out = orig(p)
        if "authed_user" in out:
            tok = out["authed_user"]["access_token"]
            e.mock.issued[tok]["scopes"] = BOTH
            out["authed_user"]["scope"] = ",".join(BOTH)
        return out
    e.mock.m_oauth_v2_access = extra
    r = run_login(e, "--client-id", cid, "--redirect-uri", redirect, "--for", "watch")
    assert r.code != 0 and "chat:write" in r.stderr and "sealed" in r.stderr
    # the refused token isn't left behind at Slack
    assert any(c["method"] == "auth.revoke" for c in e.mock.calls)
    assert not (e.home / "credentials").exists() or not list((e.home / "credentials").iterdir())
    assert e.call("status")["identities"] == []
    assert_no_secret(list(e.mock.issued), r.stdout, r.stderr)


def test_login_with_missing_read_scopes_adds_nothing(make_env):
    e = make_env()
    cid, port, redirect = new_app(e, READ)
    orig = e.mock.m_oauth_v2_access

    def fewer(p):   # an admin removed a scope after approval: the token comes back without it
        out = orig(p)
        if "authed_user" in out:
            short = [s for s in READ if s != "im:history"]
            e.mock.issued[out["authed_user"]["access_token"]]["scopes"] = short
            out["authed_user"]["scope"] = ",".join(short)
        return out
    e.mock.m_oauth_v2_access = fewer
    r = run_login(e, "--client-id", cid, "--redirect-uri", redirect)
    assert r.code != 0 and "im:history" in r.stderr
    assert e.call("status")["identities"] == []


def test_login_state_mismatch_is_refused(make_env):
    e = make_env()
    cid, port, redirect = new_app(e, READ)
    lg = Login(e, "--client-id", cid, "--redirect-uri", redirect)
    code = qs(e.mock.authorize(qs(lg.url)))["code"]
    status, _ = get(f"{redirect}?code={code}&state=forged")
    assert status == 400                          # refused, and the login keeps waiting for its own response
    assert e.call("status")["identities"] == []
    assert not (e.home / "credentials").exists() or not list((e.home / "credentials").iterdir())
    lg.approve()
    lg.finish()
    assert lg.code == 0, lg.stderr


def test_login_access_denied(make_env):
    e = make_env()
    cid, port, redirect = new_app(e, READ)
    r = run_login(e, "--client-id", cid, "--redirect-uri", redirect, approve=False)
    assert r.code != 0 and "access_denied" in r.stderr
    assert e.call("status")["identities"] == []


def test_login_times_out(make_env):
    e = make_env()
    cid, port, redirect = new_app(e, READ)
    lg = Login(e, "--client-id", cid, "--redirect-uri", redirect, "--timeout", 2)
    t = time.time()
    lg.finish()
    assert lg.code != 0 and time.time() - t < 15 and "timed out" in lg.stderr.lower()


def test_send_and_both_logins_need_a_terminal_and_a_human(make_env):
    e = make_env()
    cid, port, redirect = new_app(e, BOTH)
    for which in ("send", "both"):
        base = ["--client-id", cid, "--redirect-uri", redirect, "--for", which]
        r = subprocess.run([sys.executable, "-m", "doorbell", "login", "--no-browser", *base], env=login_env(e),
                           cwd=e.home, capture_output=True, text=True, timeout=30, input="")
        assert r.returncode != 0 and "terminal" in r.stderr
        r = subprocess.run([sys.executable, "-m", "doorbell", "login", "--no-browser", *base],
                           env=login_env(e, CLAUDECODE="1"), cwd=e.home, capture_output=True, text=True,
                           timeout=30, stdin=subprocess.DEVNULL)
        assert r.returncode != 0 and "AI agent session" in r.stderr
    assert e.mock.pending_codes == {} and e.call("status")["identities"] == []


# ---------- credential files ----------
def test_read_token_prefers_the_json_credentials(tmp_path):
    from doorbell import slackapi
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"v": 1, "token": "xoxe.xoxp-1-AbC-def", "refresh_token": "xoxe-1-zzz"}))
    assert slackapi.read_token(str(f)) == "xoxe.xoxp-1-AbC-def"
    g = tmp_path / "plain"
    g.write_text("# comment\nxoxp-1111-2222-plain\n")
    assert slackapi.read_token(str(g)) == "xoxp-1111-2222-plain"


def test_identity_add_still_takes_a_plain_token_file(make_env):
    e = make_env()
    e.add_identity()
    i = ident(e)
    assert (i["mode"], i["status"], i["token_expires_in_s"]) == ("file", "ok", None)


def test_identity_add_with_a_credentials_file(make_env):
    e = make_env()
    cid, _, redirect = new_app(e, READ, rotate=True, expires_in=7200)
    creds = get_creds(e, cid, READ, redirect)
    f = write_creds(e, creds)
    e.call("identity_add", token_file=str(f))
    i = ident(e)
    assert i["status"] == "ok" and 7000 < i["token_expires_in_s"] <= 7200


# ---------- layout B: one app, read + write, sealed ----------
@pytest.fixture
def both(make_env):
    """A daemon whose only identity was set up by login_both with a plain (non-expiring) token."""
    e = make_env()
    e.cid, _, e.redirect = new_app(e, BOTH)
    e.creds = get_creds(e, e.cid, BOTH, e.redirect)
    e.call("login_both", token_json=json.dumps(e.creds), password=PW)
    return e


def test_login_both_seals_one_token_for_watching_and_sending(both):
    e = both
    i = ident(e)
    assert (i["mode"], i["status"], i["token_file"]) == ("sealed", "ok", None)
    assert e.call("status")["sending"][e.me]["unlocked"] is True
    assert not (e.home / "credentials").exists() or not list((e.home / "credentials").iterdir())
    assert e.creds["token"].encode() not in (e.home / "doorbell.db").read_bytes()
    assert not any(e.creds["token"] in str(p.read_bytes()) for p in e.home.rglob("*")
                   if p.is_file() and p.name != "doorbell.db-wal" and p.stat().st_size < 10**6)
    # it watches with that token...
    prime(e)
    name, dm, u = watch_dm(e)
    e.mock.post(dm, "second", user=u)
    assert texts(e.wait_items(name)[0]) == ["second"]
    assert any(c["token"] == e.creds["token"] for c in e.mock.calls if c["method"] == "conversations.history")
    # ...and sends with it
    e.call("grant", agent=name, password=PW)
    it = e.items(name, claim=True)[0]
    e.call("send", name=name, seq=it["seq"], text="from one token")
    post = next(c for c in e.mock.calls if c["method"] == "chat.postMessage")
    assert post["token"] == e.creds["token"] and post["params"]["text"] == "from one token"


def test_login_both_comes_back_locked_and_unlock_catches_up(both):
    e = both
    prime(e)
    name, dm, u = watch_dm(e)
    restart(e)
    i = ident(e)
    assert i["status"] == "locked" and i["mode"] == "sealed"
    assert e.call("status")["sending"][e.me]["unlocked"] is False
    until(lambda: any("lock" in a.lower() for a in alerts(e)), 10, "a notification that the identity is locked")
    t = time.time()
    e.mock.post(dm, "sent while locked", user=u)
    time.sleep(2)
    assert e.mock.count("conversations.history", since=t) == 0       # not polled while locked
    assert e.items(name) == []
    assert "wrong password" in err(e.call, "unlock", password="nope nope nope")
    assert ident(e)["status"] == "locked"
    e.call("unlock", password=PW)
    assert ident(e)["status"] == "ok"
    assert texts(e.wait_items(name)[0]) == ["sent while locked"]     # resumed from the cursor
    assert e.mock.count("conversations.history", dm, since=t) >= 1


def test_login_both_checks_the_read_scopes(make_env):
    e = make_env()
    scopes = [s for s in BOTH if s != "im:history"]
    cid, _, redirect = new_app(e, scopes)
    creds = get_creds(e, cid, scopes, redirect)
    assert "im:history" in err(e.call, "login_both", token_json=json.dumps(creds), password=PW)


def test_login_both_updates_an_existing_file_identity(make_env):
    e = make_env()
    e.add_identity()
    assert ident(e)["mode"] == "file"
    cid, _, redirect = new_app(e, BOTH)
    e.call("login_both", token_json=json.dumps(get_creds(e, cid, BOTH, redirect)), password=PW)
    i = ident(e)
    assert i["mode"] == "sealed" and i["token_file"] is None and len(e.call("status")["identities"]) == 1


# ---------- layout C: two apps ----------
def test_two_apps_watch_by_login_and_send_by_sealed_token(make_env):
    e = make_env()
    rcid, _, rredirect = new_app(e, READ)
    r = run_login(e, "--client-id", rcid, "--redirect-uri", rredirect, "--for", "watch")
    assert r.code == 0, r.stderr
    wcid, _, wredirect = new_app(e, WRITE_ONLY)
    send = get_creds(e, wcid, WRITE_ONLY, wredirect)
    res = e.call("sender_add", token_json=json.dumps(send), password=PW)
    assert res["identity"] == e.me and res["can_react"] is True
    assert ident(e)["mode"] == "file" and ident(e)["status"] == "ok"
    assert e.call("status")["sending"][e.me]["unlocked"] is True
    assert send["token"].encode() not in (e.home / "doorbell.db").read_bytes()
    assert not any(send["token"] in p.read_text() for p in (e.home / "credentials").glob("*"))

    prime(e)
    name, dm, u = watch_dm(e)
    e.call("grant", agent=name, password=PW)
    e.mock.post(dm, "ping", user=u)
    it = e.wait_items(name, claim=True)[0]
    e.call("send", name=name, seq=it["seq"], text="pong")
    watch_token = json.loads((e.home / "credentials" / f"{e.me}.json").read_text())["token"]
    post = next(c for c in e.mock.calls if c["method"] == "chat.postMessage")
    assert post["token"] == send["token"] != watch_token


def test_sender_add_token_json_validates_like_a_file(make_env):
    e = make_env()
    cid, _, redirect = new_app(e, READ)
    creds = get_creds(e, cid, READ, redirect)
    f = write_creds(e, creds)
    e.call("identity_add", token_file=str(f))
    assert "watcher's token" in err(e.call, "sender_add", token_json=json.dumps(creds), password=PW)
    other = get_creds(e, cid, READ, redirect)     # a second read-only sign-in: can't send either
    assert "missing scopes ['chat:write']" in err(e.call, "sender_add", token_json=json.dumps(other), password=PW)
    assert e.call("status")["sending"] == {}


# ---------- renewal ----------
def test_daemon_refreshes_before_expiry_and_keeps_polling(make_env):
    e = make_env()
    cid, _, redirect = new_app(e, READ, rotate=True, expires_in=5)
    creds = get_creds(e, cid, READ, redirect)
    f = write_creds(e, creds)
    e.call("identity_add", token_file=str(f))
    prime(e)
    name, dm, u = watch_dm(e)
    until(lambda: json.loads(f.read_text())["token"] != creds["token"], 15, "credentials file rewritten")
    new = json.loads(f.read_text())
    assert new["refresh_token"] != creds["refresh_token"] and new["expires_at"] > creds["expires_at"]
    assert new["user_id"] == e.me and new["client_id"] == cid and new["v"] == 1
    mode600(f)
    assert e.mock.refresh_calls() and all(c["params"]["client_id"] == cid for c in e.mock.refresh_calls())
    assert not [x for x in e.mock.calls if x["params"].get("client_secret")]
    # a token that is already expired must never have been used for polling after the refresh
    time.sleep(6)
    e.mock.post(dm, "after expiry", user=u)
    assert texts(e.wait_items(name)[0]) == ["after expiry"]
    assert ident(e)["status"] == "ok"
    assert not [x for x in e.mock.calls if x["method"] == "conversations.history"
                and x["token"] in e.mock.issued and e.mock.issued[x["token"]]["expires_at"] < x["t"]]
    assert not list(f.parent.glob("*.tmp")) and [p.name for p in f.parent.iterdir()] == [f.name]
    assert_no_secret([creds["token"], creds["refresh_token"]], (e.home / "daemon.log").read_text())


def test_refresh_token_expired_needs_login_then_relogin_catches_up(make_env):
    e = make_env()
    cid, port, redirect = new_app(e, READ, rotate=True, expires_in=5)
    f = write_creds(e, get_creds(e, cid, READ, redirect))
    e.call("identity_add", token_file=str(f))
    prime(e)
    name, dm, u = watch_dm(e)
    cursors = {r["channel"]: r["cursor"] for r in e.db().execute("SELECT channel, cursor FROM conv")}
    assert cursors[dm]
    e.mock.expire_refresh_tokens()
    until(lambda: ident(e)["status"] == "needs_login", 20, "identity needs_login")
    assert ident(e)["error"]
    until(lambda: any("login" in a.lower() for a in alerts(e)), 10, "a notification")
    time.sleep(1.5)
    assert sum("login" in a.lower() for a in alerts(e)) == 1          # notified once
    assert "doorbell login" in json.dumps(e.call("doctor")) or "doorbell login" in e.cli("doctor").stdout
    kept = {r["channel"]: r["cursor"] for r in e.db().execute("SELECT channel, cursor FROM conv")}
    assert kept[dm] == cursors[dm]                                    # cursors are kept
    e.mock.post(dm, "while signed out", user=u)

    r = run_login(e, "--client-id", cid, "--redirect-uri", redirect)  # the person signs in again
    assert r.code == 0, r.stderr
    until(lambda: ident(e)["status"] == "ok", 10, "identity ok again")
    assert texts(e.wait_items(name)[0]) == ["while signed out"]


def test_expired_token_without_a_refresh_token_needs_login(make_env):
    e = make_env()
    cid, _, redirect = new_app(e, READ)
    creds = get_creds(e, cid, READ, redirect)
    creds["expires_at"] = time.time() + 3      # expires soon, and there is nothing to refresh with
    creds["refresh_token"] = None
    f = write_creds(e, creds)
    e.call("identity_add", token_file=str(f))
    e.mock.expire(creds["token"])
    until(lambda: ident(e)["status"] == "needs_login", 40, "needs_login")
    assert e.mock.refresh_calls() == []


def test_sealed_credentials_are_refreshed_while_unlocked(make_env):
    e = make_env()
    cid, _, redirect = new_app(e, BOTH, rotate=True, expires_in=5)
    creds = get_creds(e, cid, BOTH, redirect)
    e.call("login_both", token_json=json.dumps(creds), password=PW)
    before = e.db().execute("SELECT sealed FROM sender").fetchone()["sealed"]
    prime(e)
    name, dm, u = watch_dm(e)
    until(lambda: e.db().execute("SELECT sealed FROM sender").fetchone()["sealed"] != before, 15,
          "sealed credentials re-sealed")
    assert not (e.home / "credentials").exists() or not list((e.home / "credentials").iterdir())
    time.sleep(6)
    e.mock.post(dm, "later", user=u)
    assert texts(e.wait_items(name)[0]) == ["later"]
    assert e.call("status")["sending"][e.me]["unlocked"] is True
    e.call("grant", agent=name, password=PW)
    it = e.items(name, claim=True)[0]
    e.call("send", name=name, seq=it["seq"], text="still works")      # the new token can post
    post = next(c for c in e.mock.calls if c["method"] == "chat.postMessage")
    assert post["token"] != creds["token"] and post["token"] in e.mock.issued
    assert "xoxe" not in e.db().execute("SELECT sealed FROM sender").fetchone()["sealed"]
    # a restart keeps working with the re-sealed credentials: the old refresh token is spent
    restart(e)
    e.call("unlock", password=PW)
    assert ident(e)["status"] == "ok"
    e.mock.post(dm, "after restart", user=u)
    until(lambda: any("after restart" in texts(i) for i in e.items(name)), 15, "poll after unlock")


def test_sealed_credentials_that_expired_while_locked_are_refreshed_at_unlock(make_env):
    e = make_env()
    cid, _, redirect = new_app(e, BOTH, rotate=True, expires_in=3700)
    creds = get_creds(e, cid, BOTH, redirect)
    e.call("login_both", token_json=json.dumps(creds), password=PW)
    prime(e)
    name, dm, u = watch_dm(e)
    restart(e)
    assert ident(e)["status"] == "locked"
    e.mock.expire(creds["token"])                                    # it runs out while Doorbell is locked
    assert e.mock.refresh_calls() == []                              # nothing can refresh a locked token
    e.mock.post(dm, "while locked and expired", user=u)
    e.call("unlock", password=PW)
    until(lambda: ident(e)["status"] == "ok", 10, "ok after unlock")
    assert len(e.mock.refresh_calls()) >= 1
    assert texts(e.wait_items(name)[0]) == ["while locked and expired"]


def test_a_token_is_never_printed(make_env):
    """Across the login CLI, status, doctor, log and the daemon log."""
    e = make_env()
    cid, port, redirect = new_app(e, READ, rotate=True, expires_in=7200)
    r = run_login(e, "--client-id", cid, "--redirect-uri", redirect, "--for", "watch")
    creds = json.loads((e.home / "credentials" / f"{e.me}.json").read_text())
    outs = [r.stdout, r.stderr, (e.home / "daemon.log").read_text()]
    for cmd in (["status"], ["doctor"], ["log"], ["identity", "list"]):
        x = e.cli(*cmd, check=False)
        outs += [x.stdout, x.stderr]
    assert_no_secret(secrets_of(creds) + [creds["token"].split("xoxp-")[-1]], *outs)


# ---------- automatic layout, scope guard, logout (found in live tests) ----------
def grant_more(e, extra):
    """Slack adds up what one person allowed one app, for all their tokens: after they approve
    chat:write anywhere, every token they hold for the app can post."""
    orig = e.mock.m_oauth_v2_access

    def more(p):
        out = orig(p)
        if "authed_user" in out:
            tok = out["authed_user"]["access_token"]
            e.mock.issued[tok]["scopes"] = sorted(set(e.mock.issued[tok]["scopes"]) | set(extra))
            out["authed_user"]["scope"] = ",".join(e.mock.issued[tok]["scopes"])
        return out
    e.mock.m_oauth_v2_access = more


def test_auto_login_read_only_is_layout_a(make_env):
    e = make_env()
    cid, _, redirect = new_app(e, BOTH)
    r = run_login(e, "--client-id", cid, "--redirect-uri", redirect)
    assert r.code == 0, r.stderr
    i = ident(e)
    assert (i["mode"], i["status"]) == ("file", "ok") and "read-only" in r.stdout
    req = next(c for c in e.mock.calls if c["method"] == "oauth.v2.access")
    tok = json.loads((e.home / "credentials" / f"{e.me}.json").read_text())["token"]
    assert "chat:write" not in e.mock.issued[tok]["scopes"]        # it asked for read scopes only
    assert e.call("status")["sending"] == {}


def test_auto_login_that_can_post_is_never_saved_without_the_owner(make_env):
    e = make_env()
    cid, _, redirect = new_app(e, BOTH)
    grant_more(e, ["chat:write"])
    r = run_login(e, "--client-id", cid, "--redirect-uri", redirect)    # no terminal: like an agent
    assert r.code != 0 and "can also post" in r.stderr and "owner" in r.stderr
    assert e.call("status")["identities"] == []
    assert not (e.home / "credentials").exists() or not list((e.home / "credentials").iterdir())
    revoked = [c["token"] for c in e.mock.calls if c["method"] == "auth.revoke"]
    assert revoked and revoked[0] in e.mock.revoked
    assert_no_secret(revoked, r.stdout, r.stderr)


def test_a_login_file_token_that_gains_chat_write_stops_being_used(make_env):
    e = make_env()
    cid, _, redirect = new_app(e, BOTH)
    creds = get_creds(e, cid, READ, redirect)
    e.call("identity_add", token_file=str(write_creds(e, creds)))
    prime(e)
    name, dm, u = watch_dm(e)
    e.mock.issued[creds["token"]]["scopes"] = BOTH                   # approved chat:write elsewhere
    f = e.home / "credentials" / f"{e.me}.json"
    until(lambda: ident(e)["status"] == "needs_login", 15, "needs_login after the token can post")
    assert "able to post" in ident(e)["error"]
    until(lambda: creds["token"] in e.mock.revoked, 10, "the token revoked")   # an agent can't pick it up
    assert not f.exists()
    assert sum("login" in a.lower() for a in alerts(e)) == 1


def test_identity_add_refuses_a_login_file_that_can_post(make_env):
    e = make_env()
    cid, _, redirect = new_app(e, BOTH)
    creds = get_creds(e, cid, BOTH, redirect)
    assert "sealed" in err(e.call, "identity_add", token_file=str(write_creds(e, creds)))


def test_login_both_retires_the_old_login_file(make_env):
    e = make_env()
    cid, _, redirect = new_app(e, BOTH)
    old = get_creds(e, cid, READ, redirect)
    f = write_creds(e, old)
    e.call("identity_add", token_file=str(f))
    res = e.call("login_both", token_json=json.dumps(get_creds(e, cid, BOTH, redirect)), password=PW)
    assert "revoked" in res["retired"] and not f.exists() and old["token"] in e.mock.revoked
    assert ident(e)["mode"] == "sealed"


def test_sender_remove_deletes_the_sealed_sender(make_env):
    """0.2.0 bug: the DELETE was inside a comment, so the sealed token stayed."""
    e = make_env()
    cid, _, redirect = new_app(e, BOTH)
    e.call("identity_add", token_file=str(write_creds(e, get_creds(e, cid, READ, redirect))))
    e.call("sender_add", token_json=json.dumps(get_creds(e, cid, WRITE_ONLY, redirect)), password=PW)
    e.call("sender_remove", password=PW)
    assert e.db().execute("SELECT count(*) n FROM sender").fetchone()["n"] == 0
    assert e.call("status")["sending"] == {}


def test_sender_remove_refuses_in_single_token_mode(both):
    assert "single-token" in err(both.call, "sender_remove", password=PW)


def test_logout_revokes_and_forgets(make_env):
    e = make_env()
    cid, _, redirect = new_app(e, BOTH)
    creds = get_creds(e, cid, READ, redirect)
    f = write_creds(e, creds)
    e.call("identity_add", token_file=str(f))
    res = e.call("logout")
    assert "watch token revoked" in res["done"] and not f.exists() and creds["token"] in e.mock.revoked
    i = ident(e)
    assert i["status"] == "needs_login" and i["token_file"] is None


def test_logout_in_single_token_mode_needs_the_password(both):
    e = both
    assert "wrong password" in err(e.call, "logout", password="nope nope nope")
    res = e.call("logout", password=PW)
    assert "sending token revoked" in res["done"] and e.creds["token"] in e.mock.revoked
    assert e.call("status")["sending"] == {} and ident(e)["status"] == "needs_login"


def test_after_logout_from_single_token_mode_a_read_only_login_works(both):
    e = both
    e.call("logout", password=PW)
    assert ident(e)["mode"] == "file"
    creds = get_creds(e, e.cid, READ, e.redirect)
    e.call("identity_add", token_file=str(write_creds(e, creds)))
    i = ident(e)
    assert (i["mode"], i["status"]) == ("file", "ok")


def test_lock_pauses_watching_in_single_token_mode(both):
    e = both
    prime(e)
    name, dm, u = watch_dm(e)
    assert "paused" in e.call("lock")["watching"]
    assert ident(e)["status"] == "locked"
    t = time.time()
    e.mock.post(dm, "while locked", user=u)
    time.sleep(2)
    assert e.mock.count("conversations.history", since=t) == 0
    e.call("unlock", password=PW)
    assert texts(e.wait_items(name)[0]) == ["while locked"]


def test_pasted_redirect_address_completes_the_login(make_env):
    """SSH: the browser is on another machine, so the person pastes the localhost address."""
    e = make_env()
    cid, port, redirect = new_app(e, READ)
    verifier, challenge = oauth.pkce_pair()
    url = oauth.authorize_url(cid, READ, redirect, "st-paste", challenge, oauth_base=e.mock.base)
    where = e.mock.authorize(qs(url))                  # what the far browser's address bar shows
    got = {}
    t = threading.Thread(target=lambda: got.update(code=oauth.wait_for_code(redirect, "st-paste", timeout=10)),
                         daemon=True)
    t.start()
    def up():
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return True
        except OSError:
            return False
    until(up, 5, "listener up")
    lines = iter(["not a url", where])
    oauth.read_pasted(redirect, lambda: next(lines))
    t.join(10)
    assert got.get("code") == qs(where)["code"]


# ---------- security review fixes ----------
def test_login_both_refuses_the_watchers_own_token(make_env):
    """An agent can read the watch file; it must not seal that token as the sender."""
    e = make_env()
    cid, _, redirect = new_app(e, BOTH)
    creds = get_creds(e, cid, READ, redirect)
    e.call("identity_add", token_file=str(write_creds(e, creds)))
    e.mock.issued[creds["token"]]["scopes"] = BOTH
    assert "watcher's token" in err(e.call, "login_both", token_json=json.dumps(creds), password=PW)
    assert e.call("status")["sending"] == {}


def test_renewal_that_returns_a_token_that_can_post_retires_the_file(make_env):
    e = make_env()
    cid, _, redirect = new_app(e, BOTH, rotate=True, expires_in=5)
    creds = get_creds(e, cid, READ, redirect)
    f = write_creds(e, creds)
    e.call("identity_add", token_file=str(f))
    grant_more(e, ["chat:write"])
    orig = e.mock.m_oauth_v2_access

    def refresh_more(p):
        out = orig(p)
        if out.get("access_token"):
            e.mock.issued[out["access_token"]]["scopes"] = BOTH
            out["scope"] = ",".join(BOTH)
        return out
    e.mock.m_oauth_v2_access = refresh_more
    until(lambda: ident(e)["status"] == "needs_login", 20, "needs_login after renewal")
    assert not f.exists()


def test_identity_add_cant_replace_single_token_mode(both):
    e = both
    assert "single-token" in err(e.add_identity)
    assert ident(e)["mode"] == "sealed"


def test_logout_only_forgets_a_token_file_you_made(make_env):
    e = make_env()
    e.add_identity()
    res = e.call("logout")
    assert any("not revoked" in x for x in res["done"]) and e.mock.revoked == set()
    assert e.token_file.exists()


def test_first_sender_setup_notifies_the_owner(make_env):
    e = make_env()
    cid, _, redirect = new_app(e, BOTH)
    e.call("login_both", token_json=json.dumps(get_creds(e, cid, BOTH, redirect)), password=PW)
    assert any("just set up" in a for a in alerts(e))


def test_credentials_file_is_600_even_over_a_loose_leftover(tmp_path):
    p = tmp_path / "credentials" / "U1.json"
    p.parent.mkdir(mode=0o755)
    oauth.write_creds(str(p), {"v": 1, "token": "xoxp-1"})
    mode600(p)
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(p.parent).st_mode) == 0o700
    assert [x.name for x in p.parent.iterdir()] == ["U1.json"]


def test_signing_in_again_revokes_the_replaced_token(make_env):
    e = make_env()
    cid, _, redirect = new_app(e, READ)
    assert run_login(e, "--client-id", cid, "--redirect-uri", redirect).code == 0
    first = json.loads((e.home / "credentials" / f"{e.me}.json").read_text())["token"]
    assert run_login(e, "--client-id", cid, "--redirect-uri", redirect).code == 0
    second = json.loads((e.home / "credentials" / f"{e.me}.json").read_text())["token"]
    assert first != second and first in e.mock.revoked and second not in e.mock.revoked
    assert ident(e)["status"] == "ok"

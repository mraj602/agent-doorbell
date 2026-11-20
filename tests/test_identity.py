"""Identity add, doctor, and the DORMANT guarantee."""
import json
import os
import sys

import pytest

from conftest import until
from mock_slack import REQUIRED_SCOPES


def test_identity_add_good_token(make_env):
    e = make_env()
    r = e.cli("identity", "add", "--token-file", e.token_file)
    res = json.loads(r.stdout)
    assert res["identity"] == e.me and res["team"] == "Mock Team"
    assert res["warning"] is None
    st = e.call("status")
    assert [(i["user_id"], i["status"]) for i in st["identities"]] == [(e.me, "ok")]


@pytest.mark.skipif(sys.platform == "win32", reason="posix permissions")
@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644])
def test_identity_add_rejects_group_or_other_readable_token(make_env, mode):
    e = make_env()
    os.chmod(e.token_file, mode)
    r = e.cli("identity", "add", "--token-file", e.token_file, check=False)
    assert r.returncode != 0 and "readable by other users" in r.stderr
    assert e.call("status")["identities"] == []
    assert e.mock.count("auth.test") == 0     # rejected before the token is ever sent


def test_identity_add_rejects_missing_scopes(make_env):
    e = make_env(scopes=[s for s in REQUIRED_SCOPES if s != "im:history"])
    r = e.cli("identity", "add", "--token-file", e.token_file, check=False)
    assert r.returncode != 0 and "im:history" in r.stderr
    assert e.call("status")["identities"] == []


def test_identity_add_rejects_file_without_token(make_env):
    e = make_env()
    e.token_file.write_text("nothing useful here\n")
    r = e.cli("identity", "add", "--token-file", e.token_file, check=False)
    assert r.returncode != 0 and "no Slack token" in r.stderr


def test_identity_add_warns_about_unused_scopes_and_doctor_lists_them(make_env):
    e = make_env(scopes=REQUIRED_SCOPES + ["chat:write"])
    res = e.add_identity()
    assert "chat:write" in res["warning"]
    doc = json.loads(e.cli("doctor").stdout)
    ident = doc["identities"][0]
    assert ident["token"] == "ok" and ident["missing_scopes"] == []
    assert ident["unused_scopes"] == ["chat:write"]


def test_doctor_reports_agent_apps_seen_posting_as_you(make_env):
    e = make_env()
    e.add_identity()
    alice = e.mock.add_user("alice")
    dm = e.mock.add_dm(alice)
    name = e.register({"dms": "all"})
    e.settle(dm)
    e.mock.post(dm, "sent via app", user=e.me, app_id="A08SF47R6P4")
    e.mock.post(dm, "sent via other", user=e.me, app_id="AOTHER")
    e.mock.post(dm, "typed by hand", user=e.me)
    until(lambda: e.call("doctor")["identities"][0]["apps_seen_posting_as_you"] == {"A08SF47R6P4": 1, "AOTHER": 1},
          what="apps seen")
    doc = json.loads(e.cli("doctor").stdout)
    assert doc["config"]["agent_app_ids"] == ["A08SF47R6P4"]
    assert doc["identities"][0]["missing_scopes"] == []


def test_dormant_makes_zero_api_calls(make_env):
    e = make_env()
    e.add_identity()
    assert e.mock.methods() == {"auth.test"}
    import time
    time.sleep(1.5)                                # several poll ticks
    st = e.call("status")
    assert st["state"] == "DORMANT" and st["api_calls"]["since_start"] == 0
    assert e.mock.methods() == {"auth.test"}       # only the identity check from `identity add`

    name = e.register({"dms": "all"})
    assert e.call("status")["state"] == "POLLING"
    until(lambda: e.mock.count("users.conversations") > 0, what="polling to start")

    e.call("deregister", name=name)
    until(lambda: e.call("status")["state"] == "DORMANT", what="dormant again")
    time.sleep(1)                                  # let an in-flight poll finish
    n = len(e.mock.calls)
    time.sleep(1.5)
    assert len(e.mock.calls) == n


def test_requests_without_the_access_key_are_refused(env):
    import json as _json
    import socket as _socket
    ep = _json.loads(env.endpoint.read_text())
    if os.name != "nt":   # Windows has no Unix modes; the file sits in the user's own profile
        assert oct(env.endpoint.stat().st_mode & 0o777) == "0o600"
    for key in (None, "wrong"):
        s = _socket.create_connection(("127.0.0.1", ep["port"]))
        req = {"op": "status"} if key is None else {"op": "status", "auth": key}
        s.sendall((_json.dumps(req) + "\n").encode())
        res = _json.loads(s.makefile().readline())
        s.close()
        assert res == {"ok": False, "error": "bad or missing access key"}


def test_second_daemon_refuses_to_start(env):
    import subprocess as _sp
    import sys as _sys
    r = _sp.run([_sys.executable, "-m", "doorbell", "daemon"], env=env.env, capture_output=True, text=True,
                timeout=30)
    assert r.returncode == 0 and "already running" in r.stderr   # 0: a service manager won't respawn it


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM is POSIX")
def test_sigterm_removes_the_endpoint_file(env, tmp_path):
    import subprocess as _sp
    import sys as _sys
    from conftest import until
    home = tmp_path / "state"
    e = {**env.env, "DOORBELL_HOME": str(home), "DOORBELL_ENDPOINT": str(home / "endpoint.json")}
    p = _sp.Popen([_sys.executable, "-m", "doorbell", "daemon"], env=e, stdout=_sp.DEVNULL, stderr=_sp.PIPE)
    try:
        until(lambda: (home / "endpoint.json").exists(), what="endpoint written")
        p.terminate()
        assert p.wait(15) == 0
        assert not (home / "endpoint.json").exists()
    finally:
        p.kill()

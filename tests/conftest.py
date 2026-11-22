"""Fixtures: a mock Slack workspace plus a real `doorbell daemon` subprocess in a temp state dir.

`shared` is one daemon + mock for most tests (daemon start-up and the 1s poll tick dominate the
runtime). Tests keep isolated by using their own users, DMs, channels and agent names, and by
deregistering their agents afterwards. Tests that need a pristine daemon use `make_env`.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from mock_slack import MockSlack  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FAST = {"poll_tick": 0.2, "cadence_hot": 0.2, "cadence_warm": 0.2, "cadence_cold": 0.2,
        "thread_watch_every": 0.2, "convlist_every": 0.5, "lifecycle_every": 0.25, "rate_per_min": 60000,
        # Slack's per-app pools are lifted like rate_per_min: the shared test env collects many
        # conversations, and the real 50/min would stretch idle reads past the tests' timeouts.
        "pool_history": 1e6, "pool_search": 1e6,
        "lease": 1.5, "max_attempts": 2, "notify": False}


def until(fn, timeout=15, what="condition", step=0.1):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            v = fn()
            if v:
                return v
        except Exception as e:   # noqa: BLE001 - the daemon may not be up yet
            last = e
        time.sleep(step)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}" + (f" ({last!r})" if last else ""))


class Env:
    """One isolated doorbell state dir + mock Slack + daemon process."""

    def __init__(self, scopes=None, config=None):
        self.home = Path(tempfile.mkdtemp(prefix="dbl-", dir=tempfile.gettempdir()))
        self.endpoint = self.home / "endpoint.json"
        self.mock = MockSlack(scopes).start()
        self.config = {**FAST, **(config or {})}
        self.proc = None
        self.token_file = self.home / "token"
        self.token_file.write_text("xoxp-1111-2222-mocktoken\n")
        self.token_file.chmod(0o600)
        self.me = self.mock.me
        self.agents = []

    # ---- process control
    @property
    def env(self):
        return {**os.environ, "DOORBELL_HOME": str(self.home), "DOORBELL_ENDPOINT": str(self.endpoint),
                "DOORBELL_SLACK_API": self.mock.url,
                "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")}

    def start(self):
        (self.home / "config.yaml").write_text(yaml.safe_dump(self.config))
        self.proc = subprocess.Popen([sys.executable, "-m", "doorbell", "daemon"], env=self.env, cwd=self.home,
                                     stdout=open(self.home / "daemon.log", "a"), stderr=subprocess.STDOUT)
        until(lambda: self.endpoint.exists() and self.call("status"), 15, "daemon endpoint")
        return self

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.mock.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    # ---- talking to the daemon
    def call(self, op, **req):
        """In-process call (fast). Raises SystemExit('error: ...') on daemon errors."""
        from doorbell import cli, config
        old = config.ENDPOINT
        config.ENDPOINT = self.endpoint
        try:
            return cli.call(op, **req)
        finally:
            config.ENDPOINT = old

    def cli(self, *args, stdin=None, cwd=None, check=True, timeout=60):
        r = subprocess.run([sys.executable, "-m", "doorbell", *map(str, args)], env=self.env, input=stdin or "",
                           cwd=cwd or self.home, capture_output=True, text=True, timeout=timeout)
        if check and r.returncode != 0:
            raise AssertionError(f"doorbell {' '.join(map(str, args))} failed ({r.returncode}): {r.stderr}")
        return r

    def popen(self, *args, cwd=None):
        return subprocess.Popen([sys.executable, "-m", "doorbell", *map(str, args)], env=self.env,
                                cwd=cwd or self.home, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def db(self):
        db = sqlite3.connect(f"file:{self.home / 'doorbell.db'}?mode=ro", uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    # ---- setup helpers
    def add_identity(self):
        return self.call("identity_add", token_file=str(self.token_file))

    def register(self, sub=None, name=None, **kw):
        name = name or f"agent-{uuid.uuid4().hex[:6]}"
        res = self.call("register", name=name, sub=sub or {}, **kw)
        self.agents.append(name)
        # Windows before Python 3.13 has a ~16 ms clock: a message posted right away could get
        # the very timestamp of the registration and count as older than the agent.
        time.sleep(0.04 if os.name == "nt" else 0)
        return name

    def deregister_all(self):
        for a in self.call("status")["agents"]:
            self.call("deregister", name=a["name"])
        self.agents.clear()

    def items(self, name, claim=False):
        return self.call("inbox", name=name, claim=claim)["items"]

    def wait_items(self, name, n=1, timeout=15, claim=False):
        until(lambda: len(self.items(name)) >= n, timeout, f"{n} item(s) for {name}")
        return self.items(name, claim=claim)

    def agent(self, name):
        return next((a for a in self.call("status")["agents"] if a["name"] == name), None)

    def wait_stored(self, channel, ts, timeout=15):
        """The daemon has fetched this message (it is in the messages table)."""
        until(lambda: self.db().execute("SELECT 1 FROM messages WHERE channel=? AND ts=?", (channel, ts))
              .fetchone(), timeout, f"message {ts} stored")

    def settle(self, channel, timeout=15):
        """Wait for two more polls of a conversation, so anything posted before now has been routed."""
        t = time.time()
        until(lambda: self.mock.count("conversations.history", channel, since=t) >= 2, timeout,
              f"two polls of {channel}")

    def resolved(self):
        return [json.loads(r["detail"]) | {"agent": r["agent"]} for r in
                self.db().execute("SELECT * FROM audit WHERE kind='resolve' ORDER BY ts")]


@pytest.fixture
def make_env():
    envs = []

    def make(**kw):
        e = Env(**kw)
        envs.append(e)
        return e.start()
    yield make
    for e in envs:
        e.stop()


@pytest.fixture(scope="session")
def shared():
    e = Env()
    e.start()
    e.add_identity()
    # The first conversation list after start-up reads every conversation from "now" on its first
    # poll. Prime it with a throwaway agent so later tests' conversations count as new ones.
    e.general = e.mock.add_channel("general")
    prime = e.register({"dms": "none", "channels": [e.general]}, name="prime")
    until(lambda: e.db().execute("SELECT 1 FROM conv WHERE channel=? AND cursor IS NOT NULL",
                                 (e.general,)).fetchone(), 15, "priming poll")
    e.deregister_all()
    yield e
    e.stop()


@pytest.fixture
def env(shared):
    shared.deregister_all()
    yield shared
    shared.deregister_all()


@pytest.fixture
def alice(env):
    return env.mock.add_user("alice")


@pytest.fixture
def bob(env):
    return env.mock.add_user("bob")

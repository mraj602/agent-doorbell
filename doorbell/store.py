"""SQLite state: identities, agents, conversation cursors, fetched messages, per-agent queues."""
import json
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS identities(
  user_id TEXT PRIMARY KEY, team_id TEXT, team_url TEXT, token_file TEXT,
  status TEXT DEFAULT 'ok', error TEXT, added_at REAL);
CREATE TABLE IF NOT EXISTS agents(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, identity TEXT, sub_json TEXT,
  status TEXT, resume_policy TEXT, idle_ttl REAL, max_catchup REAL, on_manual_reply TEXT,
  floor_ts TEXT, last_seen REAL, last_acked_ts TEXT, inactive_since REAL, created_at REAL,
  last_alert REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS conv(
  identity TEXT, channel TEXT, kind TEXT, other_user TEXT, name TEXT,
  cursor TEXT, last_activity REAL DEFAULT 0, next_poll REAL DEFAULT 0,
  PRIMARY KEY(identity, channel));
CREATE TABLE IF NOT EXISTS thread_watch(
  identity TEXT, channel TEXT, parent_ts TEXT, watermark TEXT, mine INTEGER DEFAULT 0,
  expires REAL, next_poll REAL DEFAULT 0, PRIMARY KEY(identity, channel, parent_ts));
CREATE TABLE IF NOT EXISTS messages(
  identity TEXT, channel TEXT, ts TEXT, thread_ts TEXT, user TEXT, bot_id TEXT, app_id TEXT,
  subtype TEXT, text TEXT, raw TEXT, fetched_at REAL, PRIMARY KEY(identity, channel, ts));
CREATE TABLE IF NOT EXISTS items(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER, channel TEXT, thread_key TEXT,
  state TEXT, attempts INTEGER DEFAULT 0, lease_until REAL, created_at REAL, updated_at REAL,
  outcome TEXT, note TEXT, last_ts TEXT);
CREATE INDEX IF NOT EXISTS items_agent_state ON items(agent_id, state);
CREATE TABLE IF NOT EXISTS item_msgs(item_seq INTEGER, channel TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS deliveries(
  agent_id INTEGER, channel TEXT, ts TEXT, item_seq INTEGER, PRIMARY KEY(agent_id, channel, ts));
CREATE TABLE IF NOT EXISTS users_cache(
  user_id TEXT PRIMARY KEY, name TEXT, real_name TEXT, display_name TEXT, is_bot INTEGER,
  fetched_at REAL);
CREATE TABLE IF NOT EXISTS usergroups_cache(
  group_id TEXT PRIMARY KEY, handle TEXT, members TEXT, fetched_at REAL);
CREATE TABLE IF NOT EXISTS api_calls(ts REAL, method TEXT, ok INTEGER);
CREATE TABLE IF NOT EXISTS detect_state(
  identity TEXT PRIMARY KEY, sweep_wm REAL, app_token_file TEXT, socket TEXT DEFAULT 'off',
  socket_note TEXT);
CREATE TABLE IF NOT EXISTS audit(ts REAL, kind TEXT, agent TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS sender(
  identity TEXT PRIMARY KEY, sealed TEXT, scopes TEXT, added_at REAL);
CREATE TABLE IF NOT EXISTS grants(
  identity TEXT, agent TEXT, mac TEXT, granted_at REAL, PRIMARY KEY(identity, agent));
CREATE TABLE IF NOT EXISTS send_requests(
  agent TEXT PRIMARY KEY, why TEXT, requested_at REAL);
CREATE TABLE IF NOT EXISTS drafts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT, identity TEXT, item_seq INTEGER,
  channel TEXT, thread_ts TEXT, text TEXT, state TEXT, created_at REAL, decided_at REAL,
  sent_ts TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS sent(
  identity TEXT, channel TEXT, ts TEXT, agent TEXT, item_seq INTEGER, via TEXT, sent_at REAL,
  PRIMARY KEY(identity, channel, ts));
"""


def connect(path):
    db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    cols = {r[1] for r in db.execute("PRAGMA table_info(identities)")}
    if "mode" not in cols:   # 0.3: 'file' (token file) or 'sealed' (single-token mode, see sending.py)
        db.execute("ALTER TABLE identities ADD COLUMN mode TEXT DEFAULT 'file'")
    for table in ("conv", "thread_watch"):   # 0.4: when a conversation or thread stops being hot
        if "hot_until" not in {r[1] for r in db.execute(f"PRAGMA table_info({table})")}:
            db.execute(f"ALTER TABLE {table} ADD COLUMN hot_until REAL DEFAULT 0")
    return db


def audit(db, kind, agent=None, **detail):
    db.execute("INSERT INTO audit VALUES(?,?,?,?)", (time.time(), kind, agent, json.dumps(detail)))


def ts_now():
    return f"{time.time():.6f}"

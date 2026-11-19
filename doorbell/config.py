"""Paths and tunables. Any key in DEFAULTS can be overridden in $DOORBELL_HOME/config.yaml."""
import os
from pathlib import Path

import yaml

def _default_home():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "doorbell"
    return Path.home() / ".local/state/doorbell"


HOME = Path(os.environ.get("DOORBELL_HOME", _default_home()))
# Where the running daemon publishes its local address and access key (readable by you only).
ENDPOINT = Path(os.environ.get("DOORBELL_ENDPOINT", HOME / "endpoint.json"))
DB = HOME / "doorbell.db"
CONFIG_FILE = HOME / "config.yaml"

DEFAULTS = {
    # Slack Web API base URL. Tests point this at a local mock.
    "slack_api_base": os.environ.get("DOORBELL_SLACK_API", "https://slack.com/api/"),
    # Slack apps that post "as you" on an agent's behalf. A message from you sent through one
    # of these apps resolves the open item as "replied". A08SF47R6P4 is the Claude app
    # ("Sent using @Claude"). `doorbell doctor` lists the app IDs seen on your recent messages.
    "agent_app_ids": ["A08SF47R6P4"],
    # Poll cadence by conversation activity (seconds).
    "cadence_hot": 30,       # activity in the last hour
    "cadence_warm": 120,     # activity today
    "cadence_cold": 600,     # older
    "thread_watch_every": 120,
    "thread_watch_days": 7,
    "convlist_every": 300,
    "usergroups_every": 3600,
    "rate_per_min": 40,
    "poll_tick": 1,          # seconds between scans for due conversations (tests lower it)
    "history_page": 50,
    # Agent lifecycle.
    "idle_ttl": 6 * 3600,
    "purge_after": 7 * 86400,
    "max_catchup": 24 * 3600,
    "lease": 900,
    "max_attempts": 3,
    "lifecycle_every": 10,
    "renew_before": 3600,    # renew a sign-in this many seconds before it expires
    "slack_client_id": os.environ.get("DOORBELL_SLACK_CLIENT_ID"),   # the Slack app for `doorbell login`
    "backlog_alert_after": 900,
    "message_retention": 7 * 86400,
    "ignore_subtypes": [
        "channel_join", "channel_leave", "group_join", "group_leave",
        "message_changed", "message_deleted", "channel_topic", "channel_purpose",
        "channel_name", "pinned_item", "unpinned_item", "bot_add", "bot_remove",
    ],
    "notify": True,
    # Run this instead of the desktop notification, with the message as the last argument,
    # e.g. ["ntfy", "publish", "my-topic"] or ["/path/to/script"].
    "notify_command": [],
    # Sending (optional, see `doorbell sender add`). The most direct sends one agent may make in
    # an hour; drafts you approve don't count.
    "send_rate_per_hour": 20,
    # Appended to every message Doorbell sends, e.g. " _(sent by my AI assistant)_". Empty by
    # default: messages look exactly like ones you type.
    "send_footer": "",
    # Local TCP port for agents (127.0.0.1 only). 0 picks a free port; agents find it in endpoint.json.
    "port": 0,
}


def load():
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        user = yaml.safe_load(CONFIG_FILE.read_text()) or {}
        if "claude_app_ids" in user:   # name used before the first release
            user.setdefault("agent_app_ids", user.pop("claude_app_ids"))
        unknown = set(user) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"{CONFIG_FILE}: unknown keys {sorted(unknown)}")
        cfg.update(user)
    return cfg

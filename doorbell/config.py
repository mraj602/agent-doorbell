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
    # How Doorbell finds new messages (docs/design/detection.md):
    #   auto    - pick for you: socket when `doorbell socket enable` was run and it is connected,
    #             else search when the watch list is too big for history polling, else history
    #   history - conversations.history per watched conversation (no search scope needed)
    #   search  - one search.messages sweep covers every conversation and thread you're in
    #   socket  - Slack pushes events over Socket Mode (your own app only), search catches up
    "detect": "auto",
    # People who sign in to the same Slack app. Slack's limits are per app per workspace, so
    # each person's Doorbell takes a fair share of them: pool / team_size.
    "team_size": 1,
    # Slack's documented per-app, per-workspace minimums (calls a minute) and the share of them
    # Doorbell plans to use.
    "pool_history": 50,
    "pool_search": 20,
    "budget_share": 0.75,
    # Poll cadence (seconds). A conversation or thread is hot from new activity until nothing
    # new has arrived for hot_for seconds.
    "cadence_hot": 30,       # while hot
    "cadence_warm": 120,     # idle (history mode); stretched when the watch list exceeds the budget
    "cadence_cold": 600,     # unused since 0.4; kept so older config files still load
    "hot_for": 120,
    # Search sweeps: how often while idle (stretched to fit pool_search / team_size), how far
    # back each sweep re-reads (to catch messages Slack indexed late), and the most pages (100
    # messages each) per sweep (4x after a gap of over an hour).
    "sweep_every": 120,
    "sweep_overlap": 300,
    "sweep_max_pages": 5,
    # Socket Mode: safety sweep while connected, and how many times "another connection is
    # open" may be seen in a row before Socket Mode is turned off.
    "socket_sweep_every": 900,
    "socket_max_strikes": 2,
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
    # Ask for search:read at `doorbell login` (the app must list it). Lets one search call cover
    # every conversation; see "detect".
    "login_search": False,
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


# Keys that may be 0; every other number must be above 0.
_ZERO_OK = {"port", "sweep_overlap", "send_rate_per_hour", "backlog_alert_after"}
_NULLABLE = {"slack_client_id"}
# Whole numbers; every other number (seconds, shares) may have a fraction.
_COUNTS = {"team_size", "sweep_max_pages", "socket_max_strikes", "rate_per_min", "history_page",
           "max_attempts", "port", "send_rate_per_hour"}
DETECT_MODES = ("auto", "history", "search", "socket")


def validate(user, where=CONFIG_FILE):
    """Check user overrides against DEFAULTS (types and ranges); raise ValueError naming the key."""
    unknown = set(user) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
    for k, v in user.items():
        d = DEFAULTS[k]
        bad = None
        if k in _NULLABLE and v is None:
            continue
        if isinstance(d, bool):
            if not isinstance(v, bool):
                bad = "true or false"
        elif k in _COUNTS:
            if isinstance(v, bool) or not isinstance(v, int):
                bad = "a whole number"
        elif isinstance(d, (int, float)):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                bad = "a number"
        elif isinstance(d, list):
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                bad = "a list of strings"
        elif isinstance(d, str) or d is None:
            if not isinstance(v, str):
                bad = "a string"
        if bad is None and isinstance(v, (int, float)) and not isinstance(v, bool):
            if v < 0 or (v == 0 and k not in _ZERO_OK):
                bad = "a number above 0" if k not in _ZERO_OK else "a number of 0 or more"
            elif k == "budget_share" and v > 1:
                bad = "a number from 0 to 1 (a share of Slack's limit)"
            elif k == "port" and v > 65535:
                bad = "a port number (0 picks a free one)"
        if bad is None and k == "detect" and v not in DETECT_MODES:
            bad = "one of " + ", ".join(DETECT_MODES)
        if bad:
            raise ValueError(f"{where}: {k} must be {bad}, not {v!r}")


def load():
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        user = yaml.safe_load(CONFIG_FILE.read_text()) or {}
        if "claude_app_ids" in user:   # name used before the first release
            user.setdefault("agent_app_ids", user.pop("claude_app_ids"))
        if not isinstance(user, dict):
            raise ValueError(f"{CONFIG_FILE}: expected key: value lines")
        validate(user)
        cfg.update(user)
    return cfg

"""Minimal blocking Slack Web API client (read-only usage). Called from worker threads."""
import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request


class SlackError(Exception):
    def __init__(self, method, error):
        super().__init__(f"{method}: {error}")
        self.error = error


class RateLimited(Exception):
    def __init__(self, retry_after):
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


def read_token(path):
    with open(path) as f:
        m = re.search(r"xox[pb]-[A-Za-z0-9-]+", f.read())
    if not m:
        raise ValueError(f"no Slack token found in {path}")
    return m.group(0)


class Slack:
    def __init__(self, token, base="https://slack.com/api/"):
        self.token = token
        self.base = base if base.endswith("/") else base + "/"
        self.scopes = None   # filled from the X-OAuth-Scopes header of the last response

    def call(self, method, **params):
        params = {k: v for k, v in params.items() if v is not None}
        url = f"{self.base}{method}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read())
                    if r.headers.get("X-OAuth-Scopes") is not None:
                        self.scopes = sorted(s.strip() for s in r.headers["X-OAuth-Scopes"].split(",") if s.strip())
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    raise RateLimited(int(e.headers.get("Retry-After", "30")))
                if attempt == 2:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError,
                    http.client.IncompleteRead):
                if attempt == 2:
                    raise
            time.sleep(1 + attempt)
        if not data.get("ok"):
            if data.get("error") == "ratelimited":
                raise RateLimited(30)
            raise SlackError(method, data.get("error"))
        return data

    def paged(self, method, key, **params):
        out, cursor = [], None
        while True:
            d = self.call(method, cursor=cursor, **params)
            out += d.get(key, [])
            cursor = (d.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return out


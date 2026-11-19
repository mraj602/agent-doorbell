# Site images

Sources for the images on the project page (`site/`). Regenerate with:

```bash
# 1. real `doorbell status` output from the daemon against tests/mock_slack.py (temp state dir, fake token)
TMPDIR=/tmp/doorbell-site DOORBELL_SITE_TMP=/tmp/doorbell-site python3 scripts/site/capture_status.py
# 2. render every PNG (needs google-chrome and Pillow)
DOORBELL_SITE_TMP=/tmp/doorbell-site python3 scripts/site/render.py
```

- `render.py` builds the status screenshot from the captured JSON (trimmed fields; the DORMANT run shows
  a settled daemon: zero calls, longer uptime) and screenshots the HTML mockups with headless Chrome,
  then crops and palette-quantizes the PNGs.
- `terminal-setup.html`: illustrative Claude Code `/agent-doorbell:setup` flow (not a recorded transcript).
- `slack-thread.html`: Slack-style thread with made-up names (Alex Doe, #team-builds).
- `flow.html`, `social-card.html` (1200x630), `icon.svg` (the bell; `site/favicon.svg` is its rounded version).
- Fonts come from `site/fonts/` (IBM Plex, SIL OFL 1.1).

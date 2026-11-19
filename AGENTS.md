# Notes for AI coding agents working on this repo

- Python 3.10+, standard library plus PyYAML only. Don't add dependencies.
- Package `doorbell/`: `daemon.py` (polling, routing, lifecycle), `server.py` (ops over local TCP),
  `cli.py` (client + Claude Code hooks), `agentkit.py` + `templates/` (init-agent), `runner.py`
  (dispatch), `service.py` (systemd/launchd/Task Scheduler). Design: `docs/ARCHITECTURE.md`.
- Tests: `pip install -e '.[test]' && pytest`. They run a real daemon against the mock Slack in
  `tests/mock_slack.py`; no Slack account needed. `tests/e2e_slack.py` hits real Slack and is
  opt-in.
- Invariants to keep: the daemon never posts to Slack and never calls an LLM; DORMANT means
  zero Slack API calls; acks don't depend on the model; Doorbell never writes agent harness settings or permissions;
  it must run on Linux, macOS and Windows (no unix-only APIs without a fallback).
- Never commit tokens, workspace IDs, user IDs or names from a real workspace. Use fake IDs
  like `U012ABCDEF` in tests and docs.
- Commits need a DCO sign-off (`git commit -s`). Update CHANGELOG.md under [Unreleased].

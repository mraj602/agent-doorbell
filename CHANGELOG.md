# Changelog

All notable changes to Agent Doorbell are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.1] - unreleased

The first public version. Read-only: Agent Doorbell watches Slack and wakes agents, and
never posts. Everything below is new.

### Added

- `doorbelld`, a token-free polling watcher for Slack. It goes DORMANT, making zero Slack API
  calls, when no agents are registered.
- Per-agent subscriptions: direct messages, group DMs, mentions, threads you are in, channels
  and user groups.
- A queue with leases, redelivery and stuck-item detection, so an event is not lost if an agent
  dies midway.
- Checkpoint catch-up, so messages that arrived while the daemon or an agent was down are
  delivered when it returns.
- `doorbell init-agent`, which writes a harness-neutral agent folder: `AGENTS.md`
  (instructions and the queue protocol), `sub.yaml` and a `CLAUDE.md` pointer. It never writes
  permission settings. `--claude-code` adds slash commands and hooks that ack items
  automatically.
- Agents register themselves (`doorbell register`) on their first run.
- `doorbell dispatch`, a headless runner that wakes any agent CLI, and registers the agent if
  it hasn't yet.
- Linux, macOS and Windows: the CLI talks to the daemon over 127.0.0.1 with a random access
  key, and `doorbell service install` uses systemd, launchd or Task Scheduler.
- Desktop notifications on all three, or your own `notify_command`.
- `doorbell doctor` to check the setup.
- A Slack app manifest (`slack-app-manifest.yaml`) that requests read-only user token scopes.

### Planned for the next release

- Sending through Doorbell: agents write drafts that you approve from the terminal, or, for
  agents you grant send access with your password, reply directly. The sending token is
  sealed with that password and the daemon enforces the send policy.

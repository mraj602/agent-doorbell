# Changelog

All notable changes to Agent Doorbell are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - Unreleased

Sign in to Slack with one command instead of copying a token.

### Added

- `doorbell login`: Slack OAuth with PKCE and a `localhost` redirect. Any workspace member,
  not only app collaborators, signs in with one command and a click on Allow. The token is
  never shown or pasted, and no client secret is used. Options: `--client-id`, `--for
  watch|send|both`, `--redirect-uri`, `--team`, `--no-browser`, `--timeout`, `--as`. The
  Client ID can also come from config `slack_client_id` or `DOORBELL_SLACK_CLIENT_ID`.
- Three app layouts: A, one read-only app; B, one read+write app; C, two apps. See
  `docs/ORG-SETUP.md`.
- Single-token mode (layout B, `doorbell login --for both`): one token watches and sends,
  sealed with your password, with no file on disk. After a restart the identity is `locked`
  until `doorbell unlock`, which resumes polling from the saved cursors, so nothing is missed.
- Token renewal for rotating tokens, before they expire while the daemon runs. If renewal
  fails the identity shows `needs_login`; `status` and `doctor` say to run `doorbell login`.
- `status` shows each identity's `mode`, `status` (`ok`, `locked`, `needs_login`, `broken`)
  and `token_expires_in_s`.
- `slack-app-manifest-readwrite.yaml` for layout B.
- `docs/ORG-SETUP.md`: for workspace admins, how to choose a layout, reuse an existing
  internal app, enable PKCE (one-way), share the Client ID and revoke a member's token.
- `docs/COMPARISON.md`: Agent Doorbell compared with about 20 ways to put an agent on Slack
  (OpenClaw, Claude in Slack, the Slack MCP, Cursor, Devin, Socket Mode and webhook bots,
  n8n, Zapier and others), use cases, and when not to use it. Also on the website.

### Changed

- `slack-app-manifest.yaml` and `slack-app-manifest-sender.yaml` have `pkce_enabled: true`
  and the redirect URL `http://localhost:8765/`. Enabling PKCE makes the app a public client
  and can't be undone except by Slack support.
- `docs/SETUP.md` is built around `doorbell login`; the manual token file still works.
- The setup skill runs `doorbell login --for watch` (the person clicks Allow); the status
  skill explains `locked` and `needs_login`.

### Security

- A token that can post (`chat:write`) is never stored in a plain file: a watch-only login
  refuses it, and `--for both` and `--for send` seal it with your password.
- The login callback listens on 127.0.0.1 only, checks `state`, and serves one request.
  Credentials files are mode 600 and rewritten atomically on renewal.

## [0.2.0] - 2026-11-19

Optional sending through Doorbell, with the policy enforced by Doorbell and your password
instead of by each agent's harness. Watching stays read-only and works as before.

### Added

- `doorbell sender add --token-file F`: a second Slack token with `chat:write` (app manifest
  `slack-app-manifest-sender.yaml`), sealed with a password you choose (scrypt and
  AES-256-GCM). The decrypted token is kept only in the daemon's memory.
- Drafts: agents run `doorbell draft <seq> --text ...` (or `--text-file -`). The draft stays in
  Doorbell, the item is acked as `drafted`, and you get a notification. `doorbell review`
  goes through them (approve, edit, reject, skip) with one password prompt; also
  `doorbell drafts`, `doorbell approve <id> [--text]` and `doorbell reject <id>`.
- Direct sending: `doorbell request-send --why` notifies you; `doorbell grant <agent>`
  (password) allows `doorbell send <seq>` and `doorbell react <seq> <emoji>`, only into the
  conversation of one of the agent's own open items and at most `send_rate_per_hour` times
  an hour (default 20). `doorbell revoke <agent>` takes it back.
- `doorbell unlock` after a restart, `doorbell lock`, `doorbell passwd`, `doorbell sender remove`.
- Config keys `send_rate_per_hour` and `send_footer`.
- `doorbell status` shows sending state, grants, pending drafts and send requests; the status
  skill explains them.
- The generated `AGENTS.md` and the `handle` and `setup` skills cover the new reply options.

### Changed

- `doorbell init-agent` warns when the folder is a project repo: an agent folder of its own
  is usually better, because an existing `AGENTS.md` or `CLAUDE.md` is kept.

### Security

- Commands that need the sending password require a terminal and refuse to run inside a
  known AI agent session. Password checks are serialised and back off after wrong passwords.
- Grants are bound to the agent's subscription and dropped on deregister. Direct sends are
  serialised and rate-limited together with reactions, and can't `@channel`. `review` shows
  control characters escaped. Messages Doorbell sent are recorded, so they count as the agent's
  reply, not as one you typed.
- New dependency: `cryptography`.

## [0.1.0] - 2026-11-19

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
  permission settings.
- A Claude Code plugin, installed from this repository's marketplace: `/agent-doorbell:setup`
  walks a new user through setup with a check per step; `/agent-doorbell:handle` and
  `/agent-doorbell:listen` handle the queue headless or live; `/agent-doorbell:status`
  explains what the watcher is doing. Its hooks ack items after a Slack reply, draft or
  reaction, and act only in agent folders.
- Agents register themselves (`doorbell register`) on their first run.
- `doorbell dispatch`, a headless runner that wakes any agent CLI, and registers the agent if
  it hasn't yet.
- Linux, macOS and Windows: the CLI talks to the daemon over 127.0.0.1 with a random access
  key, and `doorbell service install` uses systemd, launchd or Task Scheduler.
- Desktop notifications on all three, or your own `notify_command`.
- `doorbell doctor` to check the setup.
- A Slack app manifest (`slack-app-manifest.yaml`) that requests read-only user token scopes.

[Unreleased]: https://github.com/mraj602/agent-doorbell/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/mraj602/agent-doorbell/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mraj602/agent-doorbell/releases/tag/v0.1.0

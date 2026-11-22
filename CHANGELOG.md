# Changelog

All notable changes to Agent Doorbell are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.1] - 2026-11-23

### Fixed

- `doorbell service install --dispatch NAME --dir DIR -- <command>`: options after the action
  (`--dispatch`, `--dir`, `--help`) were taken as part of the command, so only the daemon was
  installed and `--help` ran the install. They are parsed as options now.

### Added

- `doorbell service install --dispatch NAME --handoff CMD [--handoff-ok CODES]`: a dispatcher
  service that hands batches off; the command after `--` is optional with `--handoff`.

### Docs

- `--handoff` help and the runner docs say that `wy peers send` refuses outside a Claude Code
  session, so a dispatcher service can't hand off through Watchyard.
- Help text for `status --verbose`, `drafts` and the internal `hook` command.

## [0.4.0] - 2026-11-22

Finds new messages the cheapest way for each person (Socket Mode, search or history), and can
hand batches to an agent session that is already running.

### Added

- Three ways to find new messages, chosen per person (`detect: auto`, see
  [docs/design/detection.md](docs/design/detection.md)):
  - **Socket Mode** for a Slack app only you use: Slack pushes each message (under a second,
    about zero API calls). `doorbell socket enable --app-token-file <file> --only-me`. A search
    sweep (or, without `search:read`, one history read per conversation) catches up on every
    connect and every 15 minutes. Dead connections are found by ping within 90 s. New manifest
    `slack-app-manifest-personal.yaml`.
  - **Search sweeps:** one `search.messages` call every 2 minutes covers every conversation and
    thread you're in; the conversation it points at is then read in full. Used when the watch
    list is too big for your share of the app's limits. Needs `search:read`
    (`doorbell login --search`; the manifests now list the scope).
  - **History polling** as before, for short watch lists or without the search scope.
- `team_size`: how many people sign in to the same Slack app. Each person's Doorbell plans on a
  fair share of Slack's per-app limits and spaces its sweeps to match.
- Mentions of you in conversations no agent watches are found by search sweeps.
- `doorbell config get|set|unset`: edits `config.yaml` with checks; the daemon reloads it
  without a restart. `config.yaml` is now checked for types and ranges when it loads.
- `doorbell status` shows `detection`: the mode and why, sweep interval and slowdown, hot
  conversations and threads, Socket Mode state.
- A small WebSocket client in the standard library (`doorbell/wsclient.py`), so Socket Mode adds
  no dependency.
- `doorbell dispatch --handoff CMD`: hand each new batch (JSON on stdin) to a session that is
  already running, for example through Watchyard (`wy peers send tag:<tag> --one --stdin`),
  and run the usual command only when the handoff fails (`--handoff-ok`, default `0,8`). A
  handed-off batch is sent once and stays claimed until it is resolved or its lease runs out.
- `doorbell inbox --claim` can return only the items it just claimed, and `wait` can wake for
  new items only (used by `--handoff`).

### Security

- Socket Mode turns itself off (desktop alert, audit entry) when Slack reports a second open
  connection on two connects in a row, or delivers an event that isn't authorized for you alone.
  That event is dropped unread, isn't stored and isn't acknowledged. Watching continues by
  polling. Socket Mode stays closed while no agent is live.

### Fixed

- A conversation that appears between two refreshes of the conversation list is read from
  before the previous refresh, so its first messages aren't skipped when a refresh runs late.

### Changed

- Cadence: a conversation or thread is hot (read every 30 s) from new activity until nothing
  new has arrived for 2 minutes (`hot_for`); idle ones are read every 2 minutes in history mode
  and not at all in search and Socket Mode, where a sweep or an event wakes them.
  `cadence_cold` is no longer used.
- Rate limits are tracked per Slack method: a rate-limited search doesn't pause history reads.
  Rate-limited sweeps slow down (up to 8×) and speed up again.
- A sweep that can't vouch for a gap (more matches than its page cap, or the first sweep after
  history polling or a restart) is followed by one read of each watched conversation from its
  cursor, so nothing is skipped.
- Project page: describes every feature and the logic in plain terms (watching, agents,
  replying, sign-in, the three Slack app layouts, the security model, when not to use it),
  without version labels. The setup screenshot shows `doorbell login`.
- README links to the project page and no longer states a version in its status line.
- Architecture: a complete "Slack access logic" section (decision flow of `doorbell login`,
  renewal rules, the scope guard, lock and unlock, logout).

## [0.3.0] - 2026-11-20

Sign in to Slack with one command instead of copying a token.

### Added

- `doorbell login`: Slack OAuth with PKCE and a `localhost` redirect. Any workspace member,
  not only app collaborators, signs in with one command and a click on Allow. The token is
  never shown or pasted, and no client secret is used. Options: `--client-id`, `--send`,
  `--for watch|send|both`, `--redirect-uri`, `--team`, `--no-browser`, `--timeout`. The
  Client ID can also come from config `slack_client_id` or `DOORBELL_SLACK_CLIENT_ID`.
- Automatic layouts. With no flags, login asks Slack for the read scopes only and decides from
  what the returned token can really do. A token that can't post is saved in a credentials
  file (mode 600) and watching is unattended (layout A). A token that can post is explained,
  and, only for the owner in a terminal, sealed with a password so that one token watches and
  sends (layout B, single-token mode). If it isn't the owner in a terminal, login refuses,
  revokes the new token and saves nothing. `--for watch` refuses a token that can post.
- `doorbell login --send` turns on sending. With the app you watch with, it switches to one
  sealed token (layout B), revokes the old file token and deletes its file. With another app
  (`--client-id`), the watch token stays read-only in its file and the new token is sealed
  (layout C). Layout C needs two different Slack apps.
- Slack adds up the scopes a person approved for one app across all of their tokens for it, so
  a read-only sign-in can return a token that can post. Doorbell checks the token it got. The
  docs explain how to reset it and why approvals are per person.
- A guard: if a file token saved by `doorbell login` later becomes able to post, the daemon
  revokes it at once, deletes its file, sets `needs_login` and notifies once. Renewal never
  writes a token that can post to a file. `doorbell login` then seals a new one.
- `doorbell logout [--as U]`: revokes Doorbell's tokens (`auth.revoke`) and forgets them.
  Agents and cursors stay. It asks for the password when a sealed token exists. A token file
  you made yourself is only forgotten, not revoked. Other tools
  that use the same app keep their tokens, and so the accumulated scopes.
- Sign-in from a browser on another machine (SSH, servers): `doorbell login` prints the link;
  after Allow, paste the `http://localhost:8765/doorbell/callback?code=...&state=...` address
  the browser ends on into the waiting command (it prompts in a terminal). The code is
  single-use and useless without the PKCE verifier, which never leaves the machine.
  `ssh -L 8765:localhost:8765` also works.
- Single-token mode (layout B): one token watches and sends, sealed with your password, with no
  file on disk. After a restart the identity is `locked` (one notification) until
  `doorbell unlock`, which resumes polling from the saved cursors and renews the token if it
  expired meanwhile. `doorbell lock` also pauses watching until `doorbell unlock`.
- Token renewal. Tokens from a localhost sign-in expire after 12 hours and come with a refresh
  token; Doorbell renews them on its own with no client secret, at most once per half of a
  token's life and when less than an hour is left. With PKCE refresh tokens last 30 days, so
  after 30 days without Doorbell, sign in again. If renewal fails the identity shows
  `needs_login` with one notification; cursors are kept, so `doorbell login` catches up.
- `status` shows each identity's `mode` (`file` or `sealed`), `status` (`ok`, `locked`,
  `needs_login`, `broken`), `token_expires_in_s`, `client_id` and a `next` hint.
- `slack-app-manifest-readwrite.yaml` for an app that allows read and write scopes.
- `docs/ORG-SETUP.md`: for workspace admins, how to reuse an existing internal app (redirect
  URL, scopes, PKCE), the warning that **Reinstall to Workspace** grants the installer all
  configured user scopes, sharing the Client ID, and revoking a member's token.
- A troubleshooting table for sign-in in `docs/SETUP.md`.
- `docs/COMPARISON.md`: Agent Doorbell compared with about 20 ways to put an agent on Slack
  (OpenClaw, Claude in Slack, the Slack MCP, Cursor, Devin, Socket Mode and webhook bots,
  n8n, Zapier and others), use cases, and when not to use it. Also on the website.

### Changed

- `slack-app-manifest.yaml` and `slack-app-manifest-sender.yaml` have `pkce_enabled: true`
  and the redirect URL `http://localhost:8765/`. Enabling PKCE makes the app a public client
  and can't be undone except by Slack support.
- `doorbell login` ignores the `identify` scope Slack adds to every sign-in.
- `docs/SETUP.md` is built around `doorbell login`; the manual token file still works.
- The setup skill runs `doorbell login` (the person clicks Allow); the status skill explains
  `locked` and `needs_login`.

### Fixed

- `doorbell sender remove` didn't delete the sealed sender token in 0.2.0 (a code line ended
  up inside a comment). In single-token mode it now refuses: switch watching to a read-only
  token first. `sender remove` explains `doorbell logout` instead.
- After `doorbell logout` from single-token mode, a read-only `doorbell login` works again.
- `doorbell service install` works on headless Macs (launchd `bootstrap` with a background
  session) and on reinstall, and on every OS fails loudly if the daemon doesn't start.
- Starting a second daemon exits cleanly instead of with an error.
- `doorbell service install` on Windows: the task's command lost the backslashes in the
  Python path, so the daemon never started. Paths with spaces work on every OS too. The task
  is now `AgentDoorbell-doorbelld`, for the current user only, so installing needs no
  administrator, and it starts the daemon again within 5 minutes if it stops.

### Security

- A token that can post (`chat:write`) is never stored in a plain file, and never kept for a
  login that an agent ran. It is sealed with your password, or revoked.
- The login callback listens on 127.0.0.1 only and checks `state`; a response with the wrong
  `state` gets a 400 and the login keeps waiting, so another local process can't end it.
  Credentials files are written through a new temporary file with mode 600 in a mode-700
  folder and replaced atomically on renewal.
- `login_both` and `sender add` refuse the watcher's own token, and setting up sending for the
  first time sends a desktop notification ("If that wasn't you, run `doorbell logout`").
- Every change to sealed credentials (renewal, `passwd`, replacing, `unlock`, `logout`) runs
  under one lock, so a renewal can't re-seal with a stale key.
- `identity add` can't replace an identity in single-token mode. Approving a draft whose
  sealed token had to be renewed no longer unlocks Doorbell as a side effect.
- `doorbell login --for send|both` doesn't open a browser by itself: you open the printed link.
- Signing in again revokes the token it replaces.
- The login listener answers each connection in its own thread with a 10-second limit, so a
  connection that never sends anything can't hold up the sign-in or its timeout. For a
  `localhost` redirect it also listens on `::1`, for browsers that try IPv6 first.

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

[Unreleased]: https://github.com/mraj602/agent-doorbell/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/mraj602/agent-doorbell/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/mraj602/agent-doorbell/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/mraj602/agent-doorbell/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/mraj602/agent-doorbell/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mraj602/agent-doorbell/releases/tag/v0.1.0

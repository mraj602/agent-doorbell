# Agent Doorbell for Slack

**Website: <https://mraj602.github.io/agent-doorbell/>**

[![ci](https://github.com/mraj602/agent-doorbell/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mraj602/agent-doorbell/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/agent-doorbell.svg)](https://pypi.org/project/agent-doorbell/)
[![Python](https://img.shields.io/pypi/pyversions/agent-doorbell.svg)](https://pypi.org/project/agent-doorbell/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Let your AI agents sleep. Agent Doorbell watches Slack and wakes them only when someone
needs them.**

An AI agent that "watches Slack" usually does it by checking: every few minutes it spends a
model turn to find out that nothing happened. Agent Doorbell moves the watching into a tiny
local daemon with no LLM. It polls Slack with a read-only token, queues the messages meant for
you, and rings the agent (Claude Code, Codex, Gemini CLI or your own) only when there is
something to handle. **Zero tokens while nobody is calling.**

> **Unofficial.** Agent Doorbell is an independent project. It is not affiliated with,
> endorsed by or connected to Slack Technologies, Salesforce, Anthropic or OpenAI. "Slack",
> "Claude" and other names are trademarks of their owners, used only to describe
> compatibility.

```
Slack ──(read-only polls; none when no agent listens)──▶ doorbelld ──▶ per-agent queue
                                                             │ rings
your agent ◀── doorbell wait (blocks, costs nothing) ────────┘
     │  handles the batch: replies or drafts with its own Slack tool, or escalates to you
     └──▶ hooks and observed replies ack each item; unfinished items come back
```

## Why

- **No token waste.** The agent runs only when a message needs it. With no agents
  registered, the daemon makes no Slack calls at all.
- **It's your agent, with your context.** Replies come from your own agent, with your repos,
  tools and instructions, not from a generic bot.
- **Harness-neutral, and sending only with your approval.** Doorbell never touches your
  agent's settings. The agent replies with its own Slack tool under its own permissions, or
  through Doorbell: it writes drafts you approve with `doorbell review`, and
  sends directly only after you grant it with your password.
- **Nothing gets lost.** Items are leased and acked. A crashed or rate-limited agent gets them
  back. A laptop that was asleep catches up from where it stopped.
- **No server, no webhook, no public URL.** It runs on your laptop or dev box, on Slack's free
  plan.
- **Any agent, any OS.** Linux, macOS and Windows. Agents register themselves and use three
  commands: `wait`, `inbox --claim` and `ack`. Claude Code gets a plugin with a guided
  `/agent-doorbell:setup`, handle and listen skills, and hooks that ack automatically.

## Quick start

```bash
pipx install agent-doorbell              # or: uv tool install agent-doorbell

# 1. Start the watcher
doorbell service install

# 2. Sign in to Slack: open the link, click Allow. No token is shown or pasted.
#    Needs the Client ID of a Doorbell Slack app (see "The Slack app and the three layouts")
doorbell login --client-id <Client ID>

# 3. Create an agent folder, then edit its AGENTS.md (instructions) and sub.yaml (who to listen to)
doorbell init-agent ~/doorbell-agents/main --owner "Your Name"
claude plugin marketplace add mraj602/agent-doorbell   # Claude Code: skills and auto-ack hooks
claude plugin install agent-doorbell@agent-doorbell

# 4. Run it headless: one short agent run per batch. The agent registers itself.
#    Your agent's own flags decide what it may do; this one may only write drafts.
doorbell service install --dispatch main --dir ~/doorbell-agents/main -- \
    claude -p "/agent-doorbell:handle" --model sonnet --allowedTools "Bash(doorbell *)" \
    "mcp__plugin_slack_slack__slack_send_message_draft" \
    --disallowedTools "mcp__plugin_slack_slack__slack_send_message" "mcp__plugin_slack_slack__slack_schedule_message"
```

Optional: let agents reply through Doorbell with only `Bash(doorbell *)` allowed. In your own
terminal run `doorbell login --send` (add `--client-id <write app's Client ID>` to use a second
app). It asks for a password. Agents then write drafts that you approve with
`doorbell review`; `doorbell grant <agent>` lets one send directly. See
[Sending through Doorbell](docs/SETUP.md#sending-through-doorbell-optional). To sign out, run
`doorbell logout`.

On a server or over SSH, `doorbell login` prints the link. Open it on your laptop, click
Allow, then paste the `http://localhost:8765/...` address the browser ends on into the waiting
command. See [SETUP](docs/SETUP.md#on-a-server-over-ssh-or-headless).

The full walkthrough, with a check after every step, is in **[docs/SETUP.md](docs/SETUP.md)**.
It is written so an AI agent can follow it for you too. To use a hand-made token instead of
`doorbell login`, see [Manual token](docs/SETUP.md#manual-token-alternative).

## The Slack app and the three layouts

`doorbell login` works with an app that has PKCE on. Any workspace member can sign in, not
only app collaborators. A workspace admin sets up the app once (a manifest, or any existing
internal app) and shares its Client ID, which is not a secret:
**[docs/ORG-SETUP.md](docs/ORG-SETUP.md)**.

With no flags, `doorbell login` asks Slack for the read scopes only, then decides from what the
returned token can really do:

- It can't post: saved in a credentials file (mode 600), and watching is unattended. **Layout A.**
- It can post: Doorbell says why, and (only for the owner in a terminal) seals it with a
  password. One token watches and sends. **Layout B.** Anywhere else it refuses, revokes the
  token and saves nothing.

Slack adds up the scopes one person has approved for one app, across all of that person's
tokens for it. That is why a read-only sign-in can return a token that can post. Approvals are
per person, so on one app that allows write scopes, one member can be in layout A and another
in layout B.

| Layout | Apps | Agents can send through Doorbell | After a reboot |
|---|---|---|---|
| **A. Watch only**: read-only token in a file | 1 | No | Watching resumes by itself |
| **B. One sealed token** that watches and sends (`doorbell login --send`, same app) | 1 | Yes | Locked until `doorbell unlock`; nothing is missed |
| **C. Two apps**: read-only file token, plus a sealed token from a write app (`doorbell login --send --client-id <write app>`) | 2 different | Yes | Watching resumes by itself; sending needs `doorbell unlock` |

A token that can post is never stored in a plain file: it is sealed with your password.

### How sign-in works

`doorbell login` is Slack OAuth with PKCE and a localhost redirect (`http://localhost:8765/`):
no client secret, nobody needs to be an app collaborator, you click Allow once, and the token
is never shown. The login listener binds 127.0.0.1 and refuses any response whose `state` does
not match. Over SSH, open the link on a laptop and paste back the localhost address it ends
on, or tunnel the port with `ssh -L 8765:localhost:8765` (see
[SETUP](docs/SETUP.md#on-a-server-over-ssh-or-headless)). Tokens rotate every 12 hours and are
renewed automatically; if one can't be renewed, run `doorbell login` again (cursors are kept).
`doorbell logout` revokes and forgets them. `doorbell status` shows each identity's mode
(file or sealed) and status (`ok`, `locked`, `needs_login` or `broken`). The app settings an
admin needs are in [docs/ORG-SETUP.md](docs/ORG-SETUP.md).

## What the agent may do

You choose, in your agent's own permissions (see [SETUP step 6](docs/SETUP.md#6-decide-what-the-agent-may-do)):

| Level | The agent can | Good for |
|---|---|---|
| Triage | post nothing; sort items into escalated (with a summary) or ignored | a filter and a digest |
| Draft | write drafts in the right DM or thread; you review and send | starting out with replies |
| Reply | post as you, react, draft | routine questions you trust it with |

Or let Doorbell hold the send policy: give the agent only `Bash(doorbell *)`; it writes drafts you approve with `doorbell review`, and sends directly only after `doorbell grant`. See [Sending through Doorbell](docs/SETUP.md#sending-through-doorbell-optional).

## How it compares

| | Agent Doorbell | Agent checks Slack on a timer | Hosted bot + LLM API |
|---|---|---|---|
| Tokens while idle | **zero** | a model turn per check, all day | zero |
| Replies as | you, or drafts for you | you | a bot |
| Your repos and tools | yes | yes | no |
| Infrastructure | a local daemon | none | server, public URL or socket, API key |
| Catches up after downtime | yes | no | depends |

### Against the main alternatives

Facts from [docs/COMPARISON.md](docs/COMPARISON.md), researched in November 2026 from each
project's own docs. Products change quickly, so check the linked docs there before you rely on
a detail. "—" means the vendor's docs don't say.

| | Agent Doorbell | OpenClaw, Hermes Agent (self-hosted Socket Mode bots) | Claude in Slack, Cursor, Devin, ChatGPT, Copilot (hosted bots) | Slack MCP / agent's own Slack tool on a timer | n8n, Zapier, Make |
|---|---|---|---|---|---|
| How it hears about messages | Polling with cursors | Socket Mode WebSocket | Events API into the vendor's cloud | On demand: the agent decides to look | n8n: Events API; Zapier: mostly instant webhook; Make: polling (15 min by default) |
| Tokens while idle | **Zero** (no API calls when no agent listens) | Zero (OpenClaw has an optional 30-minute proactive job) | Zero; billed per use | A model turn per check, all day | Per workflow run, task or credit |
| Misses messages while down | **No**, it catches up | Yes (no catch-up documented) | No (vendor cloud) | Not watched at all | n8n: after the retry window; Zapier: no (cloud); Make: likely caught up |
| Replies as | **You**, or drafts for you ("Sent using @Claude" if the agent uses the Slack MCP) | A bot (OpenClaw can use a user token) | Their bot app | You, with a "Sent using @Claude" footer that can't be turned off | Bot or user token; Zapier: the Zapier app |
| Needs a public URL or server | **No**, a local daemon | No | No | No | n8n (self-hosted): yes; Zapier and Make: no, they run in the cloud |
| Free Slack plan | **Yes** (one of the 10 app slots) | — | Devin: yes; the earlier Claude in Slack app needs paid Slack; others — | Plan support unclear | Uses an app slot; Zapier: 100 tasks a month free |
| Works with your local repos and tools | **Yes** | Yes, they run on your machine | No, they run in the vendor's cloud | Yes, it is your own agent | — |
| Routes to several of your own agents | **Yes**, a subscription and queue per agent | OpenClaw: yes ("bindings"); Hermes: limited | No | No | Workflow branches, paths or routers |

Most self-hosted Slack agents (OpenClaw, Hermes Agent, Claude Code Slack bots) keep a Socket
Mode WebSocket open and lose messages sent while it's down; hosted bots (Claude in Slack,
Cursor, Devin) don't miss, but answer as a bot in their cloud, usually on paid plans.
Doorbell polls with cursors, so it catches up after downtime, with no public URL, on any
Slack plan.

**Use it** for an always-on watcher that never misses a message, a coordinator agent that
routes to project agents, free Slack workspaces without webhooks, and replies that look like
yours (or, through your agent's Slack MCP tool, are visibly from Claude). **Don't** use it for
replies within seconds, a team bot everyone @mentions, or if no machine of yours is ever on.
The full comparison of about 20 tools, use cases and sources is in
**[docs/COMPARISON.md](docs/COMPARISON.md)**.

## Documentation

- [Setup guide](docs/SETUP.md): install, `doorbell login`, first agent, test, troubleshooting
- [Slack app setup for a workspace](docs/ORG-SETUP.md): for admins; the app, PKCE, scope accumulation, sharing the Client ID
- [Compared with other tools](docs/COMPARISON.md): when to use it and when not, use cases
- [Architecture](docs/ARCHITECTURE.md): design, queue and acks, security model
- [Running agents for days](docs/RUNNING.md): live sessions vs headless dispatch, Claude Code limits
- [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## Status and limits

Runs on Linux, macOS and Windows. One person per daemon. Replies arrive
within about 30 seconds, not instantly. See
[Limits and known gaps](docs/ARCHITECTURE.md#14-limits-and-known-gaps).

## License

[MIT](LICENSE).

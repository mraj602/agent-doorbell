# Agent Doorbell for Slack

[![ci](https://github.com/mraj602/agent-doorbell/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mraj602/agent-doorbell/actions/workflows/ci.yml)
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
- **Read-only and harness-neutral.** Doorbell itself never posts and never touches your
  agent's settings. Whether the agent replies, drafts or only triages is set where it
  belongs: in your agent's own permissions (for Claude Code, one command-line flag).
- **Nothing gets lost.** Items are leased and acked. A crashed or rate-limited agent gets them
  back. A laptop that was asleep catches up from where it stopped.
- **No server, no webhook, no public URL.** It runs on your laptop or dev box, on Slack's free
  plan.
- **Any agent, any OS.** Linux, macOS and Windows. Agents register themselves and use three
  commands: `wait`, `inbox --claim` and `ack`. Claude Code also gets optional slash commands
  and hooks that ack automatically.

## Quick start

```bash
pipx install agent-doorbell              # or: uv tool install agent-doorbell

# 1. Create your own read-only Slack app from slack-app-manifest.yaml and save its xoxp- token
mkdir -p ~/.config/doorbell
( umask 077; read -rsp 'Slack user token: ' t; echo; printf '%s\n' "$t" > ~/.config/doorbell/slack-token )

# 2. Start the watcher and add the token
doorbell service install
doorbell identity add --token-file ~/.config/doorbell/slack-token

# 3. Create an agent folder, then edit its AGENTS.md (instructions) and sub.yaml (who to listen to)
doorbell init-agent ~/doorbell-agents/main --claude-code --owner "Your Name"

# 4. Run it headless: one short agent run per batch. The agent registers itself.
#    Your agent's own flags decide what it may do; this one may only write drafts.
doorbell service install --dispatch main --dir ~/doorbell-agents/main -- \
    claude -p --model sonnet /doorbell-handle --allowedTools "Bash(doorbell *)" \
    "mcp__plugin_slack_slack__slack_send_message_draft" \
    --disallowedTools "mcp__plugin_slack_slack__slack_send_message" "mcp__plugin_slack_slack__slack_schedule_message"
```

On Windows, save the token with the PowerShell lines in the setup guide. The full
walkthrough, with a check after every step, is in **[docs/SETUP.md](docs/SETUP.md)**. It is
written so an AI agent can follow it for you too.

## What the agent may do

You choose, in your agent's own permissions (see [SETUP step 6](docs/SETUP.md#6-decide-what-the-agent-may-do)):

| Level | The agent can | Good for |
|---|---|---|
| Triage | post nothing; sort items into escalated (with a summary) or ignored | a filter and a digest |
| Draft | write drafts in the right DM or thread; you review and send | starting out with replies |
| Reply | post as you, react, draft | routine questions you trust it with |

Sending through Doorbell itself, with drafts you approve from the terminal, is planned for the next release.

## How it compares

| | Agent Doorbell | Agent checks Slack on a timer | Hosted bot + LLM API |
|---|---|---|---|
| Tokens while idle | **zero** | a model turn per check, all day | zero |
| Replies as | you, or drafts for you | you | a bot |
| Your repos and tools | yes | yes | no |
| Infrastructure | a local daemon | none | server, public URL or socket, API key |
| Catches up after downtime | yes | no | depends |

Why polling and not Slack's Events API or webhooks? Polling needs no public URL or always-on
socket, and it can catch up after downtime, which events can't. The comparison table,
pros and cons, and the security model are in **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

## Documentation

- [Setup guide](docs/SETUP.md): install, Slack app, first agent, test, troubleshooting
- [Architecture](docs/ARCHITECTURE.md): design, queue and acks, security model, comparisons
- [Running agents for days](docs/RUNNING.md): live sessions vs headless dispatch, Claude Code limits
- [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## Status and limits

Version 0.0.1: early, read-only. Linux is the tested platform; macOS and Windows should work
but are not covered by CI yet. One person per daemon. Replies arrive
within about 30 seconds, not instantly. See
[Limits and known gaps](docs/ARCHITECTURE.md#14-limits-and-known-gaps).

## License

[MIT](LICENSE).

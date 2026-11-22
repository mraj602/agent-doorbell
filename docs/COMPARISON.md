# Agent Doorbell compared: when to use it, and when not to

There are many ways to put an AI agent on Slack. This page compares the realistic options,
maps them to use cases, and says plainly when something else is the better choice.
Researched in November 2026 from each project's own docs; sources are at the end. Products
change quickly, so check the linked docs before you rely on a detail.

**In one line:** Agent Doorbell is a personal, local watcher that never misses a message
(it catches up after sleep, restarts and crashes), costs nothing while quiet, routes to
several of your own agents, works on any Slack plan with no public URL, and can reply so it
looks like you typed it. It is the wrong tool if you need replies within seconds, a bot the
whole team can @mention, or nothing running on your own machine.

## 1. How the options connect to Slack

How a tool hears about new messages decides whether it can miss them.

| Model | Who uses it | Needs | Messages sent while it's down |
|---|---|---|---|
| **Polling the Web API with cursors** | Agent Doorbell; Make's "Watch" modules | A token; a machine that is on some of the time | **Caught up** when it comes back |
| **Socket Mode** (a WebSocket the app keeps open) | OpenClaw, Hermes Agent, ZeroClaw, most "Claude Code Slack bot" projects, Bolt apps | A process that stays connected; an app-level token | **Lost.** Slack's docs: "you may lose events until you establish a connection" |
| **Events API** (Slack calls your HTTPS URL) | Hosted products (Claude, Cursor, Devin, ChatGPT, GitHub, Zapier), n8n, Bolt apps | A public URL that is always up | Retried 3 times over about 5 minutes, or hourly for 24 hours with *Delayed Events*; then lost. Slack calls it "a best-effort system" |
| **On demand (MCP)** | Slack's official MCP server and the Claude Slack connector; community MCP servers | An agent that decides to look | Not watched at all: nothing happens until the agent asks |
| **Browser session token** | mautrix-slack (a Matrix bridge), "stealth" MCP servers | Your `xoxc`/`xoxd` browser login | mautrix-slack backfills; MCP servers don't watch. Using session tokens is not a supported API and may break Slack's terms |

Hosted products don't run anything on your laptop: Slack pushes events to the vendor's
cloud, which is always on, so they don't miss mentions. Their trade-offs are different: they
answer when @mentioned, reply as a bot or with an AI label, run in a cloud sandbox without
your local repos, and usually need paid plans and a Slack admin.

## 2. The options side by side

| Tool | Connection | Public URL | Misses messages while down | Replies appear as | Slack Free plan | Cost while idle | Routes to several of your agents | Runs |
|---|---|---|---|---|---|---|---|---|
| **Agent Doorbell** | Socket Mode on your own app, else polling (search or history) | No | **No** (catches up) | **You**, or "Sent using @Claude" if the agent uses the Slack MCP | Yes (one of the 10 app slots) | **Zero** (no API calls when no agent listens) | **Yes** (subscriptions and queues per agent) | Your machine; MIT, free |
| **OpenClaw** | Socket Mode (or webhook / relay) | No / Yes | Yes (auto-reconnect only, no catch-up documented) | A bot, or you with its user-token mode | Not stated | Zero per message; optional 30-min proactive job | Yes ("bindings") | Your machine; MIT, free |
| **Hermes Agent**, **ZeroClaw** | Socket Mode | No | Yes (not documented) | A bot | Not stated | Zero | Limited | Your machine; open source |
| **Claude Code Slack bots** (mpociot, claude-code-slack-channel, …) | Socket Mode | No | Yes (claude-code-slack-channel makes *replies* crash-safe, not missed events) | A bot | Not stated | Zero, but some need an open Claude Code session | Some | Your machine; hobby to early |
| **Claude in Slack / Claude Tag** (Anthropic) | Events API → Anthropic cloud | No | No (vendor cloud) | The Claude app in channels | Earlier app: paid Slack only. Claude Tag: Claude Team/Enterprise plan | Usage-billed; scheduled watching at most hourly | No (per-channel setup) | Anthropic's cloud |
| **Slack MCP server / Claude Slack connector** | On demand | No | Doesn't watch | You, with a "Sent using @Claude" footer that can't be turned off | Plan support unclear | Only when the agent checks (costs model tokens each time) | No | Slack / Anthropic |
| **Cursor, Devin, ChatGPT (Codex), GitHub Copilot** in Slack | Events API → vendor cloud | No | No (vendor cloud) | Their bot app | Devin: yes; others not stated | Usage-billed | No (your local agents can't join) | Vendor cloud; paid |
| **Bolt app** you build | Socket Mode or Events API | No / Yes | Socket Mode: yes. Webhook: after the retry window | Your choice (bot or user token) | Not restricted by the docs | Yours | You build it | Wherever you host it |
| **n8n** Slack Trigger | Events API | **Yes** (self-hosted) | After the retry window | Bot or user token | Uses an app slot | Per workflow run | Workflow branches | Self-hosted or cloud |
| **Zapier** | Mostly instant (webhook) | No | No (vendor cloud) | The Zapier app | Uses an app slot; 100 tasks/month free | Per task | Zap paths | Cloud |
| **Make** | Polling (15 min by default) | No | Likely caught up | You (user connection) | Uses an app slot. Not in the Marketplace, so Slack's reduced history rate limit applies | Per credit | Router module | Cloud |
| **Pipedream** | Instant sources | No | Not documented | You or a bot | Not documented | Per credit | Workflows | Cloud; **workflows shut down 31 March 2027** |
| **mautrix-slack** (Matrix bridge) | Browser session | No | No (backfills) | You (puppeted) | Yes | n/a: you still need an agent on Matrix | Via Matrix | Self-hosted; AGPL |
| **Matterbridge** | Legacy RTM | No | Yes | A bridge bot | New Slack apps can't use RTM | n/a | n/a | Dormant since 2023 |
| **Agent polls Slack itself** (`/loop`, cron + Slack MCP) | On demand, on a timer | No | Re-reads or misses, depending on the prompt | You (MCP footer applies) | Same as the MCP | **A model turn per check, all day** | Each agent re-reads everything | Your agent |

"Not stated" means the vendor's docs don't say either way; check before you depend on it.

## 3. Which fits which use case

| Use case | Best fit | Also consider | Why |
|---|---|---|---|
| **Always-on watcher that never misses a message; replies needn't be instant** | **Agent Doorbell** | An Events API app on an always-on server with Delayed Events (needs a public URL); mautrix-slack | Doorbell keeps a cursor per conversation and leases and acks per item, so sleep, restarts and crashes lose nothing. Socket Mode bots document no catch-up. Hosted bots don't miss, but only answer @mentions |
| **A coordinator agent that routes to project agents and applies policy** | **Agent Doorbell** | OpenClaw (static bindings); n8n (build it yourself) | Agents register and deregister themselves, each with its own subscription and queue; drafts, grants and the audit log give a policy layer (see [section 4](#4-use-cases-in-more-detail)) |
| **Two people's agents talking to each other** | **Doorbell on both sides** (experimental) | OpenClaw in user-token mode; two Bolt bots | With user tokens, each side sees the other's messages as a person's message. Needs loop rules (see below) |
| **Free Slack workspace, no webhooks, no missed messages** | **Agent Doorbell** | Zapier (100 tasks/month), Make (polling, reduced rate limit), Devin (works on Free Slack, paid product) | The earlier Claude in Slack app needs paid Slack; Claude Tag needs a Claude Team/Enterprise plan; Socket Mode bots miss messages while down |
| **Replies should look like you typed them** | **Doorbell sending**, OpenClaw user-token mode, Make user connections | A Slack draft (agent writes it, you send it) | The Slack MCP / Claude connector, Claude, Cursor, Devin, ChatGPT and Copilot are all visibly AI |
| **Replies should say they're from AI** | **Doorbell with the agent's Slack MCP tool** ("Sent using @Claude"), or Doorbell's `send_footer` | Claude in Slack, Claude Tag | Transparency with your colleagues |
| **A bot the whole team @mentions; instant answers** | Claude Tag, Devin, Cursor, a Bolt bot | — | Doorbell is one person's watcher (instant only with Socket Mode on your own app) |
| **Nothing running on my machine** | Claude Tag routines, Devin, Copilot, Zapier | — | Doorbell needs a machine that is on some of the time (it catches up when it's back) |

## 4. Use cases in more detail

### A server that doesn't miss messages

Run `doorbelld` on an always-on box (a home server or a small VM) with
`doorbell service install`, and the agents next to it with `doorbell dispatch`. Messages arrive
in under a second with Socket Mode on your own app, or within about 30 seconds for active
conversations when polling. If the box reboots, the daemon picks up from its cursors and a
search sweep; if an agent crashes mid-item, the lease expires and the item comes back;
after a few failed attempts it is marked stuck and you get a notification. Nothing depends on
a WebSocket staying up or on Slack retrying a webhook: the socket only makes delivery faster.

### A coordinator agent with project agents behind it

One pattern for many projects:

- A **coordinator** agent folder subscribes to everything that should reach you (your DMs,
  mentions, the channels you care about). Its `AGENTS.md` holds the policy: who gets an
  answer, what is never answered, when to ask you.
- **Project agents** (one per repo or area) are the experts. Either they have their own
  narrower Doorbell subscriptions (`channels: ["#payments"]`), and Doorbell routes to them
  directly, or the coordinator hands them questions with whatever your harness offers
  (Claude Code subagents, a `claude -p` or `codex exec` call in the project folder).
- The coordinator applies the policy to the answer: reply, draft for you
  (`doorbell draft`), escalate, or ignore. Granted agents can send directly
  (`doorbell grant`); everything is in `doorbell log`.
- Agents register and deregister themselves as they start and stop, so adding a project
  agent is adding a folder with `doorbell init-agent`.

Doorbell provides the queue, routing by subscription, acks and the send policy. Routing *by
meaning* (reading a message and deciding which expert should answer) is the coordinator's
job, written in its `AGENTS.md`; Doorbell has no LLM.

### Two people's agents talking to each other

If you and a colleague both run Doorbell, your agent's reply (posted with your user token) is
a normal message from you to them, so their agent wakes up, and the other way round. That
makes agent-to-agent conversations possible with no extra infrastructure, in a DM or a shared
channel. It is experimental: add explicit rules to both `AGENTS.md` files (answer only
questions, stop after one exchange, escalate to the human otherwise), keep the hourly send
limit low, and prefer drafts until you trust it. Doorbell never queues your own messages, so
an agent can't wake itself.

### Free Slack workspaces

Free plans allow ten apps (Doorbell takes one) and hide messages older than 90 days, which
only matters if the watcher is off for more than 90 days. Doorbell needs no webhook, public
URL or paid feature, and it doesn't depend on Slack's AI features, some of which need a paid
plan.

## 5. Replying: through your agent's Slack tool, or through Doorbell

Both work, and you can mix them per agent:

| | **Agent's Slack MCP tool** (e.g. the Claude Slack connector) | **Doorbell sending** | **Draft** |
|---|---|---|---|
| Who it appears from | You, plus a "Sent using @Claude" footer that can't be turned off | You, like a message you typed. Add a footer with `send_footer` if you want one | You, when you send it |
| Who decides what may be sent | Your harness's permissions | Doorbell: your password, per-agent grants, hourly limit, only into conversations that reached that agent | You, every time |
| Setup | The Slack plugin or connector in your agent | A second Slack app and `doorbell sender add` | Either of the two |
| Works the same for every harness | No | Yes | Yes with `doorbell draft` |

Two details worth knowing. The "Sent using @Claude" footer is reported by users to be a real
mention of the Claude app, so in channels where Anthropic's Claude app is installed it may
wake it. And a message posted with a user token through any app, Doorbell included, still
carries that app's ID in the API data: it looks typed by you in Slack, but it isn't
indistinguishable to someone reading the raw API. If your workplace expects AI-written
messages to be labelled, label them.

## 6. Rate limits

In May 2025 Slack cut `conversations.history` and `conversations.replies` to 1 request a
minute and 15 messages for apps *commercially distributed outside the Slack Marketplace*.
Slack's changelog: "Internal customer-built applications are not impacted by these changes
and continue to have a rate limit of 1,000 messages per request at 50+ requests per minute."
Each Doorbell user creates their own app from the manifest in their own workspace, and
Doorbell is free and not sold, so it is an internal app and keeps the normal limits. A paid
or hosted version of Doorbell would be commercial distribution and would change this.

Doorbell budgets its own calls (at most 40 a minute, and a fair share of each Slack pool when
`team_size` people share one app). With `search:read`, one search call every 2 minutes covers
every conversation a person is in, so the watch list no longer sets the cost. See
[design/detection.md](design/detection.md).

## 7. When not to use Agent Doorbell

- **You need replies within seconds on a shared app.** Doorbell is instant only with Socket Mode
  on an app that is yours alone; on a shared app it polls (about 30 s for active conversations,
  about 2 minutes for quiet ones). Use your own app, or a Socket Mode or Events API bot.
- **You want a team bot anyone can @mention.** Doorbell is one person's watcher with one
  person's token. Use Claude Tag, Devin, Cursor or a Bolt bot.
- **No machine of yours is ever on.** Use a hosted product.
- **You watch hundreds of busy channels.** The polling budget makes coverage slow; an Events
  API app scales better.
- **You need triggers on search results, files, canvases, reactions or edits.** Doorbell
  watches messages.
- **Your workspace forbids custom apps or user tokens, or requires AI-labelled messages.** Use
  the admin-approved official options, which label their posts.
- **You plan to sell or host it for others.** That is commercial distribution under Slack's
  API terms and its reduced rate limits.
- **You're on macOS or Windows and need it to be rock solid today.** Linux is the tested
  platform.

## Sources

Slack: [rate-limit changes for non-Marketplace apps (2025-05-29)](https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps),
[clarification (2025-06-03)](https://docs.slack.dev/changelog/2025/06/03/rate-limits-clarity),
[Events API](https://docs.slack.dev/apis/events-api/),
[Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode),
[legacy RTM](https://docs.slack.dev/legacy/legacy-rtm-api),
[Slack MCP server](https://docs.slack.dev/ai/slack-mcp-server),
[developing agents](https://docs.slack.dev/ai/developing-agents),
[free workspace limits](https://slack.com/help/articles/115002422943),
[API terms](https://slack.com/terms-of-service/api).
Anthropic: [Claude Slack connector](https://claude.com/docs/connectors/slack/index),
[Claude Code in Slack](https://code.claude.com/docs/en/slack),
[Claude Tag](https://claude.com/docs/claude-tag/overview),
[Claude Tag agent identity](https://claude.com/docs/claude-tag/concepts/agent-identity),
[Claude Tag routines](https://claude.com/docs/claude-tag/users/proactivity),
[Claude Code channels](https://code.claude.com/docs/en/channels);
"Sent using @Claude" footer: [slackapi/slack-skills-plugin#25](https://github.com/slackapi/slack-skills-plugin/issues/25),
[anthropics/claude-code#72562](https://github.com/anthropics/claude-code/issues/72562).
Others: [OpenClaw Slack](https://docs.openclaw.ai/channels/slack),
[OpenClaw multi-agent](https://docs.openclaw.ai/concepts/multi-agent),
[Hermes Agent Slack](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/messaging/slack.md),
[ZeroClaw Slack](https://github.com/zeroclaw-labs/zeroclaw/blob/master/docs/book/src/channels/slack.md),
[mpociot/claude-code-slack-bot](https://github.com/mpociot/claude-code-slack-bot),
[claude-code-slack-channel](https://github.com/jeremylongshore/claude-code-slack-channel),
[korotovsky/slack-mcp-server](https://github.com/korotovsky/slack-mcp-server),
[Cursor](https://cursor.com/docs/integrations/slack),
[Devin](https://docs.devin.ai/integrations/slack),
[ChatGPT in Slack](https://learn.chatgpt.com/docs/third-party/slack),
[GitHub Copilot in Slack](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/integrate-coding-agent-with-slack),
[n8n Slack Trigger](https://docs.n8n.io/integrations/builtin/trigger-nodes/n8n-nodes-base.slacktrigger/),
[Zapier](https://help.zapier.com/hc/en-us/articles/8495993391629),
[Make](https://apps.make.com/slack),
[Pipedream pricing](https://pipedream.com/docs/pricing),
[Matterbridge](https://github.com/42wim/matterbridge),
[mautrix-slack](https://docs.mau.fi/bridges/go/slack/authentication.html).

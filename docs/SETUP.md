# Setup guide

This guide takes you from nothing to an agent that handles your Slack messages. It takes about
15 minutes on Linux, macOS or Windows. Every step ends with a **check**, so you, or an AI agent
doing the setup for you, can confirm it worked before moving on.

> **Using Claude Code?** Install the plugin and let it walk you through everything below:
>
> ```
> /plugin marketplace add mraj602/agent-doorbell
> /plugin install agent-doorbell@agent-doorbell
> /agent-doorbell:setup
> ```
>
> **Another AI agent?** Point it at this file and say "follow docs/SETUP.md". There is a
> section for agents at the end: [Notes for AI agents doing the setup](#notes-for-ai-agents-doing-the-setup).

**Who does what.** You do the steps that need you: signing in to Slack (one click on Allow),
starting the watcher, and deciding what the agent may do. The agent does its own part: it registers
itself and starts listening.

| Step | Who |
|---|---|
| [1. Install](#1-install) | You (or your agent) |
| [2. Start the watcher](#2-start-the-watcher) | You (or your agent) |
| [3. Get a Slack app and its Client ID](#3-get-a-slack-app-and-its-client-id) | You, or whoever manages the workspace |
| [4. Sign in with `doorbell login`](#4-sign-in-with-doorbell-login) | **You**, one click in a browser |
| [5. Create an agent folder](#5-create-an-agent-folder) | You (or your agent) |
| [6. Decide what the agent may do](#6-decide-what-the-agent-may-do) | **You** |
| [7. Run the agent](#7-run-the-agent) | You start it; it registers itself |
| [8. Test it end to end](#8-test-it-end-to-end) | You, plus someone to message you |

Also: [Everyday use](#everyday-use) · [Several agents](#several-agents) · [Configuration](#configuration) ·
[Troubleshooting](#troubleshooting) · [Uninstall](#uninstall)

## Before you start

You need:

- **Linux, macOS or Windows**, with Python 3.10 or newer.
- **A Slack workspace, and the Client ID of a Doorbell Slack app in it.** Free plans work.
  Anyone in the workspace can sign in to an existing app; creating one needs permission to
  create apps (see [ORG-SETUP.md](ORG-SETUP.md)). Some workspaces require an admin to approve
  apps; if so, the first sign-in asks for approval.
- **An agent CLI with a non-interactive mode**: Claude Code (`claude -p`), Codex
  (`codex exec`), Gemini CLI (`gemini -p`), or your own script.
- **If the agent should reply**, a Slack tool in that agent, for example the Slack plugin for
  Claude Code (`/plugin install slack@claude-plugins-official`, then sign in when it asks).
  If you only want triage (summaries of what needs you), no Slack tool is needed.

Agent Doorbell watches Slack with a read-only token. You get it by signing in with
`doorbell login`, which never shows it to you or to an agent. Replies come either from your
agent's own Slack tool, under the rules your agent's harness enforces, or, if you set it up,
through Doorbell itself with your approval
([Sending through Doorbell](#sending-through-doorbell-optional)).

## 1. Install

```bash
pipx install agent-doorbell
# or: uv tool install agent-doorbell
# or the latest from GitHub: pipx install git+https://github.com/mraj602/agent-doorbell
```

**Check:** `doorbell --version` prints `doorbell 0.1.0` or newer.

## 2. Start the watcher

```bash
doorbell service install
```

This installs and starts `doorbelld` as a per-user service that starts at login: a systemd
user unit on Linux, a launchd agent on macOS, a Task Scheduler task (`AgentDoorbell\doorbelld`)
on Windows. On Linux, also run `loginctl enable-linger $USER` if it should keep running while
you are logged out. Without a service manager, run `doorbell daemon` under your own supervisor
(tmux, supervisord, a container).

**Check:** `doorbell status` prints JSON with `"state": "DORMANT"`. Dormant means no agents
yet, so it makes zero Slack API calls.

## 3. Get a Slack app and its Client ID

`doorbell login` needs the **Client ID** of a Slack app that has PKCE on. The Client ID is not
a secret, and there is no client secret.

- **Your workspace already has one.** Ask whoever set it up for the Client ID. You don't need
  to be an app collaborator. Skip to step 4.
- **You create it** (or you are the admin). Pick a layout, create the app from its manifest,
  and copy the Client ID from **Basic Information**. [ORG-SETUP.md](ORG-SETUP.md) has the
  details and the trade-offs.

| Layout | Apps | Manifest | Agents can send through Doorbell |
|---|---|---|---|
| **A. One read-only app** | 1 | [`slack-app-manifest.yaml`](../slack-app-manifest.yaml) | No |
| **B. One read+write app** (single-token mode) | 1 | [`slack-app-manifest-readwrite.yaml`](../slack-app-manifest-readwrite.yaml) | Yes. After a reboot you run `doorbell unlock` |
| **C. Two apps** | 2 | `slack-app-manifest.yaml` + [`slack-app-manifest-sender.yaml`](../slack-app-manifest-sender.yaml) | Yes. Watching resumes by itself after a reboot |

To create an app: open <https://api.slack.com/apps>, choose **Create New App → From a
manifest**, pick your workspace, paste the manifest (YAML tab), and create it. If your
workspace requires admin approval for apps, it is requested at first sign-in.

Then give Doorbell the Client ID, either once, for all later commands:

```bash
export DOORBELL_SLACK_CLIENT_ID=<Client ID>      # or put slack_client_id: <Client ID> in config.yaml
```

or with `--client-id <Client ID>` on each `doorbell login`. The daemon reads `config.yaml`
when it starts, so if you set the config key, restart it (`doorbell service install` again).

> **PKCE is one-way.** The manifests turn it on, which makes the app a public client. Only
> Slack support can turn it off again. This matters only if you add PKCE to an existing app
> that other tools use; see [ORG-SETUP.md](ORG-SETUP.md#enabling-pkce-on-an-existing-app-is-one-way).

**Check:** you have a Client ID that looks like `1234567890.1234567890`.

## 4. Sign in with `doorbell login`

Run the command for your layout. Each one prints `Open this link to sign in: <url>` and opens
your browser. Click **Allow** on Slack's page. The browser then lands on a page that says
"Signed in to Doorbell, you can close this tab". The token is never shown and never pasted.

### Layout A: read-only

```bash
doorbell login --client-id <Client ID>
```

`--for watch` is the default. The token is saved in a credentials file in the state folder
(`credentials/<your user ID>.json`, mode 600) and added as your identity.

**Check:** the output shows your Slack user ID and workspace. `doorbell doctor` shows your
identity with `"token": "ok"` and `"missing_scopes": []`.

### Layout B: one read+write app

In your own terminal, not inside an agent:

```bash
doorbell login --for both --client-id <Client ID>
```

After you click **Allow**, it asks for a password (new on the first run; 8 characters or
more). No file is written: the one token is sealed with your password, and used both for
watching and for sending.

**Check:** `doorbell status` shows your identity with `"mode": "sealed"` and `"status": "ok"`,
and `"sending"` with `"unlocked": true`.

After every restart of the daemon (a reboot, an update) the identity is `locked`: it isn't
polled, and you get a notification. Run `doorbell unlock`. Polling resumes from where it
stopped, so no message is missed.

### Layout C: two apps

Watching first (an agent may run this one; you only click Allow):

```bash
doorbell login --client-id <read app's Client ID>
```

Then sending, in your own terminal:

```bash
doorbell login --for send --client-id <write app's Client ID>
```

It asks for a sending password, and seals the token with it. This is the same as
[`sender add`](#sending-through-doorbell-optional), without a token file.

**Check:** `doorbell doctor` shows your identity `"token": "ok"`. For sending, `doorbell
status` shows `"sending"` with your user and `"unlocked": true`.

### Options

| Option | Meaning |
|---|---|
| `--client-id ID` | The app's Client ID. Default: config `slack_client_id`, else `DOORBELL_SLACK_CLIENT_ID`. |
| `--for watch\|send\|both` | What to sign in for (default `watch`). `send` and `both` ask for the password and refuse to run inside an agent. |
| `--redirect-uri URI` | Default `http://localhost:8765/doorbell/callback`. Must be under a redirect URL registered in the app. Doorbell listens on that port. |
| `--team T` | Pick the workspace when you belong to several. |
| `--no-browser` | Print the link and don't open a browser. See below. |
| `--timeout 300` | Seconds to wait for you to click Allow. |
| `--as USER_ID` | Which identity, when you have several. |

**On a server or over SSH** (no browser on the box): run with `--no-browser`, then open the
link on a machine that can reach the box's port 8765. The easy way is a tunnel from your
laptop, `ssh -L 8765:localhost:8765 user@box`, then open the link in your laptop's browser.
Slack redirects to `localhost:8765`, which the tunnel carries back to the box.

### How tokens last

Some Slack apps issue tokens that rotate. Doorbell renews them by itself, about an hour before
they expire, while the daemon runs. With PKCE the refresh token lasts 30 days, so a machine
that is off for longer needs `doorbell login` again. If renewal fails, the identity shows
`"status": "needs_login"`, you get a notification, and `doorbell status` and `doorbell doctor`
say to run `doorbell login`. Queued items and cursors are kept. In layout B a sealed token is
renewed only while unlocked, so it is renewed at `doorbell unlock` if it expired while locked.

### Manual token (alternative)

If you can't use `doorbell login` (for example the app has no PKCE), you can still create a
User OAuth Token by hand and give Doorbell the file. This works as before:

1. Create the app from [`slack-app-manifest.yaml`](../slack-app-manifest.yaml) (you may remove
   the `pkce_enabled` and `redirect_urls` lines), install it, and copy the **User OAuth
   Token** (`xoxp-`). Only app collaborators can install the app this way.
2. Save it to a file only you can read. Paste the token when asked; it isn't echoed or stored
   in your shell history.

   Linux and macOS:

   ```bash
   mkdir -p ~/.config/doorbell
   ( umask 077; read -rsp 'Slack user token: ' t; echo; printf '%s\n' "$t" > ~/.config/doorbell/slack-token )
   ```

   Windows (PowerShell). Files in your profile are private to you by default:

   ```powershell
   New-Item -ItemType Directory -Force "$HOME\.config\doorbell" | Out-Null
   $s = Read-Host 'Slack user token' -AsSecureString
   [Net.NetworkCredential]::new('', $s).Password | Set-Content -NoNewline "$HOME\.config\doorbell\slack-token"
   ```

3. Add it: `doorbell identity add --token-file ~/.config/doorbell/slack-token`.

**Check:** the file contains one line starting with `xoxp-` (on Linux and macOS,
`ls -l` shows `-rw-------`). `identity add` shows your user and `"warning": null`, and
`doorbell doctor` shows `"token": "ok"`. A manual token doesn't renew. A token that can post
(`chat:write`) belongs in `doorbell sender add`, not in a plain file.

## 5. Create an agent folder

```bash
doorbell init-agent ~/doorbell-agents/main --owner "Your Name"
```

Use a folder of its own, not a project repo. Repos often have their own `AGENTS.md` or
`CLAUDE.md`, which `init-agent` won't overwrite, so the agent would miss its Doorbell
instructions. Give the agent read access to repos when it runs instead (step 6).

It writes:

| File | Purpose |
|---|---|
| `.doorbell-agent` | The agent's name (`main`, from the folder name; change it with `--name`). |
| `sub.yaml` | Whose messages and which channels the agent listens to. |
| `AGENTS.md` | The agent's instructions: what to answer, ignore or leave for you, and the queue commands. Codex, Gemini CLI and most other agents read it. |
| `CLAUDE.md` | One line, `@AGENTS.md`, so Claude Code reads the same instructions. |
Doorbell never writes your agent's permission settings. Those stay yours (step 6).

**Claude Code: install the plugin** (once per machine, not per folder):

```
/plugin marketplace add mraj602/agent-doorbell
/plugin install agent-doorbell@agent-doorbell
```

From a shell: `claude plugin marketplace add mraj602/agent-doorbell` and
`claude plugin install agent-doorbell@agent-doorbell`. It adds:

| Part | What it does |
|---|---|
| `/agent-doorbell:handle` | Claim the queue once, handle every item, stop. For headless runs. |
| `/agent-doorbell:listen` | Live mode: wait in the background, handle messages as they arrive. |
| `/agent-doorbell:setup`, `/agent-doorbell:status` | This guide as a skill; a plain-language status. |
| Hooks | Ack an item when the agent replies, drafts or reacts in Slack, and stop it from ending a turn with claimed items unresolved. Active only in agent folders (with `.doorbell-agent`). |

The plugin grants no permissions either.

**Check:** `ls -a ~/doorbell-agents/main` lists the files above.

## 6. Decide what the agent may do

Two parts: what the agent *should* do (instructions), and what it *can* do (your harness's
permissions).

**Instructions.** Edit `AGENTS.md`. Fill in the `TODO` lines: who you are, what you own, which
repos or docs the agent may read, how it may reply, and recurring requests. Keep the
"Leave for you" and "Rules that always apply" sections.

**Who it listens to.** Edit `sub.yaml`. The defaults are: all your DMs, group DMs, @mentions of
you, and threads you posted in. Add people with `users: ["@alex"]` and channels with
`channels: ["#team-x"]`. Start small; you can widen it later.

**Permissions.** Instructions can be argued with: a message could try to talk the agent into
something. So set the hard limit in your agent's harness. Pick one of three levels:

| Level | The agent may | Good for |
|---|---|---|
| Triage | Run `doorbell` commands only. It sorts items into "escalated" (with a summary) and "ignored". | A filter and summaries. Safest. |
| Draft | Also write Slack drafts, which you review and send. | Starting out with replies. |
| Reply | Also post and react as you. | When you trust the setup. |

**Claude Code.** In headless runs (`claude -p`), tools that aren't allowed are denied rather
than asked about, so the command line is the limit. Use these flags (the names are for the
Slack plugin; for the claude.ai connector use the prefix `mcp__claude_ai_Slack__`):

```bash
# Triage
--allowedTools "Bash(doorbell *)"
# Draft
--allowedTools "Bash(doorbell *)" "mcp__plugin_slack_slack__slack_send_message_draft" \
--disallowedTools "mcp__plugin_slack_slack__slack_send_message" "mcp__plugin_slack_slack__slack_schedule_message"
# Reply
--allowedTools "Bash(doorbell *)" "mcp__plugin_slack_slack__slack_send_message" \
    "mcp__plugin_slack_slack__slack_send_message_draft" "mcp__plugin_slack_slack__slack_add_reaction"
```

Rules from your own Claude Code settings still apply, and a deny always wins over an allow.
That's why the draft line denies sending explicitly, in case your settings allow it elsewhere.
Don't run the agent with `--dangerously-skip-permissions` or a `bypassPermissions` default mode.

**Or let Doorbell hold the send policy.** Give the agent only the triage line above
(`Bash(doorbell *)`) and set up [Sending through Doorbell](#sending-through-doorbell-optional):
the agent can then write drafts that you approve, and send directly only after you grant it
with your password. This works the same for every harness.

**Other agents.** Use their own controls: Codex sandbox and approval settings, Gemini CLI tool
settings, or simply don't give the agent a Slack tool (triage).

If the agent should read repos outside its folder, give it access when it runs, for example
`--add-dir ~/code/my-service` for Claude Code. Put `--add-dir` after the prompt: it takes
several values, as do `--allowedTools` and `--disallowedTools`.

**Check:** `grep -c TODO AGENTS.md` prints `0`, or only TODOs you chose to leave.

## 7. Run the agent

You don't register the agent: it registers itself (`doorbell register`, using `sub.yaml`) the
first time it runs. A dispatcher also registers it if needed. Pick one way to run it.
Headless is recommended for anything that runs for days; see [RUNNING.md](RUNNING.md) for why.

**A. Headless with Claude Code (recommended).** A service waits for work and starts a fresh,
short `claude -p` run for each batch. Needs the plugin from step 5. Add the flags for your
level from step 6:

```bash
doorbell service install --dispatch main --dir ~/doorbell-agents/main -- \
    claude -p "/agent-doorbell:handle" --model sonnet --allowedTools "Bash(doorbell *)" \
    "mcp__plugin_slack_slack__slack_send_message_draft" \
    --disallowedTools "mcp__plugin_slack_slack__slack_send_message" "mcp__plugin_slack_slack__slack_schedule_message"
```

**B. Headless with another agent.** Same idea; the agent reads AGENTS.md:

```bash
doorbell service install --dispatch main --dir ~/doorbell-agents/main -- \
    codex exec "Follow AGENTS.md: handle the doorbell queue once, then stop."
```

**C. Live Claude Code session** (good for watching it work; stops on reboot):

```bash
cd ~/doorbell-agents/main && claude "/agent-doorbell:listen"
```

Approve its `doorbell` commands when asked, or allow `Bash(doorbell *)` in your settings.

**Check:** `doorbell status` shows your agent with `"status": "active"` and `"waiters": 1`,
and `"state": "POLLING"`. `poll_set` lists the conversations being watched. If you see
`unknown users` or `unknown channel`, fix the names in `sub.yaml` (you must be a member of
private channels you list) and have the agent run `doorbell register` again.

Dispatcher logs: `journalctl --user -u doorbell-dispatch-main -f` (Linux),
`~/Library/Logs/dev.agent-doorbell.doorbell-dispatch-main.log` (macOS). On Windows, use
`doorbell status` and `doorbell log`.

## 8. Test it end to end

Your own messages are never queued, so you need someone (or something) else to message you:

1. Ask a colleague to DM you "test: what's 2+2?". To test alone, use a second Slack account,
   or add a bot's ID to `allow_bots` and have the bot DM you.
2. Within about 30 seconds the dispatcher log shows `woken: 1 pending` and then
   `resolved [<seq>]`.
3. Depending on the level: a draft reply appears in that DM (in the message box, or under
   **Drafts & sent**), a reply is posted, or the item is escalated with a summary.
4. `doorbell log` shows `deliver` and `resolve` entries for the item.

**Check:** `doorbell status` shows the item under `items` as done, and `stuck` is empty.

After your agent's first real reply, run `doorbell doctor`: under `apps_seen_posting_as_you`
you'll see the ID of the app your agent posts through. If it isn't `A08SF47R6P4` (Claude),
add it to `agent_app_ids` in the config file (see [Configuration](#configuration)) so the
watcher recognises those replies as the agent's, not yours.

## Everyday use

| Command | What it does |
|---|---|
| `doorbell status` | DORMANT or POLLING, agents and their states, poll set, API usage, stuck items |
| `doorbell log --since 3600` | Audit trail for the last hour: deliveries, resolutions, subscription changes |
| `doorbell inbox` | What's queued for the agent in this folder |
| `doorbell sub add users @someone --why "..."` | Listen to someone else too (the agent can do this as well; you get a notification) |
| `doorbell retry <seq>` | Re-queue a stuck item |
| `doorbell deregister` | Stop listening. With no agents left, the daemon makes zero API calls. |
| `doorbell doctor` | Token, scopes, and the app IDs seen posting as you |
| `doorbell review` | Approve, edit or reject the agents' drafts (if you set up sending) |
| `doorbell unlock` | After a reboot, let granted agents send again (layout B: also resumes watching) |
| `doorbell login` | Sign in again, for example after `needs_login` |

## Sending through Doorbell (optional)

New in 0.2.0. Lets agents answer through Doorbell instead of their own Slack tool, with the
policy enforced by Doorbell and your password, the same on every harness. The watcher keeps
its read-only token (layouts A and C), or one sealed token does both (layout B).

1. **Get a write token.** Layout B already did it: `doorbell login --for both` (step 4)
   gives one token that watches and sends. For layout C, create the app from
   [`slack-app-manifest-sender.yaml`](../slack-app-manifest-sender.yaml) (scopes
   `chat:write`, `reactions:write`) and share its Client ID (see
   [ORG-SETUP.md](ORG-SETUP.md)).
2. **Sign in for sending, in your own terminal:**
   `doorbell login --for send --client-id <write app's Client ID>`. Click **Allow**, then
   choose a sending password (8 characters or more). The token is sealed with it in
   Doorbell's database and never written to a file. (The older route still works: save a
   token to a file and run `doorbell sender add --token-file <file>`, then delete the file: a
   readable token file is all an agent would need to set up sending of its own.)
3. **Tell the agent how to reply.** In `AGENTS.md`, under "How you may reply", keep the
   Doorbell draft or send line. The agent's commands are in its protocol section.

**Drafts.** Any agent may run `doorbell draft <seq> --text ...`. The draft stays in Doorbell
(not in Slack), the item is acked, and you get a notification. Review them with
`doorbell review`: it shows each draft with the message it answers, asks for the password
once, and lets you approve, edit and approve, reject or skip each one. Or use
`doorbell drafts`, `doorbell approve <id> [--text ...]` and `doorbell reject <id>`.

**Direct sending.** An agent can ask with `doorbell request-send --why "..."` (you get a
notification). Allow it with `doorbell grant <agent>` (password). A granted agent's
`doorbell send <seq> --text ...` posts as you, but only into the conversation of one of its
own open items (a channel message is answered in its thread), at most `send_rate_per_hour`
times an hour (default 20, reactions included). `doorbell react <seq> <emoji>` adds a
reaction the same way. `@channel`, `@here` and `@everyone` in a direct send become plain
text. The grant covers the agent's subscription as it is when you grant: if the subscription
changes (`doorbell sub`, or `sub.yaml` and `doorbell register`), grant again. Take it back
with `doorbell revoke <agent>`.

**After a restart Doorbell is locked.** The decrypted token lives only in the daemon's memory,
so after a reboot or a daemon restart, direct sends fail (the agent drafts instead, and you
get a notification) until you run `doorbell unlock`. In layout B the watching token is sealed
too, so watching also waits for `doorbell unlock`, then continues from the saved cursors. `doorbell lock` stops all direct sends at
once. `doorbell passwd` changes the password; `doorbell sender remove` (password) deletes the
sealed token and every grant.

| Command | Who | Password |
|---|---|---|
| `doorbell draft <seq> --text ...` / `--text-file -` | agent | no |
| `doorbell send <seq> ...`, `doorbell react <seq> <emoji>` | granted agent | no (needs `grant` and `unlock`) |
| `doorbell request-send --why "..."` | agent | no |
| `doorbell login --for send\|both`, `doorbell sender add --token-file F` | you | sets it |
| `doorbell review`, `doorbell approve <id>` | you | yes |
| `doorbell grant <agent>`, `doorbell unlock`, `doorbell passwd`, `doorbell sender remove` | you | yes |
| `doorbell reject <id>`, `doorbell revoke <agent>`, `doorbell lock`, `doorbell drafts` | you | no |

The password commands need a terminal and refuse to run inside an AI agent session. That's a
convenience; the protection is that agents don't know the password. An agent that may run any
command as you is not contained by this (see [SECURITY.md](../SECURITY.md)), so keep the
agent at `Bash(doorbell *)`.

## Several agents

One daemon serves any number of agents. Each agent is its own folder, with its own
`AGENTS.md` (what it does), its own `sub.yaml` (what it listens to), its own permission level
and its own runner. Create more with `init-agent`:

```bash
doorbell init-agent ~/doorbell-agents/oncall --owner "Your Name"
doorbell init-agent ~/doorbell-agents/reviews --owner "Your Name"
```

For example:

| Agent | `sub.yaml` | `AGENTS.md` | Level and runner |
|---|---|---|---|
| `main` | defaults: your DMs, group DMs, @mentions, your threads | answer questions about what you own | draft, headless dispatch |
| `oncall` | `dms: none`, `mentions_of_me: false`, `threads_im_in: false`, `everyone_in: ["#incidents"]`, `allow_bots: ["B0ALERTBOT"]` | summarise alerts, escalate anything new | triage, headless dispatch |
| `reviews` | `dms: none`, `mentions_of_me: false`, `threads_im_in: false`, `users: ["@alex", "@sam"]`, `channels: ["#code-review"]` | answer review questions from those two people | reply, live session |

How they share the watcher:

- Each agent registers itself with its own folder's `sub.yaml`, and gets its own queue.
- The daemon polls the union of what live agents subscribe to, and each conversation only
  once, however many agents want it. With no agent listening, it polls nothing.
- A message that matches several agents is queued for each of them. Each agent claims and
  acks only its own items, so one agent's reply doesn't resolve another's. A reply you type
  yourself resolves the matching items of every agent.
- Agents come and go independently: one going inactive doesn't affect the others.
- `mentions_of_me` and `threads_im_in` apply to every conversation the daemon polls,
  including ones polled only for another agent. If `oncall` polls `#incidents` and `main` has
  `mentions_of_me: true`, an @mention of you in `#incidents` reaches both. Turn it off in one
  of them if you don't want that.

### `sub.yaml` keys

| Key | Values | Meaning |
|---|---|---|
| `dms` | `all`, `followed`, `none` | Your DMs: all of them, only from people in `users`, or none. |
| `group_dms` | `true`, `false` | Group DMs you are in. |
| `mentions_of_me` | `true`, `false` | `@you` in any conversation that is polled. |
| `threads_im_in` | `true`, `false` | New replies in threads you posted in (tracked for 7 days). |
| `users` | `["@alex", "U012ABCDEF"]` | Followed people. Their DMs (with `dms: followed`) and their messages in `channels` count. |
| `usergroups` | `["@platform-team"]` | Everyone in the group counts as followed. |
| `channels` | `["#proj-x"]` | Channels to poll for followed people and mentions. Private channels: you must be a member. |
| `everyone_in` | `["#incidents"]` | Channels where any human's message counts. |
| `allow_bots` | `["B012ABCDEF"]` | Bots whose messages count (bots are ignored otherwise). |

Change a running agent with `doorbell sub add|remove|set <key> <values> --why "..."` (audited,
with a notification). `doorbell register` resets the subscription to `sub.yaml`, so put
lasting changes in the file.

### Registration options

Options that apply to the agent rather than to what it hears are flags on
`doorbell register`, run in the agent's folder:

| Flag | Default | Meaning |
|---|---|---|
| `--resume checkpoint\|now` | `checkpoint` | After being inactive: catch up from the last handled message, or start from now. |
| `--max-catchup SECONDS` | 86400 (1 day) | The furthest back a returning agent catches up. |
| `--idle-ttl SECONDS` | 21600 (6 h) | Time without contact before the agent goes inactive (stops being polled for). |
| `--on-manual-reply defer\|ignore` | `defer` | When you reply yourself: resolve the agent's item (`defer`), or leave it for the agent. |
| `--as USER_ID` | the only identity | Which Slack identity to read as, when several tokens are added. |

## Configuration

Optional. `config.yaml` in the state folder overrides defaults from
[`doorbell/config.py`](../doorbell/config.py); unknown keys are rejected. The state folder is
`~/.local/state/doorbell` on Linux and macOS, `%LOCALAPPDATA%\doorbell` on Windows, or
`$DOORBELL_HOME` if set. Restart the daemon after changes (`doorbell service install` again
does that). Common keys:

```yaml
agent_app_ids: ["A08SF47R6P4"]  # apps whose posts "as you" count as agent replies
cadence_hot: 30                 # seconds between polls of an active conversation
idle_ttl: 21600                 # seconds without contact before an agent goes inactive
lease: 900                      # seconds an agent has to resolve a claimed item
max_attempts: 3                 # deliveries before an item is marked stuck
notify: true                    # desktop notifications (notify-send, osascript, Windows toast)
notify_command: []              # or your own command, e.g. ["ntfy", "publish", "my-topic"]
port: 0                         # local TCP port for the CLI; 0 picks a free one
send_rate_per_hour: 20          # direct sends per agent per hour (approved drafts don't count)
send_footer: ""                 # appended to messages Doorbell sends, e.g. " _(via my assistant)_"
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `doorbelld is not running` | Linux: `systemctl --user status doorbelld`. macOS: `~/Library/Logs/dev.agent-doorbell.doorbelld.log`. Windows: `schtasks /Query /TN AgentDoorbell\doorbelld`. Start it with `doorbell service install`, or run `doorbell daemon` in a terminal to see errors. |
| `doorbelld is already running` | Only one daemon per state folder. Use the running one. |
| `bad or missing access key` | The CLI and the daemon use different state folders. Check `DOORBELL_HOME`. |
| `login`: no client ID | Pass `--client-id`, or set `slack_client_id` in `config.yaml` or `DOORBELL_SLACK_CLIENT_ID`. |
| `login`: `bad_redirect_uri` | The redirect URI isn't under a redirect URL registered in the app. Add `http://localhost:8765/` in the app's **OAuth & Permissions → Redirect URLs** (the manifests have it), or pass `--redirect-uri` with a URL the app has. The scheme, host and port must match. |
| `login`: port in use | Another program has port 8765. Pick another port that is registered in the app and pass `--redirect-uri http://localhost:<port>/doorbell/callback`. Close any earlier `doorbell login` that is still waiting. |
| `login`: `invalid_code_verifier` | The sign-in was started twice, or the browser reused an old link. Run `doorbell login` again and open only the newest link. If it repeats, check that PKCE is on for the app. |
| `login`: `access_denied` | You clicked Cancel, or the workspace blocked the app. Run it again and click **Allow**. If there is no Allow button, ask an admin to approve the app ([ORG-SETUP.md](ORG-SETUP.md#approval-settings)). |
| `login`: `missing scopes` | The app lacks scopes this login needs (`chat:write` for `--for send` or `both`). Add them in the app, or use the right manifest, then sign in again. |
| `login`: waits, nothing happens | The browser can't reach this machine's localhost port, as on SSH or headless boxes. Use `--no-browser`, then open the link on a machine that can reach the box's port: `ssh -L 8765:localhost:8765 user@box`. |
| `login --for watch`: refuses a token | The token has `chat:write`. A token that can post is never saved to a plain file. Use `--for both` (one app) or sign in to a read-only app. |
| Identity `needs_login` | Slack didn't renew the token (the refresh token expired after 30 days off, or access was revoked). Run `doorbell login` again. Queued items and cursors are kept. |
| Identity `locked`, after a reboot (layout B) | The sealed token is only in memory after you unlock. Run `doorbell unlock`. Watching resumes from where it stopped. |
| `identity add`: `missing scopes` | The app's user scopes are incomplete. Re-check the manifest, reinstall the app, and sign in again (or copy the new token, if you use a manual token). |
| `identity add`: `readable by other users` | `chmod 600 <token file>`. |
| `invalid_auth` / identity `broken` | The token was revoked or the app removed. Run `doorbell login` again (or, with a manual token, create a new one and run `identity add`). |
| `no agent named ...` | The agent hasn't registered yet. It does so on its first run, or run `doorbell register` in its folder. |
| Agent never wakes | `doorbell status`: is the agent `active` with a waiter? Is the conversation in `poll_set`? Was the message from you (never queued) or a bot not in `allow_bots`? |
| Agent's `doorbell` commands are denied | Allow `Bash(doorbell *)` on the command line (step 6) or in your harness settings. |
| Agent can't use Slack | Its Slack tool isn't allowed (step 6), or isn't signed in under the account the agent runs as (`CLAUDE_CONFIG_DIR` for Claude Code). |
| Items keep coming back with `warning` | The agent isn't resolving them. Read its output (dispatcher log). |
| Item `stuck` | `doorbell status` shows it; fix the cause, then `doorbell retry <seq>`. |
| `send`: `may not send directly` | The agent isn't granted. It should draft; you can `doorbell grant <agent>`. |
| `send`: `Doorbell is locked` | The daemon restarted. Run `doorbell unlock`. |
| `send`: `no longer matches` | The agent's subscription changed since you granted it. Check it with `doorbell status`, then `doorbell grant <agent>` again. |
| `sender add` or `login --for send`: `missing scopes` | The sender token needs `chat:write`. Use `slack-app-manifest-sender.yaml` or the read+write manifest. |
| `... is for the owner, in their own terminal` | You ran a password command inside an agent session or without a terminal. Run it in your own terminal. |
| Dispatcher logs `backing off` | Runs are resolving nothing. Run the agent command by hand in the folder to see the error. |

## Uninstall

```bash
doorbell service uninstall --dispatch main   # if you installed a dispatcher
doorbell deregister --name main
doorbell service uninstall
pipx uninstall agent-doorbell
```

Then delete the state folder (see [Configuration](#configuration); it holds `credentials/`) and
any manual token file. To revoke your token, remove the app from your account in Slack's
**Apps** settings. If you created the app and nobody else uses it, delete it at
<https://api.slack.com/apps>; if you set up sending with a second app, delete that too.

## Notes for AI agents doing the setup

If you are an AI agent following this guide for a user:

- **Signing in is the user's click.** You may run `doorbell login --for watch` yourself: it
  prints a link, opens the browser, and the person clicks **Allow**. The token is never shown
  to you or saved where you can read it, so never ask for a token in the chat. Add
  `--no-browser` on a remote box and tell the person to open the link through an SSH tunnel.
- **`doorbell login --for send` and `--for both` are owner-only.** They ask for the sending
  password. Don't run them; tell the person to run them in their own terminal.
- If the person has no Client ID, send them to [ORG-SETUP.md](ORG-SETUP.md) and explain the
  three layouts (step 3). Recommend layout A unless they want Doorbell to send replies.
- **Step 6 is the user's decision.** Ask which level they want (recommend draft or triage),
  and which agent CLI runs the agent. Don't change their harness's permission settings
  yourself; show them the flags and let them choose. Fill in `AGENTS.md` with them: ask who
  they are, what they own and what they want answered, ignored or escalated.
- If you are the agent that will handle messages, register yourself with `doorbell register`
  in the agent folder. Don't widen `sub.yaml` beyond what the user asked for.
- Run each step's **check** and stop at the first failure; use [Troubleshooting](#troubleshooting).
- For step 8 the user needs a second person, account or bot to send a test message.

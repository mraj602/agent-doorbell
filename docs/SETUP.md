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

**Who does what.** You do the steps that need you: creating the Slack app and token, starting
the watcher, and deciding what the agent may do. The agent does its own part: it registers
itself and starts listening.

| Step | Who |
|---|---|
| [1. Install](#1-install) | You (or your agent) |
| [2. Create your Slack app and token](#2-create-your-slack-app-and-token) | **You**, in a browser |
| [3. Start the watcher](#3-start-the-watcher) | You (or your agent) |
| [4. Add your token](#4-add-your-token) | You (or your agent) |
| [5. Create an agent folder](#5-create-an-agent-folder) | You (or your agent) |
| [6. Decide what the agent may do](#6-decide-what-the-agent-may-do) | **You** |
| [7. Run the agent](#7-run-the-agent) | You start it; it registers itself |
| [8. Test it end to end](#8-test-it-end-to-end) | You, plus someone to message you |

Also: [Everyday use](#everyday-use) · [Configuration](#configuration) ·
[Troubleshooting](#troubleshooting) · [Uninstall](#uninstall)

## Before you start

You need:

- **Linux, macOS or Windows**, with Python 3.10 or newer.
- **A Slack workspace where you may create an app.** Free plans work. Some workspaces require
  an admin to approve new apps; if so, the install step asks for approval.
- **An agent CLI with a non-interactive mode**: Claude Code (`claude -p`), Codex
  (`codex exec`), Gemini CLI (`gemini -p`), or your own script.
- **If the agent should reply**, a Slack tool in that agent, for example the Slack plugin for
  Claude Code (`/plugin install slack@claude-plugins-official`, then sign in when it asks).
  If you only want triage (summaries of what needs you), no Slack tool is needed.

Agent Doorbell only reads Slack. It never posts. Replies, if any, come from your agent's own
Slack tool, under whatever rules your agent's harness enforces.

## 1. Install

```bash
pipx install agent-doorbell
# or: uv tool install agent-doorbell
# or the latest from GitHub: pipx install git+https://github.com/mraj602/agent-doorbell
```

**Check:** `doorbell --version` prints `doorbell 0.1.0` or newer.

## 2. Create your Slack app and token

Each person creates their own small, private app. That keeps the app "internal", which keeps
Slack's normal rate limits, and its token can only read.

1. Open <https://api.slack.com/apps> and choose **Create New App → From a manifest**.
2. Pick your workspace.
3. Paste the contents of [`slack-app-manifest.yaml`](../slack-app-manifest.yaml) (YAML tab),
   then **Next → Create**.
4. Open **Install App** in the sidebar and choose **Install to Workspace**, then **Allow**.
   If your workspace needs admin approval, request it and continue once approved.
5. Copy the **User OAuth Token**. It starts with `xoxp-`.
6. Save it to a file only you can read. Paste the token when asked; it isn't echoed or stored
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

**Check:** the file contains one line starting with `xoxp-`. On Linux and macOS,
`ls -l ~/.config/doorbell/slack-token` shows `-rw-------`.

The app has **read scopes only** (`channels`, `groups`, `im`, `mpim` history and read,
`users:read`, `usergroups:read`).

## 3. Start the watcher

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

## 4. Add your token

```bash
doorbell identity add --token-file ~/.config/doorbell/slack-token
```

**Check:** the output shows your Slack user ID, name and workspace, and `"warning": null`.
Then run `doorbell doctor`: your identity shows `"token": "ok"` and `"missing_scopes": []`.

## 5. Create an agent folder

```bash
doorbell init-agent ~/doorbell-agents/main --owner "Your Name"
```

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
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `doorbelld is not running` | Linux: `systemctl --user status doorbelld`. macOS: `~/Library/Logs/dev.agent-doorbell.doorbelld.log`. Windows: `schtasks /Query /TN AgentDoorbell\doorbelld`. Start it with `doorbell service install`, or run `doorbell daemon` in a terminal to see errors. |
| `doorbelld is already running` | Only one daemon per state folder. Use the running one. |
| `bad or missing access key` | The CLI and the daemon use different state folders. Check `DOORBELL_HOME`. |
| `identity add`: `missing scopes` | The app's user scopes are incomplete. Re-check the manifest, reinstall the app, copy the new token. |
| `identity add`: `readable by other users` | `chmod 600 <token file>`. |
| `invalid_auth` / identity `broken` | The token was revoked or the app removed. Create a new token and run `identity add` again. |
| `no agent named ...` | The agent hasn't registered yet. It does so on its first run, or run `doorbell register` in its folder. |
| Agent never wakes | `doorbell status`: is the agent `active` with a waiter? Is the conversation in `poll_set`? Was the message from you (never queued) or a bot not in `allow_bots`? |
| Agent's `doorbell` commands are denied | Allow `Bash(doorbell *)` on the command line (step 6) or in your harness settings. |
| Agent can't use Slack | Its Slack tool isn't allowed (step 6), or isn't signed in under the account the agent runs as (`CLAUDE_CONFIG_DIR` for Claude Code). |
| Items keep coming back with `warning` | The agent isn't resolving them. Read its output (dispatcher log). |
| Item `stuck` | `doorbell status` shows it; fix the cause, then `doorbell retry <seq>`. |
| Dispatcher logs `backing off` | Runs are resolving nothing. Run the agent command by hand in the folder to see the error. |

## Uninstall

```bash
doorbell service uninstall --dispatch main   # if you installed a dispatcher
doorbell deregister --name main
doorbell service uninstall
pipx uninstall agent-doorbell
```

Then delete the state folder (see [Configuration](#configuration)) and your token file, and
delete the app at <https://api.slack.com/apps> (this revokes the token).

## Notes for AI agents doing the setup

If you are an AI agent following this guide for a user:

- **The user must do step 2 themselves** in a browser. Never ask them to paste the token into
  the chat. Give them the command from step 2.6 so the token goes straight into a file, then
  verify with the check.
- **Step 6 is the user's decision.** Ask which level they want (recommend draft or triage),
  and which agent CLI runs the agent. Don't change their harness's permission settings
  yourself; show them the flags and let them choose. Fill in `AGENTS.md` with them: ask who
  they are, what they own and what they want answered, ignored or escalated.
- If you are the agent that will handle messages, register yourself with `doorbell register`
  in the agent folder. Don't widen `sub.yaml` beyond what the user asked for.
- Run each step's **check** and stop at the first failure; use [Troubleshooting](#troubleshooting).
- For step 8 the user needs a second person, account or bot to send a test message.

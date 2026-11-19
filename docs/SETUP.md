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

Agent Doorbell watches Slack with a read-only token where it can. You get it by signing in
with `doorbell login`, which never shows it to you or to an agent. Replies come either from your
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
- **You create it** (or you are the admin). Create the app from a manifest, or reuse any
  internal app, and copy the Client ID from **Basic Information**.
  [ORG-SETUP.md](ORG-SETUP.md) has the details.

| App | Manifest | Who can send through Doorbell |
|---|---|---|
| **Read-only** | [`slack-app-manifest.yaml`](../slack-app-manifest.yaml) | Nobody. Everyone watches with a read-only token |
| **Read + write** | [`slack-app-manifest-readwrite.yaml`](../slack-app-manifest-readwrite.yaml) | Members who turn it on with `doorbell login --send`. Everyone else still gets a read-only token |
| **A second, write app** | [`slack-app-manifest-sender.yaml`](../slack-app-manifest-sender.yaml) | Needed only for layout C below |

To create an app: open <https://api.slack.com/apps>, choose **Create New App → From a
manifest**, pick your workspace, paste the manifest (YAML tab), and create it. If your
workspace requires admin approval for apps, it is requested at first sign-in. No reinstall is
needed: each member's sign-in grants that member's own token.

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

```bash
doorbell login --client-id <Client ID>
```

This is the normal path. It prints `Open this link to sign in: <url>` and opens your browser.
Click **Allow** on Slack's page; the browser then shows "Signed in to Agent Doorbell". The
token is never shown and never pasted.

Doorbell asks Slack for the **read scopes only**, then looks at what the returned token can
really do. There are two outcomes:

| The token | What Doorbell does | Layout |
|---|---|---|
| **can't post** (the usual case) | Saves it in a credentials file (`credentials/<your user ID>.json`, mode 600) and adds your identity. Watching is unattended: it survives restarts by itself. | **A** |
| **can post** | Tells you why (below). Only if you are the owner, in a terminal, not inside an agent: asks you for a password, seals the token with it, and watches and sends with that one token. No file is written. | **B** |

If a token that can post comes back and the command isn't run by the owner in a terminal (for
example an agent ran it), Doorbell refuses, revokes the new token and saves nothing.

**Check (layout A):** the output says "Watching with a read-only token". `doorbell doctor`
shows your identity with `"token": "ok"` and `"missing_scopes": []`.

**Check (layout B):** `doorbell status` shows your identity with `"mode": "sealed"` and
`"status": "ok"`, and `"sending"` with `"unlocked": true`.

### Why a read-only sign-in can return a token that can post

Slack adds up the scopes one person has approved for one app, across **all** of that person's
tokens for that app, as long as any of them is alive. Revoking one token doesn't remove
scopes. Only revoking every token of that person for that app resets it; the next sign-in then
gets exactly the scopes it asks for.

Approvals are per person. One member approving `chat:write` gives nothing to another member.
So on one app that allows both read and write scopes, member X can run layout A and member Y
layout B.

### The three layouts

| | A. Watch only | B. One sealed token | C. Two apps |
|---|---|---|---|
| Apps | 1 | 1 | 2 (really two: see below) |
| Watching token | read-only, in a file | the same token that sends, sealed | read-only, in a file |
| Sending token | none | the same token, sealed | a second token from the write app, sealed |
| Agents can send through Doorbell | No | Yes | Yes |
| After a restart | Watching resumes by itself | Locked: watching and sending wait for `doorbell unlock`. Nothing is missed | Watching resumes by itself. Sending needs `doorbell unlock` |
| A leaked file gives | Read access only | Nothing: no file exists | Read access only |

A token that can post is never stored in a plain file. That is why B asks for a password and
can't resume by itself, and why C keeps the watching token separate. Layout C needs two
different Slack apps: with one app, Slack's scope accumulation would make the watching token
able to post too.

### Turn on sending

```bash
doorbell login --send
```

Run it in your own terminal. What happens depends on the app:

- **The same app you watch with:** your watching token would be able to post too, so Doorbell
  switches to one sealed token (layout B). It revokes the old file token and deletes its file.
- **Another app** (`--client-id <write app's Client ID>`): layout C. The watching token stays
  read-only in its file, and the new token is sealed.

It asks for a sending password (new on the first run; 8 characters or more). If one is
already set, it asks for the current one too.

**Check:** `doorbell status` shows `"sending"` with your user and `"unlocked": true`.

### Choosing explicitly: `--for`

`doorbell login --for watch|send|both` skips the automatic decision.

| `--for` | Token | Notes |
|---|---|---|
| `watch` | read scopes, saved in a file | Refuses a token that can post: it asks you to run `doorbell login` without `--for` instead, and revokes the token it just got. |
| `send` | chat:write (and reactions:write), sealed | Owner only. Same as `--send`. |
| `both` | read and write scopes, sealed | Owner only. One token watches and sends (layout B). |

### Options

| Option | Meaning |
|---|---|
| `--client-id ID` | The app's Client ID. Default: config `slack_client_id`, else `DOORBELL_SLACK_CLIENT_ID`. |
| `--send` | Turn on sending (see above). |
| `--for watch\|send\|both` | Choose what to sign in for instead of letting Doorbell decide. |
| `--redirect-uri URI` | Default `http://localhost:8765/doorbell/callback`. Must be under a redirect URL registered in the app. Doorbell listens on that port. |
| `--team T` | Pick the workspace when you belong to several. |
| `--no-browser` | Print the link and don't open a browser. |
| `--timeout 300` | Seconds to wait for you to click Allow. |

### On a server, over SSH, or headless

The browser is on another machine, so Slack can't reach the box. It still works:

1. Run `doorbell login` on the box. It prints the link.
2. Open the link in your laptop's browser and click **Allow**.
3. The browser ends on `http://localhost:8765/doorbell/callback?code=...&state=...`. The
   page won't load. That is expected.
4. Copy that whole address and paste it into the waiting `doorbell login`. It prompts for it
   when run in a terminal.

The code is single-use, expires within minutes, and is useless without the PKCE verifier,
which never leaves the box. The other way is a tunnel from your laptop,
`ssh -L 8765:localhost:8765 user@box`, then open the link in the laptop's browser.

### How tokens last

Tokens from a localhost sign-in **rotate**. They expire after 12 hours and come with a refresh
token. Doorbell renews them on its own, with no client secret: at most once per half of a
token's life, and when less than an hour is left. With PKCE, refresh tokens expire after 30
days, so if Doorbell doesn't run for 30 days, sign in again.

If renewal fails, the identity shows `"status": "needs_login"` and you get one notification.
Cursors and queued items are kept, so after `doorbell login` it catches up.

A sealed token is renewed while Doorbell is unlocked. If it expired while locked,
`doorbell unlock` renews it.

### Status of an identity

`doorbell status` shows, per identity:

| Field | Values |
|---|---|
| `mode` | `file` (read-only token in a file) or `sealed` (one token, layout B) |
| `status` | `ok`, `locked`, `needs_login` or `broken` |
| `token_expires_in_s` | Seconds until the token expires (renewed before that) |
| `client_id` | The app the token came from |
| `next` | What to run, when `status` isn't `ok` |

### If a file token later becomes able to post

If a token that `doorbell login` saved in a file later becomes able to post (you approved
`chat:write` for that app somewhere else, so Slack's scopes added up), the daemon stops using
it, sets the identity to `needs_login` and notifies you once. Run `doorbell login`: it seals
the token (layout B).

### Sign out: `doorbell logout`

```bash
doorbell logout            # or: doorbell logout --as U012ABCDEF
```

Revokes Doorbell's tokens with Slack (`auth.revoke`) and forgets them. Agents and cursors
stay, so a later `doorbell login` picks up where watching stopped. It asks for the password
when a sealed token exists. Other tools that use the same app keep their own tokens, and so
keep the accumulated scopes alive: to get back to a read-only sign-in, revoke those too.

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
| `doorbell lock` | Stop all direct sends (layout B: also pauses watching) until `doorbell unlock` |
| `doorbell login` | Sign in, or sign in again after `needs_login` |
| `doorbell logout` | Revoke Doorbell's Slack tokens and forget them. Agents and cursors stay |

## Sending through Doorbell (optional)

New in 0.2.0. Lets agents answer through Doorbell instead of their own Slack tool, with the
policy enforced by Doorbell and your password, the same on every harness. The watcher keeps
its read-only token (layouts A and C), or one sealed token does both (layout B).

1. **Pick the app.** Sending through the app you watch with gives layout B (that app must
   allow `chat:write`: [`slack-app-manifest-readwrite.yaml`](../slack-app-manifest-readwrite.yaml)).
   Sending through a second app gives layout C: create it from
   [`slack-app-manifest-sender.yaml`](../slack-app-manifest-sender.yaml) (scopes
   `chat:write`, `reactions:write`) and share its Client ID (see
   [ORG-SETUP.md](ORG-SETUP.md)).
2. **Turn it on, in your own terminal:** `doorbell login --send` (layout B), or
   `doorbell login --send --client-id <write app's Client ID>` (layout C). Click **Allow**,
   then choose a sending password (8 characters or more). The token is sealed with it in
   Doorbell's database and never written to a file. In layout B, Doorbell also revokes the old
   file token and deletes its file. (The older route still works: save a
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
too, so watching also waits for `doorbell unlock`, then continues from the saved cursors. If
the sealed token expired while locked, `unlock` renews it. `doorbell lock` stops all direct
sends at once; in layout B it also pauses watching until `doorbell unlock`.
`doorbell passwd` changes the password; `doorbell sender remove` (password) deletes the
sealed token and every grant. In layout B it refuses, because that token is also the
watcher's: switch watching to a read-only token first (a read-only app, or a sign-in after
every token for the app was revoked). `doorbell logout` revokes and forgets the tokens.

Fixed in 0.3.0: in 0.2.0, `doorbell sender remove` didn't delete the sealed token.

| Command | Who | Password |
|---|---|---|
| `doorbell draft <seq> --text ...` / `--text-file -` | agent | no |
| `doorbell send <seq> ...`, `doorbell react <seq> <emoji>` | granted agent | no (needs `grant` and `unlock`) |
| `doorbell request-send --why "..."` | agent | no |
| `doorbell login --send` (or `--for send\|both`), `doorbell sender add --token-file F` | you | sets it |
| `doorbell review`, `doorbell approve <id>` | you | yes |
| `doorbell grant <agent>`, `doorbell unlock`, `doorbell passwd`, `doorbell sender remove` | you | yes |
| `doorbell reject <id>`, `doorbell revoke <agent>`, `doorbell lock`, `doorbell drafts` | you | no |
| `doorbell logout` | you | yes, when a sealed token exists |

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

### Sign-in problems

| What you see | Cause and fix |
|---|---|
| Slack: `redirect_uri did not match any configured URIs` | The redirect URL isn't on the app, or wasn't saved. Under **OAuth & Permissions → Redirect URLs** add `http://localhost:8765/` and click **Save URLs**. Or pass `--redirect-uri` with a localhost URL the app has. Scheme, host and port must match. |
| `can't listen on port 8765` | Another program (or an earlier `doorbell login` still waiting) has the port. Close it, or register another localhost URL on the app and pass `--redirect-uri http://localhost:<port>/doorbell/callback`. |
| `Slack returned access_denied` | You clicked Cancel, or the workspace blocked the app. Run it again and click **Allow**. With no Allow button, ask an admin to approve the app ([ORG-SETUP.md](ORG-SETUP.md#approval-settings)). |
| `This sign-in can also post messages as you` | Slack added up your earlier approvals of `chat:write` for this app, so the read-only sign-in returned a token that can post. Doorbell won't keep it in a file. Run `doorbell login` in your own terminal to seal it (layout B), or [reset the scopes](#why-a-read-only-sign-in-can-return-a-token-that-can-post) to stay read-only. If an agent ran the login, Doorbell revoked the token and saved nothing. |
| `timed out: no sign-in within 300 seconds` | Nobody clicked Allow in time, or the browser is on another machine. Run it again. For a server or SSH, paste the `localhost:8765/doorbell/callback?code=...` address into the waiting command ([how](#on-a-server-over-ssh-or-headless)). |
| Identity `needs_login` | Renewing failed (Doorbell wasn't running for 30 days, or access was revoked), or a file token became able to post. Run `doorbell login`. Cursors and queued items are kept, so it catches up. |
| `no Slack app to sign in with` | Pass `--client-id`, or set `slack_client_id` in `config.yaml` or `DOORBELL_SLACK_CLIENT_ID`. |
| `invalid_code_verifier` | The sign-in was started twice, or the browser reused an old link. Open only the newest link. If it repeats, check that PKCE is on for the app. |
| `missing scopes` | The app lacks scopes this login needs (`chat:write` for sending). Add them under **User Token Scopes** in the app, then sign in again. |
| `login --for watch` refuses | The token can post. Run `doorbell login` without `--for` to seal it, or watch with a read-only app. |
| Identity `locked` after a restart | Layout B: the sealed token is only in memory after you unlock. Run `doorbell unlock`. Watching resumes and catches up. |

### Other problems

| Symptom | Cause and fix |
|---|---|
| `doorbelld is not running` | Linux: `systemctl --user status doorbelld`. macOS: `~/Library/Logs/dev.agent-doorbell.doorbelld.log`. Windows: `schtasks /Query /TN AgentDoorbell\doorbelld`. Start it with `doorbell service install`, or run `doorbell daemon` in a terminal to see errors. |
| `doorbelld is already running` | Only one daemon per state folder. Use the running one. |
| `bad or missing access key` | The CLI and the daemon use different state folders. Check `DOORBELL_HOME`. |
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
| `sender add` or `login --send`: `missing scopes` | The sender token needs `chat:write`. Use `slack-app-manifest-sender.yaml` or the read+write manifest. |
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

- **Signing in is the user's click.** You may run `doorbell login` yourself: it prints a link,
  opens the browser, and the person clicks **Allow**. The token is never shown to you or saved
  where you can read it, so never ask for a token in the chat. If the token can post, Doorbell
  refuses to keep it for an agent, revokes it and saves nothing; then tell the person to run
  `doorbell login` in their own terminal. On a remote box, add `--no-browser`, and tell the
  person to open the link on their laptop and paste back the `localhost:8765/...` address it
  ends on (see [On a server](#on-a-server-over-ssh-or-headless)).
- **`doorbell login --send`, `--for send`, `--for both`, `logout` and `unlock` are owner-only.**
  They ask for the sending password. Don't run them; tell the person to run them in their own
  terminal.
- If the person has no Client ID, send them to [ORG-SETUP.md](ORG-SETUP.md). Recommend a
  read-only sign-in (layout A) unless they want Doorbell to send replies.
- **Step 6 is the user's decision.** Ask which level they want (recommend draft or triage),
  and which agent CLI runs the agent. Don't change their harness's permission settings
  yourself; show them the flags and let them choose. Fill in `AGENTS.md` with them: ask who
  they are, what they own and what they want answered, ignored or escalated.
- If you are the agent that will handle messages, register yourself with `doorbell register`
  in the agent folder. Don't widen `sub.yaml` beyond what the user asked for.
- Run each step's **check** and stop at the first failure; use [Troubleshooting](#troubleshooting).
- For step 8 the user needs a second person, account or bot to send a test message.

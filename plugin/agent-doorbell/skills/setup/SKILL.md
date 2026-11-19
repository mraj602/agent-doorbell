---
name: setup
description: Set up Agent Doorbell for Slack on this machine, step by step with a check after each step. Installs the doorbell command if missing (after asking), guides the person through creating their read-only Slack app and saving its token, starts the watcher, creates an agent folder, fills in its instructions, and shows how to run the agent. Use when the user asks to set up, install or configure Agent Doorbell or a Slack watcher for their agent, or runs /agent-doorbell:setup.
allowed-tools: Bash(command -v doorbell), Bash(doorbell --version), Bash(doorbell status:*), Bash(doorbell doctor)
---

Set up Agent Doorbell for this person. Go through the steps in order, run each step's check,
and stop at the first failure (see Troubleshooting in the setup guide). Keep every question to
one sentence and name the default.

Agent Doorbell is a small daemon that watches Slack with a read-only token and wakes the
person's agent only when a message needs it. It never posts. The agent replies with its own
Slack tool, if its permissions allow.

Rules that always hold:

- **Never ask for the Slack token in the chat**, and never put it in a command line. The
  person saves it to a file with the command in step 2, in their own terminal.
- **Never change the person's Claude Code permission settings** (`settings.json`, allow or
  deny rules). Step 6 is their decision: show the flags, let them choose.
- Ask before installing anything, and before anything that starts a service.
- The full guide with every detail is
  <https://github.com/mraj602/agent-doorbell/blob/main/docs/SETUP.md>. When unsure, follow it.

## 1. Install the `doorbell` command

Run `command -v doorbell`. If it prints a path, run `doorbell --version` and continue.

Otherwise ask for a yes, then install with the first tool that exists:
`pipx install agent-doorbell`, else `uv tool install agent-doorbell`, else
`python3 -m pip install --user agent-doorbell`. If the package isn't found on PyPI, install
from GitHub instead: `pipx install git+https://github.com/mraj602/agent-doorbell` (same for
uv and pip). If the person gives you a local checkout or another source, install from that.
Python 3.10 or newer is needed.

**Check:** `doorbell --version` prints a version. If the command isn't found, the install
folder (`~/.local/bin` for pipx, uv and pip `--user`) is not on `PATH`: tell the person the
line to add to their shell profile and use the full path for the rest of this session.

## 2. The Slack app and token (the person does this)

Tell the person, in one message:

1. Open <https://api.slack.com/apps>, choose **Create New App → From a manifest**, pick the
   workspace, paste the manifest from
   <https://github.com/mraj602/agent-doorbell/blob/main/slack-app-manifest.yaml>, create it.
2. **Install App → Install to Workspace → Allow** (some workspaces need admin approval).
3. Copy the **User OAuth Token** (`xoxp-...`) and save it with this command in their own
   terminal (not here). Give the command for their OS:

   Linux and macOS:
   ```
   mkdir -p ~/.config/doorbell
   ( umask 077; read -rsp 'Slack user token: ' t; echo; printf '%s\n' "$t" > ~/.config/doorbell/slack-token )
   ```
   Windows (PowerShell):
   ```
   New-Item -ItemType Directory -Force "$HOME\.config\doorbell" | Out-Null
   $s = Read-Host 'Slack user token' -AsSecureString
   [Net.NetworkCredential]::new('', $s).Password | Set-Content -NoNewline "$HOME\.config\doorbell\slack-token"
   ```

Wait until they say it's done. If they already have a token file, use its path.

**Check:** the file exists and its first line starts with `xoxp-` (check with
`grep -c '^xoxp-' <file>`, which prints only a count, never the token).

## 3. Start the watcher

Ask for a yes, then run `doorbell service install`. It installs a per-user service that
starts at login: systemd on Linux, launchd on macOS, Task Scheduler on Windows.

If it says there is no service manager (containers, some WSL setups), ask how they want to
run it: under their own supervisor, or for now in the background with
`nohup doorbell daemon > ~/doorbell-daemon.log 2>&1 &` (it stops at logout or reboot).

**Check:** `doorbell status` shows `"state": "DORMANT"` (no agents yet, zero Slack calls).

## 4. Add the token

Run `doorbell identity add --token-file <the file from step 2>`.

**Check:** the output shows their Slack user and `"warning": null`, and `doorbell doctor`
shows `"token": "ok"` and `"missing_scopes": []`.

## 5. Create the agent folder

Ask where the agent folder should go (default `~/doorbell-agents/main`) and the name to use
in its instructions. Then run `doorbell init-agent <folder> --owner "<name>"`.

It writes `.doorbell-agent` (the agent's name), `sub.yaml` (who the agent listens to),
`AGENTS.md` (its instructions and the queue commands) and `CLAUDE.md` (`@AGENTS.md`).

**Check:** those four files exist.

## 6. Instructions, subscription and permissions

Fill in `AGENTS.md` with the person. Ask, one at a time:

1. Their role, team and what they own.
2. Which repos or folders the agent may read to answer questions.
3. What it should answer, and what it should always leave for them.
4. Which level it gets (below; default draft if they have a Slack tool, triage otherwise).

Replace the `TODO` lines with their answers, keep the "Leave for" and "Rules that always
apply" sections, and in "How you may reply" keep only the level they chose.

Then `sub.yaml`. The defaults are all their DMs, group DMs, @mentions and threads they posted
in. Add people (`users: ["@alex"]`) and channels (`channels: ["#team-x"]`) only if they ask.

Levels, set by the agent's own permissions, never by Doorbell:

| Level | The agent may | Claude Code flags for headless runs |
|---|---|---|
| Triage | run `doorbell` commands only | `--allowedTools "Bash(doorbell *)"` |
| Draft | also write Slack drafts | `--allowedTools "Bash(doorbell *)" "<slack>slack_send_message_draft" --disallowedTools "<slack>slack_send_message" "<slack>slack_schedule_message"` |
| Reply | also post and react as them | `--allowedTools "Bash(doorbell *)" "<slack>slack_send_message" "<slack>slack_send_message_draft" "<slack>slack_add_reaction"` |

`<slack>` is the prefix of their Slack tool: `mcp__plugin_slack_slack__` for the Slack plugin,
`mcp__claude_ai_Slack__` for the claude.ai connector. Find it from the tools you can see; if
they have no Slack tool, only triage works (the Slack plugin installs with
`/plugin install slack@claude-plugins-official`).

**Check:** `grep -c TODO <folder>/AGENTS.md` prints `0`, or only TODOs they chose to keep.

## 7. Run the agent

The agent registers itself on its first run; nobody needs to register it by hand.

Recommend headless dispatch: a service waits for messages and starts a short, fresh
`claude -p` run per batch, so nothing runs (or costs) while it's quiet. Show the command with
their level's flags, and run it only after a yes:

```
doorbell service install --dispatch <name> --dir <folder> -- \
    claude -p "/agent-doorbell:handle" <flags from step 6>
```

Without a service manager, the same thing runs in a terminal or under their supervisor:
`doorbell dispatch --dir <folder> -- claude -p "/agent-doorbell:handle" <flags>`.

Alternative for watching it work: a live session, `cd <folder> && claude "/agent-doorbell:listen"`
(it stops on reboot).

**Check:** `doorbell status` shows the agent `"status": "active"` with `"waiters": 1` and
`"state": "POLLING"`.

## 8. Test

Their own messages are never queued. Ask them to have someone else DM them a test question
(or use a second account). Within about 30 seconds the item is handled: a draft or reply in
that DM, or an escalated item in `doorbell log`.

**Check:** `doorbell status` shows the item done and `stuck` empty.

End with a short summary: where the agent folder is, its level, how it runs, and the two
everyday commands, `doorbell status` and `/agent-doorbell:status`.

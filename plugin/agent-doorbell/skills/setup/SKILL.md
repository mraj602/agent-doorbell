---
name: setup
description: Set up Agent Doorbell for Slack on this machine, step by step with a check after each step. Installs the doorbell command if missing (after asking), signs the person in to Slack with `doorbell login` (they click Allow; no token is pasted), starts the watcher, creates an agent folder, fills in its instructions, and shows how to run the agent. Use when the user asks to set up, install or configure Agent Doorbell or a Slack watcher for their agent, or runs /agent-doorbell:setup.
allowed-tools: Bash(command -v doorbell), Bash(doorbell --version), Bash(doorbell status:*), Bash(doorbell doctor), Bash(doorbell login --for watch:*)
---

Set up Agent Doorbell for this person. Go through the steps in order, run each step's check,
and stop at the first failure (see Troubleshooting in the setup guide). Keep every question to
one sentence and name the default.

Agent Doorbell is a small daemon that watches Slack with a read-only token and wakes the
person's agent only when a message needs it. The agent replies with its own Slack tool, if
its permissions allow, or through Doorbell (optional sending, step 6b), where the person
approves drafts and grants direct sending with a password.

Rules that always hold:

- **Never ask for a Slack token or the sending password in the chat**, and never put either
  in a command line. You may run `doorbell login --for watch`: the person clicks Allow in
  the browser and the token is never shown to you. Never run `doorbell login --for send`,
  `doorbell login --for both`, `sender add`, `grant`, `unlock`, `approve`, `review` or
  `passwd` yourself: the person runs them in their own terminal.
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

## 2. Start the watcher

Ask for a yes, then run `doorbell service install`. It installs a per-user service that
starts at login: systemd on Linux, launchd on macOS, Task Scheduler on Windows.

If it says there is no service manager (containers, some WSL setups), ask how they want to
run it: under their own supervisor, or for now in the background with
`nohup doorbell daemon > ~/doorbell-daemon.log 2>&1 &` (it stops at logout or reboot).

**Check:** `doorbell status` shows `"state": "DORMANT"` (no agents yet, zero Slack calls).

## 3. The Slack app and its Client ID

`doorbell login` needs the **Client ID** of a Slack app that has PKCE on. It is not a secret,
and there is no client secret. Ask the person: does their workspace already have a Doorbell
app? If yes, ask for the Client ID (they need not be an app collaborator). If not, they (or
an admin) create one from a manifest at <https://api.slack.com/apps> (**Create New App →
From a manifest**) and copy the Client ID from **Basic Information**. Details are in
<https://github.com/mraj602/agent-doorbell/blob/main/docs/ORG-SETUP.md>.

Explain the three layouts in a few lines and recommend A unless they want Doorbell to send:

- **A. One read-only app** (`slack-app-manifest.yaml`). Watching only. Agents reply with
  their own Slack tool, or only triage.
- **B. One read+write app** (`slack-app-manifest-readwrite.yaml`, "single-token mode"). One
  token watches and sends. It is sealed with a password, so after a restart nothing is
  polled until they run `doorbell unlock`. Nothing is missed.
- **C. Two apps**, a read app (A) and a write app (`slack-app-manifest-sender.yaml`).
  Watching resumes by itself after a restart; sending needs `doorbell unlock`.

Manifests are in <https://github.com/mraj602/agent-doorbell/blob/main/>. Turning on PKCE is
one-way (only Slack support can undo it), so warn them before they add it to an existing app
that other tools use.

**Check:** they have a Client ID.

## 4. Sign in

For watching (layouts A and C, and the read side of any layout), run:

`doorbell login --for watch --client-id <Client ID>`

(Omit `--client-id` if `slack_client_id` is in the config or `DOORBELL_SLACK_CLIENT_ID` is
set.) It prints a link and opens the browser. Tell the person to click **Allow**. Wait for it
to finish. On a remote or headless machine add `--no-browser` and tell them to open the link
through an SSH tunnel: `ssh -L 8765:localhost:8765 user@box`. If they have no browser at all
on this machine, they run the command in their own terminal.

For layout B, and for the sending side of layout C, the person runs these in their own
terminal (they ask for a password): `doorbell login --for both --client-id <ID>` for B,
`doorbell login --for send --client-id <write app's ID>` for C. Wait until they say it's done.

If they already have a token file from an older setup, `doorbell identity add --token-file
<file>` still works.

**Check:** the output shows their Slack user, `doorbell doctor` shows `"token": "ok"` and
`"missing_scopes": []`. For layout B, `doorbell status` shows `"mode": "sealed"` and
`"status": "ok"`.

If it fails: `bad_redirect_uri` means the redirect URL isn't registered in the app (the
manifests register `http://localhost:8765/`); a busy port means pick another with
`--redirect-uri`; `access_denied` means Allow wasn't clicked or an admin must approve the
app. The setup guide's Troubleshooting table has the rest.

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
4. Which level it gets (below; default draft if they have a Slack tool, triage otherwise),
   and whether replies should go through Doorbell instead (step 6b).

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

## 6b. Optional: sending through Doorbell

Offer it if they have no Slack tool, or want one send policy for every agent. Then the agent
needs only the triage flags (`Bash(doorbell *)`). If they chose layout B in step 4, sending
is already set up. For layout C, tell the person to run, in their own terminal,
`doorbell login --for send --client-id <write app's Client ID>` and choose a sending
password. Never run it yourself.

In `AGENTS.md`, under "How you may reply", keep the Doorbell draft line (or the send line, if
they will grant the agent). Explain: drafts are approved with `doorbell review`;
`doorbell grant <name>` lets the agent send directly; after a reboot, `doorbell unlock`
(in layout B it also resumes watching).

**Check:** `doorbell status` shows `"sending"` with their user and `"unlocked": true`.

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

End with a short summary: where the agent folder is, its level, how it runs, and the
everyday commands, `doorbell status` and `/agent-doorbell:status` (plus `doorbell review`
if they set up sending).

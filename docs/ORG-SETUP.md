# Slack app setup for a workspace (for admins)

This page is for the person who sets up the Slack app for a workspace. After that, every
member who wants Agent Doorbell runs one command, `doorbell login`, and clicks **Allow**. They
don't need to be app collaborators, and nobody copies or pastes a token. If you are setting it
up only for yourself, [SETUP.md](SETUP.md) covers the same steps for one person.

## How sign-in works

`doorbell login` uses Slack's OAuth flow with PKCE and a redirect to `localhost`. It prints a
link and opens it in the browser. The member clicks **Allow** on Slack's page. Slack sends the
browser back to a short-lived listener on the member's own machine, and Doorbell trades the
code for a token. The token never appears on screen. There is no client secret anywhere, so
the app's **Client ID** is the only thing you share, and it is not a secret.

References: [Using PKCE](https://docs.slack.dev/authentication/using-pkce) and
[Installing with OAuth](https://docs.slack.dev/authentication/installing-with-oauth).

## Choose a layout

| | A. One read-only app | B. One read+write app | C. Two apps (read, write) |
|---|---|---|---|
| Manifest | [`slack-app-manifest.yaml`](../slack-app-manifest.yaml) | [`slack-app-manifest-readwrite.yaml`](../slack-app-manifest-readwrite.yaml) | `slack-app-manifest.yaml` and [`slack-app-manifest-sender.yaml`](../slack-app-manifest-sender.yaml) |
| Apps used (matters on Free, which limits app slots) | 1 | 1 | 2 |
| Agents can send through Doorbell | No (agents use their own Slack tool, or only triage) | Yes | Yes |
| Login command | `doorbell login` | `doorbell login --for both` | `doorbell login`, then `doorbell login --for send --client-id <write app>` |
| Where the token is kept | A credentials file, mode 600 | Sealed with the member's password | Read: a credentials file. Write: sealed with the password |
| After a reboot | Watching resumes by itself | Locked: watching resumes after `doorbell unlock`. Nothing is missed, because polling continues from the saved cursors | Watching resumes by itself. Sending needs `doorbell unlock` |
| If the watcher's file leaks | Read access to what that member can read. It can't post | Nothing usable: the file doesn't exist, the token is sealed | Read access only. The write token is sealed |
| Best for | Triage and drafts, or agents that reply with their own Slack tool | Free workspaces that want sending, and people who can unlock after a reboot | Always-on watching plus sending, on a box that restarts unattended |

Rule behind the table: a token that can post is never stored in a plain file. That is why
layout B asks for a password and can't resume watching on its own after a restart, and why
layout C keeps the read token separate.

If you aren't sure, start with A. You can add a write app later without changing anything for
the watcher.

## Create the app

1. Open <https://api.slack.com/apps> and choose **Create New App → From a manifest**.
2. Pick the workspace and paste the manifest for your layout (YAML tab). Create the app.
3. Open **Basic Information** and copy the **Client ID**.
4. For layout C, repeat with `slack-app-manifest-sender.yaml` and keep both Client IDs.

The manifests turn on PKCE and register `http://localhost:8765/` as a redirect URL. The app is
internal: user scopes only, no bot user, no events, no Socket Mode.

## Reuse an existing internal app instead

The app's name doesn't matter. Any internal app in your workspace works if it has:

- the user scopes the layout needs (the manifest lists them);
- **PKCE** turned on;
- a redirect URL that matches the one `doorbell login` uses.

Any `localhost` URL works. The default redirect URI is `http://localhost:8765/doorbell/callback`,
which is under the registered `http://localhost:8765/` (Slack allows a redirect URI that is a
subdirectory of a registered one). If you register a different URL, members pass the full
address with `--redirect-uri`, for example `doorbell login --redirect-uri http://localhost:9000/cb`.
Doorbell listens on that port, on 127.0.0.1 only.

To add user scopes to an existing app, open **OAuth & Permissions**, add them under **User
Token Scopes**, and save. Existing installs need to sign in again to get the new scopes.

### Enabling PKCE on an existing app is one-way

Turning PKCE on marks the app as a **public client**. Slack's docs say this can't be undone in
the app's settings: only Slack support can turn it off. Before you do it on an app that other
tools use, check that nothing depends on its client secret. A public client doesn't use one.
If you aren't sure, create a new app from the manifest instead. That costs one app slot and
avoids the question.

Two more facts from Slack's docs that shape the setup:

- With PKCE, a `localhost` redirect counts as a desktop redirect, and desktop redirects can't
  request bot scopes. That fits Doorbell, which uses user scopes only.
- With PKCE, refresh tokens expire after 30 days. Doorbell renews the token before that while
  the daemon runs. If a machine is off for longer than that, the member signs in again with
  `doorbell login`.

## Share the Client ID

Give members the Client ID (and, for layout C, both). They can set it three ways:

- `--client-id <ID>` on the command;
- `slack_client_id: <ID>` in `config.yaml` in their state folder;
- the environment variable `DOORBELL_SLACK_CLIENT_ID`.

Then each member runs the commands in [SETUP.md](SETUP.md#4-sign-in-with-doorbell-login).
A short message for your team:

> To use Agent Doorbell: `pipx install agent-doorbell`, then
> `doorbell login --client-id <ID>`. Open the link and click Allow.

## Approval settings

If your workspace requires admin approval for apps, the first member who signs in sees
"request approval" instead of **Allow**. Approve the app once under **Manage apps** in your
workspace settings (or turn approval off for it). After that, other members can sign in. The
app is internal, so it appears only in your workspace.

Each member's sign-in grants the app access to **that member's** view of Slack, nothing more:
the same channels, DMs and group DMs they can already see.

## Revoke a member's token

Any of these works:

- The member runs `doorbell sender remove` (if they sealed a token) and deletes Doorbell's
  state folder, including `credentials/`. Doorbell then holds no token for them.
- The member opens Slack's **Apps** settings and removes the app from their account.
- You remove the app from the workspace, which revokes every token it issued. You can also
  delete the app at <https://api.slack.com/apps>.

A revoked token makes the member's identity `broken` (`invalid_auth`). Doorbell stops polling
for it and sends a notification. The member signs in again with `doorbell login`.

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

## What members get

`doorbell login` asks Slack for the read scopes only, then looks at what the token can really
do. What a member ends up with depends on the person, not only on the app:

| | A. Watch only | B. One sealed token | C. Two apps |
|---|---|---|---|
| Apps | 1 | 1 | 2 |
| Watching token | Read-only, in a credentials file (mode 600) | The same token that sends, sealed with the member's password | Read-only, in a credentials file |
| Sending token | None | The same sealed token | From the second app, sealed |
| Agents can send through Doorbell | No (agents use their own Slack tool, or only triage) | Yes | Yes |
| How the member gets there | `doorbell login` | `doorbell login --send` (same app), or `doorbell login` when Slack returns a token that can post | `doorbell login`, then `doorbell login --send --client-id <write app>` |
| After a reboot | Watching resumes by itself | Locked: watching resumes after `doorbell unlock`. Nothing is missed, because polling continues from the saved cursors | Watching resumes by itself. Sending needs `doorbell unlock` |
| If the watcher's file leaks | Read access to what that member can read. It can't post | Nothing usable: the file doesn't exist, the token is sealed | Read access only. The write token is sealed |

A token that can post is never stored in a plain file. That is why layout B asks for a
password and can't resume watching on its own after a restart, and why layout C keeps the
read token separate.

### Why one app can give different members different layouts

Slack adds up the scopes one person has approved for one app, across all of that person's
tokens for that app, as long as any of them is alive. Revoking one token doesn't remove
scopes. Only revoking every token of that person for that app resets it; the next sign-in
then gets exactly what it asks for.

Approvals are per person. One member's approval of `chat:write` gives nothing to another
member. So on one app that allows both read and write scopes, member X can run layout A
(read-only) and member Y layout B.

Two consequences for you:

- **Layout C genuinely needs two different Slack apps.** With one app, the watching token
  would be able to post as soon as the same person approves `chat:write` for it.
- **The guard.** If a token that `doorbell login` saved in a file later becomes able to post,
  the member's daemon revokes it at once, deletes its file, sets the identity to `needs_login`
  and notifies once. `doorbell login` then seals a new one.

## Which app

| Want | Create from |
|---|---|
| Watching only, nobody sends through Doorbell | [`slack-app-manifest.yaml`](../slack-app-manifest.yaml) |
| Members may send through Doorbell, one app (Free plans limit app slots) | [`slack-app-manifest-readwrite.yaml`](../slack-app-manifest-readwrite.yaml) |
| Always-on watching plus sending, on a box that restarts unattended | `slack-app-manifest.yaml` and [`slack-app-manifest-sender.yaml`](../slack-app-manifest-sender.yaml) |

If you aren't sure, start with the read-only app. You can add a write app later without
changing anything for the watcher.

### One app for the team, or one per person?

Slack's rate limits are per app, per workspace: everyone who signs in to one app shares its
budget. That decides how fast Doorbell can be ([design/detection.md](design/detection.md)):

| | One shared app | One app per person ([`slack-app-manifest-personal.yaml`](../slack-app-manifest-personal.yaml)) |
|---|---|---|
| Slack plan | Any (uses 1 of the Free plan's 10 app slots) | Paid, or a Free workspace with slots to spare |
| Delivery | About 30 s to 2 minutes; slower for large teams | Under a second (Socket Mode) |
| API calls | Each person takes a fair share (`team_size`) | About zero, and nobody shares limits |
| Setup | Members only sign in | Each member creates an app and an app-level token |

On a shared app, **don't turn on Socket Mode or create an app-level token**. Slack hands each
event to one open connection, so one member's messages could reach another member's Doorbell.
Doorbell refuses Socket Mode without `--only-me` and turns it off by itself if it sees a second
connection, but the app-level token itself would let its holder receive everyone's events.

For a shared app, tell members how many people use it so they can run
`doorbell config set team_size <N>`, and keep `search:read` in the app's scopes (the manifests
include it) so members can sign in with `doorbell login --search`.

## Create the app

1. Open <https://api.slack.com/apps> and choose **Create New App → From a manifest**.
2. Pick the workspace and paste the manifest (YAML tab). Create the app.
3. Open **Basic Information** and copy the **Client ID**.
4. For layout C, repeat with `slack-app-manifest-sender.yaml` and keep both Client IDs.

The manifests turn on PKCE and register `http://localhost:8765/` as a redirect URL. The app is
internal: user scopes only, no bot user. The shared manifests have no events and no Socket Mode;
the personal one subscribes to user message events over Socket Mode. No reinstall is needed
for `doorbell login`: each member's sign-in grants their own token.

**Adding `search:read` to an existing app:** under **OAuth & Permissions → User Token Scopes**
add `search:read` and save. Don't click **Reinstall to Workspace**: that grants *you* every
scope on the app, including write scopes if it has any. Members get the scope the next time they
run `doorbell login --search`.

## Reuse an existing internal app instead

The app's name doesn't matter. Any internal app in your workspace works. In its settings:

1. **OAuth & Permissions → Redirect URLs:** add `http://localhost:8765/` and click **Save
   URLs**. If you skip the save, Slack says `redirect_uri did not match any configured URIs`.
2. **User Token Scopes:** add the read scopes (`channels:history`, `channels:read`,
   `groups:history`, `groups:read`, `im:history`, `im:read`, `mpim:history`, `mpim:read`,
   `users:read`, `usergroups:read`). If members may send, add `chat:write` and
   `reactions:write` too.
3. **Enable PKCE** (one-way, for the whole app; see below). Desktop redirects can't request
   bot scopes, which Doorbell doesn't use. Existing tokens keep working.

Any `localhost` URL works. The default redirect URI is `http://localhost:8765/doorbell/callback`,
which is under the registered `http://localhost:8765/` (Slack allows a redirect URI that is a
subdirectory of a registered one). If you register a different URL, members pass the full
address with `--redirect-uri`, for example `doorbell login --redirect-uri http://localhost:9000/cb`.
Doorbell listens on that port, on 127.0.0.1 only.

> **Warning: Reinstall to Workspace.** Clicking **Reinstall to Workspace** grants the person
> who clicks it all the user scopes configured on the app, including `chat:write`. By the
> rule above, that person's next `doorbell login` returns a token that can post, and they end
> up in layout B. Avoid reinstalling unless you need it.

### Enabling PKCE on an existing app is one-way

Turning PKCE on marks the app as a **public client**. Slack's docs say this can't be undone in
the app's settings: only Slack support can turn it off. Before you do it on an app that other
tools use, check that nothing depends on its client secret. A public client doesn't use one.
If you aren't sure, create a new app from the manifest instead. That costs one app slot and
avoids the question.

Two more facts from Slack's docs that shape the setup:

- With PKCE, a `localhost` redirect counts as a desktop redirect, and desktop redirects can't
  request bot scopes. That fits Doorbell, which uses user scopes only.
- Tokens from a localhost sign-in rotate: they expire after 12 hours and come with a refresh
  token. Doorbell renews them on its own, with no client secret. With PKCE, refresh tokens
  expire after 30 days. If a machine is off for longer than that, the member signs in again
  with `doorbell login`.

## Share the Client ID

Give members the Client ID (and, for layout C, both). They can set it three ways:

- `--client-id <ID>` on the command;
- `slack_client_id: <ID>` in `config.yaml` in their state folder;
- the environment variable `DOORBELL_SLACK_CLIENT_ID`.

Then each member runs `doorbell login` ([SETUP.md](SETUP.md#4-sign-in-with-doorbell-login)).
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

- The member runs `doorbell logout`. It revokes Doorbell's tokens with Slack (it asks for the
  password when a sealed token exists) and forgets them. Agents and cursors stay.
  Other tools using the same app keep their own tokens, and so the accumulated scopes: to
  reset a member to a read-only sign-in, every token of theirs for the app must be revoked.
- The member opens Slack's **Apps** settings and removes the app from their account.
- You remove the app from the workspace, which revokes every token it issued. You can also
  delete the app at <https://api.slack.com/apps>.

A revoked token makes the member's identity `broken` (`invalid_auth`). Doorbell stops polling
for it and sends a notification. The member signs in again with `doorbell login`.

## Members on SSH or a server

Members whose Doorbell runs on a box without a browser open the link on their laptop, click
Allow, and paste the `http://localhost:8765/doorbell/callback?code=...` address the browser
ends on into the waiting `doorbell login`. The page won't load; that is expected. See
[SETUP.md](SETUP.md#on-a-server-over-ssh-or-headless).

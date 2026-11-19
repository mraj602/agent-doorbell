# Security policy

## Supported versions

Security fixes land on `main` and in the latest release. Older releases do not get fixes, so
please update.

| Version | Supported |
| --- | --- |
| `main` | Yes |
| Latest release | Yes |
| Older releases | No |

## Reporting a vulnerability

Please report security problems **privately**, not in a public issue, pull request or
discussion.

1. Open the repository's **Security** tab and choose **Report a vulnerability** (GitHub
   private vulnerability reporting). Only the maintainers see the report.
   If you cannot use GitHub, email [mraj602@gmail.com](mailto:mraj602@gmail.com) instead.
2. Describe the problem, the version or commit, how to reproduce it, and the impact you expect.
3. Do not include real Slack tokens, message contents or personal data. Redact them, or use a
   throwaway workspace.

What to expect:

- An acknowledgement within 7 days.
- An assessment and a plan, or a request for more detail, within 14 days.
- A fix on `main` and a patch release, as fast as the severity needs. We will agree a
  disclosure date with you; 90 days is the default upper bound.
- Credit in the release notes and the advisory, if you want it.

Agent Doorbell is a volunteer project and has no bug bounty.

## Security model

- **Watching is read-only.** `doorbelld` watches Slack with a token that has read scopes only.
- **Sending is optional and needs your password.** If you add a sender token
  (`doorbell sender add`), it is a separate token with `chat:write` (and optionally
  `reactions:write`), stored encrypted with a key derived from a password you choose (scrypt,
  AES-256-GCM). The decrypted token is kept only in the daemon's memory, from the time you
  type the password (`sender add` or `unlock`) until the daemon stops or you run
  `doorbell lock`. Adding, replacing or removing the sender token, approving a draft,
  granting an agent direct sending, unlocking and changing the password all need the
  password; revoking, rejecting and locking only take power away and don't. Password checks
  run one at a time, get slower after each wrong password (up to a minute), and repeated
  failures pop a notification.
- **What an agent can send is checked by the daemon.** A granted agent can only post in the
  conversation of one of its own open items, and at most `send_rate_per_hour` messages and
  reactions an hour; sends are serialised, so parallel calls can't exceed it or answer an
  item twice. `@channel`, `@here` and `@everyone` in a direct send are turned into plain
  text. A grant is signed with a key derived from the password and covers the agent's
  registration and subscription as they were when you granted it: if the subscription
  changes, or the agent is registered again, the grant stops working until you grant again.
  Rows written straight into the database don't verify, and are dropped when the key
  changes.
- **The daemon can't tell which local process is calling.** Any process with the access key
  can name any agent, so a grant works for whoever uses that agent's name. What a grant bounds
  is where messages can go: the conversations that reached that agent's subscription.
- **`doorbell review` shows text exactly.** Control characters in drafts and in the Slack
  messages they answer are shown escaped, so a draft can't hide part of itself, and drafts
  that notify a whole channel are flagged.
- **Owner commands refuse to run inside an agent.** The commands that ask for the password
  need a terminal and stop when they see a known agent harness in the environment. This is a
  convenience guard, not a security boundary: the password is the boundary.
- **The daemon never calls an LLM.** It watches Slack, queues events and wakes agents. Any
  model call happens in the agent you chose, not in Agent Doorbell.
- **The Slack token lives in a file with mode 600** that the daemon reads. It is not passed on
  command lines or written to logs.
- **The CLI talks to the daemon on 127.0.0.1 with a random access key.** The daemon writes
  the port and key to `endpoint.json` (mode 600) in its state folder. Without the key, a
  connection is refused, so other local users can't use the daemon.
- **Agent Doorbell doesn't touch agent permissions.** It never writes them, and an agent gets
  no Slack write access from Doorbell unless you grant it with your password. What the agent may do in Slack is set by you, in the
  agent's own harness.
- **Prompt injection is the main risk.** Agents read messages written by other people, and a
  message can try to give the agent instructions. Limit the damage in the agent's harness:
  triage (no Slack write tool) or draft (drafts only, sending denied) for anything you don't
  fully trust. The generated instructions tell the agent to treat messages as data, but that
  is not a guarantee.
- **An agent that can run any command as you is not contained by Doorbell.** It runs as the
  same user as the daemon, so it could, for example, read the daemon's memory while sending is
  unlocked, or replace the `doorbell` program to capture your password the next time you type
  it. Doorbell's send policy protects you against an agent that can only run `doorbell`
  commands (`Bash(doorbell *)` in Claude Code), which is the recommended setup.

## Scope

In scope: everything in this repository that ships, namely the `doorbell` package, the CLI,
the daemon, the templates, the service files it installs, and the Slack app manifest.

Examples of reports we want: the token or message contents leaking to logs, files or other
users; the endpoint or token file created with loose permissions; a way to use the daemon without
the access key; the daemon making a write call to Slack; a way for a Slack message to make
Agent Doorbell run code or change subscriptions without an audit entry; command injection in
`dispatch` or `service install`.

Out of scope:

- Prompt injection that works because of the permissions you granted the agent, or because
  of a flaw in the agent itself (report those to its vendor).
- Vulnerabilities in Slack, Claude Code, Codex or other third-party software.
- Attacks that need an attacker who already has your user account, your token file or your
  unlocked machine.
- Denial of service by someone who can already post in a channel you watch.
- Findings from automated scanners without a demonstrated impact.

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

- **The daemon is read-only.** `doorbelld` only reads from Slack. It never posts, reacts,
  edits or deletes. The Slack app asks for read scopes only.
- **The daemon never calls an LLM.** It watches Slack, queues events and wakes agents. Any
  model call happens in the agent you chose, not in Agent Doorbell.
- **The Slack token lives in a file with mode 600** that the daemon reads. It is not passed on
  command lines or written to logs.
- **The CLI talks to the daemon on 127.0.0.1 with a random access key.** The daemon writes
  the port and key to `endpoint.json` (mode 600) in its state folder. Without the key, a
  connection is refused, so other local users can't use the daemon.
- **Agent Doorbell doesn't touch agent permissions.** It never writes them and doesn't grant
  the agent any Slack write access. What the agent may do in Slack is set by you, in the
  agent's own harness.
- **Prompt injection is the main risk.** Agents read messages written by other people, and a
  message can try to give the agent instructions. Limit the damage in the agent's harness:
  triage (no Slack write tool) or draft (drafts only, sending denied) for anything you don't
  fully trust. The generated instructions tell the agent to treat messages as data, but that
  is not a guarantee.

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

# Running agents for days

Can an AI agent session sit in the background for days waiting for Slack messages? Mostly,
yes, but there are limits that matter. This page collects them and explains why Agent
Doorbell recommends **headless dispatch** for long-running use.

Facts were checked against Claude Code 2.1.x documentation in November 2026. Agent CLIs
change quickly; check the linked pages for your version.

## Claude Code: a long-lived background session

The live mode (`claude /doorbell-listen`, optionally in the background) keeps one session running. While it waits,
it runs `doorbell wait` as a background command and spends nothing.

| Question | Answer | Source |
|---|---|---|
| Is there a maximum runtime for a `--bg` session? | None documented. Sessions run under a supervisor and survive closing the terminal. | [Agent view](https://code.claude.com/docs/en/agent-view) |
| Is an idle session stopped? | Only sessions that are idle *and* finished and unattached for about an hour. A session blocked on a running background command is working, not idle. | [Agent view](https://code.claude.com/docs/en/agent-view) |
| Does a background command time out? | Not in interactive sessions: the 2 min default / 10 min cap applies to foreground commands. In unattended sessions (`-p`, SDK, CI) background work is capped (30 min default, 2 h max). Whether `--bg` sessions count as unattended isn't documented. | [Tools reference](https://code.claude.com/docs/en/tools-reference) |
| Sleep and reboot? | Sessions resume after sleep. A reboot stops them; they show as failed or stopped and must be respawned (`claude respawn <id>`). | [Agent view](https://code.claude.com/docs/en/agent-view) |
| Context growth? | Every wake resends the whole conversation, so each batch costs more than the last until auto-compaction summarises older history. Fidelity drops after many compactions. | [Costs](https://code.claude.com/docs/en/costs) |
| Usage limits? | Subscription plans have 5-hour and weekly windows. Claude Code can wait for a reset and continue, but not for a session that moved to the background: it stays stopped until you resume it. | [Costs](https://code.claude.com/docs/en/costs) |
| Wake reliability? | The session is re-invoked when a background command exits. Some users report missed or delayed notifications. | [claude-code issues](https://github.com/anthropics/claude-code/issues) |

So the live mode works and is pleasant for watching what the agent does, but over days you
must watch for reboots, usage-limit stops and a growing context.

## Headless dispatch (recommended)

`doorbell dispatch` turns it around: the long-running part is a tiny service with no LLM, and
the agent runs only per batch:

```
doorbell wait  (blocks, no tokens)  ──▶  claude -p /doorbell-handle  (fresh, short)  ──▶  exits  ──▶  wait again
```

- **No context growth:** each run starts small and costs about the same.
- **Survives reboots and crashes:** the service manager (systemd, launchd or Task Scheduler) restarts the dispatcher; items wait in
  the queue, leased items come back after 15 minutes.
- **Usage limits are harmless:** a run that hits the limit fails, items stay queued, the
  dispatcher backs off (1, 2, 4 ... up to 30 minutes) and tries again after the reset.
- **Bounded damage:** a run that resolves nothing triggers the backoff, and items that keep
  failing become `stuck` after 3 attempts, so a broken agent can't burn tokens in a loop.
- **Runs are killed after `--timeout`** (default 15 minutes).
- **Any agent works.** The dispatcher only runs a command.

The trade-off: a run remembers nothing from earlier runs except what is in files. Put durable
knowledge in `AGENTS.md` or notes the agent can read.

### Cost control for headless runs

- Pick a model per agent: `claude -p --model sonnet /doorbell-handle --allowedTools ...`.
- With API billing, cap each run: `claude -p --max-budget-usd 0.50 /doorbell-handle`.
- `claude -p` waits at most 10 minutes for background work it started (raise with
  `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`). Handling a batch shouldn't need background work.

## Other agents

| Agent | Headless command | Notes |
|---|---|---|
| OpenAI Codex CLI | `codex exec "Follow AGENTS.md: handle the doorbell queue once, then stop."` | Can resume with `codex exec resume --last`. [Repo](https://github.com/openai/codex) |
| Gemini CLI | `gemini -p "Follow AGENTS.md: handle the doorbell queue once, then stop."` | Non-interactive with `-p` or piped input. [Docs](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md) |
| Your own script | anything | Use the protocol at the bottom of the generated AGENTS.md: `inbox --claim`, act, `ack`. |

Whatever the agent, its own harness decides which Slack tools it may use. Agent Doorbell
doesn't grant or check those permissions; set them as in
[SETUP step 6](SETUP.md#6-decide-what-the-agent-may-do).

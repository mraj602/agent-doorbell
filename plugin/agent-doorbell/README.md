# Agent Doorbell plugin for Claude Code

Unofficial; not affiliated with Slack or Anthropic.

```
/plugin marketplace add mraj602/agent-doorbell
/plugin install agent-doorbell@agent-doorbell
```

From a shell: `claude plugin marketplace add mraj602/agent-doorbell` and
`claude plugin install agent-doorbell@agent-doorbell`.

| Skill | What it does |
|---|---|
| `/agent-doorbell:setup` | Sets up Agent Doorbell step by step with a check after each step. Installs the `doorbell` command if it's missing (after asking), and signs you in with `doorbell login`: you click Allow and no token is pasted. |
| `/agent-doorbell:handle` | Claims the queue once, handles every item, then stops. Use it headless: `claude -p "/agent-doorbell:handle" --allowedTools ...`. |
| `/agent-doorbell:listen` | Live mode: waits on the queue in the background and handles messages as they arrive. |
| `/agent-doorbell:status` | Explains what the watcher is doing: agents, queue, stuck items, API use, and whether an identity is `locked` or needs `doorbell login`. |

Hooks: after a Slack reply, draft, scheduled message or reaction, the matching queue item is
acked; and the agent can't end its turn while it holds claimed, unresolved items. Both act
only in an agent folder (one with a `.doorbell-agent` file), and do nothing if `doorbell`
isn't installed.

The `handle` and `listen` skills also use Doorbell's own sending (`doorbell draft`, `doorbell
send`) when the folder's `AGENTS.md` says so; see
[Sending through Doorbell](https://github.com/mraj602/agent-doorbell/blob/main/docs/SETUP.md#sending-through-doorbell-optional).

The plugin grants no permissions. Which Slack tools the agent may use is up to your Claude
Code settings or command-line flags; see the
[setup guide](https://github.com/mraj602/agent-doorbell/blob/main/docs/SETUP.md#6-decide-what-the-agent-may-do).

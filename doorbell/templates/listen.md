---
description: Live mode. Wait on the doorbell queue forever and handle Slack messages as they arrive.
---
Follow AGENTS.md (via CLAUDE.md). This command is only the loop.

1. If `doorbell status` doesn't list your agent (name in `.doorbell-agent`), run `doorbell register`.
2. Run `doorbell wait` as a **background** Bash command (`run_in_background: true`), then end your
   turn with a one-line note. While it blocks you spend nothing. Don't use sleep, /loop,
   ScheduleWakeup or Monitor.
3. When it completes, follow `.claude/commands/doorbell-handle.md` to claim and handle every item.
4. Go back to step 2.

Keep your own chat output to one short line per batch.

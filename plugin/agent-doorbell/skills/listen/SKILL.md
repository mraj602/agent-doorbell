---
name: listen
description: Live mode for Agent Doorbell. Wait on the doorbell queue in the background, at no token cost, and handle Slack messages as they arrive, forever. Use in an agent folder (one with a .doorbell-agent file) when asked to listen for, watch or wait for Slack messages, or when run as /agent-doorbell:listen.
---

Follow `AGENTS.md` in this folder for what to do with messages. This skill is only the loop.

1. If `doorbell status` doesn't list your agent (the name in `.doorbell-agent`), run
   `doorbell register`.
2. Run `doorbell wait` as a **background** Bash command (`run_in_background: true`), then end
   your turn with a one-line note. While it blocks you spend nothing. Don't use sleep, /loop,
   ScheduleWakeup or Monitor.
3. When it completes, handle the queue exactly as the `handle` skill says: claim, act on every
   item, ack every item.
4. Go back to step 2.

Keep your own chat output to one short line per batch.

If this session is also the receiver of `doorbell dispatch --handoff` batches, ask the user to
pin it (Ctrl+T in `claude agents`) so it isn't retired for being idle. Don't edit Claude Code's
files or settings yourself.

---

# Agent Doorbell protocol

Run these from this folder; your agent name is read from `.doorbell-agent`.

| Step | Command | Notes |
|---|---|---|
| Register (first run) | `doorbell register` | Uses `sub.yaml` from this folder. Only needed once: if another command says `no agent named ...`, register and continue. Re-running it resets your subscription to `sub.yaml`. |
| Wait for work | `doorbell wait` | Blocks with no cost until there is work, then prints `{"pending": n, "unresolved": m}`. |
| Claim the queue | `doorbell inbox --claim` | JSON `items`, each with `seq`, `messages`, `context`, `reply_to` (`channel_id`, `thread_ts`) and `warning`. Claimed items are leased for 15 minutes. |
| Resolve an item | `doorbell ack <seq> --outcome replied\|drafted\|reacted\|ignored\|escalated --note "..."` | Every claimed item must be resolved. Unresolved items come back when the lease expires and become `stuck` after 3 attempts. |
| Change who you listen to | `doorbell sub add users @alex --why "..."` | Only when {owner} asked for it. Audited, and {owner} gets a notification. |

To answer, use your own Slack tool in `reply_to.channel_id` / `reply_to.thread_ts`, then ack
the item with the matching outcome. If you can't post (your harness doesn't allow it, or the
tool fails), don't retry in a loop: ack the item as `escalated` with the reply you would have
sent in `--note`.

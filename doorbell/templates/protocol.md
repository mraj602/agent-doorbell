---

# Agent Doorbell protocol

Run these from this folder; your agent name is read from `.doorbell-agent`.

| Step | Command | Notes |
|---|---|---|
| Register (first run) | `doorbell register` | Uses `sub.yaml` from this folder. Only needed once: if another command says `no agent named ...`, register and continue. Re-running it resets your subscription to `sub.yaml`. |
| Wait for work | `doorbell wait` | Blocks with no cost until there is work, then prints `{"pending": n, "unresolved": m}`. |
| Claim the queue | `doorbell inbox --claim` | JSON `items`, each with `seq`, `messages`, `context`, `reply_to` (`channel_id`, `thread_ts`) and `warning`. Claimed items are leased for 15 minutes. |
| Resolve an item | `doorbell ack <seq> --outcome replied\|drafted\|reacted\|ignored\|escalated --note "..."` | Every claimed item must be resolved. Unresolved items come back when the lease expires and become `stuck` after 3 attempts. |
| Draft a reply | `doorbell draft <seq> --text-file - <<'EOF'` (text, then `EOF`) | Stored in Doorbell, not in Slack, and acks the item as `drafted`. {owner} reviews it with `doorbell review`; nothing is posted until they approve. Works for every agent once {owner} has set up sending. |
| Send a reply | `doorbell send <seq> --text-file - <<'EOF'` | Posts in the item's conversation (in its thread for channel messages) as {owner} and acks it as `replied`. Only if {owner} ran `doorbell grant <you>`; otherwise it fails and tells you to draft. |
| React | `doorbell react <seq> eyes` | Adds a reaction to the item's latest message. Needs the same grant. |
| Ask to send directly | `doorbell request-send --why "..."` | Notifies {owner}. Never ask in Slack, and never try the owner-only commands (`grant`, `unlock`, `approve`, `review`): they need {owner}'s password. |
| Change who you listen to | `doorbell sub add users @alex --why "..."` | Only when {owner} asked for it. Audited, and {owner} gets a notification. |

To answer, either use `doorbell draft` / `doorbell send` (they ack for you), or use your own
Slack tool in `reply_to.channel_id` / `reply_to.thread_ts` and then ack the item with the
matching outcome. If you can't post (your harness doesn't allow it, or the
tool fails), don't retry in a loop: ack the item as `escalated` with the reply you would have
sent in `--note`.

---
description: Claim the doorbell queue once, handle every item, then stop.
---
Follow AGENTS.md (via CLAUDE.md): it says what to do with each message and how you may reply.

1. Run `doorbell inbox --claim`. If it says `no agent named ...`, run `doorbell register` first.
2. For **every** item, decide by your instructions, then do exactly one thing:
   - **Reply, draft or react** with your Slack tool in the item's `reply_to`, if your
     instructions allow it, then `doorbell ack <seq> --outcome replied|drafted|reacted`
     (a doorbell hook may already have acked it; acking twice is harmless).
   - **Ignore:** `doorbell ack <seq> --outcome ignored --note "<why>"`.
   - **Leave for the owner:** `doorbell ack <seq> --outcome escalated --note "<one-line summary>"`.
3. Stop when every item is resolved. A Stop hook won't let you finish while a claimed item is open.

Keep your output to one short line per item.

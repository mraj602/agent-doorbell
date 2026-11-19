---
name: status
description: Show and explain what Agent Doorbell is doing, by running doorbell status and doorbell log. Use when the user asks whether the Slack watcher or doorbell is running, why an agent didn't answer, what is queued or stuck, or runs /agent-doorbell:status.
allowed-tools: Bash(doorbell status:*), Bash(doorbell log:*), Bash(doorbell doctor), Bash(command -v doorbell)
---

1. Run `command -v doorbell`. If nothing prints, say Agent Doorbell isn't installed and offer
   `/agent-doorbell:setup`. Stop.
2. Run `doorbell status`. If it says `doorbelld is not running`, say so, and give the start
   command for this OS: `doorbell service install`, or `doorbell daemon` in a terminal.
3. Summarise in a few lines:
   - `state`: DORMANT means no agent is listening and Slack isn't polled at all; POLLING
     means it is.
   - Each agent: `active` with a waiter is listening; `detached` is registered but nothing is
     waiting (its dispatcher or session isn't running); `inactive` is paused after
     `idle_ttl` without contact and catches up when it returns.
   - Pending and stuck items. For stuck items, suggest finding the cause, then
     `doorbell retry <seq>`.
   - API calls in the last hour.
4. If the user asks why a message wasn't handled, run `doorbell log --since 3600` and look
   for its `deliver` and `resolve` entries. Messages the user sent themselves are never
   queued, and bots are ignored unless listed in `allow_bots`.

Don't change anything; this skill only reads.

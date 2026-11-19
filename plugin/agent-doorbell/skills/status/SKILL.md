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
   - Each identity's `status`: `ok` is watching. `locked` means its token is sealed (layout
     B, single-token mode) and the daemon restarted: it isn't polled until the owner runs
     `doorbell unlock`, then it resumes from where it stopped and nothing is lost.
     `needs_login` means Slack didn't renew the token (the refresh token expired or access was
     revoked): the owner runs `doorbell login`; queued items and cursors are kept. `broken`
     means the token was revoked: sign in again. Say what `token_expires_in_s` shows if it is
     small and the daemon is running; it renews itself.
   - Sending, if `sending` isn't empty: whether it's unlocked (if not, the owner runs
     `doorbell unlock`), which agents are granted, `drafts_pending` (the owner runs
     `doorbell review`) and `send_requests` (the owner can run `doorbell grant <agent>`).
4. If the user asks why a message wasn't handled, run `doorbell log --since 3600` and look
   for its `deliver` and `resolve` entries. Messages the user sent themselves are never
   queued, and bots are ignored unless listed in `allow_bots`.

Don't change anything and don't run `doorbell login` or `unlock`; this skill only reads. Tell the person which command to run.

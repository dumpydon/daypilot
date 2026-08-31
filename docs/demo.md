# DayPilot demo walkthrough

This walkthrough is designed for a short portfolio or interview demo. It uses
the seeded local workspace, so it does not require access to Gmail, Google
Calendar, Google Tasks, X, or a local folder.

## Start in deterministic demo mode

```bash
cp .env.example .env
make reset-demo
make dev
```

Open [http://localhost:3000](http://localhost:3000) and leave
`DAYPILOT_DEMO_MODE=true` in `.env`. The demo database is local to the project.

## Golden prompt

```text
Prepare me for my interview with Rahul tomorrow.
```

The seeded workspace contains:

- a fictional Mail thread confirming an 11:00 AM IST interview;
- Calendar commitments around the interview and open evening time;
- three unrelated Tasks;
- a prepared Files corpus and public X corpus for read-only demonstrations.

## What to show

1. Submit the prompt and point out that tool discovery is visible.
2. Let Mail, Calendar, and Tasks reads complete automatically.
3. Open `Dependencies` and show that the plan is grounded across services.
4. Pause at `Waiting for human approval` and call out that no writes have run.
5. Approve if you want to demonstrate local writes, or Reject for a read-only
   walkthrough.
6. After approval, show the Calendar and Tasks receipts, verification status,
   and the live timeline.

The normal demo proposes one Calendar preparation block, four checklist tasks,
and a follow-up Mail draft. All three are local demo mutations and remain
approval-gated.

## Connected-mode validation

For an already-authorized managed Google account, the cross-service prompt is:

```text
Find my latest email with subject “DayPilot interview test”. Determine the interview date and time from that email. Check my calendar and find a free 60-minute preparation slot before the interview. Create a calendar event called “DayPilot Interview Prep” in that free slot and create one Google Task called “Prepare for DayPilot interview”. Do not draft or send any email. Show me the proposed plan and dependency graph before making any external changes.
```

The connected path searches Gmail, grounds the thread and interview time,
checks Calendar, proposes the two writes, and stops at the same persisted
approval checkpoint. If the mailbox does not contain a matching scheduled
interview, DayPilot completes safely without fabricating a date or proposing
dependent writes.

For a no-write connected QA pass, inspect the plan and Reject it. Never put
provider tokens in the prompt or frontend environment.

## Reset between demos

Use the Preferences panel's **Reset demo workspace** control or:

```bash
make reset-demo
```

This restores demo Mail, Calendar, Tasks, Files, and X seed data. It preserves
preferences and run history. **Clear run history** is a separate control.

## Thirty-second explanation

“DayPilot turns a goal into a grounded plan across semantic MCP services. Reads
run automatically; writes pause at a persisted LangGraph approval checkpoint.
The plan shows true data dependencies, execution is idempotent, and provider
state is read back to produce verified receipts.”

# DayPilot project notes

These notes are interview support for the project owner. They describe the
implemented system and should be kept in sync with the repository.

## Resume bullet candidates

- Built a LangGraph-orchestrated personal operations agent over a curated MCP
  surface spanning Mail, Calendar, Tasks, Files, and X, with typed plans,
  dependency-aware grounding, and persisted state.
- Implemented code-enforced HITL for external writes: exact plan hashes,
  idempotent execution records, provider read-back verification, and resource
  receipts; deterministic evaluations maintain a 0% unauthorized-write rate.
- Added provider-isolated demo and connected modes, including Composio-managed
  Gmail/Calendar/Tasks connectivity and safe, allowlisted local Files access.

## Thirty-second explanation

DayPilot is a personal operations agent that turns a natural-language goal into
a reviewable plan across connected services. LangGraph owns the lifecycle and
MCP keeps service capabilities independent from orchestration. Reads can happen
automatically, but every external write pauses at a durable approval checkpoint,
then executes once and is verified against provider state.

## Two-minute architecture explanation

The browser talks to a small FastAPI API. A `RunCoordinator` creates a run and
starts a persisted LangGraph thread. The graph first understands the request,
discovers the current tools from six isolated MCP server processes, and gathers
bounded read-only context. A planner converts normalized results into typed
actions and `depends_on` edges. Those edges carry data flow: a Mail search
grounds a thread ID, the thread grounds an interview datetime, Calendar grounds
a free slot, and the slot grounds Calendar/Tasks writes.

The gateway owns the code-level risk boundary. It classifies discovered tools,
rejects writes without a matching `WriteAuthorization`, and never gives the
model arbitrary provider SDK access. When writes exist, LangGraph persists an
interrupt and waits for a human decision. Approval is tied to the exact action
payload and plan hash. The execution ledger's unique `(run_id, action_id)` key
prevents duplicate resumes from repeating a mutation. Successful results are
read back through MCP and projected into verified or created-unverified
receipts. The local deterministic reasoner exercises the same graph and policy
when no OpenAI key is configured.

## Strong talking points

### Why a semantic MCP layer?

Provider-native actions are numerous and change independently. The planner sees
stable concepts such as `search_mail`, `find_free_slots`, and `create_event`;
provider details remain inside adapters and MCP servers.

### Why persisted HITL instead of a UI confirmation?

The approval decision is part of graph state, not a frontend boolean. A refresh,
duplicate request, or altered payload cannot turn an unapproved plan into an
authorized write.

### Why explicit dependencies?

The graph is useful only if its arrows correspond to runtime facts. A dependency
is also a grounding rule: if the upstream result is absent, the downstream
action is blocked; if it exists, the exact value is bound into the next tool
call. There is no general symbolic-template evaluator.

### Why an execution ledger?

External providers can time out after accepting a mutation. Recording an attempt
before invocation lets DayPilot surface an unknown outcome instead of blindly
retrying and potentially duplicating the user's change.

### Why deterministic evaluations?

Agent quality and write safety need repeatable evidence. Each scenario gets an
isolated seeded database, runs the real graph, and measures tool selection,
plan validity, approval correctness, dependency accuracy, execution success, and
unauthorized writes.

## Honest limitations to mention

- The project is a portfolio-grade local system, not a multi-tenant SaaS.
- Connected mode requires the user's own provider authorization.
- Local Files is read-only and explicitly allowlisted.
- Google Tasks stores dates rather than exact due times.
- Managed X availability depends on Composio app support.
- Some provider responses may be reported as created-unverified when stable
  read-back correlation is unavailable.

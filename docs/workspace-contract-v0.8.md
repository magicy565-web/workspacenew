# Workspace execution contract v0.8

Independent implementation of durable execution records, not copied QM code. Model reasoning / chain-of-thought is not requested or accepted as an event kind. This ledger records reported public activity and receipts; it cannot make external side effects exactly-once or independently prove that a host checkpoint exists.

## Claim and recovery

`POST /api/hub/tasks/claim` accepts optional `capabilities:[string]` and `host_identity:string` in addition to existing fields. Registered workers use their registered capabilities (a supplied list must be a subset); unregistered MCP callers may declare their own. Continuation capability is `continuation:HOST`, e.g. `continuation:codex`. `host_identity` is a non-secret stable fingerprint of the actual host/configuration/storage namespace, **excluding credentials**; root worker computes it. A changed command/cwd/host identity does not inherit an old checkpoint.

Every task has one stable `execution_session_id` (including legacy tasks). Successful claims retain all existing fields and add top-level `execution:{id,task_id,run_id,attempt,last_seq,recovery}`. `recovery` is outside the bounded task context; it contains `{decision,reason,checkpoint:null|Checkpoint,calls:[CallState],result_available,result_sha256,events_url,result_url}`. It does not include large result content. Worker must supply this recovery information to its executor and read required previous event pages before continuing/restarting; it must not interpret room messages as a host checkpoint.

Claim decisions: `start` (first attempt), `restart` (new execution, not continuation), `continue` (same worker ID and host identity, matching `continuation:HOST` capability and structurally valid checkpoint), `submit_result` (a staged final result can be submitted without running the model). `review_required` prevents automatic claim/complete/retry. Public reads can also report `completed` or `cancelled`. A valid checkpoint covers all recorded tool activity and reconciliations up to its `through_seq`; a checkpoint predating later tool activity cannot authorize continuation. `checkpoint` contains `{host,session_id,host_identity,checkpoint_id,through_seq,event_seq,worker_id}` and is a host-reported receipt, not a service-verified remote resource.

An unfinished tool call, or a result explicitly reported `unknown`, requires review unless its original `retry_safe` is true. `retry_safe` is a trusted executor declaration, not inferred from a tool's name. These calls are never silently cleared. Discovery of a blocked queued/expired task records the reason and changes the existing task state to `failed`; no new task/run status is introduced. Failed retries still use the same task/session and all v0.7 snapshots. Unresolved unsafe calls reject retry/complete with 409. Cancellation remains terminal.

## Events

- `POST /api/hub/tasks/{task_id}/execution/events` body `{worker_id,lease_token,event_id,kind,payload,expected_seq?}` → `{event,execution}` (201). Requires current unexpired task lease and execution permission. Ownership/cancellation/revocation are checked **before** idempotency. `event_id` is a stable ASCII identifier, max 120 chars. Repeating the same kind/payload under the current lease returns the original event/seq; same ID with different content rejects 409. `expected_seq`, when supplied, must equal the session's latest seq for a new append; it is not rechecked for an identical retry.
- `GET /api/hub/tasks/{task_id}/execution` → `{id,task_id,run_id,task_status,active:boolean,attempts:[Attempt],last_seq,recovery,can_reconcile,can_retry,can_cancel}`. `Attempt:{attempt,worker_id,host_identity,decision,reason,started_at}`. UI displays actual attempt decision, not a promise about the next worker. `can_reconcile` also respects editor/owner permission and absence of a live lease. `can_retry` respects unresolved calls and existing run ordering restrictions.
- `GET /api/hub/executions/{id}/events?after=0&limit=50` → `{events:[Event],cursor,has_more,last_seq}`. Limit 1–100. Sequence starts at 1 and is monotonically assigned transactionally; there are no mutable event records.

`Event:{seq,event_id,kind,payload,attempt,worker_id,author_id,author_name,created_at}`. Never returns lease tokens, registration session tokens or raw final-result checkpoints. `CallState:{call_id,tool,state,retry_safe,call_seq,result_seq,review_seq}`; state is `pending|unknown|succeeded|not_executed`. Tool inputs and private model traces are intentionally not accepted. A public summary must omit credentials.

Worker event kinds and exact payloads (unknown fields reject):

| kind | payload |
| --- | --- |
| `progress` | `{message:string}` (1–2,000 chars) |
| `host_session` | `{host:string,session_id:string,host_identity:string}` |
| `checkpoint` | `{host:string,session_id:string,host_identity:string,checkpoint_id:string,through_seq:int}`; must match a previously recorded host session and cover that record; through_seq cannot be in the future |
| `tool_call` | `{call_id:string,tool:string,summary?:string,retry_safe:boolean}`; call_id is unique across this stable execution session |
| `tool_result` | `{call_id:string,outcome:"succeeded"|"not_executed"|"unknown",summary?:string}`; requires its recorded call; an existing terminal result cannot be replaced |

Host/session/checkpoint identifiers are opaque, non-secret strings, max 200 chars; host is ASCII max 80; host_identity max 200. Call ID is ASCII max 120, tool name ASCII max 80, public summary max 1,000 chars. Event payload ≤16 KiB UTF-8 canonical JSON. Each session permits 1,024 worker events, 64 distinct tool calls and 100 claimed attempts; system events have a separate reserved allowance (total events ≤2,048) so completion/cancellation remains recordable. Excess rejects 413 without partial changes. System-only kinds include `attempt.started`, `result.ready`, `task.completed`, `task.failed`, `task.cancelled`, `recovery.required`, `recovery.reviewed`; workers cannot forge these kinds. Actual unknown tool results are recorded truthfully and prevent subsequent automatic work; they do not certify failure or success.

## Final result checkpoint

- `POST /api/hub/tasks/{task_id}/execution/result` body `{worker_id,lease_token,result}` → `{sha256,bytes,event_seq,execution}`. Requires the current task lease. Canonical final JSON uses the existing complete result schema and size limit (≤7 MiB). First write freezes it; same content retry is idempotent, different content rejects 409. A small `result.ready` event stores only hash and size; the large JSON is in a separate table.
- `POST /api/hub/tasks/{task_id}/execution/result/read` body `{worker_id,lease_token}` → `{result,sha256}`. Execution permission and the current live lease are required; this is not a public event-list field. On reclaim, `submit_result` means fetch here then call the existing `/tasks/{id}/complete` with exactly that result. Unknown unsafe side effects still take precedence over a staged result and require review.

Task completion persists its ledger receipt in the same transaction as final messages/artifact versions. A lost completion response can be retried using the same task/lease/result; it does not create another version. A new worker cannot reclaim a task already committed completed. If the process died after result staging but before completion, reclaim can submit that durable result without rerunning the model. Direct legacy one-shot completion remains supported.

## Human reconciliation

`POST /api/hub/executions/{id}/reconcile` body `{request_key,expected_seq,call_id?,decision,note}` → execution detail. Write permission (not execute-only) and a nonempty public note ≤2,000 chars are required. Same request key + same input is idempotent; changed payload or stale expected_seq rejects 409. A live task lease must first stop/expire; reconciliation does not race the running host.

- `confirmed_succeeded`: a specific unresolved unsafe call was checked against the actual external state; record it as succeeded.
- `confirmed_not_executed`: the external state was checked and the action did not happen; record it as not_executed. This permits a future restart; it does not itself execute anything.
- `terminate`: record the decision/note, then cancel the entire owning run through the existing cancellation semantics. call_id is optional.

Review records include actor identity, original call and decision. After all unsafe calls are reconciled, a failed task may be requeued with the existing `/runs/{id}/retry`; this does not force continuation. Previous checkpoints cannot silently skip new reconciliation records. The subsequent actual claim decides continue / restart / submit_result. UI exposes only server-supplied action flags, and does not approve results or resolve artifact comments automatically.

Workspace isolation uses the existing per-workspace database and authenticated request binding. API session/token revocation is checked by server middleware for every request; event mutations additionally recheck current membership when an authenticated actor is bound. Root must classify the three `/tasks/{id}/execution/...` POST routes as `execute`, while reconciliation remains `write`. Public errors: malformed input 400, missing scoped object 404, ownership/sequence/idempotency/recovery conflict 409, size/count overflow 413.

# Workspace v0.10: Agent sessions across user turns

**Unreleased development checkpoint.** Core, adapter, HTTP reads and the execution-dialog UI are integrated on `feature/agent-sessions-v0.10`. Actual cross-task model continuation and full UI integration still require acceptance; `main` remains the verified v0.9 baseline. See [development handoff](development-handoff-2026-11-03.md).

This is an independent implementation of persistent room/Agent identity and eligible native host continuation. It is not the QM runtime, a mailbox service, or evidence of Teamily's private routing algorithm. The caller supplies its model and authorized tools. M1 workspace and M2 Agent team execution/continuation are the immediate scope; external commercial networks and marketplaces are excluded.

## Two identities, two recovery decisions

`exec_<task_id>` remains the existing task execution ledger, attempts, tool outcomes, lease and final-result checkpoint. A separate stable Agent session is unique to `(workspace storage, room_id, agent_id)`. It persists across messages and tasks. New tasks keep their own task IDs and execution IDs.

Every successful claim adds this exact object at `claim.execution.agent_session`:

```json
{
  "id": "ags_<opaque id>",
  "room_id": "room id",
  "agent_id": "writer",
  "generation": 1,
  "turn_index": 2,
  "context_fingerprint": "sha256",
  "continuation": {
    "decision": "continue",
    "reason": "matching_completed_turn",
    "from_task_id": "previous completed task id",
    "from_execution_id": "exec_previous task id",
    "checkpoint": {
      "host": "codex",
      "session_id": "native host session id",
      "host_identity": "host fingerprint",
      "worker_id": "desktop-codex",
      "checkpoint_id": "opaque host checkpoint id"
    }
  }
}
```

The Agent-session object is bounded to 4 KiB and contains no lease, token, private model reasoning, command arguments, paths or tool output. `generation` starts at 1 on first claim and changes when a fresh native context is required. `turn_index` is the stable creation order of tasks within this logical session; retries retain it. `continuation.decision` is `fresh`, `continue`, or `task_recovery`. `from_task_id`, `from_execution_id` and `checkpoint` are null unless cross-task continuation is actually selected. Old execution `through_seq` and event sequence values are deliberately absent from the cross-task checkpoint.

Adapter priority is strict:

1. `execution.recovery.decision == continue`: resume the **current task's** recovery checkpoint.
2. Only when task recovery is `start` and Agent-session continuation is `continue`: resume the separate Agent-session checkpoint.
3. Task recovery `restart` starts a fresh native session. `submit_result` submits the frozen result without invoking the model. `review_required` cannot be claimed or replayed.

Both forms of native continuation must verify the exact host session ID returned by the host. A missing/different native session fails visibly; it cannot be silently relabelled as successful continuation. A new task's host-session/checkpoint events use its new ledger sequence space. An incompatible context fingerprint also invalidates same-task native continuation; unresolved external outcomes still take precedence over any fresh-context decision.

Reasons include `first_turn`, `matching_completed_turn`, `task_recovery_priority`, `no_completed_checkpoint`, `worker_changed`, `host_changed`, `continuation_unsupported`, `agent_instructions_changed`, `skills_changed`, `team_permissions_changed`, `source_visibility_changed`, `shared_context_changed`, `previous_turn_incomplete`, and `context_snapshot_unavailable`. Reasons describe observed server state, not a guarantee that a native host is reachable.

## Exclusive ownership and native head

The existing claim transaction also acquires the logical Agent session. Two tasks for the same room/Agent cannot be active writers simultaneously; distinct Agents may execute in parallel within the existing DAG/room ordering rules. A prior expired but unfinished task keeps precedence: expiration alone does not authorize a later task to bypass its recovery. Cancellation, failure and unknown external calls never make an old lease valid again.

Only the most recently **completed** task can supply the native head. A more recent completed task with no eligible checkpoint clears the previous native head. Changing workers/hosts and later returning to an old worker cannot resurrect an older native branch. Promotion occurs in the same transaction as final task/result/artifact completion, after the command executor has observed its command exit successfully. External hosts must uphold that command-exit obligation; server leases fence writes, but cannot physically stop all orphan processes in another host. Operating-system native-session mutexes and general host supervision are separate work, not claimed here.

The claim transaction fingerprints the effective Agent instructions and current role definition, bound skills and versions, current/frozen team permissions, room background, and active source/file visibility. The same transaction builds the supplied context. Changes invalidate the prior native context with an explicit fresh reason. Existing artifact IDs/base versions remain mandatory; continuity does not waive optimistic conflict checks. Context size and explicit omission rules remain in force.

## Unknown outcomes after cancellation

Ordinary cancellation is not a factual confirmation of an external side effect. If a cancelled task still has unsafe pending/unknown calls and no audited `terminate` review, its logical session remains blocked for future tasks. Existing `execution.recovery.decision` stays `cancelled`, but `can_reconcile` is true for an authorized editor while unresolved outcomes remain. The existing reconciliation API accepts a confirmed outcome or a note-bearing `terminate`. That records the user's inspection/decision and releases future tasks to a fresh native context; it never revives the cancelled task, asserts that an unknown side effect did not happen, or automatically retries it. This is post-execution recovery review, **not** pre-execution approval of a sensitive action.

When a ready task is blocked only by logical-session ownership/recovery, an empty claim can additionally return `blocked_sessions` (at most 20, with `blocked_sessions_omitted` for additional entries): `[{id,room_id,agent_id,blocked_by_task_id,execution_id,reason,message}]`. The message is human-readable and the execution ID points to the existing review screen. No lease or token is exposed.

## Read-only integration surface

Implemented read-only HTTP routes on this development branch are:

- `GET /api/hub/rooms/{room_id}/agent-sessions` → `list_sessions(room_id)`.
- `GET /api/hub/agent-sessions/{session_id}?after=0&limit=50` → `get_session(session_id, after=0, limit=50)`; `limit` is 1–100.

Both functions live in `studio.hub_agent_sessions`, use the current workspace-bound database, and raise 404 for IDs outside it. HTTP/MCP authorization remains the normal read scope, including viewers. No reset/send/wait/mailbox mutation endpoint is introduced in this batch.

`list_sessions` returns `{room_id,sessions:[SessionSummary]}`. `SessionSummary` is `{id,room_id,agent_id,agent_name,generation,status,current_task_id,blocked_by_task_id,blocked_reason,blocked_message,last_completed_task_id,native_head,turn_count,created_at,updated_at}`. Status is `idle`, `running` or `blocked`. `native_head` is null or `{task_id,execution_id,host,session_id,host_identity,worker_id,checkpoint_id}`. It is only a committed eligible checkpoint, not a promise that the next worker will match it. A blocked summary includes the original task ID and a human-readable reason.

`get_session` returns `{session:SessionSummary,turns:[Turn],delegations:[Delegation],cursor,has_more}`. Turns are paged by `turn_index`, in ascending order. `Turn` is `{turn_index,task_id,execution_id,run_id,title,status,attempts,created_at,updated_at,latest_attempt,delegated_from_task_id}`. `latest_attempt` is null or `{attempt,generation,worker_id,host_identity,context_fingerprint,continuation,created_at}`. Large messages, results and tool events remain in their existing explicitly read APIs.

`Delegation` is `{id,parent_task_id,child_task_id,parent_session_id,child_session_id,run_id,created_at}`. Each page returns actual delegation edges incident to that page's turns. These edges are created atomically only when a coordinator completes with an accepted plan. Explicit mentions and workflow-created tasks do not fabricate a coordinator parent. DAG dependency edges and logical session grouping are separate from delegation. Mailbox delivery/ACK/wakeup is deferred.

# Caller-owned execution protocol

The workspace supplies tasks, permissions and durable receipts. The caller supplies its model and tools. No provider key is bundled or required by the workspace. HTTP/MCP callers can implement this protocol directly; `studio.worker` connects an external command, and `studio.codex_executor` is one optional adapter.

## Command modes

The configuration's `command` is an argument array launched without a shell. `cwd` is optional, `worker_id` is stable, and `timeout_seconds` is bounded. The subprocess receives a UTF-8 JSON envelope on stdin, then EOF. Workspace registration and bearer tokens are removed from its environment; claims, heartbeats and result submission belong to the parent worker.

Default `output_protocol: "json"` remains compatible with existing commands. Return one final JSON object on stdout and exit successfully. A current server stages this result durably before completing the task. Legacy commands do not provide tool-level progress or host continuation.

Opt-in `output_protocol: "jsonl"` adds `event_protocol: "agent-workspace/events-v1"` to the input. `protocol` remains `agent-workspace/1`. Emit newline-delimited records, flushing each line:

```json
{"type":"execution_event","event_id":"attempt-1:progress-1","kind":"progress","payload":{"message":"Reading the supplied brief"}}
{"type":"result","result":{"message":"Finished","artifacts":[{"title":"Brief","kind":"markdown","content":"Actual completed work"}]}}
```

There must be exactly one final result, after all events; exit with status zero. Diagnostics belong on stderr. Total stdout/stderr are bounded, and the server validates event and result sizes. Event kinds and payload schemas are in [the v0.8 contract](workspace-contract-v0.8.md). The worker supplies `host_identity` and the latest acknowledged `through_seq` for checkpoint events. IDs must remain stable when retrying an identical event and be unique across attempts for distinct activities. There is no inference from a tool name that it is safe to repeat.

The command stream is **observational**, not a synchronous tool approval protocol: emitting a line is not proof the service has committed it. A caller that needs a durable record before a side effect must call the event HTTP/MCP API directly, await its successful receipt, then invoke its authorized tool, and record the outcome. Even that cannot make an unrelated external service transaction exactly-once; an interrupted call with an unknown result needs reconciliation or the external service's own idempotency key.

## Recovery and result submission

Each input includes `execution` with the stable session, attempt, host fingerprint and server recovery decision. On later attempts the worker reads the event pages and includes bounded public history. Old progress descriptions may be omitted explicitly; the complete call states remain in `execution.recovery.calls`. Source/history text never grants additional authority.

- `start`: first attempt.
- `restart`: new execution; do not claim that the old model session resumed. Respect previously confirmed external actions.
- `continue`: use the exact saved host session/checkpoint. Same worker ID, host fingerprint, capability and sufficient checkpoint coverage are required. A missing or incompatible host session must fail visibly; do not silently create a replacement.
- `submit_result`: the worker reads the frozen final result and submits it without starting the external command. This handles a process dying after result staging.
- `review_required`: no automatic claim or replay. An editor must inspect the external state, record a decision, then requeue or terminate.

Completion and event retries reuse identical payloads. Transport disconnects retry; revoked credentials and stale leases do not. Completed task receipts and artifact versions commit together. Public event pages expose result hashes and sizes, not the large final-result checkpoint or lease credentials.

## Codex adapter

`examples/codex-worker.json` enables the JSONL adapter and `continuation:codex`. It preserves the caller's existing CLI authentication, chosen model/profile and a read-only sandbox by default. Streaming tasks persist the native Codex session; a later `continue` uses its exact UUID through `codex exec resume`. The adapter never selects `--last`, copies credentials, or silently falls back to a fresh session. Legacy final-JSON invocation remains ephemeral.

The [official non-interactive CLI documentation](https://learn.chatgpt.com/docs/non-interactive-mode) describes JSONL output and session resumption. Implementation was also checked against the installed CLI help. The adapter maps host session and tool lifecycle receipts, discarding reasoning, command arguments, raw tool output and raw error messages from the workspace event stream. Unsupported tool activity or an unconfirmed result stops automatic recovery. Native Codex session storage remains under the caller's own control and is not included in workspace backups or distributions.

Codex CLI events describe what the host reports; this adapter does not intercept its private tool loop, enforce approvals inside third-party tools, or independently verify remote side effects. Workspace cancellation stops its managed process tree and revokes its lease; an external action already sent may still have happened. The execution view distinguishes these unresolved outcomes from confirmed completion.

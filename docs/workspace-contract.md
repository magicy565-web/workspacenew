# Agent Workspace — implementation contract v0.3

This is the first local, single-user workspace for multiple external agents. It extends the existing Python service. The model runs in the calling agent host, not in this server. QM is an architectural reference; this release does not embed or deploy QM. Existing magazine data and APIs stay intact; magazine UI moves to `/magazines`, and the new workspace is `/`.

All new API paths start with `/api/hub`. JSON requests and responses. Storage uses the existing `STUDIO_DATA_DIR`, with additive hub-prefixed SQLite tables. No fake model responses, timers simulating work, or invented connected workers. Built-in agents: `coordinator`, `researcher`, `writer`, `designer`, `developer`, `reviewer`. Unknown free-form requests go to `coordinator`; the external model may complete that task with a validated task plan. Explicit agent selection bypasses planning. Built-in templates may create known task graphs without claiming semantic classification.

## HTTP

- `GET /bootstrap` -> `{agents, rooms, templates, connection: {mode: "external", active_workers: number}}`.
- `GET /agents` -> `{agents}`.
- `POST /agents` body `{name, description, instructions}` -> agent.
- `PUT /agents/{id}` body `{name, description, instructions}` -> agent (built-ins may be edited).
- `POST /rooms` body `{title?, text, agent_id?: "auto" | id, template_id?: id}` -> full room.
- `GET /rooms/{id}` -> full room.
- `POST /rooms/{id}/messages` body `{text, agent_id?: "auto" | id}` -> full room.
- `PUT /rooms/{id}/context` body `{text}` -> full room.
- `GET /rooms/{id}/events?after=0` -> `{events, cursor}`.
- `POST /tasks/claim` body `{worker_id, agent_ids?: [id], room_id?: id}` -> `{task: object | null, lease_token?: string, lease_expires_at?: string, context?: object}`. Claim only ready work; stable exclusive lease; timeout permits reclaim; dependent tasks cannot start early. Return instructions, room context, conversation and completed dependency results.
- `POST /tasks/{id}/heartbeat` body `{worker_id, lease_token, progress?: string}` -> `{task, lease_expires_at}`.
- `POST /tasks/{id}/complete` body `{worker_id, lease_token, result: {message, artifacts?: [{id?: id, base_version?: number, title, kind: "markdown" | "text" | "html" | "json", content}], plan?: [{key, agent_id, title, instructions, depends_on: [key]}]}}` -> full room. Validate plan all-or-nothing, acyclic, max 12 tasks, max 6 artifacts, all agents exist. Only coordinator can submit plan. Completion must validate owner, unexpired lease and cancellation. Artifact revisions are optimistic: existing artifact requires current `base_version`; cannot change another room's artifacts. All results and revisions commit atomically. Completed task retries with identical result/lease are idempotent; conflicting retries reject.
- `POST /tasks/{id}/fail` body `{worker_id, lease_token, error}` -> full room.
- `POST /runs/{id}/cancel` body `{}` -> full room (invalidates running leases; late results rejected).
- `POST /runs/{id}/retry` body `{}` -> full room (retry failed tasks without redoing completed work).
- `GET /artifacts/{id}?version=N` -> full artifact including content and version history.
- `GET /artifacts/{id}/download?version=N` -> exact persisted bytes, attachment header.
- `GET /artifacts/{id}/preview?version=N` -> exact persisted content, isolated restrictive CSP. HTML preview must not have same-origin active scripts/network authority.

GET room data is side-effect-free apart from schema initialization. Derive status for expired leases truthfully; do not count workers without a valid active lease as online.

## Shapes

Agent: `{id, name, description, instructions, builtin: boolean}`.
Template: `{id, name, description, agent_ids: [id]}`.
Room summary: `{id, title, created_at, updated_at, status, last_message}`.
Room: summary plus `{context: string, messages: [Message], runs: [Run], artifacts: [ArtifactSummary]}`.
Message: `{id, role: "user" | "agent" | "system", text, agent_id: string | null, run_id: string | null, task_id: string | null, created_at}`.
Run: `{id, room_id, status, created_at, tasks: [Task]}`.
Task: `{id, run_id, agent_id, title, instructions, status, depends_on: [task_id], worker_id: string | null, attempts: number, progress: string | null, error: string | null, result: object | null, created_at, updated_at}`. Public reads never return lease tokens.
Task status: `queued`, `running`, `completed`, `failed`, `cancelled`. Ready/blocked is derived from dependencies. Run status: `queued`, `running`, `completed`, `failed`, `cancelled`. A later run in the same room waits until previous runs are terminal, preserving conversational order.
Artifact summary: `{id, room_id, task_id, title, kind, version, created_at, updated_at}`. Full artifact adds `{content, versions: [{version, created_at, task_id}]}`.
Event: `{id: integer, room_id, run_id, task_id, type, text, created_at}`.
Errors: JSON `{error: string}` with appropriate 400/404/409 response.

## Ownership

Backend agent owns `studio/hub.py`, `studio/hub_http.py`, and `tests/test_hub.py`. `hub_http.dispatch(method, path, body, query)` returns `(status, payload, mime, headers)` or `None`; core functions have concise matching names for root's MCP wrappers. Backend must announce final signatures.
UI agent owns only `studio/static/workspace.html`, `workspace.css`, `workspace.js`.
Root owns server integration, MCP/CLI adapters, documentation, user data demonstration, packaging and final checks.
Experience/QA agent owns `docs/workspace-experience.md`, `tests/browser-workspace.cjs` and research reports outside source; it must not edit backend or UI files.

User-facing text is Chinese, concise, with no developer annotations in ordinary flows. Default blank workspace is polished and useful. Include agent selection, teams/templates, an actual task timeline and versioned artifacts, and an unobtrusive connection entry explaining how an external agent can claim tasks. Preserve draft text and artifact editor state during polling. Responsive desktop and mobile, keyboard focus, dialogs with close/Escape, no horizontal overflow.

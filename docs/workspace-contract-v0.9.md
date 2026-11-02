# Workspace v0.9 — persistent teams and bounded message routing

This is an independent implementation. Authenticated Teamily observations establish a persistent group conversation and an editable Agent member set. They do not establish its private routing algorithm. A saved workflow (`/templates`) remains a versioned DAG, not a team. Existing workflow IDs and conversations are not migrated into teams.

## Team and conversation API

All paths below are relative to `/api/hub`. Existing workspace authentication applies: members may read; owner/editor with `write` may change teams or send messages. Executor registration and token scope do not grant team membership. Human access remains controlled by workspace membership, not the Agent roster.

* `GET /teams` → `{teams:[Team]}` (includes archived teams; UI may filter).
* `POST /teams` with `{name,description?:'',member_ids:[AgentID],routing_mode?:'auto',request_key}` → `201 Team`. Atomically creates the team and its empty primary conversation. Repeated identical key/input by the same actor returns the existing team, without another room.
* `GET /teams/{id}` → `Team`.
* `PUT /teams/{id}` with `{base_version,name?,description?,member_ids?,routing_mode?}` → `Team`, including `cancelled_run_ids` for this update. Omitted fields are retained. A stale version returns 409; a no-op retains the version. An archived team cannot be edited.
* `DELETE /teams/{id}` with `{base_version}` → archived `Team` and `cancelled_run_ids`. Soft archive stops every unfinished team run and prevents new messages/tasks; history and artifacts remain readable.
* `GET /teams/{id}/history?after=0&limit=50` → `{history:[TeamRevision],cursor,has_more}`. `after` is the team version; limit is 1–100. Each immutable revision contains the complete public team definition, `added_member_ids`, `removed_member_ids`, `cancelled_run_ids`, `actor_id`, `actor_name`, and `created_at`.
* `POST /teams/{id}/rooms` with optional `{text,target_agent_ids?,agent_id?,request_key?,base_team_version?}` → `200 Room`. Opens the **existing primary conversation**, never creates another conversation. With `text`, it sends one idempotent user message using the same rules below; without text, it only reads the existing room. Opening repeatedly is safe.

`Team` fields: `{id,name,description,version,member_ids,members,routing_mode,primary_room_id,room_ids,active_run_ids,archived_at,created_by,created_by_name,created_at,updated_at,routing_implementation:'bounded_user_message_v1'}`. `members` contains `{agent_id,name,description}`. `active_run_ids` includes unfinished queued, running, and failed runs; inclusion does not bypass execution review or retry limits. Update/archive responses additionally contain `cancelled_run_ids` (possibly empty). The roster contains 1–24 unique existing non-coordinator Agent IDs. The built-in coordinator is the routing role, not a visible member; arbitrary members cannot delegate recursively. Name is 1–120 chars, description 0–4,000 chars. `routing_mode` must be `'auto'` or `'mention'`; it actively controls unmentioned user-message routing. Request keys are 1–120 ASCII characters from `[A-Za-z0-9_.:/-]`.

Room and bootstrap responses add `team_id` (null for existing ordinary rooms), and full rooms add `team` (current Team or null). Bootstrap adds `teams`. Team membership is shared by its bound conversation; there is no hidden per-room copy. In this release each team has one primary conversation. Creating a team does not enqueue work. A conversation with no runs has `status:'idle'`; this also applies to a note-only conversation with no earlier task.

## Sending and continuing a team conversation

`POST /rooms/{id}/messages` retains `{text,agent_id?:'auto'}` and adds `{target_agent_ids?,request_key?,base_team_version?}`. **For team rooms, request_key is required.** It returns the full Room, with the new message and any actual tasks. `base_team_version`, when supplied, must match the current team version; stale requests return 409 before writing. Same key and canonical input by the same actor returns the same persisted conversation without adding a message or run; changed input under that key returns 409.

Routing is structured, never inferred from a text substring or display name:

* Nonempty `target_agent_ids` (1–12 unique IDs) dispatches one root task per explicitly selected current member. `agent_id` may be omitted or `auto`; combining a non-auto `agent_id` with this list is rejected. Without this list, an explicit non-auto `agent_id` is the single selected target. `coordinator` cannot be explicitly named as a team member.
* No explicit target and `routing_mode='auto'` creates one coordinator task. Its plan may contain only members permitted by the frozen run roster and current roster. This is bounded automatic routing after a user sends a message.
* No explicit target and `routing_mode='mention'` saves a human message only (`run_id=null`). It creates no task and does not claim the Agent answered.
* Changing the switch, adding members, Agent output, a task completion, or a comment does not itself enqueue any reply. Team routing creates no background timer or autonomous conversation loop; separately user-configured scheduled requests keep their existing explicit authority.

User messages expose `routing:{mode:'auto'|'direct'|'note'|'workflow',target_agent_ids,team_id,team_version}`; legacy/Agent messages have `routing:null`. `run` exposes `team_id`, `team_version`, `member_ids` (frozen), and `routing` (frozen). Task context adds `team:{id,name,version,run_version,member_ids,allowed_agent_ids,routing_mode,routing_policy}`. Its `agents` catalog is limited to eligible team members; the task's own `agent` still identifies coordinator when applicable. This bounded roster is essential context and is never silently omitted. Existing source/skill/context omission rules remain.

The lower-level run constructor also applies team constraints to scheduled runs and artifact modification requests. A workflow in a team room must use only team members. Explicit execution requests from these existing features remain explicit requests even if ordinary unmentioned chat is set to note-only; they do not become autonomous replies. An explicit artifact modification may return to its original internal coordinator if that coordinator produced the baseline; the existing modification contract still forbids delegating a plan or writing a different artifact. This does not add coordinator to the visible roster or permit explicitly mentioning it in chat.

## Membership changes and lease safety

Each run freezes the team ID, definition version, and member IDs at submission. Server-side plan validation checks **frozen members ∩ current members**, not merely whether an Agent exists globally. Adding a member cannot expand an already-submitted run. Team metadata or routing-switch changes do not cancel existing runs.

Removing any member atomically cancels **every unfinished run whose frozen roster included that member**, including runs that currently have only a coordinator task or a different member's task. This deliberately conservative rule prevents stale planning and avoids stranded dependent tasks. Failed runs are included because they could otherwise be retried. The complete affected run is stopped; completed task results and artifacts remain intact. The response and immutable revision report the cancelled run IDs. There is no silent reassignment or automatic replacement run.

The update and task completion both use the SQLite write transaction: if completion commits first, its saved output remains; after the roster update commits, late heartbeat, event append, staged-result write/read, failure, completion and retry cannot revive cancelled work. Re-adding the member does not restore an old lease or run. The user sends a fresh message to continue with the new roster. API reads never reveal lease/session credentials. The public run's persistent `cancel_reason` is `team_members_changed`, `team_archived`, or null for an ordinary cancellation; execution cancellation receipts carry the same team reason.

Archive has the same stop-and-preserve semantics for all unfinished team runs. An already completed run is not cancelled. Unacknowledged external side effects remain external: cancellation cannot undo an operation a host already performed; execution history remains available for audit. This release does not reproduce Teamily's unobserved private scheduler or introduce exactly-once external actions.

## MCP naming migration

Both local stdio (`studio.mcp_server`) and authenticated remote stdio (`studio.remote_mcp`) expose `workspace_create_team(name,member_ids,request_key,description?,routing_mode?)` and `workspace_update_team(team_id,base_version,member_ids?,name?,description?,routing_mode?)` with the real team semantics above. The former incorrectly named steps-based tools are now `workspace_create_workflow(name,description,steps)` and `workspace_update_workflow(template_id,base_version,name,description,steps)`. Passing `steps` or a workflow `template_id` to the team tools is an explicit migration error; it never silently creates a team from a DAG. Clients must refresh their tool list/schema after upgrading.

`workspace_add_message` accepts `request_key`, `target_agent_ids`, and `base_team_version` in addition to the original fields. `workspace_claim_task` accepts truthful `capabilities` and `host_identity` for v0.8 recovery. `workspace_read` / `workspace_write` expose the full JSON API, including team history and execution events; HTTP validation failures produce MCP tool errors. Local stdio retains host filesystem authority. Remote stdio always uses the configured workspace-scoped token and server-side permissions.

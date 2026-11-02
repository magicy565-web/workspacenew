# Agent Workspace v0.5 — skills and scheduled work

Paths below are relative to `/api/hub`. Existing account/workspace middleware applies. Skills contain host-side guidance; saving or binding one does not install commands, execute scripts, or grant more host permissions.

## Skills

- `GET /skills` -> `{skills:[SkillSummary]}` (includes archived records, so clients can offer an archive filter).
- `POST /skills` `{name,description,content,source_url?}` -> Skill (201). `content` is SKILL.md text, at most 256 KiB, retained exactly.
- `GET /skills/{id}?version=N` -> Skill; omitted version selects latest.
- `PUT /skills/{id}` `{base_version,name,description,content,source_url?}` -> Skill. A stale version returns 409; archived skills cannot be edited.
- `DELETE /skills/{id}` `{base_version}` -> Skill. Soft archive, idempotent for the same version; history remains readable.
- Summary: `{id,name,description,source_url,version,sha256,created_by,workspace_id,created_at,updated_at,archived_at}`. Full Skill adds `content` and `versions:[{version,sha256,created_at,created_by}]`.
- `GET /agents/{id}/skills` -> `{agent_id,version,skills:[SkillSummary]}`. A new binding has version 0.
- `PUT /agents/{id}/skills` `{base_version,skill_ids:[id]}` -> same shape. Atomic replacement, at most 12 unique active skills; a stale binding returns 409. Archiving preserves the binding record but prevents the archived skill entering subsequent claims.
- Task claim context adds `skills:[Skill]`, skill references and `skill_policy`. Skills share the existing 256 KiB context limit. Excluded content is explicitly reported in `omissions`; archived bindings are reported with reason `skill_archived`. References pin exact skill versions and hashes.

## Scheduled work

- `GET /schedules` -> `{schedules:[Schedule]}` (includes archived records).
- `POST /schedules` `{name,text,agent_id?:'auto'|id,template_id?:id,interval_seconds,next_run_at?,room_id?}` -> Schedule (201).
- `GET /schedules/{id}` -> Schedule, including `history` of the latest 30 attempts.
- `PUT /schedules/{id}` `{base_version,name,text,agent_id?,template_id?,interval_seconds,next_run_at?,room_id?}` -> Schedule. Optimistic config edit; creator does not change. Omitted `room_id` retains the existing room. Once a schedule has fired, its room cannot be changed. Omitted `next_run_at` retains the saved next occurrence.
- `POST /schedules/{id}/pause` `{paused:true|false}` -> Schedule. Resume revalidates creator permission; pausing does not cancel or delete existing runs.
- `POST /schedules/{id}/run-now` `{}` -> `{schedule,run_id,room_id,skipped_reason}`. Manually starts one run even while paused; if the previous run is still queued/running, returns null `run_id` and `skipped_reason:'previous_run_active'`. Does not change cadence.
- `DELETE /schedules/{id}` `{}` -> Schedule. Soft archive and pause; no historical task or artifact is deleted.
- Schedule: `{id,name,text,agent_id,template_id,interval_seconds,next_run_at,room_id,owner_user_id,owner_workspace_id,version,enabled,created_at,updated_at,archived_at,last_run_id,last_fired_at,last_error,pause_reason,last_run_status,run_count}`. GET single adds `history:[{id,scheduled_for,trigger,status,run_id,error,created_at}]`; status is `created`, `skipped`, or `failed`.
- Interval is an integer between 60 and 31,536,000 seconds. `next_run_at` is timezone-aware ISO 8601 (UTC or an explicit offset). Default is now + interval. A due schedule reuses its selected/generated room and creates a new run each time.
- Automatic ticks serialize in SQLite. Missed intervals create at most one catch-up run and advance the next occurrence strictly into the future. If the previous run remains queued/running, the occurrence is recorded as skipped and cadence advances; work never piles up solely because a worker is offline.
- Every start checks that the recorded creator still has editor/owner membership in this exact workspace. Revocation/downgrade pauses the schedule. A schedule created in local mode must be recreated by a signed-in member after accounts are enabled. Authenticated-mode schedule creation requires a recorded actor. Paused/archived plans retain all history.
- Scheduling queues real work; completion still requires an external worker. `last_run_status` and task errors describe the real last run.

## Core and scheduler signatures

`hub_extensions.list_skills()`, `get_skill(skill_id,version=None)`, `create_skill(name,description,content,source_url=None)`, `update_skill(skill_id,base_version,name,description,content,source_url=None)`, `archive_skill(skill_id,base_version)`, `get_agent_skills(agent_id)`, `set_agent_skills(agent_id,base_version,skill_ids)`.

`hub_extensions.list_schedules()`, `get_schedule(schedule_id)`, `create_schedule(name,text,interval_seconds,agent_id='auto',template_id=None,next_run_at=None,room_id=None)`, `update_schedule(schedule_id,base_version,name,text,interval_seconds,agent_id='auto',template_id=None,next_run_at=None,room_id=None)`, `pause_schedule(schedule_id,paused=True)`, `run_schedule_now(schedule_id)`, `archive_schedule(schedule_id)`.

`hub_extensions.tick_due(now_iso=None)` processes the currently bound workspace. `scheduler.tick_all(now_iso=None)` visits the base data directory and all distinct registered workspace directories, restores ContextVars, and returns `{workspaces:[{workspace_id,created,skipped,paused}],errors:[{workspace_id,error}]}`. Root server calls this periodically from its lifecycle-owned background thread.

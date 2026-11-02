# Agent Workspace v0.4 — shared implementation contract

This extends v0.3 without changing existing routes. All paths below are relative to `/api/hub`. This remains a local, single-user service. The caller supplies model execution. HTTP bodies and responses are JSON unless already documented as artifact downloads.

## Editable teams / workflows

- `GET /templates` -> `{templates: [Template]}`.
- `POST /templates` `{name, description, steps}` -> Template (201).
- `GET /templates/{id}` -> Template.
- `PUT /templates/{id}` `{base_version, name, description, steps}` -> Template. A stale `base_version` returns 409.
- Template: `{id,name,description,version,builtin,steps:[{key,agent_id,title,instructions,depends_on:[key]}],agent_ids:[id],created_at,updated_at}`.
- Require 1–12 steps, unique keys, known agents, acyclic dependencies. A saved workflow step cannot delegate again to `coordinator`; choose actual collaborators. Built-ins `research`, `product`, `magazine` are persisted and editable. New templates receive generated IDs.
- `POST /rooms` retains `template_id`; it instantiates the saved graph and appends the user's request to each step's saved instructions. Run gains `template_id` and `template_version` (null for direct/auto requests). Later template edits do not change existing tasks.
- `bootstrap.templates` now uses these saved Template objects.

## Room sources

- `POST /rooms/{id}/sources` `{title,kind:'text'|'markdown'|'url',content,source_url?}` -> Source (201).
- `GET /rooms/{id}/sources` -> `{sources:[Source],revoked_count}` (active sources only).
- `GET /sources/{id}` -> Source, including a revoked record for provenance/audit. Revocation removes its use in new task contexts; it is not secure erasure.
- `DELETE /sources/{id}` -> `{source:Source}`. Soft revocation is idempotent.
- Source: `{id,room_id,title,kind,content,source_url,sha256,version:1,created_by:'local_user',origin:'submitted',retrieval_status:'provided'|'not_fetched',created_at,revoked_at}`. Each submitted source is immutable; submit a new record for a revision. For `url`, `content` is an HTTP(S) URL; it is recorded only, never fetched by this service. `source_url` may attribute provided text/Markdown.
- Room gains `sources` as active source metadata (without content) and `revoked_source_count`. Each source is limited to 1 MiB; at most 100 active sources per room.
- Claim context gains readable `sources`, latest readable `artifact_contents` (including each version's author and review metadata), `references`, `omissions`, and `context_limit_bytes`. It has a 256 KiB encoded JSON ceiling. Oversized/older items are explicitly described in `omissions`; references retain IDs/version/size and retrieval routes. Current agent/task instructions stay intact. Revoked sources are excluded from later claims; already claimed contexts cannot be recalled.

## Artifact editing and review

- `PUT /artifacts/{id}` `{base_version,title,kind,content}` -> full Artifact. Saves an atomic new version, `author_type:'human'`. A conflict returns 409; the submitted text is not overwritten.
- `POST /artifacts/{id}/reviews` `{version,decision:'approved'|'changes_requested',comment}` -> full Artifact at that reviewed version.
- Artifact `versions` adds `author_type`; full Artifact adds `author_type` and `reviews:[{id,artifact_id,version,decision,comment,author_type:'human',created_at}]`. Reviews are append-only and attached to their exact version. Approving v1 does not approve v2.
- Existing agent completion creates versions with `author_type:'agent'`. Manual edits retain the original task association for provenance; `author_type` identifies that the new edit was human-authored.

## Registered workers

- `POST /workers/register` `{worker_id,name?,agent_ids,capabilities?}` -> `{worker,session_token,heartbeat_seconds:30}`.
- `POST /workers/{id}/ping` `{session_token}` -> `{worker,heartbeat_seconds:30}`.
- `POST /workers/{id}/disconnect` `{session_token}` -> `{worker}`.
- `GET /workers` -> `{workers:[Worker]}`.
- Worker: `{id,worker_id,name,agent_ids,capabilities,connected,registered_at,last_seen_at,expires_at,disconnected_at}`. Capabilities is a list of up to 32 strings. Public reads never contain credentials.
- Registered `worker_id` uses 1–120 ASCII letters, digits, `_`, `-`, `.`, or `:` and begins with a letter or digit. Human-facing names may use Chinese.
- Registration lives 90 seconds; ping every 30 seconds. Registering an already live `worker_id` returns 409, even from the same process; keep the returned token and ping. An expired/offline registration can be replaced; its old token is invalid.
- Claim body accepts `session_token`; registered IDs must authenticate a live registration. Its agent filter must be a subset of registered `agent_ids`; omitting the filter uses the registered list. Unregistered MCP callers remain compatible without a registration. Unknown registration tokens are rejected.
- Worker ping and task heartbeat are independent: neither renews the other. Existing task leases remain authoritative for completion. Disconnect does not silently discard in-progress work; explicit run cancellation invalidates it.
- `bootstrap.workers` is the public worker list; `connection.connected_workers` counts live registrations, while existing `active_workers` counts valid task owners.

## Core signatures

`list_templates()`, `get_template(template_id)`, `create_template(name,description,steps)`, `update_template(template_id,base_version,name,description,steps)`.

`add_source(room_id,title,kind,content,source_url=None)`, `list_sources(room_id)`, `get_source(source_id)`, `revoke_source(source_id)`.

`edit_artifact(artifact_id,base_version,title,kind,content)`, `review_artifact(artifact_id,version,decision,comment='')`.

`register_worker(worker_id,agent_ids,name=None,capabilities=None)`, `ping_worker(worker_id,session_token)`, `disconnect_worker(worker_id,session_token)`, `list_workers()`.

`claim_task(worker_id,agent_ids=None,room_id=None,session_token=None)` appends one optional argument; all previous signatures remain supported.

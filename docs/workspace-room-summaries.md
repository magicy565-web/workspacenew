# Real conversation summaries (compatible v0.9 extension)

`GET /api/hub/bootstrap` returns real conversation summaries in `rooms`. Existing `id`, `title`, `team_id`, `created_at`, `updated_at`, `status` and `last_message` retain their meanings. Full `GET /api/hub/rooms/{id}` includes the same new summary fields.

```json
{
  "last_message": "The first 160 characters of the latest actual message",
  "last_message_at": "2026-11-03T00:00:00.000000+00:00",
  "last_speaker": {
    "role": "agent",
    "agent_id": "writer",
    "author_id": null,
    "name": "内容编辑"
  },
  "team_members_preview": [
    {"agent_id": "writer", "name": "内容编辑"}
  ],
  "team_member_count": 1
}
```

* `last_message` is the persisted final `hub_messages` record's text prefix, at most 160 Unicode characters. It is plain text, not HTML. The complete message remains in the conversation. Messages are selected by append order, consistently with the previous implementation; sorting timestamps alone must not select an older append when times tie.
* `last_message_at` is that exact message's `created_at`, or null when no message exists. `updated_at` can also reflect task progress, source upload or team changes and **must not be labelled as the last speaking time**. Empty conversations have `last_message:""`, `last_message_at:null`, and `last_speaker:null`; creating a team does not create an invented Agent greeting.
* `last_speaker` is null or `{role:'user'|'agent',agent_id:string|null,author_id:string|null,name:string}`. Human names come from the name saved with that message; Agent names come from the existing Agent record referenced by its ID. Old records without a name use only the generic label `用户` or `Agent`, not an invented person. Display names are bounded to 120 characters. These fields are author identity, not connection/online presence.
* `team_members_preview` contains at most four **current persisted** roster members in roster order, each `{agent_id,name}`. `team_member_count` is the full current roster count. Non-team rooms return `[]` and `0`. These are Agent members, not human workspace members; no image URL or fabricated avatar/person is returned. UI may derive an initial from an actual name.
* The existing bootstrap order (`updated_at` descending, append order as a tie breaker) is retained for compatibility. A conversation UI can sort using `last_message_at ?? created_at`, but an empty conversation's creation time must not be represented as an Agent reply time.

The summary reader uses the already workspace-bound SQLite connection. Latest messages and run states are retrieved in two set-based queries, reusing bootstrap's existing Agent-name catalog. A standalone internal summary call adds one bounded-field Agent-name query; query count does not grow with room/run/member count. Run status preserves the prior priorities, including expired leases and the earliest queued/running run taking precedence over later terminal runs. This change does not redesign the separate full team catalog.

Only actual public conversation message fields and current roster IDs/names are used for previews. The reader never selects task result bodies, execution events, staged results, source contents/URLs, lease tokens or worker session credentials. Private execution or source records are not converted into last-message text. This is workspace-scoped UI data, not an unauthenticated public feed; existing account authorization applies.

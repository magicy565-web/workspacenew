# Workspace UI direction

This pass applies the `frontend-design` skill from anthropics/skills at
`ca45fc496a3436251f191354091d557da31c15b5`. The target is the existing, working
multi-agent workspace: an ongoing team conversation beside its actual output.
Authenticated Teamily observations inform the interaction model; private source,
branding and account content are not implementation assets.

## Design plan

The primary job is to send a request to a persistent team, follow actual work,
and review or revise the resulting document without losing the conversation.
The product is a working application, so its content, members and states come
from the API rather than decorative examples.

| Token | Value | Purpose |
| --- | --- | --- |
| `--ui-canvas` | `#F7F9F8` | Quiet workspace backdrop |
| `--ui-surface` | `#FFFFFF` | Reading and editing surfaces |
| `--ui-ink` | `#1F2933` | Main text |
| `--ui-muted` | `#5F6C66` | Supporting text |
| `--ui-line` | `#DFE5E1` | Meaningful grouping boundaries |
| `--ui-accent` | `#087C49` | Primary action and current selection |

Use `Segoe UI Variable`, `Segoe UI`, `PingFang SC`, `Microsoft YaHei`, sans-serif.
The Windows preview uses the installed Segoe UI/Chinese fallback rather than
assuming a remote Inter font exists. Body text is 15px with approximately 1.65
line height; controls 14px; supporting text at least 12px. Reading columns stay
below about 80 characters. Headlines are 28–32px, not marketing billboards.
Danger and warning have separate semantic colors and textual status labels.

```
First use:                          Working conversation:
[rail][search / create][Agent name]  [rail][search / create][team identity]
[text][real Agents   ][description]  [text][avatar / name  ][real conversation]
      [click to start][team action]        [last speaker   ][artifact / Studio]
                     [composer   ]        [message / time ][status / composer]
```

Desktop navigation is a 72px application rail with short, visible labels and an
approximately 300px conversation pane. Search and creation belong to that pane.
Before any history exists, it lists actual registered Agents under a distinct
start-conversation label. Selecting one chooses a target; it must not fabricate
a conversation or an Agent greeting. Once messages exist, the pane shows real
member identity, last speaker, message preview and message time. General room
activity timestamps must not masquerade as the last message time.

The main area keeps the conversation context at the top and the composer at the
bottom. On first use, show the chosen Agent's real name and description with a
direct team-creation action. An empty team shows its actual creation context and
members, not an invented assistant response. Explicit room URLs, explicit new
conversation actions and user navigation take precedence over default recovery
of the latest real room. The Studio opens when needed.

Use a light, rounded white workspace frame and a distinct conversation header
and input surface, following the inspected reference's spatial hierarchy. A very
subtle mint tint at the bottom may separate the chat canvas from its controls.
Libraries and document content align left. On phones the active conversation
fills the screen; navigation has visible names, and the keyboard/composer must
not obscure the current state or primary action.

## Review before implementation

The generic centered greeting and three equal workflow cards do not reveal the
product's persistent collaboration model. Enlarging their text does not fix that
structure. The distinctive part must be recognizable collaborators, continuing
conversations, a compact current task state, and a document surface that supports
actual comments and versions. Workflow templates remain available in their own
directory. They must not substitute for the team or conversation entry point.

Keep the reference's purposeful separation of the rail, conversation list,
conversation canvas and Studio. Avoid unexplained icon-only navigation, empty
context panes, promotional panels and controls for capabilities not implemented.
Do not manufacture unread badges, online members, chat previews or completed
work to make the first screen look populated.

The Studio header must not push content below six stacked toolbars. Keep
version, edit, comments and review discoverable in compact rows. Empty, loading,
offline, failed and conflicting states must each offer their real next action.
Never fill empty space with invented output or simulated activity.

## Verification boundaries

Use the actual served application at desktop and 390px widths, including
keyboard focus and reduced motion. First inspect the empty main workspace as
well as a workspace with real conversations; a full test fixture can conceal an
unhelpful first-use screen. Exercise team creation, continued chat,
roster changes, execution, artifact versions and comment-driven revision.
The Agent selector for a team artifact must reflect current membership; stale
selection removal preserves the user's instructions and requires a deliberate
replacement. The internal coordinator is allowed only for its own baseline.

API tests establish state transitions. Screenshots establish layout. A real
caller-model run establishes that an executor produced the output. These are
separate kinds of evidence; none alone proves parity with Teamily.

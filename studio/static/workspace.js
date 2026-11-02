"use strict";

const $ = (id) => document.getElementById(id);
const state = { conversationSearch: "", contactsExpanded: null, agents: [], rooms: [], templates: [], teams: [], connection: {}, room: null, roomId: null, view: "chat", panel: "activity", panelOpen: false, selectedTemplate: null, selectedArtifact: null, selectedVersion: null, artifact: null, drafts: new Map(), contextDirty: false, submitting: false, loading: false, roomGeneration: 0, roomReadGeneration: 0, navigationGeneration: 0, roomSignature: "", messageSignature: "", bootstrapSignature: "", artifactGeneration: 0, editingAgent: null, agentSearch: "", agentFilter: "all", teamSearch: "", teamFilter: "all", studioExpanded: false, workers: [], teamDraft: null, teamStep: null, artifactDrafts: new Map(), artifactEditing: false, reviewDecision: null, localExecutor: null, executorBusy: false, connectionInfo: null, connectTab: "instructions", toastTimer: null };
const paths = {
  more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  agent: '<rect x="4" y="7" width="16" height="13" rx="4"/><path d="M12 3v4M2 12v4m20-4v4M8 12v2m8-2v2m-7 3h6"/>',
  expand: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M3 3l6 6m12-6-6 6M3 21l6-6m15 6-6-6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>', close: '<path d="m6 6 12 12M18 6 6 18"/>',
  chat: '<path d="M20 11.5a7.5 7.5 0 0 1-7.5 7.5H5l-3 3V11.5A7.5 7.5 0 0 1 9.5 4h3a7.5 7.5 0 0 1 7.5 7.5Z"/>',
  agents: '<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M16 5a3 3 0 0 1 0 6M18 15a5 5 0 0 1 3 5"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
  book: '<path d="M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Zm0 0v15"/>',
  plug: '<path d="m8 3 4 4m2-6 4 4M5 8l11-3 3 3-3 11-3 2-10-10 2-3Zm3 8-5 5"/>',
  panel: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M5.5 8h1M5.5 12h1"/>',
  "panel-right": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
  route: '<circle cx="5" cy="5" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="12" cy="19" r="2"/><path d="M5 7v2c0 2 2 3 4 3h6c2 0 4-1 4-3V7m-7 5v5"/>',
  "arrow-up": '<path d="M12 19V5m-6 6 6-6 6 6"/>',
  arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4"/>',
  copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  pen: '<path d="m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-5-5L4 14v6Z"/>',
  design: '<path d="m12 3 9 5v8l-9 5-9-5V8l9-5Zm0 0v18M3 8l9 5 9-5"/>',
  code: '<path d="m8 6-6 6 6 6m8-12 6 6-6 6M14 3l-4 18"/>',
  shield: '<path d="m12 3 9 4v5c0 5-5 8-9 10-4-2-9-5-9-10V7l9-4Z"/><path d="m8 12 3 3 5-6"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  file: '<path d="M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 12h8M8 16h6"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5M5 7a8 8 0 0 1 13-3l2 3M4 17l2 3a8 8 0 0 0 13-3"/>',
  alert: '<path d="m12 3 10 18H2L12 3Zm0 6v5m0 3v1"/>',
  spark: '<path d="m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3 3-7Z"/>'
};
function icon(name) { return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.spark}</svg>`; }
document.querySelectorAll("[data-icon]").forEach((el) => { el.innerHTML = icon(el.dataset.icon); });
const e = (text) => String(text ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const agentStyles = { coordinator: ["route", "#E3F0E8", "#16764D"], researcher: ["search", "#E6EDF6", "#456DA1"], writer: ["pen", "#F6EDDD", "#9D743A"], designer: ["design", "#EFE8F6", "#8866A5"], developer: ["code", "#E1EEEB", "#397C6B"], reviewer: ["shield", "#F4E5E5", "#A45F63"], conversation: ["chat", "#EDF1EF", "#6C7B73"] };
function agent(id) { return state.agents.find((item) => item.id === id) || { id, name: id || "Agent" }; }
function avatarStyle(id) { if (agentStyles[id]) return agentStyles[id]; const palette = Object.values(agentStyles); const hash = [...String(id)].reduce((sum, char) => sum + char.charCodeAt(0), 0); return ["agent", ...palette[hash % 6].slice(1)]; }
function avatar(id, cls = "agent-avatar") { const [symbol, bg, color] = avatarStyle(id); return `<span class="${cls}" style="--avatar-bg:${bg};--avatar-color:${color}" aria-hidden="true">${icon(symbol)}</span>`; }
function tinyAvatars(ids) { return `<span class="tiny-avatars">${[...new Set(ids)].slice(0, 5).map((id) => { const [, bg, color] = agentStyles[id] || ["spark", "#e9eee1", "#85946f"]; return `<span class="tiny-avatar" style="--avatar-bg:${bg};--avatar-color:${color}" title="${e(agent(id).name)}">${e(agent(id).name.slice(0, 1))}</span>`; }).join("")}</span>`; }
function toast(message, error = false) { clearTimeout(state.toastTimer); $("toast").textContent = message; $("toast").classList.toggle("error", error); $("toast").hidden = false; state.toastTimer = setTimeout(() => { $("toast").hidden = true; }, error ? 6500 : 3300); }
async function api(path, { method = "GET", body } = {}) { const response = await fetch(`/api/hub${path}`, { method, headers: body === undefined ? {} : { "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) }); let data; try { data = await response.json(); } catch { throw new Error(`服务返回了无效响应（${response.status}）`); } if (!response.ok) { const error = new Error(data.error || `请求失败（${response.status}）`); error.status = response.status; if (response.status === 401) window.dispatchEvent(new Event("workspace-session-expired")); throw error; } return data; }
function safeInline(text) { let result = e(text); result = result.replace(/`([^`\n]+)`/g, "<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>"); result = result.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s<]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'); return result; }
function markdown(text) { const lines = String(text || "").replace(/\r/g, "").split("\n"); let html = "", paragraph = [], list = "", code = null; const flush = () => { if (paragraph.length) { html += `<p>${paragraph.map(safeInline).join("\n")}</p>`; paragraph = []; } if (list) { html += `</${list}>`; list = ""; } }; for (const line of lines) { if (line.startsWith("```")) { if (code !== null) { html += `<pre><code>${e(code.join("\n"))}</code></pre>`; code = null; } else { flush(); code = []; } continue; } if (code !== null) { code.push(line); continue; } const heading = /^(#{1,3})\s+(.+)$/.exec(line); const bullet = /^\s*[-*]\s+(.+)$/.exec(line); const numbered = /^\s*\d+\.\s+(.+)$/.exec(line); if (heading) { flush(); html += `<h${heading[1].length}>${safeInline(heading[2])}</h${heading[1].length}>`; } else if (bullet || numbered) { const kind = bullet ? "ul" : "ol"; if (list !== kind) { flush(); html += `<${kind}>`; list = kind; } html += `<li>${safeInline((bullet || numbered)[1])}</li>`; } else if (line.startsWith("> ")) { flush(); html += `<blockquote>${safeInline(line.slice(2))}</blockquote>`; } else if (!line.trim()) { flush(); } else { if (list) flush(); paragraph.push(line); } } if (code !== null) html += `<pre><code>${e(code.join("\n"))}</code></pre>`; flush(); return html; }
function formatTime(value) { if (!value) return ""; const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }); }
function draftKey() { return state.roomId || "new"; }
function saveDraft() { state.drafts.set(draftKey(), { text: $("composer-input").value, agent: $("composer-agent").value }); }
function restoreDraft() { const draft = state.drafts.get(draftKey()); $("composer-input").value = draft?.text || ""; $("composer-agent").value = draft?.agent || "auto"; resizeInput(); }
function resizeInput() { const input = $("composer-input"); input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 180)}px`; $("send-button").disabled = state.submitting || !input.value.trim(); }
function setSidebar(open, restoreFocus = false) { $("sidebar").classList.toggle("open", open); $("sidebar-shade").hidden = !open; $("open-sidebar").setAttribute("aria-expanded", String(open)); if (open) (document.documentElement.dataset.access === "viewer" ? $("sidebar").querySelector(".brand") : $("new-chat")).focus(); else if (restoreFocus) $("open-sidebar").focus(); }
function setView(view, { user = false } = {}) { if (user) state.navigationGeneration++; state.view = view; $("workspace-shell").classList.toggle("library-mode", view !== "chat"); for (const name of ["chat", "agents", "teams", "workflows", "skills", "schedules"]) { const section = $(name + "-view"); if (section) section.hidden = name !== view; } document.querySelectorAll("[data-view]").forEach((node) => { node.classList.toggle("active", node.dataset.view === view); }); $("view-title").textContent = view === "chat" ? state.room?.title || "新对话" : ({ agents: "我的 Agent", teams: "协作团队", workflows: "工作流", skills: "技能库", schedules: "周期任务" })[view] || "工作空间"; $("workspace-toggle").hidden = view !== "chat" || !state.room; $("room-state").hidden = view !== "chat" || !state.room; applyPanel(); setSidebar(false); setMore(false); $("open-more").classList.toggle("active", ["skills", "schedules"].includes(view)); renderIdentity(); window.dispatchEvent(new Event("workspace-view")); }
function renderSidebar() {
  const query = state.conversationSearch.trim().toLocaleLowerCase();
  const selected = $("composer-agent").value || "auto";
  const signature = JSON.stringify([state.rooms, state.agents, state.teams, query, state.roomId, selected, state.contactsExpanded]);
  if ($("sidebar").dataset.listSignature === signature) return;
  $("sidebar").dataset.listSignature = signature;
  const focused = document.activeElement;
  const focusedRoom = focused?.dataset.room, focusedContact = focused?.dataset.selectContact;
  const rooms = state.rooms.filter((room) => !query || `${room.title} ${room.last_message || ""} ${room.last_speaker?.name || ""}`.toLocaleLowerCase().includes(query));
  $("room-count").textContent = state.rooms.length || "";
  $("recent-conversations").hidden = !state.rooms.length;
  $("room-list").innerHTML = rooms.map((room) => {
    const members = room.team_members_preview || state.teams.find((team) => team.id === room.team_id)?.members || [];
    const face = room.team_id ? `<span class="conversation-avatar group-conversation-avatar">${members.slice(0, 4).map((member) => avatar(member.agent_id, "contact-avatar")).join("")}</span>` : avatar(room.last_speaker?.agent_id || "conversation", "conversation-avatar");
    const stamp = conversationTime(room.last_message_at);
    const speaker = room.last_speaker?.name;
    const preview = room.last_message ? `${speaker ? speaker + "：" : ""}${room.last_message}` : room.team_id ? `${room.team_member_count || members.length} 位 Agent · 还没有消息` : "还没有消息";
    const status = { queued: "等待接手", running: "执行中", failed: "需要处理" }[room.status];
    return `<button class="room-item${room.id === state.roomId ? " active" : ""}" data-room="${e(room.id)}" aria-current="${room.id === state.roomId ? "true" : "false"}" title="${e(room.title)}">${face}<span class="conversation-row-content"><span class="conversation-row-title"><strong>${e(room.title)}</strong>${stamp ? `<time datetime="${e(room.last_message_at)}">${e(stamp)}</time>` : ""}</span><span class="conversation-row-preview">${status ? `<span class="conversation-row-status ${e(room.status)}">${status}</span>` : ""}${e(preview)}</span></span></button>`;
  }).join("") || (query && state.rooms.length ? '<p class="context-empty">没有匹配的对话</p>' : "");
  const expanded = !!query || (state.contactsExpanded === null ? !state.rooms.length : state.contactsExpanded);
  $("toggle-contacts").setAttribute("aria-expanded", String(expanded));
  $("contact-list").hidden = !expanded;
  const contacts = state.agents.filter((item) => !query || `${item.name} ${item.description}`.toLocaleLowerCase().includes(query));
  $("contact-list").innerHTML = contacts.map((item) => {
    const active = !state.roomId && (selected === item.id || selected === "auto" && item.id === "coordinator");
    return `<button class="contact-item${active ? " active" : ""}" data-select-contact="${e(item.id)}" aria-label="选择对话 Agent：${e(item.name)}" aria-pressed="${active}">${avatar(item.id, "contact-avatar")}<span><strong>${e(item.name)}</strong><small>${e(item.description)}</small></span>${active ? `<span class="contact-selected">${icon("check")}</span>` : ""}</button>`;
  }).join("") || '<p class="context-empty">没有匹配的 Agent</p>';
  if (focusedRoom || focusedContact) [...$("sidebar").querySelectorAll("[data-room], [data-select-contact]")].find((item) => focusedRoom ? item.dataset.room === focusedRoom : item.dataset.selectContact === focusedContact)?.focus({ preventScroll: true });
}
function conversationTime(value) {
  if (!value) return "";
  const date = new Date(value), now = new Date();
  if (Number.isNaN(date.getTime())) return "";
  return date.toDateString() === now.toDateString() ? formatTime(value) : date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}
function renderIdentity() {
  const room = state.room, chatting = state.view === "chat", group = !!room?.team_id;
  const routedId = $("composer-agent").value === "auto" ? "coordinator" : $("composer-agent").value || "coordinator";
  const lastAgentMessage = room?.messages?.findLast((message) => message.role === "agent");
  const roomAgentIds = [...new Set((room?.messages || []).filter((message) => message.role === "agent").map((message) => message.agent_id))];
  const id = room ? lastAgentMessage?.agent_id || room.runs?.at(-1)?.tasks?.[0]?.agent_id || "coordinator" : routedId;
  const selected = agent(id), template = state.templates.find((item) => item.id === state.selectedTemplate);
  $("chat-identity").hidden = !chatting || group;
  $("view-title").hidden = chatting;
  document.querySelector(".topbar-actions .create-chat-team").hidden = !chatting || group;
  if (!group && chatting) {
    const subtitle = room ? roomAgentIds.length > 1 ? `${roomAgentIds.length} 位 Agent 参与` : lastAgentMessage ? selected.name : "对话" : template ? "工作流" : $("composer-agent").value === "auto" ? "自动分工" : "Agent";
    const signature = JSON.stringify([room?.id, room?.title, selected.id, selected.name, template?.name, subtitle]);
    if ($("chat-identity").dataset.signature !== signature) {
      $("chat-identity").dataset.signature = signature;
      $("chat-identity").innerHTML = `${avatar(id, "header-agent-avatar")}<span><strong>${e(room?.title || template?.name || selected.name)}</strong><small>${e(subtitle)}</small></span>`;
    }
  }
  if (!room) {
    const readonly = document.documentElement.dataset.access === "viewer";
    $("intro-avatar").innerHTML = readonly ? icon("chat") : avatar(id, "intro-agent-avatar");
    $("intro-name").textContent = readonly ? "这个空间还没有对话" : template?.name || selected.name;
    $("intro-description").textContent = readonly ? "你有查看权限，其他成员创建的对话会显示在这里。" : template?.description || selected.description || "";
  }
}
function setMore(open, restoreFocus = false) {
  $("more-menu").hidden = !open;
  $("open-more").setAttribute("aria-expanded", String(open));
  if (open) $("more-menu").querySelector("button, a")?.focus();
  else if (restoreFocus) $("open-more").focus();
}
function teamIcon(index) { return ["search", "pen", "code", "design"][index % 4]; }
function renderLibrary() {
  const selection = $("composer-agent").value;
  $("composer-agent").innerHTML = '<option value="auto">自动分配</option>' + state.agents.map((item) => `<option value="${e(item.id)}">${e(item.name)}</option>`).join("");
  $("composer-agent").value = state.agents.some((item) => item.id === selection) ? selection : "auto";
  renderAgentCards(); renderTeamCards();
  renderIdentity(); renderSidebar();
}
function renderAgentCards() {
  const search = state.agentSearch.toLocaleLowerCase();
  const items = state.agents.filter((item) => (!search || `${item.name} ${item.description}`.toLocaleLowerCase().includes(search)) && (state.agentFilter === "all" || (state.agentFilter === "builtin" ? item.builtin : !item.builtin)));
  $("agents-result-count").textContent = `${items.length} 个 Agent`;
  $("agent-grid").innerHTML = items.map((item) => `<article class="agent-card">${avatar(item.id)}<div class="agent-card-info"><h2>${e(item.name)}</h2><p>${e(item.description)}</p></div><div class="agent-card-actions"><button class="agent-chat-button" data-start-agent="${e(item.id)}">对话</button><button class="icon-button agent-edit" data-edit-agent="${e(item.id)}" aria-label="编辑 ${e(item.name)}">${icon("pen")}</button></div></article>`).join("") || '<p class="empty-library">没有找到匹配的 Agent</p>';
}
function renderTeamCards() {
  const search = state.teamSearch.toLocaleLowerCase();
  const items = state.templates.filter((item) => (!search || `${item.name} ${item.description}`.toLocaleLowerCase().includes(search)) && (state.teamFilter === "all" || (state.teamFilter === "builtin" ? item.builtin !== false : item.builtin === false)));
  $("teams-result-count").textContent = `${items.length} 个工作流`;
  $("team-grid").innerHTML = items.map((team) => `<article class="team-card"><div class="team-card-heading"><span class="team-category">${team.builtin === false ? "我的工作流" : "内置工作流"}</span><button class="icon-button" data-edit-team="${e(team.id)}" aria-label="编辑工作流：${e(team.name)}">${icon("pen")}</button></div><h2>${e(team.name)}</h2><p>${e(team.description)}</p><div class="team-members">${(team.agent_ids || []).map((id) => `<span class="team-member">${e(agent(id).name)}</span>`).join("")}</div><div class="team-card-footer">${tinyAvatars(team.agent_ids || [])}<button class="secondary-button" data-template="${e(team.id)}" aria-label="使用工作流：${e(team.name)}">开始工作流</button></div></article>`).join("") || '<p class="empty-library">没有找到匹配的工作流</p>';
}
function taskLabel(task, run) { if (task.status === "queued") { const blocked = (task.depends_on || []).some((id) => run.tasks.find((t) => t.id === id)?.status !== "completed"); const before = state.room?.runs.slice(0, state.room.runs.findIndex((r) => r.id === run.id)) || []; if (blocked || before.some((r) => ["queued", "running"].includes(r.status))) return "等待前序任务"; return "等待接手"; } return { running: "执行中", completed: "已完成", failed: "执行失败", cancelled: "已取消" }[task.status] || task.status; }
function runLabel(run) { if (run.status === "cancelled" && run.cancel_reason === "team_members_changed") return "成员已变更，本轮已停止"; if (run.status === "cancelled" && run.cancel_reason === "team_archived") return "团队已归档，本轮已停止"; return { queued: "等待 Agent 接手", running: "Agent 正在协作", completed: "协作已完成", failed: "有任务需要重试", cancelled: "协作已取消" }[run.status] || "等待 Agent 接手"; }
function statusIcon(status) { return { queued: "clock", running: "spark", completed: "check", failed: "alert", cancelled: "stop" }[status] || "clock"; }
function artifactButton(item) { return `<div class="message-artifact-card"><button class="message-artifact" data-artifact="${e(item.id)}" aria-label="打开成果：${e(item.title)}"><span>${icon("file")}</span><span><strong>${e(item.title)}</strong><small>${e(({ markdown: "文档", html: "网页", text: "文本", json: "JSON" })[item.kind] || item.kind)} · 版本 ${item.version}</small></span><span class="artifact-open-label">打开 Studio</span></button><a class="message-artifact-download" href="/api/hub/artifacts/${encodeURIComponent(item.id)}/download?version=${Number(item.version)}" download aria-label="下载成果：${e(item.title)}">${icon("download")}</a></div>`; }
function renderRoom() { if (!state.room) return; const room = state.room; $("attach-source").hidden = false; $("welcome").hidden = true; $("conversation").hidden = false; $("chat-view").classList.add("has-room"); $("workspace-toggle").hidden = state.view !== "chat"; if (state.view === "chat") $("view-title").textContent = room.title; $("room-state").hidden = state.view !== "chat"; const latestRun = room.runs[room.runs.length - 1]; $("room-state").textContent = latestRun ? runLabel(latestRun) : ""; $("room-state").classList.toggle("running", latestRun?.status === "running"); const scroll = $("chat-scroll"); const nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 120; const signature = JSON.stringify({ messages: room.messages, runs: room.runs, artifacts: room.artifacts, sources: room.sources, workers: state.workers }); if (signature !== state.roomSignature) { const expanded = [...$("task-timeline").querySelectorAll("details[open]")].map((el) => el.dataset.taskDetail); state.roomSignature = signature; const messageSignature = JSON.stringify({ messages: room.messages, artifacts: room.artifacts }); if (messageSignature !== state.messageSignature) { state.messageSignature = messageSignature; $("messages").innerHTML = room.messages.map((message) => { const artifacts = room.artifacts.filter((item) => item.task_id && item.task_id === message.task_id); if (message.role === "user") { const peer = !!message.author_id && message.author_id !== window.workspaceAccount?.session?.user?.id; return `<article class="message user${peer ? " peer" : ""}">${peer ? `<span class="member-message-avatar" aria-hidden="true">${e((message.author_name || "成员").slice(0, 1))}</span>` : ""}<div class="message-content">${peer ? `<div class="message-member-name">${e(message.author_name || "成员")}</div>` : ""}${e(message.text).replace(/\n/g, "<br>")}${window.workspaceTeams?.messageRoutingHTML(message) || ""}</div></article>`; } return `<article class="message ${e(message.role)}">${message.role === "agent" ? avatar(message.agent_id, "message-avatar") : '<span class="message-avatar">' + icon("spark") + '</span>'}<div class="message-content">${message.role === "agent" ? `<div class="message-agent-name">${e(agent(message.agent_id).name)}<time>${formatTime(message.created_at)}</time></div>` : ""}<div class="prose">${markdown(message.text)}</div>${artifacts.length ? `<div class="message-artifacts">${artifacts.map(artifactButton).join("")}</div>` : ""}${message.role === "agent" ? `<button class="message-copy" data-copy-message="${e(message.id)}" aria-label="复制回复">${icon("copy")}</button>` : ""}</div></article>`; }).join(""); } renderRunSummary(); renderTasks(); expanded.forEach((id) => { const detail = [...$("task-timeline").querySelectorAll("details")].find((item) => item.dataset.taskDetail === id); if (detail) detail.open = true; }); renderArtifactList(); renderSources(); if (nearBottom) requestAnimationFrame(() => { scroll.scrollTop = scroll.scrollHeight; }); } if (!state.contextDirty && document.activeElement !== $("room-context")) $("room-context").value = room.context || ""; $("context-state").textContent = state.contextDirty ? "有未保存的修改" : ""; renderIdentity(); window.dispatchEvent(new Event("workspace-room")); }
function renderRunSummary() {
  const room = state.room;
  const run = [...room.runs].reverse().find((item) => ["running", "queued", "failed"].includes(item.status)) || room.runs[room.runs.length - 1];
  if (!run) { $("run-summary").innerHTML = ""; return; }
  const completed = run.tasks.filter((task) => task.status === "completed").length;
  const running = run.tasks.filter((task) => task.status === "running");
  const awaiting = run.tasks.find((task) => task.status === "queued");
  const needsConnection = !state.workers.some((worker) => worker.connected);
  const connect = run.status === "queued" && needsConnection && document.documentElement.dataset.access !== "viewer";
  const detail = run.status === "running" ? running.find((task) => task.progress)?.progress || running.map((task) => agent(task.agent_id).name).join("、") : run.status === "queued" ? awaiting ? `${agent(awaiting.agent_id).name} · ${taskLabel(awaiting, run)}` : "任务已排队" : run.status === "cancelled" && run.cancel_reason ? "已有成果与历史记录保留" : `${completed} / ${run.tasks.length} 项任务完成`;
  $("run-summary").innerHTML = `<article class="run-card compact-run ${e(run.status)}" data-run-id="${e(run.id)}"><span class="run-indicator">${icon(statusIcon(run.status))}</span><div class="run-status-copy"><strong>${e(runLabel(run))}</strong><span>${e(detail)}</span></div>${connect ? '<button class="run-connect" data-open-connection>连接 Agent</button>' : ""}<button class="run-details" data-show-panel="activity" aria-label="查看任务">${icon("chevron")}</button></article>`;
}
function renderTasks() { const room = state.room; $("task-timeline").innerHTML = room.runs.length ? [...room.runs].reverse().map((run, index) => `<section class="timeline-run"><div class="timeline-heading"><span>${index === 0 ? "当前协作" : "之前的协作"}</span><span>${formatTime(run.created_at)}</span></div>${run.status === "cancelled" && run.cancel_reason ? `<p class="team-run-stopped">${e(runLabel(run))}</p>` : ""}${run.tasks.map((task) => `<article class="task-item" data-task-id="${e(task.id)}"><span class="task-status-icon ${e(task.status)}">${icon(statusIcon(task.status))}</span><div class="task-main"><div class="task-name">${e(agent(task.agent_id).name)}<span>${e(taskLabel(task, run))}</span></div><p class="task-title">${e(task.title)}</p>${task.progress ? `<p class="task-progress">${e(task.progress)}</p>` : task.status === "queued" && state.workers.some((worker) => worker.connected) && !state.workers.some((worker) => worker.connected && worker.agent_ids.includes(task.agent_id)) ? `<p class="task-progress">等待支持 ${e(agent(task.agent_id).name)} 的执行端</p>` : ""}${task.error ? `<p class="task-error">${e(task.error)}</p>` : ""}<details class="task-detail" data-task-detail="${e(task.id)}"><summary>任务说明</summary><p>${e(task.instructions)}</p></details><button class="task-execution-button" data-open-execution="${e(task.id)}" aria-label="查看执行记录">${icon("clock")}执行记录</button></div></article>`).join("")}<div class="timeline-actions">${["queued", "running"].includes(run.status) ? `<button data-cancel-run="${e(run.id)}" aria-label="取消任务">取消任务</button>` : ""}${run.status === "failed" ? `<button data-retry-run="${e(run.id)}" aria-label="重试任务">重试任务</button>` : ""}</div></section>`).join("") : `<div class="panel-empty"><span>${icon("route")}</span><p>发送需求后，在这里查看分工。</p></div>`; }
function renderArtifactList() { const artifacts = state.room?.artifacts || []; $("artifact-count").textContent = artifacts.length; $("artifact-list").hidden = artifacts.length === 1 && state.artifact?.id === artifacts[0].id; $("artifact-list").innerHTML = artifacts.length ? artifacts.map((item) => `<button class="artifact-list-item${item.id === state.selectedArtifact ? " active" : ""}" data-artifact="${e(item.id)}" aria-label="打开成果：${e(item.title)}"><span>${icon("file")}</span><span><strong>${e(item.title)}</strong><small>${e(({ markdown: "文档", html: "网页", text: "文本", json: "JSON" })[item.kind] || item.kind)}</small></span><span class="artifact-item-arrow">↗</span></button>`).join("") : `<div class="panel-empty"><span>${icon("file")}</span><p>完成的文档、设计与代码<br>会保存在这里。</p></div>`; if (state.selectedArtifact && state.artifact) { const current = artifacts.find((item) => item.id === state.selectedArtifact); if (current && current.version > Math.max(...(state.artifact.versions || []).map((item) => item.version), 0)) loadArtifact(state.selectedArtifact, state.selectedVersion, false); } }
function applyPanel() { const visible = state.panelOpen && state.view === "chat" && !!state.room; $("detail-panel").hidden = !visible; $("workspace-shell").classList.toggle("panel-open", visible); $("workspace-shell").classList.toggle("studio-open", visible && state.studioExpanded); $("expand-workspace").setAttribute("aria-label", state.studioExpanded ? "收起 Studio" : "展开 Studio"); $("workspace-toggle").setAttribute("aria-expanded", String(visible)); }
function showPanel(panel = "activity") { state.panel = panel; state.panelOpen = true; document.querySelectorAll("[data-panel]").forEach((tab) => { const active = tab.dataset.panel === panel; tab.classList.toggle("active", active); tab.setAttribute("aria-selected", String(active)); tab.tabIndex = active ? 0 : -1; }); for (const name of ["activity", "artifacts", "context"]) $("panel-" + name).hidden = name !== panel; applyPanel(); if (panel === "artifacts" && !state.selectedArtifact && state.room?.artifacts.length) loadArtifact(state.room.artifacts[0].id); }
async function loadArtifact(id, version, open = true) { const generation = ++state.artifactGeneration; const resolvedVersion = version ?? (id === state.selectedArtifact ? state.selectedVersion : null); const changedArtifact = state.selectedArtifact !== id; state.selectedArtifact = id; if (changedArtifact) { state.artifact = null; state.artifactEditing = false; $("artifact-detail").hidden = true; } if (open) { state.studioExpanded = true; showPanel("artifacts"); } if (resolvedVersion != null) state.selectedVersion = Number(resolvedVersion); try { const artifact = await api(`/artifacts/${encodeURIComponent(id)}${resolvedVersion ? `?version=${resolvedVersion}` : ""}`); if (generation !== state.artifactGeneration) return; const previewChanged = state.artifact?.id !== artifact.id || state.artifact?.version !== artifact.version || state.artifact?.content !== artifact.content; state.artifact = artifact; state.selectedArtifact = id; state.selectedVersion = Number(resolvedVersion || artifact.version); renderArtifactList(); $("artifact-detail").hidden = false; $("artifact-title").textContent = artifact.title; const versions = artifact.versions?.length ? artifact.versions : [{ version: artifact.version }]; $("artifact-version").innerHTML = [...versions].sort((a, b) => b.version - a.version).map((item) => `<option value="${item.version}">版本 ${item.version}${item.version === versions.reduce((max, v) => Math.max(max, v.version), 0) ? " · 最新" : " · 历史"}${item.author_name ? ` · ${e(item.author_name)}` : ""}</option>`).join(""); $("artifact-version").value = String(state.selectedVersion); const url = `/api/hub/artifacts/${encodeURIComponent(id)}`; $("artifact-download").href = `${url}/download?version=${state.selectedVersion}`; $("artifact-download").setAttribute("download", ""); if (previewChanged) { if (artifact.kind === "html") { $("artifact-body").innerHTML = `<iframe sandbox="" title="${e(artifact.title)}预览" src="${url}/preview?version=${state.selectedVersion}" referrerpolicy="no-referrer"></iframe>`; } else if (artifact.kind === "markdown") $("artifact-body").innerHTML = `<div class="prose">${markdown(artifact.content)}</div>`; else $("artifact-body").innerHTML = `<pre class="plain-artifact">${e(artifact.content)}</pre>`; } renderArtifactExtras(); window.dispatchEvent(new Event("workspace-artifact")); } catch (error) { toast(error.message, true); } }
function resetArtifact() { state.artifactGeneration++; state.selectedArtifact = null; state.selectedVersion = null; state.artifact = null; state.artifactEditing = false; $("artifact-detail").hidden = true; $("artifact-body").innerHTML = ""; window.dispatchEvent(new Event("workspace-artifact")); }
async function openRoom(id, { fromHash = false, initialNavigation = null } = {}) { if (initialNavigation === null) state.navigationGeneration++; const navigationGeneration = initialNavigation ?? state.navigationGeneration; if (id === state.roomId && state.room) { setView("chat"); return; } saveDraft(); const generation = ++state.roomGeneration; const readGeneration = ++state.roomReadGeneration; state.loading = true; try { const room = await api(`/rooms/${encodeURIComponent(id)}`); if (generation !== state.roomGeneration || readGeneration !== state.roomReadGeneration || navigationGeneration !== state.navigationGeneration) return; state.roomId = id; state.room = room; state.contextDirty = false; state.roomSignature = ""; state.selectedTemplate = null; $("selected-team").hidden = true; $("composer-agent").disabled = false; resetArtifact(); restoreDraft(); setView("chat"); renderRoom(); renderSidebar(); if (!fromHash) history.replaceState(null, "", `#room/${encodeURIComponent(id)}`); requestAnimationFrame(() => { $("chat-scroll").scrollTop = $("chat-scroll").scrollHeight; }); } catch (error) { toast(error.message, true); } finally { if (generation === state.roomGeneration) state.loading = false; } }
function newChat({ agentId = "auto", templateId = null } = {}) { state.navigationGeneration++; if (document.documentElement.dataset.access === "viewer" && state.rooms.length) { openRoom(state.rooms[0].id); return; } saveDraft(); state.roomGeneration++; state.roomId = null; state.room = null; state.loading = false; state.roomSignature = ""; state.contextDirty = false; state.selectedTemplate = templateId; state.panelOpen = false; resetArtifact(); $("attach-source").hidden = true; $("welcome").hidden = false; $("conversation").hidden = true; $("chat-view").classList.remove("has-room"); $("selected-team").hidden = !templateId; if (templateId) $("selected-team-name").textContent = state.templates.find((team) => team.id === templateId)?.name || "工作流"; setView("chat"); restoreDraft(); $("composer-agent").value = agentId; saveDraft(); renderIdentity(); $("run-summary").innerHTML = ""; $("composer-agent").disabled = !!templateId; renderSidebar(); history.replaceState(null, "", "#new"); $("composer-input").focus(); }
async function bootstrap({ initial = false } = {}) { const navigationGeneration = state.navigationGeneration; const data = await api("/bootstrap"); state.agents = data.agents || []; state.rooms = (data.rooms || []).sort((a, b) => (Date.parse(b.last_message_at || b.created_at) || 0) - (Date.parse(a.last_message_at || a.created_at) || 0)); state.templates = data.templates || []; state.teams = (data.teams || []).map((team) => { const known = [state.teams.find((item) => item.id === team.id), state.room?.team].filter((item) => item?.id === team.id).sort((a, b) => b.version - a.version)[0]; return known && known.version > team.version ? known : team; }); state.connection = data.connection || {}; state.workers = data.workers || []; renderWorkers(); renderLocalExecutor(); renderSidebar(); const signature = JSON.stringify({ agents: state.agents, templates: state.templates }); if (signature !== state.bootstrapSignature) { state.bootstrapSignature = signature; renderLibrary(); } const workers = Number(state.connection.active_workers) || 0; const connected = Number(state.connection.connected_workers) || 0; $("connection-status").textContent = workers ? `${workers} 个执行端正在执行` : connected ? `${connected} 个执行端已连接` : state.localExecutor?.state === "running" ? "进程已启动，等待连接" : "尚未连接执行端"; window.dispatchEvent(new Event("workspace-data")); if (initial) { $("initial-loading").hidden = true; if (navigationGeneration !== state.navigationGeneration) return; setView("chat"); const match = /^#room\/(.+)$/.exec(location.hash); if (match) await openRoom(decodeURIComponent(match[1]), { fromHash: true, initialNavigation: navigationGeneration }); else if (state.rooms.length && (location.hash !== "#new" || document.documentElement.dataset.access === "viewer")) await openRoom(state.rooms[0].id, { initialNavigation: navigationGeneration }); else renderIdentity(); } }
async function sendMessage(event) {
  event.preventDefault(); if (state.submitting || state.loading) return;
  const input = $("composer-input"), submittedInput = input.value, text = submittedInput.trim(); if (!text) return;
  state.submitting = true; state.roomReadGeneration++; resizeInput();
  const originRoom = state.roomId, generation = state.roomGeneration;
  const body = { text, agent_id: $("composer-agent").value };
  if (!originRoom && state.selectedTemplate) body.template_id = state.selectedTemplate;
  try {
    if (state.room?.team_id && window.workspaceTeams) { delete body.agent_id; Object.assign(body, window.workspaceTeams.composePayload(state.room, text)); }
    const room = await api(originRoom ? `/rooms/${encodeURIComponent(originRoom)}/messages` : "/rooms", { method: "POST", body });
    const savedDraft = state.drafts.get(originRoom || "new");
    if (!savedDraft || savedDraft.text === submittedInput) state.drafts.delete(originRoom || "new");
    window.workspaceTeams?.messageSent(originRoom, body);
    if (generation === state.roomGeneration && state.roomId === originRoom) {
      state.roomReadGeneration++; state.room = room; state.roomId = room.id; state.selectedTemplate = null;
      $("selected-team").hidden = true; $("composer-agent").disabled = false;
      if (input.value === submittedInput) input.value = "";
      state.roomSignature = "";
      if (!originRoom) { state.contextDirty = false; resetArtifact(); }
      renderRoom(); saveDraft(); history.replaceState(null, "", `#room/${encodeURIComponent(room.id)}`);
      if (!document.querySelector("dialog[open]")) input.focus();
      requestAnimationFrame(() => { $("chat-scroll").scrollTop = $("chat-scroll").scrollHeight; });
    }
    await bootstrap();
  } catch (error) {
    window.workspaceTeams?.messageFailed(originRoom, body, error);
    toast(error.message, true);
    if (error.status === 409 && originRoom && state.roomId === originRoom) await window.workspaceUI.refreshRoom().catch(() => {});
  } finally { state.submitting = false; resizeInput(); window.dispatchEvent(new Event("workspace-composer")); }
}
function editAgent(id = null) { state.editingAgent = id; const item = id ? agent(id) : {}; $("agent-dialog-title").textContent = id ? "编辑 Agent" : "创建 Agent"; $("agent-name").value = item.name || ""; $("agent-description").value = item.description || ""; $("agent-instructions").value = item.instructions || ""; $("agent-dialog").showModal(); $("agent-name").focus(); }
async function saveAgent(event) { event.preventDefault(); const button = $("save-agent"); button.disabled = true; try { await api(state.editingAgent ? `/agents/${encodeURIComponent(state.editingAgent)}` : "/agents", { method: state.editingAgent ? "PUT" : "POST", body: { name: $("agent-name").value.trim(), description: $("agent-description").value.trim(), instructions: $("agent-instructions").value.trim() } }); $("agent-dialog").close(); await bootstrap(); toast("Agent 已保存"); } catch (error) { toast(error.message, true); } finally { button.disabled = false; } }
function renderConnection() {
  const data = state.connectionInfo, workerTab = state.connectTab === "workers";
  $("connection-code").hidden = workerTab;
  $("worker-list").hidden = !workerTab;
  $("copy-connection").hidden = workerTab;
  $("connection-code").textContent = data ? state.connectTab === "mcp" ? JSON.stringify(data.mcp_config, null, 2) : data.instructions : "正在读取连接信息…";
  $("connect-description").textContent = workerTab ? "这里显示实际连接到当前工作空间的执行端。" : state.connectTab === "mcp" ? "将这段配置添加到支持 MCP 的 Agent 客户端。" : "复制以下内容，发给你正在使用的 Agent。";
  $("copy-connection").innerHTML = icon("copy") + (state.connectTab === "mcp" ? "复制 MCP 配置" : "复制接入说明");
  $("copy-connection").disabled = !data;
  document.querySelectorAll("[data-connect-tab]").forEach((tab) => { const active = tab.dataset.connectTab === state.connectTab; tab.classList.toggle("active", active); tab.setAttribute("aria-selected", String(active)); });
  renderWorkers();
}
function renderWorkers() {
  const signature = JSON.stringify(state.workers);
  if ($("worker-list").dataset.signature === signature) return;
  $("worker-list").dataset.signature = signature;
  $("worker-list").innerHTML = state.workers.length ? state.workers.map((worker) => `<article class="worker-card"><div class="worker-heading"><strong>${e(worker.name || worker.worker_id)}</strong><span class="worker-state ${worker.connected ? "connected" : ""}">${worker.connected ? "已连接" : "已离线"}</span></div><p>${e((worker.agent_ids || []).map((id) => agent(id).name).join(" · "))}</p><div class="worker-capabilities">${(worker.capabilities || []).map((name) => `<span>${e(name)}</span>`).join("")}</div><small>最近活动 ${formatTime(worker.last_seen_at)}</small></article>`).join("") : '<div class="panel-empty"><span>' + icon("plug") + '</span><p>' + (document.documentElement.dataset.access === "viewer" ? "当前空间没有已连接的执行端" : "还没有连接的执行端<br>复制接入说明，交给你的 Agent。") + '</p></div>';
}
function renderLocalExecutor() {
  const executor = state.localExecutor;
  if (!executor?.local_control) { $("local-executor").hidden = true; return; }
  $("local-executor").hidden = false;
  const registered = state.workers.some((worker) => worker.worker_id === executor.worker_id && worker.connected);
  const executing = !!state.room?.runs.some((run) => run.tasks.some((task) => task.worker_id === executor.worker_id && task.status === "running"));
  const label = state.executorBusy ? "正在更新连接…" : executor.state === "running" ? executing ? "正在执行任务" : registered ? "已连接" : "已启动，等待连接" : executor.state === "failed" ? "执行器已退出" : executor.codex_available ? "可以启动" : "未找到本机 Codex";
  const signature = JSON.stringify({ executor, label, busy: state.executorBusy });
  if ($("local-executor").dataset.signature === signature) return;
  $("local-executor").dataset.signature = signature;
  $("local-executor").innerHTML = `<div class="local-executor-top"><span class="local-executor-icon">${icon("code")}</span><div><strong>Codex</strong><span class="local-executor-state">${e(label)}</span></div><button class="${executor.state === "running" ? "secondary" : "primary"}-button" data-executor-action="${executor.state === "running" ? "stop" : "start"}" aria-label="${executor.state === "running" ? "停止" : "启动"} Codex 执行器"${state.executorBusy || (!executor.codex_available && executor.state !== "running") ? " disabled" : ""}>${state.executorBusy ? "请稍候…" : executor.state === "running" ? "停止执行器" : "启动 Codex"}</button></div><p>使用这台电脑已登录的 Codex 账号，领取并执行空间里的任务。</p>`;
}
async function refreshLocalExecutor() {
  try { state.localExecutor = await api("/executors/local"); renderLocalExecutor(); }
  catch { state.localExecutor = null; renderLocalExecutor(); }
}
async function controlLocalExecutor(action) {
  if (state.executorBusy) return;
  state.executorBusy = true; renderLocalExecutor();
  try { state.localExecutor = await api(`/executors/codex/${action}`, { method: "POST", body: {} }); await bootstrap(); if (action === "start" && state.localExecutor.state !== "running") toast("执行器未能启动，请检查本机 Codex 登录状态。", true); else toast(action === "start" ? "Codex 进程已启动，连接状态将自动更新。" : "执行器已停止"); }
  catch (error) { toast(error.message, true); await refreshLocalExecutor(); }
  finally { state.executorBusy = false; renderLocalExecutor(); }
}
async function openConnection() {
  const readonly = document.documentElement.dataset.access === "viewer"; if (readonly) state.connectTab = "workers"; $("connection-dialog").querySelector(".dialog-header h2").textContent = readonly ? "连接状态" : "连接你的 Agent"; $("connection-dialog").querySelector(".connect-intro p").textContent = readonly ? "你可以查看执行端和任务进度。当前账户仅有查看权限。" : "使用你已有的 Agent 和模型，领取这里的任务并回传成果。";
  if (!$("connection-dialog").open) $("connection-dialog").showModal(); renderConnection();
  if (readonly) return;
  const results = await Promise.allSettled([api("/connection"), refreshLocalExecutor()]);
  if (results[0].status === "fulfilled") { state.connectionInfo = results[0].value; renderConnection(); }
  else { $("connection-code").textContent = results[0].reason.message; toast(results[0].reason.message, true); }
}
async function copyText(text) { try { await navigator.clipboard.writeText(text); toast("已复制"); } catch { toast("无法自动复制，请选择内容后复制。", true); } }
async function runAction(id, action, button) { button.disabled = true; const roomId = state.roomId; try { const room = await api(`/runs/${encodeURIComponent(id)}/${action}`, { method: "POST", body: {} }); if (state.roomId === roomId) { state.room = room; renderRoom(); } await bootstrap(); } catch (error) { toast(error.message, true); } finally { if (button.isConnected) button.disabled = false; } }
async function saveContext() { if (!state.roomId) return; const roomId = state.roomId; const text = $("room-context").value; $("save-context").disabled = true; try { const room = await api(`/rooms/${encodeURIComponent(roomId)}/context`, { method: "PUT", body: { text } }); if (state.roomId === roomId) { state.room = room; state.contextDirty = $("room-context").value !== text; renderRoom(); $("context-state").textContent = state.contextDirty ? "有未保存的修改" : "已保存"; } toast("共享资料已保存"); } catch (error) { toast(error.message, true); } finally { $("save-context").disabled = false; } }

function renderArtifactExtras() {
  if (!state.artifact) return;
  const editing = state.artifactEditing && state.artifactDrafts.has(state.selectedArtifact);
  const operation = state.artifactDrafts.get(state.selectedArtifact)?.operation;
  $("save-artifact").disabled = !!operation;
  $("rebase-artifact-draft").disabled = !!operation;
  $("cancel-artifact-edit").disabled = !!operation;
  $("artifact-edit-title").readOnly = operation === "saving";
  $("artifact-edit-content").readOnly = operation === "saving";
  $("artifact-editor").hidden = !editing;
  $("artifact-body").hidden = !!editing;
  $("edit-artifact").classList.toggle("active", !!editing);
  $("artifact-preview-tab").classList.toggle("active", !editing);
  $("artifact-version").disabled = !!editing;
  $("approve-artifact").disabled = !!editing;
  $("request-artifact-changes").disabled = !!editing;
  const reviews = (state.artifact.reviews || []).filter((review) => review.version === state.selectedVersion);
  $("artifact-review-status").hidden = !reviews.length;
  if (reviews.length) {
    const review = reviews[reviews.length - 1];
    $("artifact-review-status").className = `artifact-review-status ${review.decision}`;
    $("artifact-review-status").innerHTML = `<strong>${review.decision === "approved" ? "此版本已通过审核" : "此版本需要修改"}</strong>${review.author_name ? `<span class="review-author">${e(review.author_name)}</span>` : ""}${review.comment ? `<p>${e(review.comment)}</p>` : ""}`;
  }
}
function startArtifactEdit() {
  if (!state.artifact) return;
  let draft = state.artifactDrafts.get(state.selectedArtifact);
  if (!draft) {
    draft = { id: state.selectedArtifact, baseVersion: state.selectedVersion, title: state.artifact.title, kind: state.artifact.kind, content: state.artifact.content };
    state.artifactDrafts.set(state.selectedArtifact, draft);
  }
  state.artifactEditing = true;
  $("artifact-edit-title").value = draft.title;
  $("artifact-edit-content").value = draft.content;
  $("artifact-edit-state").textContent = `基于版本 ${draft.baseVersion}`; $("artifact-conflict").hidden = !draft.conflict;
  renderArtifactExtras();
  $("artifact-edit-content").focus();
}
function persistArtifactDraft() {
  const draft = state.artifactDrafts.get(state.selectedArtifact);
  if (!draft || !state.artifactEditing) return;
  draft.title = $("artifact-edit-title").value;
  draft.content = $("artifact-edit-content").value;
  $("artifact-edit-state").textContent = "有未保存的修改";
}
function cancelArtifactEdit() { if (state.artifactDrafts.get(state.selectedArtifact)?.operation) return; state.artifactDrafts.delete(state.selectedArtifact); state.artifactEditing = false; $("artifact-conflict").hidden = true; renderArtifactExtras(); }
function previewArtifactDraft() { persistArtifactDraft(); state.artifactEditing = false; renderArtifactExtras(); }
async function saveArtifact() {
  persistArtifactDraft();
  const draft = state.artifactDrafts.get(state.selectedArtifact);
  if (!draft || draft.operation) return;
  if (!draft.title.trim()) { toast("请填写成果标题。", true); return; }
  const roomId = state.roomId;
  const currentDraft = () => state.selectedArtifact === draft.id && state.artifactDrafts.get(draft.id) === draft;
  draft.operation = "saving"; renderArtifactExtras();
  try {
    const artifact = await api(`/artifacts/${encodeURIComponent(draft.id)}`, { method: "PUT", body: { base_version: draft.baseVersion, title: draft.title.trim(), kind: draft.kind, content: draft.content } });
    const wasCurrent = currentDraft();
    state.artifactDrafts.delete(draft.id);
    if (wasCurrent && state.roomId === roomId) { state.artifactEditing = false; await loadArtifact(draft.id, artifact.version, false); }
    if (roomId && state.roomId === roomId) { const room = await api(`/rooms/${encodeURIComponent(roomId)}`); if (state.roomId === roomId) { state.room = room; renderRoom(); } }
    toast("已保存新版本");
  } catch (error) { if (error.status === 409) draft.conflict = true; if (currentDraft()) { $("artifact-edit-state").textContent = "保存未完成，草稿已保留"; $("artifact-conflict").hidden = !draft.conflict; } toast(error.message, true); }
  finally { draft.operation = null; renderArtifactExtras(); }
}
async function rebaseArtifactDraft() {
  persistArtifactDraft();
  const draft = state.artifactDrafts.get(state.selectedArtifact); if (!draft || draft.operation) return;
  const currentDraft = () => state.selectedArtifact === draft.id && state.artifactDrafts.get(draft.id) === draft;
  draft.operation = "rebasing"; renderArtifactExtras();
  $("artifact-edit-state").textContent = "正在读取最新版本…";
  try {
    const latest = await api(`/artifacts/${encodeURIComponent(draft.id)}`);
    if (state.artifactDrafts.get(draft.id) !== draft) return;
    draft.baseVersion = latest.version; draft.conflict = false;
    if (currentDraft()) {
      await loadArtifact(draft.id, latest.version, false);
      if (currentDraft()) {
        $("artifact-conflict").hidden = true;
        $("artifact-edit-state").textContent = `草稿已保留，保存将新建版本 ${latest.version + 1}`;
        toast(`已切换到最新版本 ${latest.version}，请确认草稿后保存。`);
      }
    }
  } catch (error) { toast(error.message, true); }
  finally { draft.operation = null; renderArtifactExtras(); }
}
function openReview(decision) {
  if (!state.artifact) return;
  state.reviewDecision = { decision, id: state.selectedArtifact, version: state.selectedVersion };
  $("review-dialog-title").textContent = decision === "approved" ? "通过审核" : "要求修改";
  $("review-version-note").textContent = `${state.artifact.title} · 版本 ${state.selectedVersion}`;
  $("review-comment").value = "";
  $("review-dialog").showModal();
  $("review-comment").focus();
}
async function saveReview(event) {
  event.preventDefault();
  const review = state.reviewDecision;
  if (!review) return;
  $("submit-review").disabled = true;
  try {
    await api(`/artifacts/${encodeURIComponent(review.id)}/reviews`, { method: "POST", body: { version: review.version, decision: review.decision, comment: $("review-comment").value.trim() } });
    $("review-dialog").close();
    if (state.selectedArtifact === review.id && state.selectedVersion === review.version) await loadArtifact(review.id, review.version, false);
    toast("审核意见已保存");
  } catch (error) { toast(error.message, true); }
  finally { $("submit-review").disabled = false; }
}
function renderSources() {
  const sources = (state.room?.sources || []).filter((source) => source.origin !== "uploaded_file");
  $("sources-list").innerHTML = sources.length ? sources.map((source) => `<article class="source-item" data-source-record="${e(source.id)}"><button class="source-open" data-source="${e(source.id)}" aria-label="查看资料：${e(source.title)}"><span>${icon(source.kind === "url" ? "search" : "file")}</span><span><strong>${e(source.title)}</strong><small>${source.kind === "url" ? "网页链接 · 等待 Agent 访问" : source.kind === "markdown" ? "Markdown" : "文本资料"}</small></span></button><button class="icon-button" data-revoke-source="${e(source.id)}" aria-label="移除资料：${e(source.title)}">${icon("close")}</button></article>`).join("") : '<p class="sources-empty">添加文档、研究资料或网页链接，供协作者参考。</p>';
}
function openSourceForm() {
  if (!state.roomId) return;
  $("source-form").reset(); sourceKindChanged();
  $("source-dialog").showModal(); $("source-title").focus();
}
function sourceKindChanged() {
  const url = $("source-kind").value === "url";
  $("source-url-field").hidden = url;
  $("source-url-note").hidden = !url;
  $("source-content").placeholder = url ? "https://…" : "粘贴参考内容…";
  $("source-content").rows = url ? 2 : 7;
}
async function saveSource(event) {
  event.preventDefault();
  const roomId = state.roomId;
  if (!roomId) return;
  $("save-source").disabled = true;
  try {
    await api(`/rooms/${encodeURIComponent(roomId)}/sources`, { method: "POST", body: { title: $("source-title").value.trim(), kind: $("source-kind").value, content: $("source-content").value, source_url: $("source-kind").value === "url" ? null : $("source-url").value.trim() || null } });
    $("source-dialog").close();
    if (state.roomId === roomId) { state.room = await api(`/rooms/${encodeURIComponent(roomId)}`); renderRoom(); showPanel("context"); }
    toast("资料已添加");
  } catch (error) { toast(error.message, true); }
  finally { $("save-source").disabled = false; }
}
async function openSource(id) {
  try {
    const source = await api(`/sources/${encodeURIComponent(id)}`);
    $("source-detail-title").textContent = source.title;
    const content = source.origin === "uploaded_file" ? (source.content.includes("\n\n") ? source.content.slice(source.content.indexOf("\n\n") + 2) : "") : source.content; $("source-detail-content").innerHTML = content.trim() ? source.kind === "markdown" ? markdown(content) : `<p>${e(content)}</p>` : "<p>此附件没有可提取的文字，请在资料中下载原件。</p>";
    const url = source.source_url || (source.kind === "url" ? source.content : "");
    $("source-detail-link").hidden = !/^https?:\/\//i.test(url);
    if (/^https?:\/\//i.test(url)) $("source-detail-link").href = url;
    $("source-detail-dialog").showModal();
  } catch (error) { toast(error.message, true); }
}
async function revokeSource(id, button) {
  button.disabled = true;
  const roomId = state.roomId;
  try {
    await api(`/sources/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (state.roomId === roomId) { state.room = await api(`/rooms/${encodeURIComponent(roomId)}`); renderRoom(); }
    toast("资料已移除");
  } catch (error) { toast(error.message, true); }
  finally { if (button.isConnected) button.disabled = false; }
}
async function importSourceFile() {
  const file = $("source-file").files[0];
  if (!file) return;
  if (file.size > 1024 * 1024) { toast("请选择 1 MB 以内的文本文件。", true); return; }
  try {
    $("source-content").value = await file.text();
    $("source-title").value = file.name;
    $("source-kind").value = /\.md$/i.test(file.name) ? "markdown" : "text";
    sourceKindChanged();
  } catch { toast("无法读取这个文件。", true); }
}
async function editTeam(id = null) {
  try {
    const item = id ? await api(`/templates/${encodeURIComponent(id)}`) : { name: "", description: "", steps: [{ key: "step_1", agent_id: "researcher", title: "研究需求", instructions: "", depends_on: [] }] };
    state.teamDraft = JSON.parse(JSON.stringify(item));
    state.teamStep = item.steps[0]?.key;
    $("team-dialog-title").textContent = id ? "编辑工作流" : "创建工作流";
    $("team-name").value = item.name;
    $("team-description").value = item.description;
    $("team-save-state").textContent = id ? `版本 ${item.version}` : "";
    $("step-agent").innerHTML = state.agents.filter((item) => item.id !== "coordinator").map((item) => `<option value="${e(item.id)}">${e(item.name)}</option>`).join("");
    renderWorkflow(); renderStepProperties();
    $("team-dialog").showModal(); $("team-name").focus();
  } catch (error) { toast(error.message, true); }
}
function stepDepths(steps) {
  const depths = new Map(), visiting = new Set(), byKey = new Map(steps.map((step) => [step.key, step]));
  function visit(key) {
    if (visiting.has(key)) throw new Error("任务之间存在循环依赖，请调整前序任务。");
    if (depths.has(key)) return depths.get(key);
    if (!byKey.has(key)) throw new Error("前序任务已不存在，请重新选择。");
    visiting.add(key);
    const deps = byKey.get(key).depends_on || [];
    const depth = deps.length ? 1 + Math.max(...deps.map(visit)) : 0;
    visiting.delete(key); depths.set(key, depth); return depth;
  }
  steps.forEach((step) => visit(step.key)); return depths;
}
function renderWorkflow() {
  if (!state.teamDraft) return;
  const steps = state.teamDraft.steps;
  let depths;
  try { depths = stepDepths(steps); $("team-save-state").classList.remove("error"); }
  catch (error) { depths = new Map(steps.map((step) => [step.key, 0])); $("team-save-state").textContent = error.message; $("team-save-state").classList.add("error"); }
  const rows = new Map(), positions = new Map();
  steps.forEach((step) => { const depth = depths.get(step.key) || 0; const row = rows.get(depth) || 0; positions.set(step.key, { x: 28 + depth * 225, y: 35 + row * 112 }); rows.set(depth, row + 1); });
  const width = Math.max(480, 35 + (Math.max(...depths.values(), 0) + 1) * 225), height = Math.max(310, 50 + Math.max(...rows.values(), 1) * 112);
  const edges = steps.flatMap((step) => (step.depends_on || []).map((key) => { const from = positions.get(key), to = positions.get(step.key); if (!from || !to) return ""; return `<path d="M ${from.x + 180} ${from.y + 35} C ${from.x + 203} ${from.y + 35}, ${to.x - 23} ${to.y + 35}, ${to.x} ${to.y + 35}" marker-end="url(#flow-arrow)"/>`; })).join("");
  $("workflow-canvas").innerHTML = `<div class="flow-stage" style="width:${width}px;height:${height}px"><svg class="flow-edges" style="width:${width}px;height:${height}px" viewBox="0 0 ${width} ${height}" aria-hidden="true"><defs><marker id="flow-arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6" fill="#a4b99b"/></marker></defs>${edges}</svg>${steps.map((step, index) => { const pos = positions.get(step.key); return `<button class="flow-node${step.key === state.teamStep ? " selected" : ""}" type="button" data-select-step="${e(step.key)}" aria-label="选择步骤：${e(step.title)}" style="left:${pos.x}px;top:${pos.y}px"><span class="flow-node-number">${index + 1}</span><span><strong>${e(step.title || "未命名步骤")}</strong><small>${e(agent(step.agent_id).name)}</small></span></button>`; }).join("")}</div>`;
  $("add-team-step").disabled = steps.length >= 12;
}
function renderStepProperties() {
  const steps = state.teamDraft?.steps || [], step = steps.find((item) => item.key === state.teamStep);
  if (!step) return;
  $("step-title").value = step.title;
  $("step-agent").value = step.agent_id;
  $("step-instructions").value = step.instructions;
  $("step-dependencies").innerHTML = steps.filter((item) => item.key !== step.key).map((item) => `<option value="${e(item.key)}"${(step.depends_on || []).includes(item.key) ? " selected" : ""}>${e(item.title || "未命名步骤")}</option>`).join("");
  $("remove-team-step").disabled = steps.length <= 1;
}
function updateSelectedStep() {
  const step = state.teamDraft?.steps.find((item) => item.key === state.teamStep);
  if (!step) return;
  step.title = $("step-title").value;
  step.agent_id = $("step-agent").value;
  step.instructions = $("step-instructions").value;
  step.depends_on = [...$("step-dependencies").selectedOptions].map((option) => option.value);
  renderWorkflow();
}
function addTeamStep() {
  if (!state.teamDraft || state.teamDraft.steps.length >= 12) return;
  updateSelectedStep();
  const keys = new Set(state.teamDraft.steps.map((item) => item.key)); let number = 1; while (keys.has(`step_${number}`)) number++;
  const step = { key: `step_${number}`, agent_id: "writer", title: `新步骤 ${number}`, instructions: "", depends_on: state.teamStep ? [state.teamStep] : [] };
  state.teamDraft.steps.push(step); state.teamStep = step.key;
  renderWorkflow(); renderStepProperties(); $("step-title").focus(); $("step-title").select();
}
function removeTeamStep() {
  if (!state.teamDraft || state.teamDraft.steps.length <= 1) return;
  const key = state.teamStep;
  state.teamDraft.steps = state.teamDraft.steps.filter((step) => step.key !== key).map((step) => ({ ...step, depends_on: step.depends_on.filter((id) => id !== key) }));
  state.teamStep = state.teamDraft.steps[0].key;
  renderWorkflow(); renderStepProperties();
}
async function saveTeam(event) {
  event.preventDefault(); if (!state.teamDraft) return;
  updateSelectedStep();
  const draft = state.teamDraft;
  try {
    stepDepths(draft.steps);
    if (draft.steps.some((step) => !step.title.trim() || !step.instructions.trim())) throw new Error("请为每个步骤填写名称和工作指令。");
    $("save-team").disabled = true;
    await api(draft.id ? `/templates/${encodeURIComponent(draft.id)}` : "/templates", { method: draft.id ? "PUT" : "POST", body: { name: $("team-name").value.trim(), description: $("team-description").value.trim(), steps: draft.steps, ...(draft.id ? { base_version: draft.version } : {}) } });
    $("team-dialog").close(); await bootstrap(); toast("工作流已保存");
  } catch (error) { $("team-save-state").textContent = "保存未完成，修改已保留"; toast(error.message, true); }
  finally { $("save-team").disabled = false; }
}
let pollRunning = false;
async function poll() { if (pollRunning || state.submitting || state.loading || document.hidden || (window.workspaceAccount && !window.workspaceAccount.ready)) return; pollRunning = true; const roomId = state.roomId, generation = state.roomGeneration, readGeneration = ++state.roomReadGeneration; try { const [boot, room] = await Promise.allSettled([bootstrap(), roomId ? api(`/rooms/${encodeURIComponent(roomId)}`) : Promise.resolve(null)]); if (room.status === "fulfilled" && room.value && state.roomId === roomId && state.roomGeneration === generation && readGeneration === state.roomReadGeneration && !state.submitting) { state.room = room.value; renderRoom(); } if (boot.status === "rejected") $("connection-status").textContent = "暂时无法连接本地服务"; if ($("connection-dialog").open && !state.executorBusy) await refreshLocalExecutor(); } finally { pollRunning = false; } }

$("new-chat").addEventListener("click", () => newChat());
$("conversation-search").addEventListener("input", (event) => { state.conversationSearch = event.target.value; renderSidebar(); });
$("toggle-contacts").addEventListener("click", () => { state.contactsExpanded = $("toggle-contacts").getAttribute("aria-expanded") !== "true"; renderSidebar(); });
$("open-more").addEventListener("click", () => setMore($("more-menu").hidden));
window.matchMedia("(max-width:760px)").addEventListener("change", () => { setSidebar(false); setMore(false); });
document.addEventListener("click", (event) => { if (!event.target.closest(".rail-more-wrap")) setMore(false); else if (event.target.closest("#more-menu button, #more-menu a")) setMore(false); });
document.addEventListener("keydown", (event) => {
  if ($("more-menu").hidden) return;
  if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setMore(false, true); }
  else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
    const items = [...$("more-menu").querySelectorAll("button,a")];
    const index = items.indexOf(document.activeElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    event.preventDefault(); items[next]?.focus();
  }
});
document.addEventListener("focusin", (event) => { if (!$("more-menu").hidden && !event.target.closest(".rail-more-wrap")) setMore(false); });
document.addEventListener("keydown", (event) => {
  if (event.key !== "Tab" || !$("sidebar").classList.contains("open") || document.querySelector("dialog[open]")) return;
  const controls = [...$("sidebar").querySelectorAll('button:not([disabled]),a[href],input:not([disabled])')].filter((item) => item.getClientRects().length);
  const first = controls[0], last = controls.at(-1);
  if (event.shiftKey && (document.activeElement === first || !$("sidebar").contains(document.activeElement))) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && (document.activeElement === last || !$("sidebar").contains(document.activeElement))) { event.preventDefault(); first?.focus(); }
});
document.querySelector(".rail-brand").addEventListener("click", (event) => { event.preventDefault(); newChat(); });
document.querySelector(".brand").addEventListener("click", (event) => { event.preventDefault(); newChat(); });
$("open-sidebar").addEventListener("click", () => setSidebar(true));
$("sidebar-shade").addEventListener("click", () => setSidebar(false, true));
$("composer").addEventListener("submit", sendMessage);
$("composer-input").addEventListener("input", () => { resizeInput(); saveDraft(); });
$("composer-input").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("composer").requestSubmit(); } });
$("composer-agent").addEventListener("change", () => { saveDraft(); renderIdentity(); renderSidebar(); });
$("clear-team").addEventListener("click", () => { state.selectedTemplate = null; $("selected-team").hidden = true; $("composer-agent").disabled = false; renderIdentity(); renderSidebar(); });
$("agent-search").addEventListener("input", () => { state.agentSearch = $("agent-search").value; renderAgentCards(); });
$("team-search").addEventListener("input", () => { state.teamSearch = $("team-search").value; renderTeamCards(); });
$("expand-workspace").addEventListener("click", () => { state.studioExpanded = !state.studioExpanded; applyPanel(); });
$("create-team").addEventListener("click", () => editTeam());
$("team-form").addEventListener("submit", saveTeam);
$("add-team-step").addEventListener("click", addTeamStep);
$("remove-team-step").addEventListener("click", removeTeamStep);
for (const name of ["step-title", "step-agent", "step-instructions", "step-dependencies"]) $(name).addEventListener(name === "step-agent" || name === "step-dependencies" ? "change" : "input", updateSelectedStep);
$("attach-source").addEventListener("click", openSourceForm);
$("add-source").addEventListener("click", openSourceForm);
$("source-form").addEventListener("submit", saveSource);
$("source-kind").addEventListener("change", sourceKindChanged);
$("source-file").addEventListener("change", importSourceFile);
$("edit-artifact").addEventListener("click", startArtifactEdit);
$("artifact-preview-tab").addEventListener("click", previewArtifactDraft);
$("copy-artifact-draft").addEventListener("click", () => copyText($("artifact-edit-content").value));
$("rebase-artifact-draft").addEventListener("click", rebaseArtifactDraft);
$("cancel-artifact-edit").addEventListener("click", cancelArtifactEdit);
$("artifact-edit-title").addEventListener("input", persistArtifactDraft);
$("artifact-edit-content").addEventListener("input", persistArtifactDraft);
$("save-artifact").addEventListener("click", saveArtifact);
$("approve-artifact").addEventListener("click", () => openReview("approved"));
$("request-artifact-changes").addEventListener("click", () => openReview("changes_requested"));
$("review-form").addEventListener("submit", saveReview);
$("create-agent").addEventListener("click", () => editAgent());
$("agent-form").addEventListener("submit", saveAgent);
$("workspace-toggle").addEventListener("click", () => { if (state.panelOpen) { state.panelOpen = false; applyPanel(); } else showPanel(state.panel); });
$("close-workspace").addEventListener("click", () => { state.panelOpen = false; applyPanel(); $("workspace-toggle").focus(); });
$("room-context").addEventListener("input", () => { state.contextDirty = $("room-context").value !== (state.room?.context || ""); $("context-state").textContent = state.contextDirty ? "有未保存的修改" : ""; window.dispatchEvent(new Event("workspace-room")); });
$("save-context").addEventListener("click", saveContext);
$("artifact-version").addEventListener("change", () => loadArtifact(state.selectedArtifact, Number($("artifact-version").value)));
$("connection-button").addEventListener("click", openConnection);
$("copy-connection").addEventListener("click", () => copyText($("connection-code").textContent));
document.addEventListener("click", (event) => { const button = event.target.closest("button"); if (!button || button.disabled) return; if (button.dataset.view) setView(button.dataset.view, { user: true }); if (button.dataset.agentFilter) { state.agentFilter = button.dataset.agentFilter; document.querySelectorAll("[data-agent-filter]").forEach((item) => item.classList.toggle("active", item === button)); renderAgentCards(); } if (button.dataset.teamFilter) { state.teamFilter = button.dataset.teamFilter; document.querySelectorAll("[data-team-filter]").forEach((item) => item.classList.toggle("active", item === button)); renderTeamCards(); } if (button.dataset.room) openRoom(button.dataset.room); if (button.dataset.template) newChat({ templateId: button.dataset.template }); if (button.dataset.startAgent) newChat({ agentId: button.dataset.startAgent }); if (button.dataset.selectContact) newChat({ agentId: button.dataset.selectContact === "coordinator" ? "auto" : button.dataset.selectContact }); if (button.dataset.editAgent) editAgent(button.dataset.editAgent); if (button.dataset.editTeam) editTeam(button.dataset.editTeam); if (button.dataset.selectStep) { updateSelectedStep(); state.teamStep = button.dataset.selectStep; renderWorkflow(); renderStepProperties(); } if (button.dataset.source) openSource(button.dataset.source); if (button.dataset.revokeSource) revokeSource(button.dataset.revokeSource, button); if (button.dataset.panel) showPanel(button.dataset.panel); if (button.dataset.showPanel) showPanel(button.dataset.showPanel); if (button.dataset.artifact) loadArtifact(button.dataset.artifact); if (button.hasAttribute("data-open-connection")) openConnection(); if (button.hasAttribute("data-close-dialog")) button.closest("dialog").close(); if (button.dataset.executorAction) controlLocalExecutor(button.dataset.executorAction); if (button.dataset.connectTab) { state.connectTab = button.dataset.connectTab; renderConnection(); } if (button.dataset.cancelRun) runAction(button.dataset.cancelRun, "cancel", button); if (button.dataset.retryRun) runAction(button.dataset.retryRun, "retry", button); if (button.dataset.copyMessage) { const message = state.room?.messages.find((item) => item.id === button.dataset.copyMessage); if (message) copyText(message.text); } });
document.addEventListener("keydown", (event) => { if (event.defaultPrevented) return; const dialog = document.querySelector("dialog[open]"); if (event.key === "Tab" && dialog) { const focusables = [...dialog.querySelectorAll('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')].filter((item) => item.getClientRects().length); if (focusables.length) { const first = focusables[0], last = focusables[focusables.length - 1]; if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) { event.preventDefault(); first.focus(); } } } if (event.key === "Escape" && !document.querySelector("dialog[open]")) { if ($( "sidebar").classList.contains("open")) setSidebar(false, true); else if (state.panelOpen) { state.panelOpen = false; applyPanel(); $("workspace-toggle").focus(); } } });
document.querySelector(".panel-tabs").addEventListener("keydown", (event) => { if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return; event.preventDefault(); const tabs = [...document.querySelectorAll("[data-panel]")]; let index = tabs.indexOf(document.activeElement); index = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length; tabs[index].focus(); showPanel(tabs[index].dataset.panel); });
window.addEventListener("hashchange", () => { const match = /^#room\/(.+)$/.exec(location.hash); if (match) openRoom(decodeURIComponent(match[1]), { fromHash: true }); else if (location.hash === "#new" && state.roomId) newChat(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
window.workspaceUI = { agentAvatar: avatar, getAgents: () => state.agents, getTemplates: () => state.templates, getTeams: () => state.teams, setView, getRooms: () => state.rooms, getView: () => state.view, getEditingAgent: () => state.editingAgent, openRoom, openArtifact: loadArtifact, showPanel, openSource, expandStudio: () => { state.studioExpanded = true; applyPanel(); }, getCurrentRoom: () => state.room, getSelectedArtifact: () => state.artifact ? { id: state.selectedArtifact, version: state.selectedVersion, title: state.artifact.title, kind: state.artifact.kind, task_id: state.artifact.task_id, modification_request_id: state.artifact.modification_request_id, source_version: state.artifact.source_version } : null, refreshRoom: async () => { const id = state.roomId; if (!id || state.loading) return; const readGeneration = ++state.roomReadGeneration; const room = await api(`/rooms/${encodeURIComponent(id)}`); if (state.roomId === id && readGeneration === state.roomReadGeneration) { state.room = room; renderRoom(); } }, refresh: bootstrap, toast };
Promise.resolve(window.workspaceAccountReady ?? true).then((allowed) => allowed ? bootstrap({ initial: true }) : undefined).catch((error) => { $("initial-loading").innerHTML = `<div class="page-error"><p>${e(error.message)}</p><button class="secondary-button" id="retry-bootstrap">重新连接</button></div>`; $("retry-bootstrap").addEventListener("click", () => location.reload()); });
setInterval(poll, 3500);

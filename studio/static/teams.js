"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const ui = () => window.workspaceUI;
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  const writable = () => document.documentElement.dataset.access !== "viewer";
  const key = (prefix) => `${prefix}-${crypto.randomUUID()}`;
  const group = { teams: [], search: "", filter: "active", listSignature: "", info: null, infoGeneration: 0, infoTab: "members", editor: null, opening: 0, targets: new Map(), requests: new Map(), mention: null, headerSignature: "", history: [], busy: false };
  const symbols = {
    plus: '<path d="M12 5v14M5 12h14"/>', close: '<path d="m6 6 12 12M18 6 6 18"/>',
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    members: '<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M16 5a3 3 0 0 1 0 6M18 15a5 5 0 0 1 3 5"/>',
    arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>', settings: '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/>',
    check: '<path d="m5 12 4 4L19 6"/>'
  };
  const svg = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${symbols[name] || symbols.members}</svg>`;
  const colors = ["#e6edf5", "#eee7f4", "#f5ebde", "#e4efe8", "#f3e5e7", "#e3eef0"];
  const agents = () => (ui()?.getAgents() || []).filter((item) => item.id !== "coordinator");
  const agentName = (id) => agents().find((item) => item.id === id)?.name || id;
  const currentTeam = () => knownTeam(ui()?.getCurrentRoom()?.team);
  const memberName = (team, id) => team?.members?.find((item) => item.agent_id === id)?.name || agentName(id);
  const selectedTargets = (id) => group.targets.get(id) || [];
  const time = (value) => value ? new Date(value).toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
  const face = (id, name) => { if (ui()?.agentAvatar) return ui().agentAvatar(id, "group-avatar"); let index = 0; for (const char of id) index = (index + char.charCodeAt(0)) % colors.length; return `<span class="group-avatar" style="--group-avatar:${colors[index]}" aria-hidden="true">${esc((name || agentName(id)).slice(0, 1))}</span>`; };
  const faces = (team, limit = 4) => `<span class="group-avatar-stack" aria-hidden="true">${(team.members || []).slice(0, limit).map((item) => face(item.agent_id, item.name)).join("")}${team.member_ids.length > limit ? `<span class="group-avatar group-avatar-more">+${team.member_ids.length - limit}</span>` : ""}</span>`;
  const notify = (message, error = false) => ui()?.toast(message, error);
  async function request(path, { method = "GET", body } = {}) {
    const response = await fetch(`/api/hub${path}`, { method, headers: body === undefined ? {} : { "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    let result; try { result = await response.json(); } catch { throw new Error(`服务返回了无效响应（${response.status}）`); }
    if (!response.ok) { const error = new Error(result.error || `请求失败（${response.status}）`); error.status = response.status; if (response.status === 401) window.dispatchEvent(new Event("workspace-session-expired")); throw error; }
    return result;
  }

  byId("main-content").insertAdjacentHTML("beforeend", `
    <section class="library-view persistent-teams-view" id="teams-view" hidden>
      <div class="library-toolbar"><div class="library-switch"><button data-view="agents">Agent</button><button class="active" data-view="teams">协作团队</button><button data-view="workflows">工作流</button></div><label class="library-search">${svg("search")}<input id="group-search" aria-label="搜索团队" placeholder="搜索团队…"></label><button class="primary-button group-mutation" id="create-group" aria-label="创建团队">${svg("plus")}创建团队</button></div>
      <div class="library-filters" aria-label="团队筛选"><button class="active" data-group-filter="active">全部团队</button><button data-group-filter="archived">已归档</button><span id="group-result-count"></span></div>
      <div class="persistent-team-grid" id="persistent-team-grid"></div>
    </section>`);
  document.querySelector(".topbar-left").insertAdjacentHTML("beforeend", '<button class="group-header" id="group-header" aria-label="团队信息" hidden></button>');
  document.querySelector(".composer-tools").insertAdjacentHTML("beforeend", '<div class="group-composer-tools" id="group-composer-tools" hidden><button type="button" class="group-mention-button" id="open-group-mentions" aria-label="点名 Agent"><span aria-hidden="true">@</span><span id="group-mention-label">点名 Agent</span></button><button type="button" class="group-clear-targets" id="clear-group-mentions" aria-label="清除点名" hidden>清除</button><span class="group-mode-label" id="group-mode-label"></span></div>');
  byId("composer-input").insertAdjacentHTML("beforebegin", '<div id="group-target-chips" class="group-target-chips" hidden></div>');
  byId("messages").insertAdjacentHTML("beforebegin", '<div class="group-chat-empty" id="group-chat-empty" hidden></div>');
  document.body.insertAdjacentHTML("beforeend", `
    <dialog class="app-dialog group-editor-dialog" id="group-editor-dialog" aria-labelledby="group-editor-title"><form id="group-editor-form"><header class="dialog-header"><h2 id="group-editor-title">创建团队</h2><button type="button" class="icon-button" data-close-dialog aria-label="关闭">${svg("close")}</button></header>
      <div class="group-editor-content" id="group-editor-content"><div class="group-definition"><label for="group-name">团队名称</label><input id="group-name" aria-label="团队名称" required maxlength="120" placeholder="给团队起一个名字"><label for="group-description">团队说明</label><textarea id="group-description" aria-label="团队说明" rows="2" maxlength="4000" placeholder="这个团队负责什么（可选）"></textarea></div>
        <div class="group-member-heading"><h3>Agent 成员</h3><span id="group-member-count"></span></div><div class="group-member-options" id="group-member-options"></div>
        <label class="group-routing-setting"><span><strong>自动分工</strong><small>开启后，未点名的消息会交给协调员分配；关闭后只保存消息。</small></span><input type="checkbox" role="switch" id="group-routing" aria-label="自动分工"></label>
        <p class="group-form-error" id="group-form-error" role="status" hidden></p>
      </div><footer class="dialog-footer">        <div class="group-removal-warning" id="group-removal-warning" role="status" hidden></div>
        <div class="group-conflict" id="group-conflict" role="status" hidden><p>团队已有新的修改。你的选择与文字已保留。</p><button type="button" class="small-button" id="group-read-latest">保留选择，读取最新成员</button></div>
<span id="group-editor-state"></span><button type="button" class="secondary-button" data-close-dialog>取消</button><button type="submit" class="primary-button" id="save-group">创建并打开群聊</button></footer></form></dialog>
    <dialog class="app-dialog group-info-dialog" id="group-info-dialog" aria-labelledby="group-info-title"><header class="dialog-header"><h2 id="group-info-title">团队信息</h2><button type="button" class="icon-button" data-close-dialog aria-label="关闭">${svg("close")}</button></header>
      <div class="group-info-content"><div class="group-info-identity"><div id="group-info-avatars"></div><h3 id="group-info-name"></h3><p id="group-info-description"></p><span id="group-info-count"></span></div>
        <div class="group-info-actions"><button class="secondary-button" id="group-info-open">打开群聊 ${svg("arrow")}</button><button class="secondary-button group-mutation" id="group-info-edit" aria-label="编辑团队成员">${svg("members")}编辑成员</button></div>
        <div class="group-info-tabs" role="tablist" aria-label="团队信息内容"><button class="active" id="group-tab-members" role="tab" aria-selected="true" aria-controls="group-info-members-panel" data-group-tab="members">成员</button><button id="group-tab-history" role="tab" aria-selected="false" aria-controls="group-info-history-panel" data-group-tab="history">变更记录</button></div>
        <section id="group-info-members-panel" role="tabpanel" aria-labelledby="group-tab-members"><div id="group-info-members" class="group-info-members"></div><label class="group-routing-setting"><span><strong>自动分工</strong><small id="group-info-routing-note"></small></span><input type="checkbox" role="switch" id="group-info-routing" aria-label="自动分工"></label><p class="group-info-status" id="group-info-status"></p></section>
        <section id="group-info-history-panel" role="tabpanel" aria-labelledby="group-tab-history" hidden><div id="group-info-history" class="group-history"></div><button class="text-button" id="group-history-more" hidden>更多记录</button></section>
        <details class="group-archive group-mutation" id="group-archive"><summary>归档团队</summary><p>归档会停止这个团队所有未完成的轮次。群聊、执行记录和成果仍可查看。</p><button class="group-danger-button" id="archive-group">确认归档团队</button></details>
      </div></dialog>
    <dialog class="app-dialog group-mention-dialog" id="group-mention-dialog" aria-labelledby="group-mention-title"><form id="group-mention-form"><header class="dialog-header"><h2 id="group-mention-title">点名 Agent</h2><button type="button" class="icon-button" data-close-dialog aria-label="关闭">${svg("close")}</button></header><div class="group-mention-content"><p class="group-picker-note">选择本次要交给谁，最多 12 位。</p><div class="group-member-options" id="group-mention-options"></div><p class="group-form-error" id="group-mention-error" role="status" hidden></p></div><footer class="dialog-footer"><button type="button" class="secondary-button" id="mention-select-none">清除点名</button><button type="submit" class="primary-button">确认点名</button></footer></form></dialog>`);

  function setError(id, message) { byId(id).textContent = message || ""; byId(id).hidden = !message; }
  function teamById(id) { return group.teams.find((item) => item.id === id); }
  function renderDirectory() {
    const items = group.teams.filter((team) => (!!team.archived_at === (group.filter === "archived")) && (!group.search || `${team.name} ${team.description}`.toLocaleLowerCase().includes(group.search.toLocaleLowerCase())));
    byId("group-result-count").textContent = `${items.length} 个团队`;
    const signature = JSON.stringify({ items, writable: writable() });
    if (signature === group.listSignature) return; group.listSignature = signature;
    const focused = document.activeElement?.closest("[data-open-group], [data-group-info]");
    const focusId = focused?.dataset.openGroup || focused?.dataset.groupInfo, focusType = focused?.hasAttribute("data-open-group") ? "open-group" : "group-info";
    byId("persistent-team-grid").innerHTML = items.length ? items.map((team) => `<article class="persistent-team-card" data-team-id="${esc(team.id)}"><div class="persistent-team-card-top">${faces(team)}<button class="icon-button" data-group-info="${esc(team.id)}" aria-label="团队信息：${esc(team.name)}">${svg("settings")}</button></div><h2>${esc(team.name)}</h2><p>${esc(team.description || team.members.map((item) => item.name).join("、"))}</p><div class="persistent-team-card-meta"><span>${team.member_ids.length} 位 Agent</span><span>${team.archived_at ? "已归档" : team.routing_mode === "auto" ? "自动分工" : "仅点名响应"}</span></div><div class="persistent-team-card-footer"><span>${team.active_run_ids.length ? `${team.active_run_ids.length} 轮未结束` : ""}</span><button class="secondary-button" data-open-group="${esc(team.id)}" aria-label="打开群聊：${esc(team.name)}">打开群聊 ${svg("arrow")}</button></div></article>`).join("") : `<div class="group-directory-empty"><span>${svg("members")}</span><h2>${group.search ? "没有找到匹配的团队" : group.filter === "archived" ? "没有已归档的团队" : "还没有团队"}</h2><p>${group.search ? "试试其他名称。" : group.filter === "archived" ? "归档后，群聊和成果仍会保留。" : writable() ? "选择一起协作的 Agent，开启一个持续的群聊。" : "你可以查看这个空间的团队与群聊。"}</p>${!group.search && group.filter === "active" && writable() ? '<button class="primary-button" data-create-group>创建团队</button>' : ""}</div>`;
    if (focusId) [...byId("persistent-team-grid").querySelectorAll(`[data-${focusType}]`)].find((node) => node.dataset[focusType === "open-group" ? "openGroup" : "groupInfo"] === focusId)?.focus();
  }
  function knownTeam(team) {
    if (!team) return null;
    const known = [group.teams.find((item) => item.id === team.id), group.info, ui()?.getCurrentRoom()?.team].filter((item) => item?.id === team.id).sort((a, b) => b.version - a.version)[0];
    return known && known.version > team.version ? known : team;
  }
  function updateGroup(team) {
    team = knownTeam(team);
    const index = group.teams.findIndex((item) => item.id === team.id);
    if (index < 0) group.teams.unshift(team); else group.teams[index] = team;
    if (group.info?.id === team.id) { group.info = team; renderInfo(); }
    renderDirectory();
  }
  async function refresh() { await ui().refresh(); await ui().refreshRoom(); }
  async function openGroup(id, button) {
    const generation = ++group.opening; if (button) button.disabled = true;
    try { const team = await request(`/teams/${encodeURIComponent(id)}`); if (generation !== group.opening) return; updateGroup(team); if (byId("group-info-dialog").open) byId("group-info-dialog").close(); await ui().openRoom(team.primary_room_id); }
    catch (error) { notify(error.message, true); } finally { if (button?.isConnected) button.disabled = false; }
  }
  function renderEditorChoices() {
    const draft = group.editor; if (!draft) return;
    byId("group-member-options").innerHTML = agents().map((item) => `<label class="group-member-choice">${face(item.id, item.name)}<span><strong>${esc(item.name)}</strong><small>${esc(item.description)}</small></span><input type="checkbox" name="group-member" value="${esc(item.id)}" aria-label="选择 Agent：${esc(item.name)}"${draft.members.has(item.id) ? " checked" : ""}></label>`).join("");
    updateEditorSummary();
  }
  function updateEditorSummary() {
    const draft = group.editor; if (!draft) return;
    byId("group-member-count").textContent = `${draft.members.size} / 24`;
    const removed = draft.baseline.filter((id) => !draft.members.has(id));
    byId("group-removal-warning").hidden = !removed.length;
    byId("group-removal-warning").textContent = removed.length ? `将移除 ${removed.slice(0, 2).map((id) => agentName(id).slice(0, 12)).join("、")}${removed.length > 2 ? ` 等 ${removed.length} 位 Agent` : ""}。涉及这些成员的未完成轮次会停止，已完成成果保留。` : "";
  }
  function openEditor(team = null) {
    if (!writable() || team?.archived_at) return;
    if (byId("group-info-dialog").open) byId("group-info-dialog").close();
    group.editor = { id: team?.id || null, version: team?.version, baseline: [...(team?.member_ids || [])], members: new Set(team?.member_ids || []), requestKey: key("team"), inputSignature: null, busy: false, conflict: false };
    byId("group-editor-title").textContent = team ? "编辑团队" : "创建团队";
    byId("group-name").value = team?.name || ""; byId("group-description").value = team?.description || ""; byId("group-routing").checked = team?.routing_mode !== "mention";
    byId("save-group").textContent = team ? "保存成员" : "创建并打开群聊"; byId("save-group").disabled = false;
    byId("group-editor-state").textContent = team ? `版本 ${team.version}` : "";
    byId("group-conflict").hidden = true; byId("group-conflict").querySelector("p").textContent = "团队已有新的修改。你的选择与文字已保留。"; byId("group-read-latest").disabled = false; setError("group-form-error", "");
    byId("group-editor-form").querySelectorAll("input,textarea").forEach((input) => { input.disabled = false; }); byId("group-editor-content").removeAttribute("aria-busy"); renderEditorChoices(); byId("group-editor-dialog").showModal(); byId("group-name").focus();
  }
  async function saveGroup(event) {
    event.preventDefault(); const draft = group.editor; if (!draft || draft.busy || !writable()) return;
    if (!draft.members.size || draft.members.size > 24) { setError("group-form-error", "请选择 1 至 24 位 Agent。"); return; }
    if (draft.conflict) { setError("group-form-error", "请先读取最新成员，核对后再保存。"); return; }
    const body = { name: byId("group-name").value.trim(), description: byId("group-description").value.trim(), member_ids: [...draft.members], routing_mode: byId("group-routing").checked ? "auto" : "mention" };
    const signature = JSON.stringify(body); if (draft.inputSignature && draft.inputSignature !== signature) draft.requestKey = key("team"); draft.inputSignature = signature;
    if (draft.id) body.base_version = draft.version; else body.request_key = draft.requestKey;
    draft.busy = true; byId("save-group").disabled = true; byId("group-editor-content")?.setAttribute("aria-busy", "true"); byId("group-editor-form").querySelectorAll("input,textarea").forEach((input) => { input.disabled = true; }); setError("group-form-error", "");
    try {
      const team = await request(draft.id ? `/teams/${encodeURIComponent(draft.id)}` : "/teams", { method: draft.id ? "PUT" : "POST", body });
      updateGroup(team); const owned = group.editor === draft && byId("group-editor-dialog").open;
      if (owned) byId("group-editor-dialog").close();
      await refresh();
      notify(team.cancelled_run_ids?.length ? `成员已更新，${team.cancelled_run_ids.length} 轮未完成任务已停止。已完成的成果保留。` : draft.id ? "团队已更新" : "团队已创建");
      if (owned && group.editor === draft) { if (draft.id) await openInfo(team.id); else await openGroup(team.id); }
    } catch (error) {
      if (group.editor === draft) { setError("group-form-error", error.status === 409 ? "保存未完成，请核对最新成员后再保存。" : error.message); if (error.status === 409 && draft.id) { draft.conflict = true; byId("group-conflict").hidden = false; } }
      else notify(error.message, true);
    } finally { draft.busy = false; if (group.editor === draft) { byId("save-group").disabled = draft.conflict; byId("group-editor-content").removeAttribute("aria-busy"); byId("group-editor-form").querySelectorAll("input,textarea").forEach((input) => { input.disabled = false; }); } }
  }
  async function readLatestMembers() {
    const draft = group.editor; if (!draft?.id || draft.busy) return; const button = byId("group-read-latest"); button.disabled = true;
    try { const team = await request(`/teams/${encodeURIComponent(draft.id)}`); if (group.editor !== draft) return; if (team.archived_at) { setError("group-form-error", "团队已归档。你的草稿仍保留，无法继续保存到这个团队。"); return; } draft.version = team.version; draft.baseline = [...team.member_ids]; draft.conflict = false; updateEditorSummary(); byId("group-editor-state").textContent = `已读取版本 ${team.version}，请核对后保存`; byId("group-conflict").querySelector("p").textContent = "已读取最新成员。你的选择与文字保持不变，核对后请再次保存。"; byId("save-group").disabled = false; setError("group-form-error", ""); }
    catch (error) { if (group.editor === draft) setError("group-form-error", error.message); }
    finally { if (group.editor === draft) button.disabled = false; }
  }

  function renderInfo() {
    const team = group.info; if (!team) return;
    const identitySignature = JSON.stringify([team.name, team.description, team.members, team.archived_at]);
    if (byId("group-info-avatars").dataset.signature !== identitySignature) {
      byId("group-info-avatars").dataset.signature = identitySignature; byId("group-info-avatars").innerHTML = faces(team);
      byId("group-info-name").textContent = team.name; byId("group-info-description").textContent = team.description; byId("group-info-description").hidden = !team.description;
      byId("group-info-count").textContent = `${team.member_ids.length} 位 Agent${team.archived_at ? " · 已归档" : ""}`;
      byId("group-info-members").innerHTML = team.members.map((item) => `<article data-team-member-id="${esc(item.agent_id)}">${face(item.agent_id, item.name)}<div><strong>${esc(item.name)}</strong><p>${esc(item.description)}</p></div></article>`).join("");
    }
    byId("group-info-routing").checked = team.routing_mode === "auto"; byId("group-info-routing").disabled = !writable() || !!team.archived_at || group.busy;
    byId("group-info-routing-note").textContent = team.routing_mode === "auto" ? "未点名的消息在发送后交给协调员分配。" : "未点名的消息只保存；点名后才创建任务。";
    byId("group-info-edit").hidden = !!team.archived_at; byId("group-archive").hidden = !!team.archived_at;
    byId("group-info-status").textContent = team.archived_at ? "已归档，历史记录与成果仍可查看。" : team.active_run_ids.length ? `${team.active_run_ids.length} 轮任务尚未结束` : "";
  }
  async function openInfo(id) {
    const generation = ++group.infoGeneration;
    try { const team = await request(`/teams/${encodeURIComponent(id)}`); if (generation !== group.infoGeneration) return; group.info = knownTeam(team); group.infoTab = "members"; group.history = []; byId("group-archive").open = false; renderInfo(); showInfoTab("members"); if (!byId("group-info-dialog").open) byId("group-info-dialog").showModal(); }
    catch (error) { notify(error.message, true); }
  }
  function showInfoTab(tab) {
    group.infoTab = tab;
    for (const name of ["members", "history"]) { const button = byId(`group-tab-${name}`); button.classList.toggle("active", name === tab); button.setAttribute("aria-selected", String(name === tab)); button.tabIndex = name === tab ? 0 : -1; byId(`group-info-${name}-panel`).hidden = name !== tab; }
    if (tab === "history") loadHistory(false);
  }
  async function loadHistory(more = false) {
    const id = group.info?.id, generation = group.infoGeneration; if (!id) return;
    const button = byId("group-history-more"); button.disabled = true;
    if (!more) { group.history = []; byId("group-info-history").innerHTML = '<p class="group-info-status">正在读取…</p>'; }
    try { const result = await request(`/teams/${encodeURIComponent(id)}/history?after=${more ? group.historyCursor || 0 : 0}&limit=50`); if (id !== group.info?.id || generation !== group.infoGeneration) return; group.history.push(...result.history); group.historyCursor = result.cursor; button.hidden = !result.has_more;
      byId("group-info-history").innerHTML = [...group.history].reverse().map((entry) => `<article><strong>版本 ${Number(entry.version)}</strong><small>${esc(entry.actor_name || "空间成员")} · ${esc(time(entry.created_at))}</small>${entry.added_member_ids?.length ? `<p>加入：${esc(entry.added_member_ids.map(agentName).join("、"))}</p>` : ""}${entry.removed_member_ids?.length ? `<p>移除：${esc(entry.removed_member_ids.map(agentName).join("、"))}</p>` : ""}${entry.cancelled_run_ids?.length ? `<p>已停止 ${entry.cancelled_run_ids.length} 轮未完成任务</p>` : ""}<span>${entry.archived_at ? "已归档" : entry.routing_mode === "auto" ? "自动分工" : "仅点名响应"}</span></article>`).join("") || '<p class="group-info-status">暂无变更记录</p>';
    } catch (error) { if (id === group.info?.id) byId("group-info-history").textContent = error.message; } finally { button.disabled = false; }
  }
  async function changeRouting() {
    const team = group.info; if (!team || group.busy || !writable()) return; const mode = byId("group-info-routing").checked ? "auto" : "mention"; group.busy = true; byId("group-info-routing").disabled = true;
    try { const updated = await request(`/teams/${encodeURIComponent(team.id)}`, { method: "PUT", body: { base_version: team.version, routing_mode: mode } }); updateGroup(updated); await refresh(); }
    catch (error) { notify(error.status === 409 ? "团队已被其他成员修改，请核对最新设置后重试。" : error.message, true); try { const latest = await request(`/teams/${encodeURIComponent(team.id)}`); updateGroup(latest); } catch {} }
    finally { group.busy = false; if (group.info?.id === team.id) renderInfo(); }
  }
  async function archiveGroup() {
    const team = group.info; if (!team || group.busy || !writable()) return; group.busy = true; byId("archive-group").disabled = true;
    try { const archived = await request(`/teams/${encodeURIComponent(team.id)}`, { method: "DELETE", body: { base_version: team.version } }); updateGroup(archived); await refresh(); notify(`团队已归档${archived.cancelled_run_ids?.length ? `，${archived.cancelled_run_ids.length} 轮未完成任务已停止` : ""}。`); }
    catch (error) { notify(error.message, true); }
    finally { group.busy = false; byId("archive-group").disabled = false; renderInfo(); }
  }

  function renderComposer() {
    const room = ui()?.getCurrentRoom(), team = knownTeam(room?.team), visible = !!team && ui()?.getView() === "chat";
    byId("chat-view").classList.toggle("team-empty", !!team && !room.messages.length); byId("group-header").hidden = !visible; byId("view-title").hidden = ui()?.getView() === "chat"; byId("chat-identity").hidden = visible || ui()?.getView() !== "chat";
    byId("group-composer-tools").hidden = !team; document.querySelector(".composer-tools .agent-picker").hidden = !!team;
    byId("composer-input").disabled = !!team?.archived_at;
    if (!team) { byId("group-target-chips").hidden = true; byId("group-chat-empty").hidden = true; byId("composer-caption").textContent = "Shift + Enter 换行"; return; }
    const headerSignature = JSON.stringify([team.id, team.name, team.members]);
    if (headerSignature !== group.headerSignature) { group.headerSignature = headerSignature; byId("group-header").innerHTML = `${faces(team, 3)}<span><strong>${esc(team.name)}</strong><small>${team.member_ids.length} 位 Agent</small></span><span class="group-header-chevron" aria-hidden="true">⌄</span>`; }
    const ids = selectedTargets(room.id), missing = ids.filter((id) => !team.member_ids.includes(id));
    byId("group-mention-label").textContent = ids.length ? `已点名 ${ids.length} 位` : "点名 Agent"; byId("clear-group-mentions").hidden = !ids.length;
    byId("group-mode-label").textContent = ids.length ? "" : team.routing_mode === "auto" ? "自动分工" : "仅点名响应";
    byId("open-group-mentions").disabled = !!team.archived_at;
    byId("group-target-chips").hidden = !ids.length;
    const chipSignature = JSON.stringify([ids, team.members]);
    if (byId("group-target-chips").dataset.signature !== chipSignature) { byId("group-target-chips").dataset.signature = chipSignature; byId("group-target-chips").innerHTML = ids.map((id) => `<span class="group-target-chip${missing.includes(id) ? " removed" : ""}">@${esc(memberName(team, id))}${missing.includes(id) ? " · 已移除" : ""}<button type="button" data-remove-target="${esc(id)}" aria-label="取消点名：${esc(memberName(team, id))}">×</button></span>`).join(""); }
    byId("composer-caption").textContent = team.archived_at ? "团队已归档，历史记录与成果仍可查看" : missing.length ? room.runs[room.runs.length - 1]?.cancel_reason === "team_members_changed" ? "本轮已停止，请调整点名后继续" : "有点名成员已移除，请调整后发送" : ids.length ? "本次需求将分别交给已点名的 Agent" : team.routing_mode === "auto" ? "未点名时，发送后自动分工" : "未点名的消息只保存到群聊";
    if (team.archived_at || missing.length) byId("send-button").disabled = true;
    byId("group-chat-empty").hidden = !!room.messages.length;
    if (!room.messages.length) { const signature = JSON.stringify([team.name, team.description, team.members, team.archived_at, team.created_at, team.created_by_name]); if (byId("group-chat-empty").dataset.signature !== signature) { byId("group-chat-empty").dataset.signature = signature; byId("group-chat-empty").innerHTML = `<div class="group-established" data-team-created="${esc(team.id)}"><time datetime="${esc(team.created_at)}">${esc(time(team.created_at))}</time><p>${esc(team.created_by_name || "空间成员")}创建了团队</p></div>${faces(team)}<h1>${esc(team.name)}</h1><p class="group-empty-members">${esc(team.members.map((member) => member.name).join("、"))}</p>${team.description ? `<p>${esc(team.description)}</p>` : ""}${team.archived_at ? "<p>这个团队已归档</p>" : ""}`; } }
    if (group.mention && byId("group-mention-dialog").open && group.mention.roomId === room.id) refreshMentionRoster(team);
  }
  function renderMentions(team) {
    const draft = group.mention;
    byId("group-mention-options").innerHTML = team.members.map((item) => `<label class="group-member-choice">${face(item.agent_id, item.name)}<span><strong>${esc(item.name)}</strong><small>${esc(item.description)}</small></span><input type="checkbox" name="mention-member" value="${esc(item.agent_id)}" aria-label="点名：${esc(item.name)}"${draft.ids.has(item.agent_id) ? " checked" : ""}></label>`).join("");
    draft.rosterSignature = JSON.stringify(team.members);
  }
  function refreshMentionRoster(team) {
    const draft = group.mention; if (draft.rosterSignature === JSON.stringify(team.members)) return;
    const hadRemoved = [...draft.ids].some((id) => !team.member_ids.includes(id));
    for (const id of draft.ids) if (!team.member_ids.includes(id)) draft.ids.delete(id);
    const focusedId = document.activeElement?.name === "mention-member" ? document.activeElement.value : null;
    renderMentions(team); setError("group-mention-error", hadRemoved ? "团队成员已更新，已移除的成员不能继续点名。请核对后确认。" : "团队成员已更新，请核对后确认。");
    if (focusedId) ([...byId("group-mention-options").querySelectorAll("input")].find((input) => input.value === focusedId) || byId("group-mention-dialog").querySelector('[type="submit"]')).focus();
  }
  function openMentions() {
    const room = ui().getCurrentRoom(), team = knownTeam(room?.team); if (!team || team.archived_at || !writable()) return;
    group.mention = { roomId: room.id, ids: new Set(selectedTargets(room.id).filter((id) => team.member_ids.includes(id))) };
    setError("group-mention-error", ""); renderMentions(team); byId("group-mention-dialog").showModal();
  }
  function confirmMentions(event) {
    event.preventDefault(); const room = ui().getCurrentRoom(), draft = group.mention;
    if (!draft || room?.id !== draft.roomId || !room.team || room.team.archived_at) { setError("group-mention-error", "群聊已变更，请关闭后重新选择。"); return; }
    if (draft.ids.size > 12) { setError("group-mention-error", "每条消息最多点名 12 位 Agent。"); return; }
    if ([...draft.ids].some((id) => !room.team.member_ids.includes(id))) { setError("group-mention-error", "有成员已移除，请重新选择。"); return; }
    group.targets.set(room.id, [...draft.ids]); byId("group-mention-dialog").close(); renderComposer(); byId("composer-input").dispatchEvent(new Event("input", { bubbles: true })); byId("composer-input").focus();
  }
  function composePayload(room, text) {
    const team = knownTeam(room.team); if (!team || team.archived_at) throw new Error("团队已归档，无法发送新消息。");
    const ids = selectedTargets(room.id); if (ids.some((id) => !team.member_ids.includes(id))) throw new Error("有点名成员已移除，请调整后发送。");
    const signature = JSON.stringify({ text, target_agent_ids: [...ids] });
    let pending = group.requests.get(room.id);
    if (!pending || pending.signature !== signature) { pending = { signature, body: { target_agent_ids: [...ids], base_team_version: team.version, request_key: key("message") } }; group.requests.set(room.id, pending); }
    return { ...pending.body, target_agent_ids: [...pending.body.target_agent_ids] };
  }
  function messageSent(roomId, body) {
    if (!roomId || !body.request_key) return;
    if (group.requests.get(roomId)?.body.request_key === body.request_key) group.requests.delete(roomId);
    if (JSON.stringify(selectedTargets(roomId)) === JSON.stringify(body.target_agent_ids || [])) group.targets.delete(roomId);
  }
  function messageFailed(roomId, body, error) {
    if (error.status >= 400 && error.status < 500 && group.requests.get(roomId)?.body.request_key === body.request_key) group.requests.delete(roomId);
  }
  function messageRoutingHTML(message) {
    const routing = message.routing; if (!routing?.team_id) return "";
    const label = routing.mode === "direct" ? `交给 ${routing.target_agent_ids.map(agentName).join("、")}` : routing.mode === "auto" ? "自动分工" : routing.mode === "note" ? "群聊消息" : "工作流";
    return `<div class="group-message-routing" data-routing-mode="${esc(routing.mode)}">${esc(label)}</div>`;
  }

  byId("create-group").addEventListener("click", () => openEditor());
  byId("group-search").addEventListener("input", (event) => { group.search = event.target.value; renderDirectory(); });
  byId("group-editor-form").addEventListener("submit", saveGroup);
  byId("group-member-options").addEventListener("change", (event) => { if (event.target.name !== "group-member" || !group.editor) return; event.target.checked ? group.editor.members.add(event.target.value) : group.editor.members.delete(event.target.value); updateEditorSummary(); });
  byId("group-read-latest").addEventListener("click", readLatestMembers);
  byId("group-info-open").addEventListener("click", (event) => group.info && openGroup(group.info.id, event.currentTarget));
  byId("group-info-edit").addEventListener("click", () => openEditor(group.info));
  byId("group-info-routing").addEventListener("change", changeRouting);
  byId("archive-group").addEventListener("click", archiveGroup);
  byId("group-history-more").addEventListener("click", () => loadHistory(true));
  byId("group-header").addEventListener("click", () => { const team = currentTeam(); if (team) openInfo(team.id); });
  byId("open-group-mentions").addEventListener("click", openMentions);
  byId("group-mention-form").addEventListener("submit", confirmMentions);
  byId("group-mention-options").addEventListener("change", (event) => { if (event.target.name !== "mention-member" || !group.mention) return; event.target.checked ? group.mention.ids.add(event.target.value) : group.mention.ids.delete(event.target.value); });
  byId("mention-select-none").addEventListener("click", () => { if (!group.mention) return; group.mention.ids.clear(); byId("group-mention-options").querySelectorAll("input").forEach((input) => { input.checked = false; }); });
  byId("clear-group-mentions").addEventListener("click", () => { const id = ui().getCurrentRoom()?.id; if (id) group.targets.delete(id); renderComposer(); byId("composer-input").dispatchEvent(new Event("input", { bubbles: true })); });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button"); if (!button || button.disabled) return;
    if (button.hasAttribute("data-create-group")) openEditor();
    if (button.dataset.openGroup) openGroup(button.dataset.openGroup, button);
    if (button.dataset.groupInfo) openInfo(button.dataset.groupInfo);
    if (button.dataset.groupFilter) { group.filter = button.dataset.groupFilter; document.querySelectorAll("[data-group-filter]").forEach((item) => item.classList.toggle("active", item === button)); renderDirectory(); }
    if (button.dataset.groupTab) showInfoTab(button.dataset.groupTab);
    if (button.dataset.removeTarget) { const id = ui().getCurrentRoom()?.id; if (id) group.targets.set(id, selectedTargets(id).filter((target) => target !== button.dataset.removeTarget)); renderComposer(); byId("composer-input").dispatchEvent(new Event("input", { bubbles: true })); }
  });
  document.querySelector(".group-info-tabs").addEventListener("keydown", (event) => { if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return; event.preventDefault(); const tab = event.key === "Home" ? "members" : event.key === "End" ? "history" : group.infoTab === "members" ? "history" : "members"; showInfoTab(tab); byId(`group-tab-${tab}`).focus(); });
  byId("composer-input").addEventListener("input", () => { const room = ui().getCurrentRoom(); if (room?.team && (room.team.archived_at || selectedTargets(room.id).some((id) => !room.team.member_ids.includes(id)))) byId("send-button").disabled = true; });
  window.addEventListener("workspace-data", () => { group.teams = ui().getTeams().map(knownTeam); renderDirectory(); if (group.info) { const current = teamById(group.info.id); if (current && current.version >= group.info.version) { group.info = current; renderInfo(); } } });
  window.addEventListener("workspace-room", renderComposer); window.addEventListener("workspace-view", renderComposer); window.addEventListener("workspace-composer", renderComposer);
  window.workspaceTeams = { composePayload, messageSent, messageFailed, messageRoutingHTML };
  renderDirectory(); renderComposer();
})();

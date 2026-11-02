"use strict";

(() => {
  const ui = () => window.workspaceUI;
  const $ = (id) => document.getElementById(id);
  const e = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const icons = { skill: '<path d="M4 3h13l3 3v15H4V3Zm4 5h8M8 12h8M8 16h5"/>', clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', plus: '<path d="M12 5v14M5 12h14"/>', search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>', close: '<path d="m6 6 12 12M18 6 6 18"/>' };
  const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.skill}</svg>`;
  const extension = { skills: [], schedules: [], skillSearch: "", skillFilter: "active", scheduleFilter: "active", editingSkill: null, skillLatest: null, binding: null, editingSchedule: null, lastLoaded: {}, loading: new Set() };
  const writable = () => document.documentElement.dataset.access !== "viewer";
  const stamp = (value) => { if (!value) return "—"; const date = new Date(value); return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); };
  const intervalName = (seconds) => seconds === 604800 ? "每周" : seconds === 86400 ? "每天" : seconds === 3600 ? "每小时" : seconds % 86400 === 0 ? `每 ${seconds / 86400} 天` : seconds % 3600 === 0 ? `每 ${seconds / 3600} 小时` : `每 ${seconds / 60} 分钟`;
  const notify = (message, error = false) => ui()?.toast(message, error);
  async function api(path, { method = "GET", body } = {}) { const response = await fetch(`/api/hub${path}`, { method, headers: body === undefined ? {} : { "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) }); let data; try { data = await response.json(); } catch { throw new Error("服务返回了无效响应。"); } if (!response.ok) { const error = new Error(data.error || "操作未完成。"); error.status = response.status; throw error; } return data; }
  const nav = [{ id: "skills", name: "技能库", icon: "skill" }, { id: "schedules", name: "周期任务", icon: "clock" }];
  document.getElementById("more-menu").insertAdjacentHTML("afterbegin", nav.map((item) => `<button class="nav-item" data-view="${item.id}" aria-label="${item.name}">${icon(item.icon)}${item.name}</button>`).join(""));
  document.querySelector(".main-nav").insertAdjacentHTML("beforeend", nav.map((item) => `<button class="nav-item" data-view="${item.id}">${icon(item.icon)}${item.name}</button>`).join(""));
  $("main-content").insertAdjacentHTML("beforeend", `
    <section class="library-view extension-view" id="skills-view" hidden><div class="library-toolbar"><h1 class="extension-title">技能库</h1><label class="library-search">${icon("search")}<input id="skill-search" aria-label="搜索技能" placeholder="搜索技能…"></label><button class="secondary-button extension-mutation" id="open-bindings">绑定到 Agent</button><button class="primary-button extension-mutation" id="create-skill">${icon("plus")}新建技能</button></div><div class="library-filters"><button class="active" data-skill-filter="active">可用技能</button><button data-skill-filter="archived">已归档</button><span id="skill-result-count"></span></div><div class="skill-grid" id="skill-grid"></div></section>
    <section class="library-view extension-view" id="schedules-view" hidden><div class="library-toolbar"><h1 class="extension-title">周期任务</h1><span class="extension-toolbar-spacer"></span><button class="primary-button extension-mutation" id="create-schedule">${icon("plus")}新建计划</button></div><div class="library-filters"><button class="active" data-schedule-filter="active">全部计划</button><button data-schedule-filter="paused">已暂停</button><button data-schedule-filter="archived">已归档</button><span id="schedule-result-count"></span></div><div class="schedule-list" id="schedule-list"></div></section>
  `);
  document.body.insertAdjacentHTML("beforeend", `
    <dialog id="skill-dialog" class="app-dialog skill-dialog"><form id="skill-form"><header class="dialog-header"><h2 id="skill-dialog-title">新建技能</h2><div class="skill-header-actions"><select id="skill-version" aria-label="技能版本" hidden></select><button class="icon-button" type="button" data-close-dialog aria-label="关闭">${icon("close")}</button></div></header><div class="dialog-content skill-form-content"><div class="skill-metadata"><div class="skill-import"><label class="small-button" for="skill-file">导入 SKILL.md</label><input id="skill-file" type="file" accept=".md,.txt" hidden></div><label for="skill-name">技能名称</label><input id="skill-name" required maxlength="100" placeholder="例如：产品需求审阅"><label for="skill-description">技能说明</label><input id="skill-description" required maxlength="600" placeholder="这个技能帮助 Agent 做好什么"><label for="skill-source">技能来源</label><input id="skill-source" type="url" placeholder="https://（可选）"><p class="field-help">绑定后，技能内容会作为工作指引提供给 Agent。</p></div><div class="skill-content-field"><label for="skill-content">技能内容</label><textarea id="skill-content" aria-label="技能内容" required spellcheck="false" placeholder="# 技能名称&#10;&#10;说明适用场景、工作步骤与交付要求…"></textarea></div></div><footer class="dialog-footer"><span class="skill-edit-state" id="skill-edit-state"></span><button class="secondary-button" type="button" data-close-dialog>关闭</button><button class="primary-button extension-mutation" id="save-skill" type="submit">保存技能</button></footer></form></dialog>
    <dialog id="binding-dialog" class="app-dialog"><form id="binding-form"><header class="dialog-header"><h2>为 Agent 选择技能</h2><button class="icon-button" type="button" data-close-dialog aria-label="关闭">${icon("close")}</button></header><div class="dialog-content"><label for="binding-agent">绑定 Agent</label><select id="binding-agent" aria-label="绑定 Agent"></select><div class="binding-list" id="binding-list"></div><p class="field-help">最多绑定 12 个技能。归档的技能不会继续提供给新任务。</p></div><footer class="dialog-footer"><button class="secondary-button" type="button" data-close-dialog>取消</button><button class="primary-button" id="save-bindings" type="submit">保存绑定</button></footer></form></dialog>
    <dialog id="schedule-dialog" class="app-dialog"><form id="schedule-form"><header class="dialog-header"><h2 id="schedule-dialog-title">新建计划</h2><button class="icon-button" type="button" data-close-dialog aria-label="关闭">${icon("close")}</button></header><div class="dialog-content"><label for="schedule-name">计划名称</label><input id="schedule-name" required maxlength="140" placeholder="例如：每周行业简报"><label for="schedule-text">任务要求</label><textarea id="schedule-text" required rows="4" maxlength="16000" placeholder="每次需要 Agent 完成什么…"></textarea><label for="schedule-executor">执行方式</label><select id="schedule-executor" aria-label="执行方式"></select><div class="schedule-form-row"><div><label for="schedule-interval">重复间隔</label><select id="schedule-interval" aria-label="重复间隔"><option value="86400">每天</option><option value="604800">每周</option><option value="3600">每小时</option><option value="custom">自定义</option></select></div><div id="schedule-custom-field" hidden><label for="schedule-custom">间隔分钟</label><input id="schedule-custom" aria-label="间隔分钟" type="number" min="1" max="525600" step="1" value="60"></div><div><label for="schedule-start">首次运行时间</label><input id="schedule-start" aria-label="首次运行时间" type="datetime-local"></div></div><label for="schedule-room">关联对话</label><select id="schedule-room" aria-label="关联对话"></select><p class="field-help">到时创建任务，由连接的 Agent 执行。上一轮未结束时会跳过本轮。</p></div><footer class="dialog-footer"><button class="secondary-button" type="button" data-close-dialog>取消</button><button class="primary-button" id="save-schedule" type="submit">保存计划</button></footer></form></dialog>
    <dialog id="schedule-history-dialog" class="app-dialog"><header class="dialog-header"><h2 id="schedule-history-title">运行记录</h2><button class="icon-button" type="button" data-close-dialog aria-label="关闭">${icon("close")}</button></header><div class="dialog-content"><div id="schedule-history"></div></div></dialog>
  `);
  const agentFooter = $("agent-form").querySelector(".dialog-footer");
  agentFooter.insertAdjacentHTML("afterbegin", '<button type="button" class="secondary-button" id="manage-agent-skills" hidden>管理技能</button>');
  async function loadView(view, quiet = false) {
    if (!["skills", "schedules"].includes(view) || extension.loading.has(view)) return;
    extension.loading.add(view);
    try {
      const data = await api(`/${view}`);
      const items = data[view] || []; const changed = !extension.lastLoaded[view] || JSON.stringify(extension[view]) !== JSON.stringify(items); extension[view] = items; extension.lastLoaded[view] = Date.now();
      if (changed) { if (view === "skills") renderSkills(); else renderSchedules(); }
    } catch (error) { if (!quiet) notify(error.message, true); }
    finally { extension.loading.delete(view); }
  }
  function renderSkills() {
    const query = extension.skillSearch.toLocaleLowerCase();
    const items = extension.skills.filter((skill) => (extension.skillFilter === "archived" ? !!skill.archived_at : !skill.archived_at) && (!query || `${skill.name} ${skill.description}`.toLocaleLowerCase().includes(query)));
    $("skill-result-count").textContent = `${items.length} 个技能`;
    $("skill-grid").innerHTML = items.length ? items.map((skill) => `<article class="skill-card"><div class="skill-card-top"><span class="skill-symbol">${icon("skill")}</span><span class="skill-version-label">v${skill.version}${skill.archived_at ? " · 已归档" : ""}</span></div><h2>${e(skill.name)}</h2><p>${e(skill.description)}</p><div class="skill-card-actions"><button class="secondary-button" data-edit-skill="${e(skill.id)}" aria-label="${skill.archived_at || !writable() ? "查看" : "编辑"}技能：${e(skill.name)}">${skill.archived_at || !writable() ? "查看内容" : "编辑技能"}</button>${!skill.archived_at && writable() ? `<button class="text-button" data-archive-skill="${e(skill.id)}" aria-label="归档技能：${e(skill.name)}">归档</button>` : ""}</div></article>`).join("") : `<div class="extension-empty"><span>${icon("skill")}</span><h2>${extension.skillFilter === "archived" ? "没有已归档的技能" : "把好方法留给 Agent"}</h2><p>${extension.skillFilter === "archived" ? "归档的技能仍可查看历史内容。" : "创建或导入 SKILL.md，再绑定给合适的 Agent。"}</p></div>`;
  }
  function renderSchedules() {
    const items = extension.schedules.filter((item) => extension.scheduleFilter === "archived" ? !!item.archived_at : extension.scheduleFilter === "paused" ? !item.archived_at && !item.enabled : !item.archived_at);
    $("schedule-result-count").textContent = `${items.length} 个计划`;
    $("schedule-list").innerHTML = items.length ? items.map((item) => {
      const status = item.archived_at ? "已归档" : !item.enabled ? "已暂停" : "已启用";
      const lastStatus = ({ queued: "等待 Agent 接手", running: "执行中", completed: "已完成", failed: "失败", cancelled: "已取消" })[item.last_run_status];
      const agents = ui()?.getAgents() || [], templates = ui()?.getTemplates() || [];
      const executor = item.template_id ? templates.find((team) => team.id === item.template_id)?.name || "工作流" : item.agent_id === "auto" ? "自动分配" : agents.find((agent) => agent.id === item.agent_id)?.name || item.agent_id;
      return `<article class="schedule-card"><div class="schedule-main"><span class="schedule-symbol">${icon("clock")}</span><div><h2>${e(item.name)}<span class="schedule-state ${item.enabled && !item.archived_at ? "enabled" : ""}">${status}</span></h2><p>${e(item.text)}</p><div class="schedule-meta"><span>${e(intervalName(item.interval_seconds))}</span><span>${e(executor)}</span>${lastStatus ? `<span class="schedule-last-status ${e(item.last_run_status)}">最近运行：${e(lastStatus)}</span>` : ""}<span>${item.enabled && !item.archived_at ? `下次 ${stamp(item.next_run_at)}` : `最近运行 ${stamp(item.last_fired_at)}`}</span></div>${item.last_error ? `<p class="schedule-error">${e(item.last_error)}</p>` : ""}${item.pause_reason ? `<p class="schedule-error">${e(item.pause_reason)}</p>` : ""}</div></div><div class="schedule-actions"><button class="text-button" data-schedule-history="${e(item.id)}" aria-label="查看记录：${e(item.name)}">调度记录</button>${!item.archived_at && writable() ? `<button class="text-button" data-edit-schedule="${e(item.id)}" aria-label="编辑计划：${e(item.name)}">编辑</button><button class="text-button" data-toggle-schedule="${e(item.id)}" aria-label="${item.enabled ? "暂停" : "恢复"}计划：${e(item.name)}">${item.enabled ? "暂停" : "恢复"}</button><button class="text-button schedule-archive" data-archive-schedule="${e(item.id)}" aria-label="归档计划：${e(item.name)}">归档</button><button class="secondary-button" data-run-schedule="${e(item.id)}" aria-label="立即运行：${e(item.name)}">立即运行</button>` : ""}</div></article>`;
    }).join("") : '<div class="extension-empty"><span>' + icon("clock") + '</span><h2>让重复的工作按时开始</h2><p>设置任务、协作者和时间，运行记录会保存在这里。</p></div>';
  }
  function fillSkill(skill, latest) {
    extension.editingSkill = skill; extension.skillLatest = latest || skill;
    $("skill-name").value = skill?.name || ""; $("skill-description").value = skill?.description || ""; $("skill-source").value = skill?.source_url || ""; $("skill-content").value = skill?.content || "";
    const readonly = !writable() || !!skill?.archived_at || (!!skill && skill.version !== extension.skillLatest.version);
    for (const id of ["skill-name", "skill-description", "skill-source", "skill-content"]) $(id).readOnly = readonly;
    $("skill-file").disabled = readonly;
    $("save-skill").hidden = readonly;
    $("skill-version").hidden = !skill;
    if (skill) { $("skill-version").innerHTML = [...(extension.skillLatest.versions || [])].sort((a, b) => b.version - a.version).map((version) => `<option value="${version.version}">版本 ${version.version}${version.version === extension.skillLatest.version ? " · 最新" : ""}</option>`).join(""); $("skill-version").value = String(skill.version); }
    $("skill-edit-state").textContent = readonly && skill ? skill.archived_at ? "已归档，保留历史内容" : "查看历史版本" : "";
    $("skill-dialog-title").textContent = !skill ? "新建技能" : readonly ? "技能内容" : "编辑技能";
  }
  async function editSkill(id = null) {
    try { const skill = id ? await api(`/skills/${encodeURIComponent(id)}`) : null; fillSkill(skill); $("skill-dialog").showModal(); $("skill-name").focus(); } catch (error) { notify(error.message, true); }
  }
  async function saveSkill(event) {
    event.preventDefault(); $("save-skill").disabled = true;
    const skill = extension.editingSkill;
    try {
      await api(skill ? `/skills/${encodeURIComponent(skill.id)}` : "/skills", { method: skill ? "PUT" : "POST", body: { name: $("skill-name").value.trim(), description: $("skill-description").value.trim(), content: $("skill-content").value, source_url: $("skill-source").value.trim() || null, ...(skill ? { base_version: skill.version } : {}) } });
      $("skill-dialog").close(); await loadView("skills"); notify("技能已保存");
    } catch (error) { $("skill-edit-state").textContent = "保存未完成，内容已保留"; notify(error.message, true); }
    finally { $("save-skill").disabled = false; }
  }
  async function archiveSkill(id) { const skill = extension.skills.find((item) => item.id === id); if (!skill) return; try { await api(`/skills/${encodeURIComponent(id)}`, { method: "DELETE", body: { base_version: skill.version } }); await loadView("skills"); notify("技能已归档"); } catch (error) { notify(error.message, true); } }
  async function openBindings(agentId) {
    $("binding-agent").innerHTML = (ui()?.getAgents() || []).map((agent) => `<option value="${e(agent.id)}">${e(agent.name)}</option>`).join("");
    if (agentId) $("binding-agent").value = agentId;
    $("binding-dialog").showModal();
    await loadBindings();
  }
  async function loadBindings() {
    const id = $("binding-agent").value; $("save-bindings").disabled = true; extension.binding = null; $("binding-list").innerHTML = '<p class="binding-empty">正在读取技能…</p>';
    try {
      const [binding, skills] = await Promise.all([api(`/agents/${encodeURIComponent(id)}/skills`), api("/skills")]);
      if ($( "binding-agent").value !== id) return;
      extension.binding = binding; const selected = new Set(binding.skills.map((skill) => skill.id));
      const active = skills.skills.filter((skill) => !skill.archived_at);
      $("binding-list").innerHTML = active.length ? active.map((skill) => `<label class="binding-item"><input type="checkbox" name="skill" value="${e(skill.id)}" aria-label="${e(skill.name)}"${selected.has(skill.id) ? " checked" : ""}><span><strong>${e(skill.name)} <small>v${skill.version}</small></strong><span>${e(skill.description)}</span></span></label>`).join("") : '<p class="binding-empty">先创建一个技能，再来为 Agent 选择。</p>';
    } catch (error) { notify(error.message, true); }
    finally { if ($("binding-agent").value === id) $("save-bindings").disabled = !extension.binding; }
  }
  async function saveBindings(event) {
    event.preventDefault(); if (!extension.binding) return; $("save-bindings").disabled = true;
    try { await api(`/agents/${encodeURIComponent(extension.binding.agent_id)}/skills`, { method: "PUT", body: { base_version: extension.binding.version, skill_ids: [...$("binding-list").querySelectorAll('input:checked')].map((input) => input.value) } }); $("binding-dialog").close(); notify("技能绑定已保存"); }
    catch (error) { notify(error.message, true); }
    finally { $("save-bindings").disabled = false; }
  }
  function dateInput(value) { if (!value) return ""; const date = new Date(value); if (Number.isNaN(date.getTime())) return ""; return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16); }
  async function editSchedule(id = null) {
    try {
      const schedule = id ? await api(`/schedules/${encodeURIComponent(id)}`) : null;
      extension.editingSchedule = schedule; $("schedule-form").reset();
      $("schedule-dialog-title").textContent = schedule ? "编辑计划" : "新建计划";
      $("schedule-name").value = schedule?.name || ""; $("schedule-text").value = schedule?.text || "";
      $("schedule-executor").innerHTML = '<option value="auto">自动分配</option><optgroup label="Agent">' + (ui()?.getAgents() || []).map((agent) => `<option value="${e(agent.id)}">${e(agent.name)}</option>`).join("") + '</optgroup><optgroup label="工作流">' + (ui()?.getTemplates() || []).map((team) => `<option value="team:${e(team.id)}">${e(team.name)}</option>`).join("") + '</optgroup>';
      $("schedule-executor").value = schedule?.template_id ? `team:${schedule.template_id}` : schedule?.agent_id || "auto";
      const interval = schedule?.interval_seconds || 86400;
      $("schedule-interval").value = [3600, 86400, 604800].includes(interval) ? String(interval) : "custom";
      $("schedule-custom").value = interval / 60; $("schedule-custom-field").hidden = $("schedule-interval").value !== "custom";
      $("schedule-start").value = dateInput(schedule?.next_run_at);
      $("schedule-room").innerHTML = '<option value="">自动创建对话</option>' + (ui()?.getRooms() || []).map((room) => `<option value="${e(room.id)}">${e(room.title)}</option>`).join("");
      $("schedule-room").value = schedule?.room_id || ""; $("schedule-room").disabled = !!schedule?.run_count;
      $("schedule-dialog").showModal(); $("schedule-name").focus();
    } catch (error) { notify(error.message, true); }
  }
  async function saveSchedule(event) {
    event.preventDefault(); $("save-schedule").disabled = true;
    const schedule = extension.editingSchedule, executor = $("schedule-executor").value;
    const body = { name: $("schedule-name").value.trim(), text: $("schedule-text").value.trim(), interval_seconds: $("schedule-interval").value === "custom" ? Number($("schedule-custom").value) * 60 : Number($("schedule-interval").value), agent_id: executor.startsWith("team:") ? "auto" : executor, template_id: executor.startsWith("team:") ? executor.slice(5) : null, ...(schedule ? { base_version: schedule.version } : {}) };
    if (!schedule?.run_count) body.room_id = $("schedule-room").value || null;
    if ($("schedule-start").value && (!schedule || $("schedule-start").value !== dateInput(schedule.next_run_at))) body.next_run_at = new Date($("schedule-start").value).toISOString();
    try { await api(schedule ? `/schedules/${encodeURIComponent(schedule.id)}` : "/schedules", { method: schedule ? "PUT" : "POST", body }); $("schedule-dialog").close(); await loadView("schedules"); notify("计划已保存"); }
    catch (error) { notify(error.message, true); }
    finally { $("save-schedule").disabled = false; }
  }
  async function scheduleAction(id, action, button) {
    button.disabled = true; const schedule = extension.schedules.find((item) => item.id === id);
    try {
      const result = await api(`/schedules/${encodeURIComponent(id)}${action === "archive" ? "" : `/${action}`}`, { method: action === "archive" ? "DELETE" : "POST", body: action === "pause" ? { paused: !!schedule?.enabled } : {} });
      await loadView("schedules");
      if (action === "run-now") { notify(result.skipped_reason ? "上一轮任务尚未结束，本次未重复创建。" : "任务已创建，等待 Agent 接手。" ); if (result.run_id) await ui()?.refresh(); }
      else notify(action === "archive" ? "计划已归档" : schedule?.enabled ? "计划已暂停" : "计划已恢复");
    } catch (error) { notify(error.message, true); }
    finally { if (button.isConnected) button.disabled = false; }
  }
  async function showScheduleHistory(id) {
    try {
      const schedule = await api(`/schedules/${encodeURIComponent(id)}`); $("schedule-history-title").textContent = schedule.name;
      $("schedule-history").innerHTML = `${schedule.room_id ? `<button class="small-button schedule-room-link" data-schedule-room="${e(schedule.room_id)}">打开关联对话 ↗</button>` : ""}${schedule.history?.length ? `<div class="schedule-history-list">${schedule.history.map((item) => `<article><span class="history-dot ${e(item.status)}"></span><div><strong>${{ created: "已创建任务", skipped: "已跳过", failed: "创建失败" }[item.status] || e(item.status)}</strong><p>${e(item.error || (item.status === "skipped" ? "上一轮任务尚未结束" : item.trigger === "manual" ? "手动运行" : "按计划运行"))}</p><small>${stamp(item.created_at)}</small></div></article>`).join("")}</div>` : '<p class="binding-empty">还没有运行记录。</p>'}`;
      $("schedule-history-dialog").showModal();
    } catch (error) { notify(error.message, true); }
  }
  $("skill-search").addEventListener("input", () => { extension.skillSearch = $("skill-search").value; renderSkills(); });
  $("create-skill").addEventListener("click", () => editSkill()); $("skill-form").addEventListener("submit", saveSkill);
  $("skill-version").addEventListener("change", async () => { try { const skill = await api(`/skills/${encodeURIComponent(extension.skillLatest.id)}?version=${$("skill-version").value}`); fillSkill(skill, extension.skillLatest); } catch (error) { notify(error.message, true); } });
  $("skill-file").addEventListener("change", async () => { const file = $("skill-file").files[0]; if (!file) return; if (file.size > 256 * 1024) { notify("技能文件需要小于 256 KB。", true); return; } try { $("skill-content").value = await file.text(); if (!$("skill-name").value) $("skill-name").value = file.name.replace(/\.(md|txt)$/i, ""); } catch { notify("无法读取技能文件。", true); } });
  $("open-bindings").addEventListener("click", () => openBindings()); $("binding-agent").addEventListener("change", loadBindings); $("binding-form").addEventListener("submit", saveBindings);
  $("manage-agent-skills").addEventListener("click", () => { const id = ui()?.getEditingAgent(); if (id) { $("agent-dialog").close(); openBindings(id); } });
  $("create-schedule").addEventListener("click", () => editSchedule()); $("schedule-form").addEventListener("submit", saveSchedule);
  $("schedule-interval").addEventListener("change", () => { $("schedule-custom-field").hidden = $("schedule-interval").value !== "custom"; });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button"); if (!button || button.disabled) return;
    if (["skills", "schedules"].includes(button.dataset.view)) loadView(button.dataset.view);
    if (button.dataset.skillFilter) { extension.skillFilter = button.dataset.skillFilter; document.querySelectorAll("[data-skill-filter]").forEach((item) => item.classList.toggle("active", item === button)); renderSkills(); }
    if (button.dataset.scheduleFilter) { extension.scheduleFilter = button.dataset.scheduleFilter; document.querySelectorAll("[data-schedule-filter]").forEach((item) => item.classList.toggle("active", item === button)); renderSchedules(); }
    if (button.dataset.editSkill) editSkill(button.dataset.editSkill);
    if (button.dataset.archiveSkill) archiveSkill(button.dataset.archiveSkill);
    if (button.dataset.editAgent) $("manage-agent-skills").hidden = false;
    if (button.id === "create-agent") $("manage-agent-skills").hidden = true;
    if (button.dataset.editSchedule) editSchedule(button.dataset.editSchedule);
    if (button.dataset.toggleSchedule) scheduleAction(button.dataset.toggleSchedule, "pause", button);
    if (button.dataset.runSchedule) scheduleAction(button.dataset.runSchedule, "run-now", button);
    if (button.dataset.archiveSchedule) scheduleAction(button.dataset.archiveSchedule, "archive", button);
    if (button.dataset.scheduleHistory) showScheduleHistory(button.dataset.scheduleHistory);
    if (button.dataset.scheduleRoom) { $("schedule-history-dialog").close(); ui()?.openRoom(button.dataset.scheduleRoom); }
  });
  window.workspaceExtensions = { openSkill: editSkill };
  window.addEventListener("workspace-data", () => { const view = ui()?.getView(); if (["skills", "schedules"].includes(view) && Date.now() - (extension.lastLoaded[view] || 0) > 3000) loadView(view, true); });
})();

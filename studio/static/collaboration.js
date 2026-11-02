"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const ui = () => window.workspaceUI;
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const writable = () => document.documentElement.dataset.access !== "viewer";
  const stamp = (value) => { const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); };
  const shortBody = (value) => String(value || "评论").split("\n")[0].slice(0, 45);
  const paths = { comment: '<path d="M20 11a8 8 0 0 1-8 8H5l-4 3V11a9 9 0 0 1 19 0Z"/><path d="M6 9h8m-8 4h5"/>', close: '<path d="m6 6 12 12M18 6 6 18"/>', check: '<path d="m5 12 4 4L19 6"/>', quote: '<path d="M4 10h6v7H3v-5c0-4 2-6 5-7m9 5h5v7h-7v-5c0-4 2-6 5-7"/>', arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>' };
  const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.comment}</svg>`;
  const state = { artifact: null, key: null, open: false, tab: "comments", comments: [], modifications: [], commentRevision: null, modificationRevision: null, drafts: new Map(), replyDrafts: new Map(), editDrafts: new Map(), loading: false, loadedAt: 0, epoch: 0, filter: "open", locks: new Set(), dialogDraft: null, sourceRequest: null, candidate: null, selectedQuote: "", error: "" };
  const toast = (message, error = false) => ui()?.toast(message, error);
  const uuid = () => crypto.randomUUID();
  const agentName = (id) => ui()?.getAgents().find((agent) => agent.id === id)?.name || id || "Agent";
  async function api(path, { method = "GET", body } = {}) {
    const response = await fetch(`/api/hub${path}`, { method, headers: body === undefined ? {} : { "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    let data; try { data = await response.json(); } catch { throw new Error("服务返回了无效响应。"); }
    if (!response.ok) { const error = new Error(data.error || "操作未完成，请重试。"); error.status = response.status; throw error; }
    return data;
  }
  function draftFor(key = state.key) {
    if (!state.drafts.has(key)) state.drafts.set(key, { body: "", quote: "", mode: "document", selected: new Set(), instructions: "", agentId: "", attempt: null, modificationAttempt: null });
    return state.drafts.get(key);
  }
  function attemptFor(holder, property, payload) { const signature = JSON.stringify(payload); if (!holder[property] || holder[property].signature !== signature) holder[property] = { signature, key: uuid() }; return holder[property].key; }
  function originalAgent(artifact = state.artifact, room = ui()?.getCurrentRoom()) { for (const run of room?.runs || []) { const task = (run.tasks || []).find((task) => task.id === artifact?.task_id); if (task) return task.agent_id; } return null; }

  // A modification belongs to the selected baseline and its team's current
  // roster. Keep invalid drafts visible; never silently assign another Agent.
  function modificationAgents(context) {
    const agents = ui()?.getAgents() || [];
    if (!context.teamId) return agents;
    if (!context.team || context.team.archived_at) return [];
    const members = new Set(context.team.member_ids || []);
    return agents.filter((agent) => members.has(agent.id) || (agent.id === "coordinator" && context.sourceAgent === "coordinator"));
  }
  function syncModificationAgents(context = state.dialogDraft) {
    if (!context || state.dialogDraft !== context) return;
    const room = ui()?.getCurrentRoom();
    if (!context.sourceAgent && room?.id === context.roomId) context.sourceAgent = originalAgent(context.artifact, room);
    if (context.teamId && room?.team?.id === context.teamId && (!context.team || room.team.version >= context.team.version)) context.team = room.team;
    const draft = draftFor(context.key), select = $("modification-agent"), agents = modificationAgents(context);
    // Empty value intentionally means the baseline's original Agent, so native
    // required-select validation cannot be used for this control.
    const ids = new Set(agents.map((agent) => agent.id));
    const awaitingSource = !!context.teamId && !context.sourceAgent;
    const originalAllowed = !context.teamId || ids.has(context.sourceAgent);
    const valid = draft.agentId === "" ? originalAllowed || awaitingSource : ids.has(draft.agentId);
    if (!valid) draft.agentId = "__choose__";
    const options = '<option value="__choose__" disabled>选择负责修改的 Agent</option>' + (originalAllowed ? `<option value="">原成果 Agent${context.sourceAgent ? ` · ${esc(agentName(context.sourceAgent))}` : ""}</option>` : awaitingSource ? '<option value="" disabled>正在读取原成果 Agent…</option>' : "") + agents.map((agent) => `<option value="${esc(agent.id)}">${esc(agent.name)}</option>`).join("");
    // Browser serialization normalizes boolean attributes. Comparing innerHTML
    // would rebuild unchanged options on every poll and disturb the open list.
    if (context.optionsHTML !== options) { select.innerHTML = options; context.optionsHTML = options; }
    select.value = draft.agentId;
    const message = context.team?.archived_at ? "团队已归档。修改说明已保留。" : context.teamId && !context.team ? "正在确认团队成员…" : awaitingSource && draft.agentId === "" ? "正在读取这个版本的原成果 Agent…" : draft.agentId === "__choose__" ? "原选择已不在团队中。修改说明已保留，请重新选择成员。" : "";
    $("modification-agent-status").textContent = message;
    $("modification-agent-status").hidden = !message;
    select.disabled = state.locks.has("modification") || !!context.team?.archived_at;
    $("submit-modification").disabled = state.locks.has("modification") || draft.agentId === "__choose__" || (awaitingSource && draft.agentId === "") || !!context.team?.archived_at || !writable();
  }
  async function refreshModificationTeam(context) {
    if (!context.teamId) return;
    const team = await api(`/teams/${encodeURIComponent(context.teamId)}`);
    if (state.dialogDraft !== context) return;
    if (!context.team || team.version >= context.team.version) context.team = team;
    syncModificationAgents(context);
    if (!context.sourceAgent && context.roomId) {
      const room = await api(`/rooms/${encodeURIComponent(context.roomId)}`);
      if (state.dialogDraft !== context) return;
      context.sourceAgent = originalAgent(context.artifact, room);
      if (room.team?.id === context.teamId && room.team.version >= context.team.version) context.team = room.team;
      syncModificationAgents(context);
    }
  }

  const workbench = document.createElement("div"), canvas = document.createElement("div");
  workbench.className = "artifact-workbench"; canvas.className = "artifact-canvas";
  $("artifact-detail").append(workbench); workbench.append(canvas);
  ["artifact-review-status", "artifact-body", "artifact-editor"].forEach((id) => canvas.append($(id)));
  document.querySelector(".artifact-modebar>div").insertAdjacentHTML("beforeend", `<button id="toggle-artifact-comments" aria-label="评论" aria-expanded="false" aria-controls="artifact-collaboration">${icon("comment")}评论 <span id="artifact-comment-count"></span></button>`);
  workbench.insertAdjacentHTML("beforebegin", '<button class="revision-preview-link" id="artifact-modification-origin" aria-label="查看修改来源" hidden></button>');
  workbench.insertAdjacentHTML("beforeend", `
    <section class="collaboration-panel" id="artifact-collaboration" aria-label="成果评论与修改" hidden>
      <div class="collaboration-tabs" role="tablist" aria-label="成果协作"><button id="collaboration-comments-tab" role="tab" aria-selected="true" aria-controls="collaboration-comments-view" data-collaboration-tab="comments">评论<span id="collaboration-comment-total"></span></button><button id="collaboration-requests-tab" role="tab" aria-selected="false" aria-controls="collaboration-requests-view" data-collaboration-tab="requests" tabindex="-1">修改记录<span id="collaboration-request-total"></span></button><button class="icon-button collaboration-back" id="close-artifact-comments" aria-label="关闭评论"><span class="collaboration-close-icon">${icon("close")}</span><span class="collaboration-back-label"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m14 6-6 6 6 6"/></svg>返回正文</span></button></div>
      <section class="collaboration-view" id="collaboration-comments-view" role="tabpanel" aria-labelledby="collaboration-comments-tab"><div class="collaboration-scroll"><div class="collaboration-scope"><span id="comments-version-label"></span><select id="comment-filter" aria-label="筛选评论"><option value="open">待解决</option><option value="all">全部评论</option><option value="resolved">已解决</option></select></div><div id="comment-load-error" class="collaboration-error" role="status" hidden></div><div id="comment-thread-list"></div></div>
        <div class="revision-selection collaboration-mutation" id="revision-selection" hidden><div class="revision-selection-top"><span id="revision-selected-count"></span><button id="clear-selected-comments">取消选择</button></div><button class="primary-button" id="open-modification" aria-label="交给 Agent 修改">交给 Agent 修改 ${icon("arrow")}</button></div>
        <form class="comment-compose collaboration-mutation" id="comment-compose"><div class="comment-compose-tools"><select id="comment-anchor-mode" aria-label="评论范围"><option value="document">全文评论</option><option value="quote">引用文本</option></select><button type="button" id="quote-selected-text" aria-label="引用选中内容">${icon("quote")}引用选中内容</button></div><label class="sr-only" for="comment-quote">引用文本</label><textarea class="comment-quote-input" id="comment-quote" aria-label="引用文本" placeholder="粘贴这个版本中需要修改的原文…" maxlength="2000" hidden></textarea><label class="sr-only" for="comment-body">评论内容</label><textarea class="comment-body-input" id="comment-body" aria-label="评论内容" maxlength="4000" placeholder="写下你的想法或修改意见…" required></textarea><div class="comment-compose-footer"><small id="comment-draft-label"></small><button class="primary-button" id="post-comment" type="submit">发表评论</button></div></form><p class="collaboration-readonly" id="comments-readonly" hidden>你可以查看这个版本的讨论。</p>
      </section>
      <section class="collaboration-view" id="collaboration-requests-view" role="tabpanel" aria-labelledby="collaboration-requests-tab" hidden><div class="collaboration-scroll"><div class="collaboration-scope"><span>这个成果的修改记录</span><button class="text-button" id="refresh-modifications" aria-label="刷新修改记录">刷新</button></div><div id="modification-list"></div></div></section>
    </section>`);
  document.body.insertAdjacentHTML("beforeend", `
    <dialog class="app-dialog revision-dialog" id="modification-dialog" aria-labelledby="modification-dialog-title"><form id="modification-form"><header class="dialog-header"><h2 id="modification-dialog-title">交给 Agent 修改</h2><button class="icon-button" type="button" data-close-dialog aria-label="关闭">${icon("close")}</button></header><div class="dialog-content"><p class="revision-dialog-summary" id="modification-summary"></p><div class="revision-compose"><label for="modification-agent">负责修改的 Agent</label><select id="modification-agent" aria-label="负责修改的 Agent" aria-describedby="modification-agent-status"></select><p id="modification-agent-status" class="modification-agent-status" role="status" hidden></p><label for="modification-instructions">修改说明</label><textarea id="modification-instructions" aria-label="修改说明" rows="4" maxlength="8000" placeholder="补充整体要求（可选）"></textarea></div><div class="revision-snapshot" id="modification-selected-preview"></div><p class="collaboration-error" id="modification-submit-error" role="alert" hidden></p></div><footer class="dialog-footer"><button class="secondary-button" type="button" data-close-dialog>取消</button><button class="primary-button" id="submit-modification" type="submit" aria-label="提交修改请求">提交修改请求</button></footer></form></dialog>
    <dialog class="app-dialog revision-dialog" id="modification-source-dialog" aria-labelledby="modification-source-title"><header class="dialog-header"><h2 id="modification-source-title">修改来源</h2><button class="icon-button" type="button" data-close-dialog aria-label="关闭">${icon("close")}</button></header><div class="dialog-content" id="modification-source-content"></div><footer class="dialog-footer"><button class="secondary-button" id="open-modification-baseline">查看基线版本</button><button class="primary-button" type="button" data-close-dialog>完成</button></footer></dialog>
    <dialog class="app-dialog revision-candidate-dialog" id="modification-candidate-dialog" aria-labelledby="modification-candidate-title"><header class="dialog-header"><h2 id="modification-candidate-title">对比候选版本</h2><button class="icon-button" type="button" data-close-dialog aria-label="关闭">${icon("close")}</button></header><div class="revision-candidate-body"><p class="revision-candidate-intro" id="candidate-intro"></p><div class="revision-apply-conflict" id="candidate-apply-conflict" role="alert" hidden><span></span><button id="reload-candidate-comparison" type="button">读取最新版本重新对比</button></div><div class="revision-compare-grid"><section class="revision-compare-panel"><div class="revision-compare-heading"><strong>当前成果</strong><span id="candidate-current-version"></span></div><pre id="candidate-current-content" aria-label="当前版本内容"></pre></section><section class="revision-compare-panel"><div class="revision-compare-heading"><strong>Agent 候选</strong><span id="candidate-source-version"></span></div><pre id="candidate-proposed-content" data-candidate-content aria-label="候选版本内容"></pre></section></div></div><footer class="dialog-footer"><span class="revision-draft-note" id="candidate-apply-note"></span><button class="secondary-button" type="button" data-close-dialog>暂不应用</button><button class="primary-button collaboration-mutation" id="apply-modification-candidate" aria-label="应用候选版本">应用候选版本</button></footer></dialog>`);

  function persistComposer() { if (!state.key) return; const draft = draftFor(); draft.body = $("comment-body").value; draft.quote = $("comment-quote").value; draft.mode = $("comment-anchor-mode").value; }
  function restoreComposer() { const draft = draftFor(); $("comment-body").value = draft.body; $("comment-quote").value = draft.quote; $("comment-anchor-mode").value = draft.mode; $("comment-quote").hidden = draft.mode !== "quote"; $("comment-draft-label").textContent = `版本 ${state.artifact.version}`; $("comments-version-label").textContent = `版本 ${state.artifact.version} 的讨论`; $("comments-readonly").hidden = writable(); }
  function syncArtifact() {
    const artifact = ui()?.getSelectedArtifact();
    const key = artifact ? `${artifact.id}:${artifact.version}` : null;
    const changed = key !== state.key;
    if (changed) {
      persistComposer(); state.epoch++; state.artifact = artifact; state.key = key; state.comments = []; state.modifications = []; state.commentRevision = null; state.modificationRevision = null; state.loadedAt = 0; state.loading = false; state.error = ""; state.selectedQuote = "";
      $("comment-thread-list").replaceChildren(); $("modification-list").replaceChildren();
      for (const id of ["artifact-comment-count", "collaboration-comment-total", "collaboration-request-total"]) $(id).textContent = "";
      showLoadError("");
      if (artifact) restoreComposer(); else setOpen(false, false);
    } else state.artifact = artifact;
    const origin = $("artifact-modification-origin"); origin.hidden = !artifact?.modification_request_id;
    if (artifact?.modification_request_id) origin.textContent = `由版本 ${artifact.source_version} 的修改请求生成 ↗`;
    if (state.open && artifact) { renderSelection(); loadData(changed); }
  }
  function setOpen(open, focus = true) {
    state.open = !!open && !!state.artifact;
    $("artifact-collaboration").hidden = !state.open;
    $("toggle-artifact-comments").classList.toggle("active", state.open);
    $("toggle-artifact-comments").setAttribute("aria-expanded", String(state.open));
    $("workspace-shell").classList.toggle("studio-comments-open", state.open);
    if (state.open) { ui()?.expandStudio?.(); loadData(); if (focus) $(state.tab === "comments" ? (writable() ? "comment-body" : "collaboration-comments-tab") : "collaboration-requests-tab").focus(); }
    else if (focus) $("toggle-artifact-comments").focus();
  }
  function showTab(tab) {
    state.tab = tab;
    for (const name of ["comments", "requests"]) { const active = name === tab; $( `collaboration-${name}-tab`).setAttribute("aria-selected", String(active)); $(`collaboration-${name}-tab`).tabIndex = active ? 0 : -1; $(`collaboration-${name}-view`).hidden = !active; }
  }
  function showLoadError(message) { state.error = message; const node = $("comment-load-error"); node.hidden = !message; node.innerHTML = message ? `${esc(message)}<button type="button" data-retry-collaboration>重新读取</button>` : ""; }
  async function loadData(force = false) {
    if (!state.artifact || (!state.open && !force) || state.loading || (!force && Date.now() - state.loadedAt < 2500)) return;
    const epoch = state.epoch, artifact = state.artifact; state.loading = true;
    if (state.commentRevision === null) $("comment-thread-list").innerHTML = '<p class="collaboration-empty">正在读取讨论…</p>';
    try {
      const results = await Promise.all([api(`/artifacts/${encodeURIComponent(artifact.id)}/comments?version=${artifact.version}`), api(`/artifacts/${encodeURIComponent(artifact.id)}/modifications`)]);
      if (epoch !== state.epoch) return;
      const [comments, modifications] = results; state.loadedAt = Date.now(); showLoadError("");
      if (comments.revision !== state.commentRevision || force) { state.comments = comments.comments || []; state.commentRevision = comments.revision; renderThreads(); }
      if (modifications.revision !== state.modificationRevision || force) { state.modifications = modifications.modifications || []; state.modificationRevision = modifications.revision; renderModifications(); }
    } catch (error) { if (epoch === state.epoch) { showLoadError(error.message); if (state.commentRevision === null) $("comment-thread-list").replaceChildren(); } }
    finally { if (epoch === state.epoch) state.loading = false; }
  }
  async function reloadData() { state.loadedAt = 0; if (state.loading) { state.epoch++; state.loading = false; } await loadData(true); }
  function anchorHTML(comment) {
    const anchor = comment.anchor || {};
    if (anchor.type !== "quote") return "";
    const label = anchor.status === "matched" ? "引用原文" : anchor.status === "ambiguous" ? "引用出现多次" : "未找到这段原文";
    return `<blockquote class="comment-anchor" title="${esc(label)}"><small>${esc(label)} · v${comment.artifact_version}</small><br>${esc(anchor.quote || "")}</blockquote>`;
  }
  function authorHTML(comment) { return `<div class="comment-author"><span class="comment-avatar" aria-hidden="true">${esc((comment.author_name || "成员").slice(0, 1))}</span><strong>${esc(comment.author_name || "成员")}</strong><time title="${esc(comment.updated_at || comment.created_at)}">${esc(stamp(comment.created_at))}</time></div>`; }
  function threadHTML(comment, comments, { interactive = true } = {}) {
    const replies = comments.filter((item) => item.parent_id === comment.id);
    const selected = interactive && draftFor().selected.has(comment.id);
    return `<div class="comment-author"><span class="comment-avatar" aria-hidden="true">${esc((comment.author_name || "成员").slice(0, 1))}</span><strong>${esc(comment.author_name || "成员")}</strong><time>${esc(stamp(comment.created_at))}</time>${interactive && writable() ? `<label class="comment-select-target"><input class="comment-select" type="checkbox" data-select-thread="${esc(comment.id)}" aria-label="选择讨论：${esc(shortBody(comment.body))}" ${selected ? "checked" : ""}></label>` : ""}</div>${anchorHTML(comment)}<p class="comment-body">${esc(comment.body)}</p>${replies.length ? `<div class="comment-replies">${replies.map((reply) => `<div class="comment-reply" data-comment-id="${esc(reply.id)}">${authorHTML(reply)}<p class="comment-body">${esc(reply.body)}</p>${interactive && writable() ? `<div class="comment-thread-actions"><button data-edit-comment="${esc(reply.id)}" data-root-comment="${esc(comment.id)}" aria-label="编辑回复">编辑</button></div>` : ""}</div>`).join("")}</div>` : ""}${interactive ? `<div class="comment-thread-actions">${writable() ? `<button data-reply-comment="${esc(comment.id)}" aria-label="回复评论">回复${replies.length ? ` · ${replies.length}` : ""}</button><button data-edit-comment="${esc(comment.id)}" data-root-comment="${esc(comment.id)}" aria-label="编辑评论">编辑</button><button data-status-comment="${esc(comment.id)}" aria-label="${comment.status === "resolved" ? "重开评论" : "解决评论"}">${comment.status === "resolved" ? "重开" : `${icon("check")}解决`}</button>` : ""}${comment.status === "resolved" ? '<span class="comment-resolved-label">已解决</span>' : ""}</div>` : ""}`;
  }
  function renderThreads() {
    const roots = state.comments.filter((comment) => !comment.parent_id), selected = draftFor().selected;
    const ids = new Set(roots.map((comment) => comment.id)); for (const id of selected) if (!ids.has(id)) selected.delete(id);
    const visible = roots.filter((comment) => state.filter === "all" || (state.filter === "resolved") === (comment.status === "resolved"));
    const list = $("comment-thread-list"); list.querySelectorAll(".collaboration-empty").forEach((item) => item.remove());
    for (const node of [...list.querySelectorAll(":scope>[data-thread-id]")]) if (!visible.some((comment) => comment.id === node.dataset.threadId)) node.remove();
    for (const comment of visible) {
      let node = [...list.children].find((item) => item.dataset.threadId === comment.id);
      if (!node) { node = document.createElement("article"); node.className = "comment-thread"; node.dataset.threadId = comment.id; node.innerHTML = `<div class="comment-thread-display"></div><form class="comment-inline-form" data-reply-form="${esc(comment.id)}" hidden><textarea aria-label="回复内容" placeholder="回复这条讨论…" maxlength="4000" required></textarea><div class="comment-inline-form-actions"><button class="text-button" type="button" data-hide-comment-form>收起</button><button class="primary-button" type="submit" aria-label="发送回复">发送回复</button></div></form><form class="comment-inline-form" data-edit-form="${esc(comment.id)}" hidden><textarea aria-label="编辑评论内容" maxlength="4000" required></textarea><p class="collaboration-error" role="alert" hidden></p><div class="comment-inline-form-actions"><button class="text-button" type="button" data-cancel-comment-edit>取消</button><button class="primary-button" type="submit" aria-label="保存评论">保存评论</button></div></form>`; list.append(node); }
      const display = node.querySelector(".comment-thread-display"), html = threadHTML(comment, state.comments);
      if (display.innerHTML !== html) { const active = document.activeElement; const selector = display.contains(active) ? active.getAttribute("aria-label") : null; display.innerHTML = html; if (selector) [...display.querySelectorAll("[aria-label]")].find((item) => item.getAttribute("aria-label") === selector)?.focus({ preventScroll: true }); }
      node.classList.toggle("is-selected", selected.has(comment.id)); node.classList.toggle("is-resolved", comment.status === "resolved");
    }
    if (!visible.length) list.innerHTML = `<p class="collaboration-empty">${state.filter === "resolved" ? "还没有已解决的讨论" : roots.length ? "这个版本没有待解决的评论" : "从一条评论开始讨论"}</p>`;
    const openCount = roots.filter((comment) => comment.status !== "resolved").length;
    $("artifact-comment-count").textContent = openCount || ""; $("collaboration-comment-total").textContent = roots.length || "";
    renderSelection();
  }
  function renderSelection() { if (!state.key) return; const count = draftFor().selected.size; $("revision-selection").hidden = !count || !writable(); $("revision-selected-count").textContent = `已选择 ${count} 条讨论`; $("open-modification").disabled = count > 20; }
  async function postComment(event) {
    event.preventDefault(); if (!state.artifact || state.locks.has("comment")) return;
    persistComposer(); const key = state.key, artifact = { ...state.artifact }, draft = draftFor(key), body = draft.body.trim(); if (!body) return;
    const payload = { artifact_version: artifact.version, body, ...(draft.mode === "quote" ? { anchor: { quote: draft.quote.trim() } } : {}) };
    if (draft.mode === "quote" && !draft.quote.trim()) { $("comment-quote").focus(); return; }
    const requestKey = attemptFor(draft, "attempt", payload); state.locks.add("comment"); $("post-comment").disabled = true;
    try {
      await api(`/artifacts/${encodeURIComponent(artifact.id)}/comments`, { method: "POST", body: { ...payload, request_key: requestKey } });
      if (draft.body.trim() === body) { draft.body = ""; draft.quote = ""; draft.mode = "document"; draft.attempt = null; if (state.key === key) restoreComposer(); }
      if (state.key === key) { await reloadData(); $("comment-body").focus(); }
    } catch (error) { toast(error.message, true); }
    finally { state.locks.delete("comment"); $("post-comment").disabled = false; }
  }
  function openReply(id) {
    const node = [...$("comment-thread-list").children].find((item) => item.dataset.threadId === id); if (!node) return;
    const form = node.querySelector("[data-reply-form]"); form.hidden = false;
    if (!state.replyDrafts.has(id)) state.replyDrafts.set(id, { body: "", attempt: null });
    form.querySelector("textarea").value = state.replyDrafts.get(id).body; form.querySelector("textarea").focus();
  }
  async function sendReply(form) {
    const id = form.dataset.replyForm, comment = state.comments.find((item) => item.id === id), draft = state.replyDrafts.get(id);
    if (!comment || !draft || state.locks.has(id)) return; const body = form.querySelector("textarea").value.trim(); if (!body) return;
    const key = state.key, payload = { artifact_version: comment.artifact_version, body, parent_id: id }, requestKey = attemptFor(draft, "attempt", payload); state.locks.add(id); form.querySelector('[type="submit"]').disabled = true;
    try { await api(`/artifacts/${encodeURIComponent(comment.artifact_id)}/comments`, { method: "POST", body: { ...payload, request_key: requestKey } }); if (draft.body.trim() === body) { draft.body = ""; draft.attempt = null; form.querySelector("textarea").value = ""; form.hidden = true; } if (state.key === key) await reloadData(); }
    catch (error) { toast(error.message, true); }
    finally { state.locks.delete(id); if (form.isConnected) form.querySelector('[type="submit"]').disabled = false; }
  }
  function openCommentEdit(id, rootId) {
    const comment = state.comments.find((item) => item.id === id), node = [...$("comment-thread-list").children].find((item) => item.dataset.threadId === rootId); if (!comment || !node) return;
    let draft = state.editDrafts.get(id); if (!draft) { draft = { body: comment.body, baseVersion: comment.version }; state.editDrafts.set(id, draft); }
    const form = node.querySelector("[data-edit-form]"); form.dataset.editCommentId = id; form.hidden = false; form.querySelector("textarea").value = draft.body; form.querySelector(".collaboration-error").hidden = true; form.querySelector("textarea").focus();
  }
  async function saveCommentEdit(form) {
    const id = form.dataset.editCommentId, draft = state.editDrafts.get(id); if (!draft || state.locks.has(id)) return;
    draft.body = form.querySelector("textarea").value; if (!draft.body.trim()) return; const key = state.key; state.locks.add(id); form.querySelector('[type="submit"]').disabled = true;
    try { await api(`/comments/${encodeURIComponent(id)}`, { method: "PUT", body: { base_version: draft.baseVersion, body: draft.body.trim() } }); state.editDrafts.delete(id); form.hidden = true; if (state.key === key) await reloadData(); }
    catch (error) { const box = form.querySelector(".collaboration-error"); box.hidden = false; box.innerHTML = `${esc(error.message)}${error.status === 409 ? `<button type="button" data-rebase-comment="${esc(id)}">保留草稿，读取最新修订</button>` : ""}`; }
    finally { state.locks.delete(id); if (form.isConnected) form.querySelector('[type="submit"]').disabled = false; }
  }
  async function changeCommentStatus(id, button) {
    const comment = state.comments.find((item) => item.id === id); if (!comment || state.locks.has(id)) return;
    const key = state.key; state.locks.add(id); button.disabled = true;
    try { await api(`/comments/${encodeURIComponent(id)}`, { method: "PUT", body: { base_version: comment.version, status: comment.status === "resolved" ? "open" : "resolved" } }); if (state.key === key) await reloadData(); }
    catch (error) { toast(error.message, true); if (error.status === 409 && state.key === key) await reloadData(); }
    finally { state.locks.delete(id); if (button.isConnected) button.disabled = false; }
  }
  function selectedComments(draft, comments = state.comments) { return comments.filter((comment) => draft.selected.has(comment.id) || draft.selected.has(comment.parent_id)); }
  function snapshotHTML(comments) { return comments.filter((comment) => !comment.parent_id).map((comment) => `<article class="comment-thread" data-snapshot-thread-id="${esc(comment.id)}">${threadHTML(comment, comments, { interactive: false })}</article>`).join(""); }
  function openModification() {
    if (!state.artifact || !draftFor().selected.size) return;
    const draft = draftFor(), artifact = { ...state.artifact }, room = ui()?.getCurrentRoom();
    const context = { key: state.key, artifact, commentIds: [...draft.selected], roomId: room?.id, sourceAgent: originalAgent(artifact), teamId: room?.team_id || null, team: room?.team || null };
    state.dialogDraft = context;
    $("modification-summary").innerHTML = `<strong>${esc(artifact.title)}</strong><br>版本 ${artifact.version} · ${draft.selected.size} 条讨论`;
    syncModificationAgents(context); $("modification-instructions").value = draft.instructions;
    $("modification-selected-preview").innerHTML = '<h3>选中的讨论</h3>' + snapshotHTML(selectedComments(draft));
    $("modification-submit-error").hidden = true; $("modification-dialog").showModal(); $("modification-instructions").focus();
    refreshModificationTeam(context).catch((error) => { if (state.dialogDraft === context) { $("modification-submit-error").hidden = false; $("modification-submit-error").textContent = `无法确认团队成员：${error.message}`; } });
  }
  function persistModificationDraft() { if (!state.dialogDraft) return; const draft = draftFor(state.dialogDraft.key); draft.instructions = $("modification-instructions").value; draft.agentId = $("modification-agent").value; }
  async function submitModification(event) {
    event.preventDefault(); const context = state.dialogDraft; if (!context || state.locks.has("modification")) return;
    persistModificationDraft(); const draft = draftFor(context.key);
    state.locks.add("modification"); $("submit-modification").disabled = true; $("modification-submit-error").hidden = true;
    try {
      await refreshModificationTeam(context);
      if (state.dialogDraft !== context) return;
      syncModificationAgents(context);
      if (draft.agentId === "__choose__" || (context.teamId && !context.sourceAgent && !draft.agentId) || context.team?.archived_at || !writable()) return;
      const payload = { base_version: context.artifact.version, comment_ids: context.commentIds, instructions: draft.instructions.trim(), ...(draft.agentId ? { agent_id: draft.agentId } : {}) }, requestKey = attemptFor(draft, "modificationAttempt", payload);
      await api(`/artifacts/${encodeURIComponent(context.artifact.id)}/modifications`, { method: "POST", body: { ...payload, request_key: requestKey } });
      for (const id of context.commentIds) draft.selected.delete(id);
      draft.modificationAttempt = null;
      const sameDialog = state.dialogDraft === context;
      if (draft.instructions.trim() === payload.instructions) {
        draft.instructions = "";
        if (sameDialog) $("modification-instructions").value = "";
      }
      // A response from a closed dialog may complete while another artifact's
      // draft is being edited. Its receipt must never close or clear that draft.
      if (sameDialog) $("modification-dialog").close();
      if (state.key === context.key) { if (sameDialog) showTab("requests"); renderSelection(); await reloadData(); }
      await ui()?.refreshRoom(); toast("修改请求已创建，等待 Agent 接手。");
    } catch (error) { if (state.dialogDraft === context) { $("modification-submit-error").hidden = false; $("modification-submit-error").textContent = error.message; } }
    finally { state.locks.delete("modification"); syncModificationAgents(); }
  }
  const requestLabels = { queued: "等待接手", running: "执行中", failed: "执行失败", cancelled: "已取消", completed: "已生成新版本", conflict: "待确认候选" };
  function renderModifications() {
    const list = $("modification-list"), items = [...state.modifications].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
    list.querySelectorAll(".collaboration-empty").forEach((item) => item.remove());
    for (const node of [...list.children]) if (!items.some((item) => item.id === node.dataset.revisionRequestId)) node.remove();
    for (const request of items) {
      let node = [...list.children].find((item) => item.dataset.revisionRequestId === request.id);
      if (!node) { node = document.createElement("article"); node.className = "revision-card"; node.dataset.revisionRequestId = request.id; list.append(node); }
      const label = requestLabels[request.status] || request.status;
      const html = `<div class="revision-card-header"><strong>${esc(agentName(request.agent_id))}</strong><span class="revision-state ${esc(request.status)}">${esc(label)}</span></div><p class="revision-card-meta">基于版本 ${request.base_version}${request.result_version ? ` → 版本 ${request.result_version}` : ""} · ${esc(stamp(request.created_at))}</p>${request.status === "queued" ? `<p class="revision-card-note">等待 ${esc(agentName(request.agent_id))} 接手</p>` : ""}${request.status === "conflict" ? '<p class="revision-card-note">执行已完成。当前成果已有新版本，候选已保留。</p>' : ""}${request.error ? `<p class="revision-card-error">${esc(request.error)}</p>` : ""}<div class="revision-card-actions"><button data-modification-source="${esc(request.id)}" aria-label="查看修改来源">查看来源</button>${request.result_version ? `<button class="revision-primary-action" data-modification-result="${esc(request.id)}" aria-label="查看结果版本">查看版本 ${request.result_version} ↗</button>` : ""}${request.candidate && !request.result_version ? `<button class="revision-primary-action" data-modification-candidate="${esc(request.id)}" aria-label="对比候选版本">对比候选版本</button>` : ""}${writable() && ["queued", "running"].includes(request.status) ? `<button data-modification-cancel="${esc(request.id)}" aria-label="取消修改请求">取消</button>` : ""}${writable() && request.status === "failed" ? `<button data-modification-retry="${esc(request.id)}" aria-label="重试修改请求">重试</button>` : ""}</div>`;
      if (node.innerHTML !== html) node.innerHTML = html;
    }
    for (const request of items) { const node = [...list.children].find((item) => item.dataset.revisionRequestId === request.id); if (node) list.append(node); }
    if (!items.length) list.innerHTML = '<p class="collaboration-empty">选中讨论后，可以交给 Agent 修改。</p>';
    $("collaboration-request-total").textContent = items.length || "";
  }
  async function requestRunAction(id, action, button) {
    const request = state.modifications.find((item) => item.id === id); if (!request || state.locks.has(id)) return;
    state.locks.add(id); button.disabled = true;
    try { await api(`/runs/${encodeURIComponent(request.run_id)}/${action}`, { method: "POST", body: {} }); await reloadData(); await ui()?.refreshRoom(); }
    catch (error) { toast(error.message, true); }
    finally { state.locks.delete(id); if (button.isConnected) button.disabled = false; }
  }
  async function openSource(id) {
    const dialog = $("modification-source-dialog"); $("modification-source-content").innerHTML = '<p class="collaboration-empty">正在读取修改来源…</p>'; $("open-modification-baseline").disabled = true; state.sourceRequest = null; dialog.showModal();
    try {
      const request = await api(`/modifications/${encodeURIComponent(id)}`); if (!dialog.open) return; state.sourceRequest = request;
      const snapshot = request.snapshot || {}, source = snapshot.source || {};
      $("modification-source-content").innerHTML = `<dl class="revision-source-metadata"><div><dt>引用版本</dt><dd>版本 ${request.base_version}</dd></div><div><dt>负责修改</dt><dd>${esc(snapshot.agent?.name || agentName(request.agent_id))}</dd></div><div><dt>原成果 Agent</dt><dd>${esc(source.agent_name || agentName(source.agent_id))}</dd></div><div><dt>提交人</dt><dd>${esc(snapshot.created_by_name || request.created_by_name || "成员")}</dd></div></dl>${snapshot.instructions ? `<p class="revision-card-note">${esc(snapshot.instructions)}</p>` : ""}<div class="revision-snapshot" data-modification-snapshot="${esc(id)}"><h3>提交时的讨论</h3>${snapshotHTML(snapshot.comments || [])}</div><details class="revision-baseline-details"><summary>查看提交时的原文</summary><pre>${esc(snapshot.baseline?.content || "")}</pre></details>`;
      $("open-modification-baseline").disabled = false;
    } catch (error) { $("modification-source-content").innerHTML = `<p class="collaboration-error">${esc(error.message)}</p>`; }
  }
  async function openCandidate(id) {
    const dialog = $("modification-candidate-dialog"); state.candidate = null; $("apply-modification-candidate").disabled = true; $("candidate-apply-conflict").hidden = true; $("candidate-current-content").textContent = "正在读取…"; $("candidate-proposed-content").textContent = ""; $("candidate-intro").textContent = ""; $("candidate-apply-note").textContent = ""; dialog.showModal();
    try {
      const request = await api(`/modifications/${encodeURIComponent(id)}`); if (!request.candidate?.content && request.candidate?.content !== "") throw new Error("这个请求没有可应用的候选内容。");
      const latest = await api(`/artifacts/${encodeURIComponent(request.artifact_id)}`); if (!dialog.open) return;
      state.candidate = { request, latest, applyKey: uuid(), stale: false }; renderCandidate();
    } catch (error) { $("candidate-current-content").textContent = ""; $("candidate-intro").textContent = error.message; }
  }
  function renderCandidate() {
    const current = state.candidate; if (!current) return; const { request, latest } = current;
    $("candidate-intro").textContent = `Agent 基于版本 ${request.base_version} 完成了修改。请对照当前版本 ${latest.version}，确认是否使用候选全文。`;
    $("candidate-current-version").textContent = `版本 ${latest.version}`; $("candidate-source-version").textContent = `基于版本 ${request.base_version}`;
    $("candidate-current-content").textContent = latest.content; $("candidate-proposed-content").textContent = request.candidate.content;
    $("candidate-apply-note").textContent = `应用会生成新版本，当前版本 ${latest.version} 仍保留。`;
    $("apply-modification-candidate").disabled = current.stale || !!request.result_version || !writable();
  }
  async function reloadCandidate() {
    if (!state.candidate) return; const current = state.candidate; $("reload-candidate-comparison").disabled = true;
    try { const latest = await api(`/artifacts/${encodeURIComponent(current.request.artifact_id)}`); if (state.candidate !== current) return; current.latest = latest; current.stale = false; $("candidate-apply-conflict").hidden = true; renderCandidate(); }
    catch (error) { toast(error.message, true); }
    finally { $("reload-candidate-comparison").disabled = false; }
  }
  async function applyCandidate() {
    const current = state.candidate; if (!current || current.stale || state.locks.has("candidate")) return;
    state.locks.add("candidate"); $("apply-modification-candidate").disabled = true;
    try {
      const result = await api(`/modifications/${encodeURIComponent(current.request.id)}/apply`, { method: "POST", body: { base_version: current.latest.version, request_key: current.applyKey } });
      $("modification-candidate-dialog").close(); showTab("requests"); await reloadData(); await ui()?.refreshRoom(); toast(`候选已保存为版本 ${result.result_version}。`);
    } catch (error) {
      $("candidate-apply-conflict").hidden = false; $("candidate-apply-conflict").querySelector("span").textContent = error.status === 409 ? "当前成果已更新，候选仍保留。重新对比最新版本后，再决定是否应用。" : error.message;
      current.stale = error.status === 409; $("reload-candidate-comparison").hidden = !current.stale;
    } finally { state.locks.delete("candidate"); if (state.candidate === current) renderCandidate(); }
  }
  function quoteSelection() { const selected = state.selectedQuote; $("comment-anchor-mode").value = "quote"; $("comment-quote").hidden = false; if (selected) $("comment-quote").value = selected.slice(0, 2000); persistComposer(); $("comment-quote").focus(); }

  $("toggle-artifact-comments").addEventListener("click", () => { syncArtifact(); setOpen(!state.open); });
  $("close-artifact-comments").addEventListener("click", () => setOpen(false));
  const compactComments = window.matchMedia("(max-width:850px)");
  function labelCommentsBack() { $("close-artifact-comments").setAttribute("aria-label", compactComments.matches ? "返回正文" : "关闭评论"); }
  compactComments.addEventListener("change", labelCommentsBack); labelCommentsBack();
  $("comment-compose").addEventListener("submit", postComment);
  for (const id of ["comment-body", "comment-quote"]) $(id).addEventListener("input", persistComposer);
  $("comment-anchor-mode").addEventListener("change", () => { $("comment-quote").hidden = $("comment-anchor-mode").value !== "quote"; persistComposer(); });
  $("quote-selected-text").addEventListener("click", quoteSelection);
  $("comment-filter").addEventListener("change", () => { state.filter = $("comment-filter").value; renderThreads(); });
  $("clear-selected-comments").addEventListener("click", () => { draftFor().selected.clear(); renderThreads(); });
  $("open-modification").addEventListener("click", openModification);
  $("modification-form").addEventListener("submit", submitModification);
  $("modification-instructions").addEventListener("input", persistModificationDraft); $("modification-agent").addEventListener("change", () => { persistModificationDraft(); syncModificationAgents(); });
  $("modification-dialog").addEventListener("close", () => { persistModificationDraft(); state.dialogDraft = null; });
  $("refresh-modifications").addEventListener("click", reloadData);
  $("artifact-modification-origin").addEventListener("click", () => { if (state.artifact?.modification_request_id) openSource(state.artifact.modification_request_id); });
  $("open-modification-baseline").addEventListener("click", () => { const request = state.sourceRequest; if (!request) return; $("modification-source-dialog").close(); ui()?.openArtifact(request.artifact_id, request.base_version); });
  $("apply-modification-candidate").addEventListener("click", applyCandidate);
  $("reload-candidate-comparison").addEventListener("click", reloadCandidate);
  $("modification-candidate-dialog").addEventListener("close", () => { state.candidate = null; });
  $("comment-thread-list").addEventListener("input", (event) => { const reply = event.target.closest("[data-reply-form]"), edit = event.target.closest("[data-edit-form]"); if (reply && state.replyDrafts.has(reply.dataset.replyForm)) state.replyDrafts.get(reply.dataset.replyForm).body = event.target.value; if (edit && state.editDrafts.has(edit.dataset.editCommentId)) state.editDrafts.get(edit.dataset.editCommentId).body = event.target.value; });
  $("comment-thread-list").addEventListener("change", (event) => { if (!event.target.matches("[data-select-thread]")) return; const id = event.target.dataset.selectThread; event.target.checked ? draftFor().selected.add(id) : draftFor().selected.delete(id); event.target.closest("[data-thread-id]").classList.toggle("is-selected", event.target.checked); renderSelection(); });
  $("comment-thread-list").addEventListener("submit", (event) => { event.preventDefault(); if (event.target.matches("[data-reply-form]")) sendReply(event.target); if (event.target.matches("[data-edit-form]")) saveCommentEdit(event.target); });
  document.addEventListener("selectionchange", () => { const selection = window.getSelection(); if (!selection?.rangeCount || selection.isCollapsed) return; const range = selection.getRangeAt(0); if ($("artifact-body").contains(range.commonAncestorContainer)) state.selectedQuote = selection.toString(); });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button"); if (!button || button.disabled) return;
    if (button.dataset.collaborationTab) showTab(button.dataset.collaborationTab);
    if (button.hasAttribute("data-retry-collaboration")) reloadData();
    if (button.dataset.replyComment) openReply(button.dataset.replyComment);
    if (button.dataset.editComment) openCommentEdit(button.dataset.editComment, button.dataset.rootComment);
    if (button.dataset.statusComment) changeCommentStatus(button.dataset.statusComment, button);
    if (button.hasAttribute("data-hide-comment-form")) button.closest("form").hidden = true;
    if (button.hasAttribute("data-cancel-comment-edit")) { const form = button.closest("form"); state.editDrafts.delete(form.dataset.editCommentId); form.hidden = true; }
    if (button.dataset.rebaseComment) { const id = button.dataset.rebaseComment; button.disabled = true; api(`/comments/${encodeURIComponent(id)}`).then((comment) => { const draft = state.editDrafts.get(id); if (draft) draft.baseVersion = comment.version; button.closest(".collaboration-error").hidden = true; toast("已读取最新修订，草稿保留。请确认后保存。"); }).catch((error) => toast(error.message, true)).finally(() => { if (button.isConnected) button.disabled = false; }); }
    if (button.dataset.modificationSource) openSource(button.dataset.modificationSource);
    if (button.dataset.modificationCandidate) openCandidate(button.dataset.modificationCandidate);
    if (button.dataset.modificationCancel) requestRunAction(button.dataset.modificationCancel, "cancel", button);
    if (button.dataset.modificationRetry) requestRunAction(button.dataset.modificationRetry, "retry", button);
    if (button.dataset.modificationResult) { const request = state.modifications.find((item) => item.id === button.dataset.modificationResult); if (request?.result_version) ui()?.openArtifact(request.artifact_id, request.result_version); }
    if (["edit-artifact", "artifact-preview-tab"].includes(button.id) && state.open && innerWidth <= 850) setOpen(false, false);
    if (button.id === "expand-workspace" && !$("workspace-shell").classList.contains("studio-open")) setOpen(false, false);
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && state.open && !document.querySelector("dialog[open]") && !$("detail-panel").hidden) { event.preventDefault(); event.stopImmediatePropagation(); setOpen(false); } }, true);
  document.querySelector(".collaboration-tabs").addEventListener("keydown", (event) => { if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key) || !event.target.matches("[role=tab]")) return; event.preventDefault(); const tab = event.key === "Home" ? "comments" : event.key === "End" ? "requests" : state.tab === "comments" ? "requests" : "comments"; showTab(tab); $(`collaboration-${tab}-tab`).focus(); });
  window.addEventListener("workspace-artifact", syncArtifact);
  window.addEventListener("workspace-room", () => { syncArtifact(); syncModificationAgents(); if (state.open && !$("panel-artifacts").hidden) loadData(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.open) loadData(true); });
  setInterval(() => { if (!document.hidden && state.open && !$("detail-panel").hidden && !$("panel-artifacts").hidden) loadData(); }, 3000);
})();

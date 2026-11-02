"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const ui = () => window.workspaceUI;
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const writable = () => document.documentElement.dataset.access !== "viewer";
  const toast = (message, error = false) => ui()?.toast(message, error);
  const stamp = (value, seconds = false) => { if (!value) return ""; const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", ...(seconds ? { second: "2-digit" } : {}) }); };
  const paths = { list: '<path d="M8 6h12M8 12h12M8 18h12M3 6h.01M3 12h.01M3 18h.01"/>', close: '<path d="m6 6 12 12M18 6 6 18"/>', refresh: '<path d="M20 8a8 8 0 1 0 .2 7M20 3v5h-5"/>', check: '<path d="m5 12 4 4L19 6"/>', tool: '<path d="m13 7 4 4m-2-8 6 6-4 4-2-2-9 9H3v-3l9-9-2-2 5-5Z"/>', clock: '<circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/>', alert: '<path d="m12 3 10 18H2L12 3Zm0 6v5m0 3h.01"/>', session: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m7 9 3 3-3 3m6 0h4"/>', person: '<circle cx="12" cy="7" r="3"/><path d="M5 21v-3a7 7 0 0 1 14 0v3"/>', stop: '<rect x="5" y="5" width="14" height="14" rx="3"/>' };
  const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.list}</svg>`;
  const decisions = { start: "首次执行", restart: "重新执行", continue: "继续已有宿主会话", submit_result: "提交已保存的结果", review_required: "等待核对", completed: "已完成", cancelled: "已取消" };
  const taskStates = { queued: "等待接手", running: "执行中", completed: "已完成", failed: "执行已中断", cancelled: "已取消" };
  const sessionReasons = { first_turn: "这是这个 Agent 在对话中的第一次工作。", matching_completed_turn: "已匹配上次完成工作的上下文。", task_recovery_priority: "沿用这项任务的恢复决定。", no_completed_checkpoint: "上次完成的工作没有可续接记录。", worker_changed: "执行端已变更。", host_changed: "宿主环境已变更。", continuation_unsupported: "当前执行端不支持续接。", agent_instructions_changed: "Agent 指令已变更。", skills_changed: "绑定的技能已变更。", team_permissions_changed: "团队成员或权限已变更。", source_visibility_changed: "可使用的资料已变更。", shared_context_changed: "对话背景已变更。", previous_turn_incomplete: "上一次工作未正常完成。", context_snapshot_unavailable: "此前的工作上下文不可用。" };
  let sessionHistory = null, dialogGeneration = 0, outputRead = null;
  const artifactMetadata = new Map();
  const eventTitles = { "attempt.started": "执行端已接手", progress: "执行进度", host_session: "宿主会话已记录", checkpoint: "会话检查点已记录", tool_call: "发起工具调用", tool_result: "工具调用结果", "result.ready": "结果已保存，等待提交", "task.completed": "任务已完成", "task.failed": "执行已中断", "task.cancelled": "任务已取消", "recovery.required": "需要核对外部调用", "recovery.reviewed": "核对决定已记录" };
  const callStates = { pending: "尚无结果", unknown: "结果未确认", succeeded: "已完成", not_executed: "未执行" };
  const reviewDecisions = { confirmed_succeeded: "已确认调用完成", confirmed_not_executed: "已确认没有执行", terminate: "终止本次协作" };
  const reasons = { completion_committed: "完成结果已提交", run_cancelled: "本次协作已取消", external_outcome_unknown: "外部调用的结果尚未确认，需要先核对实际状态。", final_result_durably_staged: "最终结果已保存，无需重新生成。", first_attempt: "首次领取这项任务。", matching_host_checkpoint: "执行端与已有宿主会话匹配。", checkpoint_unavailable_or_host_mismatch: "未匹配到可直接续接的宿主会话。", attempt_limit_reached: "执行次数达到上限，请新建任务。" };
  const state = { taskId: null, task: null, execution: null, events: new Map(), cursor: 0, hasMore: false, eventsLoaded: false, selectedAttempt: null, selectedLatest: true, epoch: 0, loading: false, loadedAt: 0, attemptsSignature: "", summarySignature: "", recoverySignature: "", drafts: new Map(), busy: false, pendingTasks: new Set(), retrying: new Set(), reviewOpen: false, acknowledgementAttempt: null };
  async function api(path, { method = "GET", body } = {}) {
    const response = await fetch(`/api/hub${path}`, { method, headers: body === undefined ? {} : { "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    let data; try { data = await response.json(); } catch { throw new Error("服务返回了无效响应。"); }
    if (!response.ok) { const error = new Error(data.error || "操作未完成，请重试。"); error.status = response.status; throw error; }
    return data;
  }
  function findTask(id) { for (const run of ui()?.getCurrentRoom()?.runs || []) { const task = (run.tasks || []).find((item) => item.id === id); if (task) return { ...task, run_id: run.id }; } return null; }
  function draftFor(id = state.taskId) { if (!state.drafts.has(id)) state.drafts.set(id, { note: "", decision: "", callId: "", needsCallChoice: false, attempt: null }); return state.drafts.get(id); }
  function currentAttempt() { return [...(state.execution?.attempts || [])].sort((a, b) => b.attempt - a.attempt)[0] || null; }
  function unsafeCalls() { return (state.execution?.recovery?.calls || []).filter((call) => !call.retry_safe && ["pending", "unknown"].includes(call.state)); }
  function needsOutcomeReview() { if (typeof state.execution?.outcome_review_required === "boolean") return state.execution.outcome_review_required; return state.execution?.recovery?.decision === "review_required" || (state.execution?.task_status === "cancelled" && state.execution.can_reconcile); }
  function modeLabel(value) { return decisions[value] || value || "尚未领取"; }
  function taskStatus() { return needsOutcomeReview() ? (state.execution?.task_status === "cancelled" ? "已取消 · 结果待核对" : "等待核对") : taskStates[state.execution?.task_status] || state.execution?.task_status || "正在读取"; }

  document.body.insertAdjacentHTML("beforeend", `
    <dialog class="app-dialog execution-dialog" id="execution-dialog" aria-labelledby="execution-title"><header class="dialog-header"><div class="execution-heading"><h2 id="execution-title">执行记录</h2><p id="execution-task-title"></p></div><div class="execution-dialog-actions"><button class="icon-button" type="button" id="refresh-execution" aria-label="刷新执行记录" title="刷新执行记录">${icon("refresh")}</button><button class="icon-button" type="button" data-close-dialog aria-label="关闭执行记录">${icon("close")}</button></div></header>
      <dl class="execution-overview"><div><dt>任务状态</dt><dd><span class="execution-status" id="execution-task-status">正在读取</span></dd></div><div><dt>当前执行批次</dt><dd id="execution-current-attempt">—</dd></div><div><dt>执行端</dt><dd id="execution-current-worker">—</dd></div></dl>
      <details class="execution-agent-session" id="execution-agent-session" hidden><summary aria-label="查看 Agent 会话历程"><span><small>最近一次工作上下文</small><strong id="execution-agent-session-heading"></strong></span><span class="execution-agent-session-mode" id="execution-agent-session-mode"></span></summary><div class="execution-agent-session-body"><p id="execution-agent-session-reason"></p><div class="execution-session-toolbar"><span id="execution-agent-session-status"></span><button class="small-button" id="refresh-agent-session" type="button" aria-label="刷新会话历程">刷新历程</button></div><p class="execution-session-error" id="execution-agent-session-error" role="alert" hidden></p><div id="execution-agent-session-blocked" class="execution-session-blocked" hidden></div><ol class="execution-session-turns" id="execution-agent-session-turns"></ol><section id="execution-agent-delegations" hidden><h3>委派关系</h3><ul id="execution-agent-delegation-list"></ul></section><button class="small-button" id="execution-agent-session-more" type="button" hidden>读取更多工作记录</button></div></details>
      <p class="execution-load-error" id="execution-load-error" role="alert" hidden></p>
      <div class="execution-layout"><aside class="execution-attempts" aria-label="执行批次"><h3 class="execution-attempts-heading">执行批次</h3><div class="execution-attempt-list" id="execution-attempt-list" role="tablist" aria-label="执行批次列表"></div></aside><section class="execution-main" aria-label="执行时间线"><div class="execution-attempt-summary"><div><strong id="execution-selected-heading">正在读取记录</strong><p id="execution-selected-subtitle"></p></div><span class="execution-live-note" id="execution-record-count"></span></div><div class="execution-review-banner" id="execution-review-banner" hidden><strong id="execution-review-heading"></strong><p>这些调用可能已经产生外部变更。请先核对实际结果，避免重复操作。</p><button class="execution-mutation" id="show-execution-review" type="button">核对调用</button></div><div class="execution-event-scroll" id="execution-event-scroll"><ol class="execution-events" id="execution-events"></ol><p class="execution-empty" id="execution-events-empty">正在读取执行记录…</p><button class="small-button" id="execution-load-more" hidden>读取更多事件</button></div></section></div>
      <section class="execution-recovery" id="execution-recovery"><div class="execution-recovery-summary"><div><h3 id="execution-recovery-title"></h3><p id="execution-recovery-description"></p><details class="execution-session-info" id="execution-session-info" hidden><summary>查看宿主会话记录</summary><pre id="execution-session-content"></pre></details></div></div><div class="execution-task-outputs" id="execution-task-outputs" aria-label="本次成果" hidden></div><p class="execution-outputs-error" id="execution-outputs-error" role="status" hidden></p>
        <form class="execution-decision execution-mutation" id="execution-review-form" hidden><h4>记录核对结果</h4><label for="execution-review-call">需要核对的调用</label><select id="execution-review-call" aria-label="需要核对的调用"></select><div class="execution-call" id="execution-selected-call" hidden></div><fieldset><legend class="sr-only">核对结果</legend><label><input type="radio" name="execution-decision" value="confirmed_succeeded"><span>已确认调用完成<small>已在对应系统核对，操作已经完成。</small></span></label><label><input type="radio" name="execution-decision" value="confirmed_not_executed"><span>已确认没有执行<small>已核实该操作没有产生外部变更。</small></span></label><label><input type="radio" name="execution-decision" value="terminate"><span>终止本次协作<small>取消这次协作中所有未完成的任务。</small></span></label></fieldset><label for="execution-review-note">核对说明</label><textarea id="execution-review-note" aria-label="核对说明" placeholder="记录你核对到的结果和依据…" maxlength="2000" rows="3" required></textarea><p class="execution-decision-error" id="execution-review-error" role="alert" hidden></p><div class="execution-decision-actions"><button class="secondary-button" id="hide-execution-review" type="button">收起</button><button class="primary-button" id="submit-execution-review" type="submit" aria-label="保存核对结果">保存核对结果</button></div></form>
        <div class="execution-recovery-actions"><button class="secondary-button execution-stop execution-mutation" id="cancel-execution-run" type="button" aria-label="停止本次协作" hidden>停止本次协作</button><span class="execution-recovery-note" id="execution-action-note"></span><button class="secondary-button execution-mutation" id="review-execution-button" type="button" hidden>核对外部调用</button><button class="primary-button execution-mutation" id="requeue-execution" type="button" aria-label="重新排队" hidden>重新排队</button></div><p class="execution-acknowledgement" id="execution-acknowledgement" role="status" hidden></p>
      </section>
    </dialog>`);

  function setError(message) { const node = $("execution-load-error"); node.hidden = !message; node.innerHTML = message ? `${esc(message)} <button type="button" data-retry-execution>重新读取</button>` : ""; }
  function persistDraft() { if (!state.taskId) return; const draft = draftFor(); draft.note = $("execution-review-note").value; draft.callId = $("execution-review-call").value; draft.decision = document.querySelector('input[name="execution-decision"]:checked')?.value || ""; }
  function restoreDraft() { const draft = draftFor(); $("execution-review-note").value = draft.note; document.querySelectorAll('input[name="execution-decision"]').forEach((input) => { input.checked = input.value === draft.decision; }); }
  function updateBusy() {
    state.busy = state.pendingTasks.has(state.taskId);
    $("submit-execution-review").disabled = state.busy || !state.execution?.can_reconcile;
    $("requeue-execution").disabled = state.busy || !state.execution?.can_retry;
    $("cancel-execution-run").disabled = state.busy || !state.execution?.can_cancel;
    $("refresh-execution").disabled = state.loading;
  }
  async function openExecution(taskId, initial = null) {
    dialogGeneration++;
    state.eventsLoaded = false; outputRead = null; $("execution-task-outputs").hidden = true; $("execution-task-outputs").replaceChildren(); $("execution-outputs-error").hidden = true;
    sessionHistory = null; $("execution-agent-session").open = false; $("execution-agent-session").hidden = true; $("execution-agent-session-turns").replaceChildren(); $("execution-agent-delegation-list").replaceChildren();
    persistDraft(); state.epoch++; state.taskId = taskId; state.task = findTask(taskId); state.execution = initial; state.events = new Map(); state.cursor = 0; state.hasMore = false; state.selectedAttempt = null; state.selectedLatest = true; state.loading = false; state.loadedAt = 0; state.attemptsSignature = ""; state.summarySignature = ""; state.recoverySignature = ""; state.reviewOpen = false; state.busy = state.pendingTasks.has(taskId); state.acknowledgementAttempt = null;
    $("execution-dialog").dataset.executionTaskId = taskId; $("execution-task-title").textContent = state.task?.title || "任务执行记录";
    $("execution-attempt-list").replaceChildren(); $("execution-events").replaceChildren(); $("execution-events-empty").hidden = false; $("execution-events-empty").textContent = "正在读取执行记录…"; $("execution-selected-heading").textContent = "正在读取记录"; $("execution-selected-subtitle").textContent = ""; $("execution-record-count").textContent = ""; $("execution-load-more").hidden = true; $("execution-review-form").hidden = true; $("execution-review-error").hidden = true; $("execution-acknowledgement").hidden = true; setError(""); restoreDraft();
    if (!$("execution-dialog").open) $("execution-dialog").showModal();
    if (initial) renderExecution(); else { $("execution-task-status").textContent = "正在读取"; $("execution-task-status").dataset.state = ""; $("execution-current-attempt").textContent = "—"; $("execution-current-worker").textContent = "—"; $("execution-recovery").hidden = true; $("execution-review-banner").hidden = true; }
    await loadExecution(true);
  }
  async function loadExecution(force = false) {
    if (!state.taskId || !$("execution-dialog").open || state.loading || (!force && Date.now() - state.loadedAt < 1800)) return;
    const epoch = state.epoch, taskId = state.taskId; state.loading = true; updateBusy();
    try {
      const execution = await api(`/tasks/${encodeURIComponent(taskId)}/execution`); if (epoch !== state.epoch) return;
      if (state.execution && state.execution.id !== execution.id) { state.events.clear(); state.cursor = 0; }
      if (state.cursor < execution.last_seq) state.eventsLoaded = false;
      state.execution = execution; state.task = findTask(taskId) || state.task;
      renderExecution(); setError("");
      for (let page = 0; page < 4; page++) {
        const result = await api(`/executions/${encodeURIComponent(execution.id)}/events?after=${state.cursor}&limit=100`); if (epoch !== state.epoch) return;
        for (const event of result.events || []) state.events.set(event.seq, event);
        state.cursor = result.cursor; state.hasMore = !!result.has_more; renderEvents();
        if (!state.hasMore) break;
      }
      state.eventsLoaded = !state.hasMore; renderAgentSession(); loadTaskOutputs();
      state.loadedAt = Date.now();
    } catch (error) { if (epoch === state.epoch) { setError(error.message); if (!state.execution) $("execution-events-empty").textContent = "未能读取执行记录"; } }
    finally { if (epoch === state.epoch) { state.loading = false; updateBusy(); } }
  }
  async function refreshAfterAction() { state.loadedAt = 0; if (state.loading) { state.epoch++; state.loading = false; } await loadExecution(true); await ui()?.refreshRoom(); }
  function renderExecution() {
    const execution = state.execution; if (!execution) return;
    const latest = currentAttempt(), attempts = [...execution.attempts].sort((a, b) => b.attempt - a.attempt);
    if (state.acknowledgementAttempt !== null && latest?.attempt > state.acknowledgementAttempt) { $("execution-acknowledgement").hidden = true; state.acknowledgementAttempt = null; }
    if (state.selectedLatest || !attempts.some((item) => item.attempt === state.selectedAttempt)) state.selectedAttempt = latest?.attempt ?? null;
    $("execution-task-title").textContent = state.task?.title || "任务执行记录";
    $("execution-task-status").textContent = taskStatus(); $("execution-task-status").dataset.state = needsOutcomeReview() ? "needs_review" : execution.task_status;
    $("execution-current-attempt").textContent = latest ? `第 ${latest.attempt} 次 · ${modeLabel(latest.decision)}` : "尚未领取";
    $("execution-current-worker").textContent = latest?.worker_id || "等待执行端接手";
    const signature = JSON.stringify({ attempts, selected: state.selectedAttempt });
    if (signature !== state.attemptsSignature) {
      state.attemptsSignature = signature; const list = $("execution-attempt-list"), active = document.activeElement?.dataset.executionAttempt;
      list.innerHTML = attempts.length ? attempts.map((attempt) => `<button class="execution-attempt" role="tab" data-execution-attempt="${attempt.attempt}" data-execution-attempt-id="${esc(execution.id)}:${attempt.attempt}" aria-label="第 ${attempt.attempt} 次执行" aria-selected="${attempt.attempt === state.selectedAttempt}" tabindex="${attempt.attempt === state.selectedAttempt ? 0 : -1}"><strong>第 ${attempt.attempt} 次${attempt.attempt === latest.attempt ? '<span class="execution-attempt-current">当前</span>' : ""}</strong><small>${esc(modeLabel(attempt.decision))}</small><small>${esc(stamp(attempt.started_at))}</small></button>`).join("") : '<p class="execution-empty">执行端领取后会留下记录。</p>';
      if (active) list.querySelector(`[data-execution-attempt="${active}"]`)?.focus({ preventScroll: true });
    }
    const selected = attempts.find((attempt) => attempt.attempt === state.selectedAttempt);
    $("execution-selected-heading").textContent = selected ? `第 ${selected.attempt} 次执行 · ${modeLabel(selected.decision)}` : "等待执行端接手";
    $("execution-selected-subtitle").textContent = selected ? `${selected.worker_id} · ${stamp(selected.started_at, true)}` : "任务尚未被领取。";
    renderAgentSession(); renderRecovery(); renderEvents();
  }
  function sessionMode(continuation) { return continuation?.decision === "continue" ? "已选择接着上次工作" : continuation?.decision === "task_recovery" ? "按本任务记录恢复" : continuation?.decision === "fresh" ? "新的工作上下文" : "上下文方式未提供"; }
  function continuationReceipt(info) {
    if (info.continuation?.decision !== "continue") return { label: sessionMode(info.continuation), status: info.continuation?.decision || "unknown" };
    const expected = info.continuation.checkpoint, attempt = currentAttempt()?.attempt;
    const matched = expected && [...state.events.values()].some((event) => event.attempt === attempt && event.kind === "host_session" && event.payload.host === expected.host && event.payload.session_id === expected.session_id && event.payload.host_identity === expected.host_identity);
    if (matched) return { label: "已收到续接回执", status: "confirmed", detail: "执行端回传的宿主会话与上次工作匹配。" };
    if (!state.eventsLoaded || state.hasMore) return { label: "已选择续接 · 正在读取回执", status: "loading" };
    return { label: state.execution.active ? "已选择续接 · 等待回执" : "已选择续接 · 未收到匹配回执", status: state.execution.active ? "awaiting" : "not_reported" };
  }
  function renderAgentSession() {
    const info = state.execution?.agent_session, panel = $("execution-agent-session"); panel.hidden = !info;
    if (!info) { sessionHistory = null; panel.open = false; return; }
    panel.dataset.agentSessionId = info.id;
    if (sessionHistory?.id !== info.id) sessionHistory = { id: info.id, session: null, turns: new Map(), delegations: new Map(), cursor: 0, hasMore: false, loading: false, loaded: false };
    const agent = ui()?.getAgents()?.find((item) => item.id === info.agent_id);
    $("execution-agent-session-heading").textContent = `${agent?.name || sessionHistory.session?.agent_name || "Agent"} · 第 ${info.turn_index} 次工作`;
    const receipt = continuationReceipt(info); $("execution-agent-session-mode").textContent = receipt.label; $("execution-agent-session-mode").dataset.continuationState = receipt.status;
    $("execution-agent-session-reason").textContent = receipt.detail || sessionReasons[info.continuation?.reason] || (info.continuation?.reason ? `上下文原因：${info.continuation.reason}` : "服务未提供上下文原因。");
    if (panel.open && !sessionHistory.loaded && !sessionHistory.loading) loadAgentSession();
  }
  async function loadAgentSession(more = false) {
    const history = sessionHistory, epoch = state.epoch, taskId = state.taskId;
    if (!history || history.loading || !$("execution-dialog").open || !$("execution-agent-session").open) return;
    const owns = () => sessionHistory === history && state.epoch === epoch && state.taskId === taskId && $("execution-dialog").open && $("execution-agent-session").open;
    history.loading = true; $("refresh-agent-session").disabled = true; $("execution-agent-session-more").disabled = true; $("execution-agent-session-error").hidden = true;
    if (!history.loaded) $("execution-agent-session-status").textContent = "正在读取工作历程…";
    try {
      const result = await api(`/agent-sessions/${encodeURIComponent(history.id)}?after=${more ? history.cursor : 0}&limit=50`);
      if (!owns()) return;
      if (result.session?.id !== history.id) throw new Error("会话记录不匹配，请重新读取。");
      if (!more) { history.turns.clear(); history.delegations.clear(); }
      for (const turn of result.turns || []) history.turns.set(turn.task_id, turn);
      for (const edge of result.delegations || []) history.delegations.set(edge.id, edge);
      history.session = result.session; history.cursor = result.cursor; history.hasMore = !!result.has_more; history.loaded = true;
      renderSessionHistory();
    } catch (error) { if (owns()) { $("execution-agent-session-error").hidden = false; $("execution-agent-session-error").textContent = error.status === 404 ? "这份会话历程当前不可用。你仍可查看下方任务记录。" : error.message; if (!history.loaded) $("execution-agent-session-status").textContent = "未能读取历程"; } }
    finally { history.loading = false; if (sessionHistory === history && state.epoch === epoch) { $("refresh-agent-session").disabled = false; $("execution-agent-session-more").disabled = false; } }
  }
  function sessionTaskButton(taskId, fallback = "") {
    const task = sessionHistory?.turns.get(taskId) || findTask(taskId), title = task?.title || fallback || `任务 ${taskId}`;
    return `<button type="button" class="execution-session-task" data-session-task="${esc(taskId)}" aria-label="查看工作记录：${esc(title)}">${esc(title)}</button>`;
  }
  function renderSessionHistory() {
    const history = sessionHistory, summary = history?.session; if (!summary) return;
    const status = { idle: "当前空闲", running: "正在工作", blocked: "等待处理" }[summary.status] || summary.status;
    $("execution-agent-session-status").textContent = `${summary.agent_name} · ${summary.turn_count} 次工作 · ${status}`;
    const block = $("execution-agent-session-blocked"); block.hidden = summary.status !== "blocked";
    block.innerHTML = summary.status === "blocked" ? `<p>${esc(summary.blocked_message || "此前的工作需要处理。")}</p>${summary.blocked_by_task_id ? sessionTaskButton(summary.blocked_by_task_id, "查看待处理工作") : ""}` : "";
    $("execution-agent-session-turns").innerHTML = [...history.turns.values()].sort((a, b) => a.turn_index - b.turn_index).map((turn) => `<li data-agent-session-task-id="${esc(turn.task_id)}" data-agent-session-turn-index="${turn.turn_index}"${turn.task_id === state.taskId ? ' aria-current="true"' : ""}><span class="execution-session-turn-number">${turn.turn_index}</span><div>${sessionTaskButton(turn.task_id)}<p>${esc(taskStates[turn.status] || turn.status)}${turn.latest_attempt ? ` · ${esc(sessionMode(turn.latest_attempt.continuation))}` : " · 尚未领取"}</p></div><time title="${esc(turn.created_at)}">${esc(stamp(turn.created_at))}</time></li>`).join("") || '<li class="execution-empty">还没有工作记录。</li>';
    const edges = [...history.delegations.values()]; $("execution-agent-delegations").hidden = !edges.length;
    $("execution-agent-delegation-list").innerHTML = edges.map((edge) => `<li data-delegation-id="${esc(edge.id)}" data-parent-task-id="${esc(edge.parent_task_id)}" data-child-task-id="${esc(edge.child_task_id)}">${sessionTaskButton(edge.parent_task_id)}<span>委派给</span>${sessionTaskButton(edge.child_task_id)}<details><summary>查看关联记录</summary><p>来源会话：${esc(edge.parent_session_id)}<br>目标会话：${esc(edge.child_session_id)}<br>运行：${esc(edge.run_id)}</p></details></li>`).join("");
    $("execution-agent-session-more").hidden = !history.hasMore;
  }
  async function loadTaskOutputs(force = false) {
    const room = ui()?.getCurrentRoom(); if (state.execution?.task_status !== "completed" || !room || !findTask(state.taskId)) return;
    const taskId = state.taskId, generation = dialogGeneration, signature = JSON.stringify([room.id, taskId, (room.artifacts || []).map((item) => [item.id, item.version])]);
    if (!force && outputRead?.signature === signature) return;
    const read = { signature, items: [], loading: true }; outputRead = read;
    const owns = () => outputRead === read && dialogGeneration === generation && state.taskId === taskId && ui()?.getCurrentRoom()?.id === room.id && $("execution-dialog").open;
    $("execution-outputs-error").hidden = true;
    try {
      const pending = [...(room.artifacts || [])], found = [];
      await Promise.all(Array.from({ length: Math.min(3, pending.length) }, async () => {
        while (pending.length && owns()) {
          const item = pending.shift(), cacheKey = `${room.id}:${item.id}:${item.version}`;
          let metadata = artifactMetadata.get(cacheKey);
          if (!metadata) { const detail = await api(`/artifacts/${encodeURIComponent(item.id)}`); metadata = { id: detail.id, version: detail.version, title: detail.title, versions: detail.versions || [] }; artifactMetadata.set(cacheKey, metadata); }
          for (const version of metadata.versions.filter((version) => version.task_id === taskId && version.author_type !== "human")) {
            if (!owns()) return;
            const exact = await api(`/artifacts/${encodeURIComponent(item.id)}?version=${version.version}`);
            if (exact.task_id === taskId && exact.version === version.version && exact.author_type !== "human") found.push({ id: exact.id, version: exact.version, title: exact.title });
          }
        }
      }));
      if (!owns()) return; read.items = found;
      $("execution-task-outputs").hidden = !found.length;
      $("execution-task-outputs").innerHTML = found.map((item) => `<button type="button" class="execution-output-button" data-execution-artifact="${esc(item.id)}" data-artifact-version="${item.version}" aria-label="打开本次成果：${esc(item.title)}，版本 ${item.version}">${icon("list")}<span><strong>${esc(item.title)}</strong><small>版本 ${item.version} · 打开 Studio</small></span></button>`).join("");
    } catch (error) { if (owns()) { $("execution-outputs-error").hidden = false; $("execution-outputs-error").innerHTML = `未能读取本次成果。<button type="button" data-retry-task-outputs>重新读取</button>`; } }
    finally { if (owns()) read.loading = false; }
  }
  async function openTaskOutput(button) {
    const item = outputRead?.items.find((item) => item.id === button.dataset.executionArtifact && item.version === Number(button.dataset.artifactVersion)); if (!item) return;
    $("execution-dialog").close(); await ui()?.openArtifact(item.id, item.version);
    const selected = ui()?.getSelectedArtifact(); if (selected?.id === item.id && selected.version === item.version) { const title = $("artifact-title"); title?.setAttribute("tabindex", "-1"); title?.focus({ preventScroll: true }); }
  }
  function eventTitle(event) {
    if (event.kind === "tool_call") return event.payload.tool || eventTitles.tool_call;
    if (event.kind === "tool_result") { const call = (state.execution?.recovery.calls || []).find((item) => item.call_id === event.payload.call_id); return `${call?.tool || "工具调用"} · ${callStates[event.payload.outcome] || event.payload.outcome}`; }
    if (event.kind === "attempt.started") { const attempt = state.execution?.attempts.find((item) => item.attempt === event.attempt); return `执行端已接手 · ${modeLabel(attempt?.decision)}`; }
    if (event.kind === "recovery.reviewed") return reviewDecisions[event.payload.decision] || eventTitles[event.kind];
    return eventTitles[event.kind] || event.kind;
  }
  function eventMessage(event) {
    const payload = event.payload || {};
    if (event.kind === "host_session") return `${payload.host} · ${payload.session_id}`;
    if (event.kind === "checkpoint") return `${payload.host} · 记录已覆盖到事件 ${payload.through_seq}`;
    if (event.kind === "result.ready") return Number.isFinite(payload.bytes) ? `${(payload.bytes / 1024).toFixed(1)} KB · 结果已保存` : "最终结果已保存";
    if (event.kind === "recovery.reviewed") return [event.author_name, payload.note].filter(Boolean).join("：");
    const value = payload.message ?? payload.summary ?? payload.reason ?? payload.error ?? "";
    return typeof value === "string" ? (reasons[value] || value) : JSON.stringify(value);
  }
  function renderEvents() {
    const events = [...state.events.values()].sort((a, b) => a.seq - b.seq).filter((event) => state.selectedAttempt === null || event.attempt === state.selectedAttempt);
    const scroll = $("execution-event-scroll"), nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80, list = $("execution-events");
    for (const node of [...list.children]) if (!events.some((event) => String(event.seq) === node.dataset.executionEventSeq)) node.remove();
    for (const event of events) {
      let node = [...list.children].find((item) => item.dataset.executionEventSeq === String(event.seq)); if (node) continue;
      node = document.createElement("li"); node.className = "execution-event"; node.dataset.executionEventId = event.event_id; node.dataset.executionEventSeq = String(event.seq); node.dataset.executionEventKind = event.kind;
      node.dataset.eventState = event.kind === "task.failed" ? "failed" : event.kind === "recovery.required" || (event.kind === "tool_result" && event.payload.outcome === "unknown") ? "unknown" : "recorded";
      const kindIcon = event.kind === "tool_call" || event.kind === "tool_result" ? "tool" : event.kind === "checkpoint" || event.kind === "host_session" ? "session" : event.kind === "task.completed" || event.kind === "result.ready" ? "check" : event.kind === "task.failed" || event.kind === "recovery.required" ? "alert" : event.kind === "task.cancelled" ? "stop" : event.kind === "recovery.reviewed" ? "person" : "clock";
      const message = eventMessage(event), payload = JSON.stringify(event.payload || {}, null, 2);
      node.innerHTML = `<span class="execution-event-icon">${icon(kindIcon)}</span><div class="execution-event-header"><strong>${esc(eventTitle(event))}</strong><time title="${esc(event.created_at)}">${esc(stamp(event.created_at, true))}</time></div>${message ? `<p class="execution-event-message">${esc(message)}</p>` : ""}<details class="execution-event-details"><summary>事件详情 · #${event.seq}</summary><pre>${esc(payload)}</pre></details>`;
      list.append(node);
    }
    $("execution-events-empty").hidden = !!events.length;
    $("execution-events-empty").textContent = state.hasMore ? "正在读取更多执行记录…" : state.selectedAttempt === null ? "执行端领取后，真实活动会显示在这里。" : "这个批次还没有执行事件。";
    $("execution-record-count").textContent = `${events.length} 条记录`;
    $("execution-load-more").hidden = !state.hasMore;
    if (nearBottom) requestAnimationFrame(() => { scroll.scrollTop = scroll.scrollHeight; });
  }
  function renderRecovery() {
    const execution = state.execution, recovery = execution.recovery, reviewRequired = needsOutcomeReview(), calls = unsafeCalls();
    $("execution-recovery").hidden = false; $("execution-review-banner").hidden = !reviewRequired;
    $("execution-review-heading").textContent = calls.length ? `${calls.length} 个外部调用的结果尚未确认` : "外部调用需要核对";
    $("show-execution-review").hidden = !execution.can_reconcile || !writable() || state.reviewOpen;
    const active = !!execution.active, canReview = execution.can_reconcile && writable();
    let title, description;
    if (reviewRequired && execution.task_status === "cancelled") { title = "已停止，外部结果仍待核对"; description = canReview ? "核对或记录终止决定后，后续工作才能继续。这不会恢复已取消的任务。" : "需要有编辑权限的成员处理未确认的外部结果。这项任务仍保持取消。"; }
    else if (reviewRequired) { title = "先核对，再继续"; description = canReview ? "记录核对结果后，再决定是否重新排队。" : active ? "执行端仍持有这个任务，当前不能修改核对结果。" : "当前需要有编辑权限的成员核对外部调用。"; }
    else if (execution.task_status === "completed") { title = "这项任务已完成"; description = "执行记录已保留，可按批次查看。"; }
    else if (execution.task_status === "cancelled") { title = execution.outcome_review_terminated ? "已记录终止决定" : "这次协作已停止"; description = execution.outcome_review_terminated && execution.unresolved_outcome_count ? "后续工作可以继续；此前的外部结果仍未确认，终止决定不会撤销已发生的操作。" : "未完成的任务已取消，已记录的外部操作不会自动撤销。"; }
    else if (active) { title = "执行端正在处理任务"; description = "这里显示执行端已回传的活动与结果。"; }
    else if (execution.can_retry) { title = recovery.result_available ? "已有保存的结果" : "可以重新排队"; description = recovery.result_available ? "执行端领取后，可以提交已保存的结果。" : "执行端接手后，会记录它实际继续了已有会话，还是重新执行。"; }
    else if (execution.task_status === "queued") { title = "等待执行端接手"; description = "领取成功后会产生新的执行批次。"; }
    else { title = "执行已中断"; description = reasons[recovery.reason] || recovery.reason || "查看上方执行记录，处理后继续。"; }
    $("execution-recovery-title").textContent = title; $("execution-recovery-description").textContent = description;
    const checkpoint = recovery.checkpoint; $("execution-session-info").hidden = !checkpoint;
    if (checkpoint) $("execution-session-content").textContent = `宿主：${checkpoint.host}\n会话：${checkpoint.session_id}\n执行端：${checkpoint.worker_id}\n覆盖到事件：${checkpoint.through_seq}\n\n这是一条宿主回传的会话记录。实际续接方式以新执行批次为准。`;
    $("requeue-execution").hidden = !execution.can_retry || !writable(); $("cancel-execution-run").hidden = !execution.can_cancel || !writable() || (reviewRequired && canReview);
    $("review-execution-button").hidden = !canReview || !reviewRequired || state.reviewOpen;
    $("execution-action-note").textContent = !writable() && reviewRequired ? "你有查看权限" : "";
    if (!canReview) { $("execution-review-form").hidden = true; state.reviewOpen = false; }
    else if (state.reviewOpen) { $("execution-review-form").hidden = false; renderReviewCalls(); }
    updateBusy();
  }
  function renderReviewCalls() {
    const calls = unsafeCalls(), draft = draftFor(), select = $("execution-review-call"), signature = JSON.stringify(calls.map((call) => [call.call_id, call.tool, call.state]));
    if (draft.callId && !calls.some((call) => call.call_id === draft.callId)) {
      draft.callId = ""; draft.decision = ""; draft.needsCallChoice = true; draft.attempt = null;
      document.querySelectorAll('input[name="execution-decision"]').forEach((input) => { input.checked = false; });
      $("execution-review-error").hidden = false; $("execution-review-error").textContent = "此前选择的调用已被处理。说明已保留，请重新选择调用并核对结果。";
    }
    if (select.dataset.signature !== signature || select.dataset.needsChoice !== String(draft.needsCallChoice)) {
      select.dataset.signature = signature; select.dataset.needsChoice = String(draft.needsCallChoice);
      const choose = draft.needsCallChoice || (!draft.callId && calls.length > 1);
      select.innerHTML = calls.length ? `${choose ? '<option value="">请选择需要核对的调用</option>' : ""}${calls.map((call) => `<option value="${esc(call.call_id)}">${esc(call.tool)} · ${esc(call.call_id)}</option>`).join("")}` : '<option value="">没有待核对的调用</option>';
      if (calls.some((call) => call.call_id === draft.callId)) select.value = draft.callId;
      else if (!choose && calls.length === 1) { select.value = calls[0].call_id; draft.callId = select.value; }
      else select.value = "";
    }
    const selected = calls.find((call) => call.call_id === select.value); $("execution-selected-call").hidden = !selected;
    if (selected) {
      const event = [...state.events.values()].find((event) => event.kind === "tool_call" && event.payload.call_id === selected.call_id);
      const callSignature = JSON.stringify({ selected, summary: event?.payload.summary });
      if ($("execution-selected-call").dataset.signature !== callSignature) { $("execution-selected-call").dataset.signature = callSignature; $("execution-selected-call").innerHTML = `<div class="execution-call-header"><strong>${esc(selected.tool)}</strong><span>${esc(callStates[selected.state] || selected.state)}</span></div>${event?.payload.summary ? `<p>${esc(event.payload.summary)}</p>` : ""}<details><summary>调用记录</summary><pre>调用 ID：${esc(selected.call_id)}\n发起事件：#${selected.call_seq}${selected.result_seq ? `\n结果事件：#${selected.result_seq}` : ""}</pre></details>`; }
    }
    const terminating = draft.decision === "terminate"; select.disabled = terminating || state.busy; $("submit-execution-review").textContent = terminating ? "记录并终止协作" : "保存核对结果";
  }
  function openReviewForm() { if (!state.execution?.can_reconcile || !writable()) return; state.reviewOpen = true; $("execution-review-form").hidden = false; restoreDraft(); renderRecovery(); $("execution-review-note").focus(); $("execution-review-form").scrollIntoView({ block: "nearest" }); }
  async function submitReview(event) {
    event.preventDefault(); if (state.busy || !state.execution?.can_reconcile) return;
    persistDraft(); const draft = draftFor(), note = draft.note.trim(), decision = draft.decision;
    const errorNode = $("execution-review-error"); errorNode.hidden = true;
    if (!decision) { errorNode.hidden = false; errorNode.textContent = "请选择核对结果。"; document.querySelector('input[name="execution-decision"]').focus(); return; }
    if (!note) { $("execution-review-note").focus(); return; }
    if (decision !== "terminate" && !draft.callId) { errorNode.hidden = false; errorNode.textContent = "请选择需要核对的调用。"; return; }
    const taskId = state.taskId, generation = dialogGeneration, execution = state.execution, payload = { expected_seq: execution.last_seq, decision, note, ...(decision !== "terminate" ? { call_id: draft.callId } : {}) }, signature = JSON.stringify(payload);
    const ownsReview = () => state.taskId === taskId && dialogGeneration === generation && $("execution-dialog").open;
    if (!draft.attempt || draft.attempt.signature !== signature) draft.attempt = { signature, key: crypto.randomUUID() };
    state.pendingTasks.add(taskId); updateBusy();
    try {
      const result = await api(`/executions/${encodeURIComponent(execution.id)}/reconcile`, { method: "POST", body: { ...payload, request_key: draft.attempt.key } });
      if (!ownsReview()) return; state.execution = result; draft.attempt = null;
      if (draft.note.trim() === note) { draft.note = ""; $("execution-review-note").value = ""; }
      draft.decision = ""; document.querySelectorAll('input[name="execution-decision"]').forEach((input) => { input.checked = false; });
      state.acknowledgementAttempt = currentAttempt()?.attempt ?? 0; $("execution-acknowledgement").hidden = false; $("execution-acknowledgement").textContent = decision === "terminate" ? "核对决定已记录，这次协作已终止。" : "核对结果已记录，尚未重新执行任务。";
      await refreshAfterAction(); if (ownsReview() && !unsafeCalls().length) { state.reviewOpen = false; $("execution-review-form").hidden = true; }
    } catch (error) {
      if (!ownsReview()) return; errorNode.hidden = false; errorNode.textContent = error.status === 409 ? "执行记录已变化，核对说明已保留。请查看最新记录后再次提交。" : error.message;
      if (error.status === 409) await refreshAfterAction();
    } finally { state.pendingTasks.delete(taskId); if (ownsReview()) { updateBusy(); renderRecovery(); } }
  }
  async function requeueCurrent() {
    const execution = state.execution; if (!execution?.can_retry || state.busy) return;
    const taskId = execution.task_id; state.pendingTasks.add(taskId); updateBusy();
    try { await api(`/runs/${encodeURIComponent(execution.run_id)}/retry`, { method: "POST", body: {} }); if (state.taskId !== taskId) { await ui()?.refreshRoom(); return; } state.acknowledgementAttempt = currentAttempt()?.attempt ?? 0; $("execution-acknowledgement").hidden = false; $("execution-acknowledgement").textContent = "已重新排队，等待执行端接手。"; state.selectedLatest = true; await refreshAfterAction(); }
    catch (error) { toast(error.message, true); if (state.taskId === taskId) await refreshAfterAction(); }
    finally { state.pendingTasks.delete(taskId); if (state.taskId === taskId) updateBusy(); }
  }
  async function cancelCurrent() {
    const execution = state.execution; if (!execution?.can_cancel || state.busy) return;
    const taskId = execution.task_id; state.pendingTasks.add(taskId); updateBusy();
    try { await api(`/runs/${encodeURIComponent(execution.run_id)}/cancel`, { method: "POST", body: {} }); if (state.taskId !== taskId) { await ui()?.refreshRoom(); return; } $("execution-acknowledgement").hidden = false; $("execution-acknowledgement").textContent = "本次协作已停止。"; await refreshAfterAction(); }
    catch (error) { toast(error.message, true); }
    finally { state.pendingTasks.delete(taskId); if (state.taskId === taskId) updateBusy(); }
  }
  async function guardedRetry(runId, button) {
    if (state.retrying.has(runId)) return; state.retrying.add(runId); button.disabled = true;
    try {
      const run = ui()?.getCurrentRoom()?.runs.find((item) => item.id === runId), failed = (run?.tasks || []).filter((task) => task.status === "failed");
      const records = await Promise.all(failed.map((task) => api(`/tasks/${encodeURIComponent(task.id)}/execution`)));
      const blocked = records.find((record) => record.recovery.decision === "review_required");
      if (blocked) { await openExecution(blocked.task_id, blocked); if (blocked.can_reconcile) openReviewForm(); return; }
      const unavailable = records.find((record) => !record.can_retry); if (unavailable) { await openExecution(unavailable.task_id, unavailable); return; }
      await api(`/runs/${encodeURIComponent(runId)}/retry`, { method: "POST", body: {} }); await ui()?.refreshRoom(); if ($( "execution-dialog").open && state.execution?.run_id === runId) await loadExecution(true);
    } catch (error) { toast(error.message, true); }
    finally { state.retrying.delete(runId); if (button.isConnected) button.disabled = false; }
  }

  $("refresh-execution").addEventListener("click", () => loadExecution(true)); $("execution-load-more").addEventListener("click", () => loadExecution(true));
  $("execution-agent-session").addEventListener("toggle", () => { if ($("execution-agent-session").open && sessionHistory && !sessionHistory.loaded) loadAgentSession(); });
  $("refresh-agent-session").addEventListener("click", () => loadAgentSession());
  $("execution-agent-session-more").addEventListener("click", () => loadAgentSession(true));
  $("show-execution-review").addEventListener("click", openReviewForm); $("review-execution-button").addEventListener("click", openReviewForm);
  $("hide-execution-review").addEventListener("click", () => { persistDraft(); state.reviewOpen = false; $("execution-review-form").hidden = true; renderRecovery(); $("review-execution-button").focus(); });
  $("execution-review-form").addEventListener("submit", submitReview);
  $("execution-review-note").addEventListener("input", persistDraft);
  $("execution-review-call").addEventListener("change", () => { const draft = draftFor(); if (draft.callId !== $("execution-review-call").value) { draft.decision = ""; draft.attempt = null; document.querySelectorAll('input[name="execution-decision"]').forEach((input) => { input.checked = false; }); } draft.needsCallChoice = false; persistDraft(); $("execution-review-error").hidden = true; renderReviewCalls(); });
  document.querySelectorAll('input[name="execution-decision"]').forEach((input) => input.addEventListener("change", () => { persistDraft(); renderReviewCalls(); }));
  $("requeue-execution").addEventListener("click", requeueCurrent); $("cancel-execution-run").addEventListener("click", cancelCurrent);
  $("execution-dialog").addEventListener("close", () => { persistDraft(); dialogGeneration++; state.epoch++; state.loading = false; const taskId = state.taskId; [...document.querySelectorAll("[data-open-execution]")].find((button) => button.dataset.openExecution === taskId)?.focus({ preventScroll: true }); });
  $("execution-attempt-list").addEventListener("keydown", (event) => { if (!["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return; const buttons = [...$("execution-attempt-list").querySelectorAll("[data-execution-attempt]")]; if (!buttons.length) return; event.preventDefault(); const old = buttons.indexOf(document.activeElement); const index = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (old + (["ArrowDown", "ArrowRight"].includes(event.key) ? 1 : -1) + buttons.length) % buttons.length; buttons[index].click(); $("execution-attempt-list").querySelector(`[data-execution-attempt="${state.selectedAttempt}"]`)?.focus(); });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button"); if (!button || button.disabled) return;
    if (button.dataset.openExecution) openExecution(button.dataset.openExecution);
    if (button.dataset.sessionTask) openExecution(button.dataset.sessionTask);
    if (button.dataset.executionArtifact) openTaskOutput(button);
    if (button.hasAttribute("data-retry-task-outputs")) loadTaskOutputs(true);
    if (button.dataset.executionAttempt) { state.selectedAttempt = Number(button.dataset.executionAttempt); state.selectedLatest = false; state.attemptsSignature = ""; $("execution-event-scroll").scrollTop = 0; renderExecution(); }
    if (button.hasAttribute("data-retry-execution")) loadExecution(true);
  });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-retry-run]"); if (!button || button.disabled || !writable()) return;
    event.preventDefault(); event.stopImmediatePropagation(); guardedRetry(button.dataset.retryRun, button);
  }, true);
  window.addEventListener("workspace-room", () => { if ($( "execution-dialog").open) loadExecution(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && $("execution-dialog").open) loadExecution(true); });
  setInterval(() => { if (!document.hidden && $("execution-dialog").open) loadExecution(); }, 2200);
  window.workspaceExecution = { open: openExecution };
})();

"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const roleName = (role) => ({ owner: "拥有者", editor: "可编辑", viewer: "只读" })[role] || role;
  const time = (value) => { if (!value) return "尚未使用"; const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); };
  const account = { session: null, ready: false, tab: "spaces", gateMode: "login", inviteToken: new URLSearchParams(location.search).get("token"), tokenSecret: null, inviteLink: null };
  window.workspaceAccount = account;
  async function request(path, { method = "GET", body } = {}) {
    const response = await fetch(`/api/account${path}`, { method, headers: body === undefined ? {} : { "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    let data; try { data = await response.json(); } catch { throw new Error("账户服务暂时不可用，请稍后重试。"); }
    if (!response.ok) { const error = new Error(data.error || "操作未完成，请重试。"); error.status = response.status; throw error; }
    return data;
  }
  document.body.insertAdjacentHTML("beforeend", `
    <section class="account-gate" id="account-gate" hidden aria-label="账户登录"><div class="account-gate-card"><a class="account-gate-brand" href="/"><span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span><span>Workspace</span></a><div id="account-gate-content"></div><p id="account-gate-error" class="account-error" role="alert" hidden></p></div></section>
    <dialog class="app-dialog account-dialog" id="account-dialog"><header class="dialog-header"><h2>账户与空间</h2><button type="button" class="icon-button" data-close-dialog aria-label="关闭">×</button></header><div class="account-layout"><nav class="account-tabs" aria-label="账户设置"><div class="account-identity"><span id="account-avatar">W</span><strong id="account-user-name">我的工作空间</strong><small id="account-user-email"></small></div><button data-account-tab="spaces" class="active">工作空间</button><button data-account-tab="members" id="account-members-tab">成员与邀请</button><button data-account-tab="tokens" id="account-tokens-tab">Agent 凭据</button><button data-account-tab="audit" id="account-audit-tab">操作记录</button><button class="account-logout" id="account-logout">退出登录</button></nav><div class="account-main"><div id="account-content"></div><p class="account-error" id="account-error" role="alert" hidden></p></div></div></dialog>
  `);
  function message(text, gate = false) { const target = byId(gate ? "account-gate-error" : "account-error"); target.textContent = text; target.hidden = !text; }
  function setSession(session) {
    account.session = session;
    const role = session.workspace?.role || "owner";
    document.documentElement.dataset.access = role;
    if (role === "viewer") { byId("connection-button").setAttribute("aria-label", "查看连接状态"); byId("connection-button").querySelector("strong").textContent = "连接状态"; }
    document.querySelectorAll("[data-open-account]").forEach((button) => { button.title = session.workspace?.name || "账户与空间"; });
    const name = session.user?.name || "W";
    document.querySelector(".rail-profile").textContent = name.slice(0, 1).toUpperCase();
    byId("sidebar-account-name").innerHTML = `${escape(session.workspace?.name || "我的工作空间")}<small>${escape(session.user?.name || "本地空间")}</small>`;
  }
  function allowWorkspace() { account.ready = true; byId("account-gate").hidden = true; byId("workspace-shell").inert = false; }
  function showGate(mode) {
    account.gateMode = mode; account.ready = false;
    byId("account-gate").hidden = false; byId("workspace-shell").inert = true;
    document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
    const setup = mode === "setup", join = mode === "join";
    const canCancel = setup && account.session?.mode === "local";
    byId("account-gate-content").innerHTML = `<h1>${setup ? "创建你的工作空间" : join ? "加入工作空间" : "欢迎回来"}</h1><p class="account-gate-description">${setup ? "设置拥有者账户，现有内容会保留在你的第一个空间。" : join ? "已有账户请使用原密码，新用户请设置你的密码。" : "登录后继续你的对话与协作。"}</p><form id="account-gate-form">${setup || join ? '<label for="account-name">你的名字</label><input id="account-name" name="name" autocomplete="name" maxlength="100" required placeholder="如何称呼你">' : ""}<label for="account-email">邮箱</label><input id="account-email" name="email" type="email" autocomplete="username" required placeholder="you@example.com"><label for="account-password">密码</label><input id="account-password" name="password" type="password" autocomplete="${setup || join ? "new-password" : "current-password"}" minlength="${setup || join ? 12 : 1}" maxlength="256" required placeholder="${setup || join ? "至少 12 个字符" : "输入你的密码"}">${setup ? '<label for="account-workspace-name">空间名称</label><input id="account-workspace-name" name="workspace_name" maxlength="100" value="我的工作空间" required>' : ""}<button class="primary-button account-submit" id="account-gate-submit" type="submit">${setup ? "创建账户并启用登录" : join ? "接受邀请" : "登录"}</button></form>${canCancel ? '<button class="account-gate-back" id="account-gate-back">返回本地空间</button>' : !setup && !join && account.session?.setup_available ? '<button class="account-gate-back" id="account-setup-link">创建首个账户</button>' : ""}`;
    message("", true);
    byId("account-gate-form").addEventListener("submit", submitGate);
    byId("account-gate-back")?.addEventListener("click", allowWorkspace);
    byId("account-setup-link")?.addEventListener("click", () => showGate("setup"));
    (byId("account-name") || byId("account-email")).focus();
  }
  async function submitGate(event) {
    event.preventDefault(); message("", true);
    const mode = account.gateMode, button = byId("account-gate-submit"); button.disabled = true;
    const body = { email: byId("account-email").value.trim(), password: byId("account-password").value };
    if (mode === "setup" || mode === "join") body.name = byId("account-name").value.trim();
    if (mode === "setup") body.workspace_name = byId("account-workspace-name").value.trim();
    if (mode === "join") body.token = account.inviteToken;
    try {
      await request(mode === "setup" ? "/setup" : mode === "join" ? "/invitations/accept" : "/login", { method: "POST", body });
      byId("account-password").value = "";
      location.replace("/");
    } catch (error) { message(error.message, true); button.disabled = false; }
  }
  async function openAccount() {
    try {
      setSession(await request("/session"));
      if (account.session.mode === "authenticated" && !account.session.user) { showGate(account.session.setup_available ? "setup" : "login"); return; }
      account.tab = "spaces";
      byId("account-user-name").textContent = account.session.user?.name || "本地空间";
      byId("account-user-email").textContent = account.session.user?.email || "仅在当前设备使用";
      byId("account-avatar").textContent = (account.session.user?.name || "W").slice(0, 1).toUpperCase();
      const owner = account.session.workspace?.role === "owner", local = account.session.mode === "local";
      byId("account-members-tab").hidden = local || !owner;
      byId("account-audit-tab").hidden = local || !owner;
      byId("account-tokens-tab").hidden = local;
      byId("account-logout").hidden = local;
      byId("account-dialog").showModal();
      await renderAccount();
    } catch (error) { message(error.message); if (!byId("account-dialog").open) byId("account-dialog").showModal(); }
  }
  async function renderAccount() {
    message("");
    document.querySelectorAll("[data-account-tab]").forEach((button) => button.classList.toggle("active", button.dataset.accountTab === account.tab));
    if (account.session?.mode === "local") {
      byId("account-content").innerHTML = `<div class="account-section-heading"><h3>本地工作空间</h3><p>你正在这台设备上使用 Workspace。</p></div><div class="local-account-card"><span class="account-local-badge">本地模式</span><h4>给空间添加账户</h4><p>启用后，需要登录才能访问内容。你可以创建独立空间、管理成员，以及给 Agent 分配访问凭据。</p><button class="primary-button" id="enable-accounts">启用账户</button></div>`;
      byId("enable-accounts").addEventListener("click", () => showGate("setup")); return;
    }
    if (account.tab === "spaces") { renderSpaces(); return; }
    const currentTab = account.tab;
    byId("account-content").innerHTML = '<p class="account-loading">正在读取…</p>';
    try {
      const data = await request(currentTab === "members" ? "/members" : currentTab === "tokens" ? "/tokens" : "/audit");
      if (account.tab !== currentTab || !byId("account-dialog").open) return;
      if (currentTab === "members") renderMembers(data);
      else if (currentTab === "tokens") renderTokens(data);
      else renderAudit(data);
    } catch (error) { byId("account-content").innerHTML = ""; message(error.message); }
  }
  function renderSpaces() {
    const session = account.session;
    byId("account-content").innerHTML = `<div class="account-section-heading"><h3>工作空间</h3><p>每个空间独立保存对话、Agent、资料和成果。</p></div><div class="account-space-list">${session.workspaces.map((space) => `<article class="account-space-card"><span class="space-avatar">${escape(space.name.slice(0, 1))}</span><div><strong>${escape(space.name)}</strong><small>${escape(roleName(space.role))}</small></div>${space.id === session.workspace?.id ? '<span class="space-current">当前空间</span>' : `<button class="small-button" data-select-workspace="${escape(space.id)}" aria-label="切换到 ${escape(space.name)}">切换</button>`}</article>`).join("")}</div><form class="account-inline-form" id="new-workspace-form"><h4>创建新空间</h4><label class="sr-only" for="new-workspace-name">新空间名称</label><div class="account-input-row"><input id="new-workspace-name" aria-label="新空间名称" maxlength="100" required placeholder="空间名称"><button class="primary-button" type="submit">创建空间</button></div></form>`;
    byId("new-workspace-form").addEventListener("submit", async (event) => { event.preventDefault(); await mutate(async () => { await request("/workspaces", { method: "POST", body: { name: byId("new-workspace-name").value.trim() } }); location.replace("/"); }); });
  }
  function renderMembers(data) {
    const pending = (data.invitations || []).filter((invite) => !invite.accepted_at && !invite.revoked_at);
    byId("account-content").innerHTML = `<div class="account-section-heading"><h3>成员与邀请</h3><p>${escape(account.session.workspace.name)}</p></div><div class="account-member-list">${data.members.map((member) => `<article class="account-member"><span class="member-avatar">${escape((member.name || member.email).slice(0, 1).toUpperCase())}</span><div><strong>${escape(member.name || member.email)}</strong><small>${escape(member.email)}</small></div>${member.role === "owner" ? '<span class="member-role-label">拥有者</span>' : `<select data-member-role="${escape(member.id)}" aria-label="${escape(member.name || member.email)} 的权限"><option value="editor"${member.role === "editor" ? " selected" : ""}>可编辑</option><option value="viewer"${member.role === "viewer" ? " selected" : ""}>只读</option></select><button class="account-remove" data-remove-member="${escape(member.id)}" aria-label="移除成员：${escape(member.email)}">移除</button>`}</article>`).join("")}</div><form class="account-inline-form" id="invite-form"><h4>邀请成员</h4><div class="account-input-row"><label class="sr-only" for="invite-email">邀请邮箱</label><input id="invite-email" aria-label="邀请邮箱" type="email" required placeholder="成员邮箱"><select id="invite-role" aria-label="邀请权限"><option value="editor">可编辑</option><option value="viewer">只读</option></select><button type="submit" class="primary-button">创建邀请</button></div><p class="account-field-note">生成邀请链接后，由你分享给对方。</p></form>${account.inviteLink ? `<div class="account-secret"><p>邀请链接</p><input aria-label="邀请链接" id="account-invite-link" readonly value="${escape(account.inviteLink)}"><button class="small-button" data-copy-account="account-invite-link">复制邀请链接</button></div>` : ""}${pending.length ? `<div class="account-pending"><h4>待加入</h4>${pending.map((invite) => `<article><div><strong>${escape(invite.email)}</strong><small>${escape(roleName(invite.role))} · ${time(invite.expires_at)} 到期</small></div><button class="account-remove" data-revoke-invite="${escape(invite.id)}">撤销</button></article>`).join("")}</div>` : ""}`;
    byId("invite-form").addEventListener("submit", async (event) => { event.preventDefault(); await mutate(async () => { const result = await request("/invitations", { method: "POST", body: { email: byId("invite-email").value.trim(), role: byId("invite-role").value } }); account.inviteLink = `${location.origin}/join?token=${encodeURIComponent(result.token)}`; await renderAccount(); }); });
  }
  function renderTokens(data) {
    const readonly = account.session.workspace.role === "viewer", tokens = (data.tokens || []).filter((token) => !token.revoked_at);
    byId("account-content").innerHTML = `<div class="account-section-heading"><h3>Agent 凭据</h3><p>凭据只允许访问当前空间，可随时撤销。</p></div>${tokens.length ? `<div class="account-token-list">${tokens.map((token) => `<article><div><strong>${escape(token.name)}</strong><p>${token.scopes.map((scope) => `<span>${escape({ read: "读取", write: "编辑", execute: "执行任务" }[scope] || scope)}</span>`).join("")}</p><small>最近使用 ${time(token.last_used_at)}</small></div><button class="account-remove" data-revoke-token="${escape(token.id)}" aria-label="撤销凭据：${escape(token.name)}">撤销</button></article>`).join("")}</div>` : '<p class="account-empty">还没有 Agent 凭据。</p>'}<form class="account-inline-form" id="token-form"><h4>创建凭据</h4><label class="sr-only" for="token-name">凭据名称</label><input id="token-name" aria-label="凭据名称" required maxlength="100" placeholder="例如：我的开发 Agent"><div class="token-scopes"><label><input type="checkbox" name="scope" value="read" checked>读取</label><label><input type="checkbox" name="scope" value="write"${readonly ? " disabled" : ""}>编辑</label><label><input type="checkbox" name="scope" value="execute"${readonly ? " disabled" : " checked"}>执行任务</label></div><button class="primary-button" type="submit">创建凭据</button></form>${account.tokenSecret ? `<div class="account-secret"><p>此凭据仅显示一次，请妥善保存。</p><input id="account-token-secret" aria-label="新建凭据" readonly type="password" autocomplete="off" value="${escape(account.tokenSecret)}"><button class="small-button" data-copy-account="account-token-secret">复制凭据</button></div>` : ""}`;
    byId("token-form").addEventListener("submit", async (event) => { event.preventDefault(); const scopes = [...byId("token-form").querySelectorAll('[name="scope"]:checked')].map((input) => input.value); if (!scopes.length) { message("请至少选择一项权限。"); return; } await mutate(async () => { const result = await request("/tokens", { method: "POST", body: { name: byId("token-name").value.trim(), scopes } }); account.tokenSecret = result.token; await renderAccount(); }); });
  }
  function renderAudit(data) {
    const events = data.events || [];
    byId("account-content").innerHTML = `<div class="account-section-heading"><h3>操作记录</h3><p>当前空间的访问与修改记录。</p></div>${events.length ? `<div class="account-audit">${[...events].reverse().map((event) => `<article><div><strong>${escape(event.method || "")} <span>${escape(event.path || event.route || "")}</span></strong><small>${escape(event.actor_name || event.actor_id || event.actor || "")} · ${time(event.created_at || event.timestamp)}</small></div><span class="audit-status">${escape(event.status || event.status_code || "")}</span></article>`).join("")}</div>` : '<p class="account-empty">暂无操作记录。</p>'}`;
  }
  async function mutate(action, button) {
    message(""); if (button) button.disabled = true;
    try { await action(); } catch (error) { message(error.message); }
    finally { if (button?.isConnected) button.disabled = false; }
  }
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button"); if (!button || button.disabled) return;
    if (button.hasAttribute("data-open-account")) openAccount();
    if (button.dataset.accountTab) { account.tab = button.dataset.accountTab; renderAccount(); }
    if (button.dataset.selectWorkspace) mutate(async () => { await request(`/workspaces/${encodeURIComponent(button.dataset.selectWorkspace)}/select`, { method: "POST", body: {} }); location.replace("/"); }, button);
    if (button.dataset.removeMember) mutate(async () => { await request(`/members/${encodeURIComponent(button.dataset.removeMember)}`, { method: "DELETE" }); await renderAccount(); }, button);
    if (button.dataset.revokeInvite) mutate(async () => { await request(`/invitations/${encodeURIComponent(button.dataset.revokeInvite)}`, { method: "DELETE" }); account.inviteLink = null; await renderAccount(); }, button);
    if (button.dataset.revokeToken) mutate(async () => { await request(`/tokens/${encodeURIComponent(button.dataset.revokeToken)}`, { method: "DELETE" }); account.tokenSecret = null; await renderAccount(); }, button);
    if (button.dataset.copyAccount) { const field = byId(button.dataset.copyAccount); if (field) navigator.clipboard.writeText(field.value).then(() => { button.textContent = "已复制"; }).catch(() => { field.focus(); field.select(); message("请复制选中的内容。"); }); }
  });
  document.addEventListener("change", (event) => { const select = event.target; if (!select.dataset.memberRole) return; mutate(async () => { await request(`/members/${encodeURIComponent(select.dataset.memberRole)}`, { method: "PUT", body: { role: select.value } }); await renderAccount(); }); });
  byId("account-logout").addEventListener("click", () => mutate(async () => { await request("/logout", { method: "POST", body: {} }); location.replace("/"); }));
  byId("account-dialog").addEventListener("close", () => { account.tokenSecret = null; account.inviteLink = null; byId("account-content").innerHTML = ""; });
  window.addEventListener("workspace-session-expired", () => { if (account.session?.mode === "authenticated" && account.ready) showGate("login"); });
  byId("workspace-shell").inert = true;
  window.workspaceAccountReady = request("/session").then((session) => {
    setSession(session);
    if (location.pathname === "/join" && account.inviteToken) { showGate("join"); return false; }
    if (session.mode === "authenticated" && !session.user) { showGate(session.setup_available ? "setup" : "login"); return false; }
    allowWorkspace(); return true;
  }).catch((error) => {
    byId("account-gate").hidden = false;
    byId("account-gate-content").innerHTML = '<h1>暂时无法连接</h1><p class="account-gate-description">请确认本地服务正在运行。</p><button class="primary-button account-submit" id="account-reconnect">重新连接</button>';
    message(error.message, true);
    byId("account-reconnect").addEventListener("click", () => location.reload());
    return false;
  });
})();

"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const ui = () => window.workspaceUI;
  const e = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const paths = { search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>', upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5"/>', file: '<path d="M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 12h8M8 16h6"/>', share: '<path d="M9 15 15 9m-7 8-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m2-1 2-2a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0"/>', close: '<path d="m6 6 12 12M18 6 6 18"/>', download: '<path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4"/>' };
  const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.file}</svg>`;
  const state = { roomId: null, files: [], fileSignature: "", filesLoadedAt: 0, filesPending: false, uploading: false, searchTimer: null, searchController: null, searchResults: [], searchIndex: -1, shareArtifact: null, shares: [], createdShare: null };
  const writable = () => document.documentElement.dataset.access !== "viewer";
  const toast = (message, error = false) => ui()?.toast(message, error);
  const size = (bytes) => bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : bytes >= 1024 ? `${Math.round(bytes / 1024)} KB` : `${bytes} B`;
  const stamp = (value) => { const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); };
  async function api(path, { method = "GET", body, signal } = {}) {
    const response = await fetch(`/api/hub${path}`, { method, ...(signal ? { signal } : {}), headers: body === undefined ? {} : { "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    let data; try { data = await response.json(); } catch { throw new Error("服务返回了无效响应。"); }
    if (!response.ok) throw new Error(data.error || "操作未完成，请重试。");
    return data;
  }
  document.getElementById("more-menu").insertAdjacentHTML("beforeend", `<button class="nav-item" data-open-search aria-label="搜索空间">${icon("search")}搜索空间</button>`);
  document.querySelector(".main-nav").insertAdjacentHTML("afterbegin", `<button class="nav-item" data-open-search>${icon("search")}搜索空间</button>`);
  $("panel-context").insertAdjacentHTML("afterbegin", `<section class="room-files"><div class="sources-heading"><h3>附件</h3><button class="small-button file-mutation" id="pick-room-files" aria-label="上传文件">${icon("upload")}上传文件</button></div><p class="file-upload-state" id="file-upload-state" role="status" hidden></p><div id="room-file-list" class="room-file-list"></div></section>`);
  $("source-dialog").querySelector(".source-import").innerHTML = `<button type="button" class="small-button" id="upload-source-file">${icon("upload")}上传文件</button>`;
  document.body.insertAdjacentHTML("beforeend", `
    <input id="room-file-input" type="file" multiple accept=".pdf,.docx,.txt,.md,.csv,.json,.png,.jpg,.jpeg,.webp" hidden>
    <dialog id="space-search-dialog" class="app-dialog search-dialog"><div class="search-box">${icon("search")}<label class="sr-only" for="space-search-input">搜索当前空间</label><input id="space-search-input" aria-label="搜索当前空间" placeholder="搜索对话、资料、成果和技能…" maxlength="200" autocomplete="off"><button class="icon-button" type="button" data-close-dialog aria-label="关闭">${icon("close")}</button></div><div class="search-results" id="space-search-results"></div><footer class="search-footer"><span id="space-search-status">仅搜索当前空间</span><span>↑ ↓ 选择 <kbd>Enter</kbd> 打开</span></footer></dialog>
    <dialog id="artifact-share-dialog" class="app-dialog"><header class="dialog-header"><h2>分享成果</h2><button class="icon-button" type="button" data-close-dialog aria-label="关闭">${icon("close")}</button></header><div class="dialog-content"><div class="share-summary"><span>${icon("share")}</span><div><strong id="share-artifact-title"></strong><small id="share-artifact-version"></small></div></div><p class="share-description">持有链接的人可查看这个版本，后续修改不会改变分享内容。对话与参考资料不会随链接公开。</p><form id="artifact-share-form" class="share-create file-mutation"><label for="share-expiry">链接有效期</label><div><select id="share-expiry" aria-label="链接有效期"><option value="7">7 天</option><option value="1">1 天</option><option value="30">30 天</option></select><button class="primary-button" id="create-artifact-share" type="submit">生成查看链接</button></div></form><div id="share-created" class="share-created" hidden><label for="share-link">成果分享链接</label><input id="share-link" aria-label="成果分享链接" readonly><div class="share-link-actions"><button class="primary-button" id="copy-share-link" type="button">复制分享链接</button><a class="small-button" id="open-share-link" target="_blank" rel="noopener noreferrer">打开分享页 ↗</a></div><p id="share-link-scope"></p></div><div class="share-history" id="share-history"></div></div></dialog>
  `);
  const download = $("artifact-download"), toolbar = download.parentElement, actions = document.createElement("div");
  actions.className = "artifact-toolbar-actions"; toolbar.insertBefore(actions, download); actions.appendChild(download);
  actions.insertAdjacentHTML("beforeend", `<button class="small-button" id="share-artifact" aria-label="分享成果">${icon("share")}分享</button>`);
  function extractionLabel(file) {
    if (file.extraction_status === "image_only") return "图片原件 · 未识别文字";
    if (file.extraction_status === "no_text") return "未检测到可提取文字";
    if (file.extraction_status === "partial") return "已提取部分文字";
    if (["unsupported", "failed", "error"].includes(file.extraction_status)) return "未提取文字";
    return file.source_id ? "文字可供 Agent 参考" : "原件已保存";
  }
  function suppressFileSources() {
    const ids = new Set(state.files.filter((file) => !file.revoked_at).map((file) => file.source_id).filter(Boolean));
    document.querySelectorAll("[data-source-record]").forEach((element) => { element.hidden = ids.has(element.dataset.sourceRecord); });
  }
  function renderFiles() {
    const files = state.files.filter((file) => !file.revoked_at), signature = JSON.stringify(files);
    if (signature !== state.fileSignature) {
      state.fileSignature = signature;
      $("room-file-list").innerHTML = files.length ? files.map((file) => `<article class="room-file-card"><span class="room-file-icon">${icon("file")}</span><div class="room-file-info"><strong title="${e(file.filename)}">${e(file.filename)}</strong><small>${size(file.bytes)} · ${e(extractionLabel(file))}</small>${file.warning ? `<p>${e(file.warning)}</p>` : ""}<div class="room-file-actions"><a href="/api/hub/files/${encodeURIComponent(file.id)}/download" download aria-label="下载附件：${e(file.filename)}">下载原件</a>${file.source_id && ["extracted", "partial"].includes(file.extraction_status) ? `<button data-file-source="${e(file.source_id)}" aria-label="查看提取内容：${e(file.filename)}">查看文字</button>` : ""}${writable() ? `<button class="file-remove" data-remove-file="${e(file.id)}" aria-label="移除附件：${e(file.filename)}">移除</button>` : ""}</div></div></article>`).join("") : '<p class="files-empty">上传 PDF、Word、文本或图片，每个文件不超过 8 MB。</p>';
    }
    suppressFileSources();
  }
  async function loadFiles(force = false) {
    const room = ui()?.getCurrentRoom();
    if (!room) return;
    if (state.roomId !== room.id) { state.roomId = room.id; state.files = []; state.fileSignature = ""; state.filesLoadedAt = 0; state.filesPending = false; $("room-file-list").innerHTML = '<p class="files-empty">正在读取附件…</p>'; }
    if (state.filesPending || (!force && Date.now() - state.filesLoadedAt < 5000)) { suppressFileSources(); return; }
    const roomId = room.id; state.filesPending = true;
    try { const data = await api(`/rooms/${encodeURIComponent(roomId)}/files`); if (ui()?.getCurrentRoom()?.id !== roomId) return; state.files = data.files || []; state.filesLoadedAt = Date.now(); renderFiles(); }
    catch (error) { if (force) toast(error.message, true); }
    finally { if (state.roomId === roomId) state.filesPending = false; }
  }
  function pickFiles() { if (!ui()?.getCurrentRoom() || state.uploading) return; $("room-file-input").value = ""; $("room-file-input").click(); }
  async function fileBase64(file) { const bytes = new Uint8Array(await file.arrayBuffer()); let binary = ""; for (let index = 0; index < bytes.length; index += 32768) binary += String.fromCharCode(...bytes.subarray(index, index + 32768)); return btoa(binary); }
  async function uploadFiles() {
    const files = [...$("room-file-input").files], roomId = ui()?.getCurrentRoom()?.id;
    if (!files.length || !roomId || state.uploading) return;
    state.uploading = true; $("pick-room-files").disabled = true; $("upload-source-file").disabled = true;
    $("file-upload-state").hidden = false;
    if ($("source-dialog").open) $("source-dialog").close();
    ui()?.showPanel("context");
    let completed = 0;
    try {
      for (const [index, file] of files.entries()) {
        if (file.size > 8 * 1024 * 1024) { toast(`${file.name} 超过 8 MB，未上传。`, true); continue; }
        $("file-upload-state").textContent = `正在上传 ${file.name}（${index + 1}/${files.length}）`;
        try { await api(`/rooms/${encodeURIComponent(roomId)}/files`, { method: "POST", body: { filename: file.name, data_base64: await fileBase64(file) } }); completed++; if (ui()?.getCurrentRoom()?.id === roomId) await loadFiles(true); }
        catch (error) { toast(`${file.name}：${error.message}`, true); }
      }
      if (ui()?.getCurrentRoom()?.id === roomId) await ui()?.refreshRoom();
      if (completed) toast(`${completed} 个文件已保存`);
    } finally { state.uploading = false; $("pick-room-files").disabled = false; $("upload-source-file").disabled = false; $("file-upload-state").hidden = true; }
  }
  async function removeFile(id, button) {
    button.disabled = true;
    try { await api(`/files/${encodeURIComponent(id)}`, { method: "DELETE" }); await loadFiles(true); await ui()?.refreshRoom(); toast("附件已移除"); }
    catch (error) { toast(error.message, true); }
    finally { if (button.isConnected) button.disabled = false; }
  }
  function openSearch() {
    if (window.workspaceAccount && !window.workspaceAccount.ready) return;
    if (document.querySelector("dialog[open]") && !$("space-search-dialog").open) return;
    $("space-search-dialog").showModal(); $("space-search-input").focus(); $("space-search-input").select();
    if (!$("space-search-input").value.trim()) renderSearchEmpty();
  }
  function renderSearchEmpty(message = "输入关键词，搜索当前空间的内容。") { $("space-search-results").innerHTML = `<p class="search-empty">${e(message)}</p>`; state.searchResults = []; state.searchIndex = -1; $("space-search-status").textContent = "仅搜索当前空间"; }
  async function searchSpace() {
    const query = $("space-search-input").value.trim();
    state.searchController?.abort();
    if (!query) { renderSearchEmpty(); return; }
    const controller = new AbortController(); state.searchController = controller;
    $("space-search-status").textContent = "正在搜索…";
    try {
      const data = await api(`/search?q=${encodeURIComponent(query)}`, { signal: controller.signal });
      if (query !== $("space-search-input").value.trim()) return;
      state.searchResults = data.results || []; state.searchIndex = state.searchResults.length ? 0 : -1;
      $("space-search-status").textContent = `${state.searchResults.length} 条结果 · 当前空间`;
      $("space-search-results").innerHTML = state.searchResults.length ? state.searchResults.map((result, index) => `<button class="search-result${index === 0 ? " selected" : ""}" data-search-result="${index}"><span class="search-result-kind">${e({ room: "对话", source: "资料", artifact: "成果", skill: "技能" }[result.type] || result.type)}</span><span class="search-result-body"><strong>${e(result.title)}${result.version ? `<small>v${result.version}</small>` : ""}</strong><span>${e(result.snippet)}</span></span><span class="search-result-arrow">↗</span></button>`).join("") : '<p class="search-empty">没有找到匹配的内容，试试其他关键词。</p>';
    } catch (error) { if (error.name !== "AbortError") { renderSearchEmpty(error.message); $("space-search-status").textContent = "搜索未完成"; } }
  }
  function moveSearch(direction) { if (!state.searchResults.length) return; state.searchIndex = (state.searchIndex + direction + state.searchResults.length) % state.searchResults.length; const buttons = [...$("space-search-results").querySelectorAll("[data-search-result]")]; buttons.forEach((button, index) => button.classList.toggle("selected", index === state.searchIndex)); buttons[state.searchIndex]?.scrollIntoView({ block: "nearest" }); }
  async function openSearchResult(index) {
    const result = state.searchResults[index]; if (!result) return;
    $("space-search-dialog").close();
    try {
      if (result.type === "room") await ui()?.openRoom(result.id);
      else if (result.type === "skill") { document.querySelector('.app-rail [data-view="skills"]').click(); await window.workspaceExtensions?.openSkill(result.id); }
      else { if (result.room_id) await ui()?.openRoom(result.room_id); if (result.type === "artifact") await ui()?.openArtifact(result.id, result.version); else { ui()?.showPanel("context"); await ui()?.openSource(result.id); } }
    } catch (error) { toast(error.message, true); }
  }
  async function openShares() {
    const artifact = ui()?.getSelectedArtifact(); if (!artifact) return;
    state.shareArtifact = { ...artifact }; state.createdShare = null;
    $("share-created").hidden = true; $("share-link").value = "";
    $("share-artifact-title").textContent = artifact.title; $("share-artifact-version").textContent = `固定分享版本 ${artifact.version}`;
    $("artifact-share-form").hidden = !writable(); $("share-expiry").value = "7";
    $("share-history").innerHTML = '<p class="search-empty">正在读取分享记录…</p>';
    $("artifact-share-dialog").showModal(); await loadShares();
  }
  async function loadShares() {
    const artifact = state.shareArtifact; if (!artifact) return;
    try { const data = await api(`/artifacts/${encodeURIComponent(artifact.id)}/shares`); if (state.shareArtifact?.id !== artifact.id) return; state.shares = data.shares || []; renderShares(); }
    catch (error) { $("share-history").innerHTML = `<p class="search-empty">${e(error.message)}</p>`; }
  }
  function renderShares() {
    $("share-history").innerHTML = state.shares.length ? `<h3>分享记录</h3>${state.shares.map((share) => { const expired = new Date(share.expires_at).getTime() <= Date.now(); return `<article><div><strong>版本 ${share.version}<span>${share.revoked_at ? "已撤销" : expired ? "已过期" : "有效"}</span></strong><small>${stamp(share.expires_at)} 到期</small></div>${!share.revoked_at && !expired && writable() ? `<button class="text-button" data-revoke-share="${e(share.id)}" aria-label="撤销分享：版本 ${share.version}">撤销</button>` : ""}</article>`; }).join("")}` : '<p class="share-history-empty">还没有分享记录。</p>';
  }
  async function createShare(event) {
    event.preventDefault(); const artifact = state.shareArtifact; if (!artifact) return;
    $("create-artifact-share").disabled = true;
    try {
      const result = await api(`/artifacts/${encodeURIComponent(artifact.id)}/shares`, { method: "POST", body: { version: artifact.version, expires_days: Number($("share-expiry").value) } });
      const url = new URL(result.url, location.origin);
      if (!["http:", "https:"].includes(url.protocol)) throw new Error("分享地址无效。");
      state.createdShare = { id: result.share.id, url: url.href, local: ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname) };
      $("share-link").value = url.href; $("open-share-link").href = url.href;
      $("share-link-scope").textContent = state.createdShare.local ? "这是本机链接，只能在这台电脑上打开。" : `链接在 ${stamp(result.share.expires_at)} 到期。`;
      $("share-created").hidden = false; await loadShares();
    } catch (error) { toast(error.message, true); }
    finally { $("create-artifact-share").disabled = false; }
  }
  async function revokeShare(id, button) {
    if (!state.shareArtifact) return; button.disabled = true;
    try { await api(`/artifacts/${encodeURIComponent(state.shareArtifact.id)}/shares/${encodeURIComponent(id)}`, { method: "DELETE" }); if (state.createdShare?.id === id) { state.createdShare = null; $("share-created").hidden = true; $("share-link").value = ""; } await loadShares(); toast("分享链接已撤销"); }
    catch (error) { toast(error.message, true); }
    finally { if (button.isConnected) button.disabled = false; }
  }
  $("pick-room-files").addEventListener("click", pickFiles); $("upload-source-file").addEventListener("click", pickFiles); $("room-file-input").addEventListener("change", uploadFiles);
  $("space-search-input").addEventListener("input", () => { clearTimeout(state.searchTimer); state.searchController?.abort(); state.searchResults = []; state.searchIndex = -1; $("space-search-results").innerHTML = '<p class="search-empty">正在搜索…</p>'; state.searchTimer = setTimeout(searchSpace, 220); });
  $("space-search-input").addEventListener("keydown", (event) => { if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); moveSearch(event.key === "ArrowDown" ? 1 : -1); } else if (event.key === "Enter" && !event.isComposing && state.searchIndex >= 0) { event.preventDefault(); openSearchResult(state.searchIndex); } });
  $("space-search-dialog").addEventListener("close", () => { clearTimeout(state.searchTimer); state.searchController?.abort(); });
  $("share-artifact").addEventListener("click", openShares); $("artifact-share-form").addEventListener("submit", createShare);
  $("copy-share-link").addEventListener("click", async () => { if (!state.createdShare) return; try { await navigator.clipboard.writeText(state.createdShare.url); toast(state.createdShare.local ? "已复制本机链接，仅能在这台电脑上打开。" : "分享链接已复制"); } catch { $("share-link").focus(); $("share-link").select(); toast("请复制选中的链接。", true); } });
  $("artifact-share-dialog").addEventListener("close", () => { state.createdShare = null; $("share-link").value = ""; });
  document.addEventListener("click", (event) => { const button = event.target.closest("button"); if (!button || button.disabled) return; if (button.hasAttribute("data-open-search")) openSearch(); if (button.hasAttribute("data-search-result")) openSearchResult(Number(button.dataset.searchResult)); if (button.dataset.removeFile) removeFile(button.dataset.removeFile, button); if (button.dataset.fileSource) ui()?.openSource(button.dataset.fileSource); if (button.dataset.revokeShare) revokeShare(button.dataset.revokeShare, button); if (button.dataset.panel === "context") loadFiles(true); });
  document.addEventListener("keydown", (event) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); openSearch(); } });
  window.addEventListener("workspace-room", () => { suppressFileSources(); if (ui()?.getCurrentRoom()?.id !== state.roomId || !$("panel-context").hidden) loadFiles(); });
})();

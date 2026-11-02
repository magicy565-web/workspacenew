'use strict';
const $ = id => document.getElementById(id);
let projects = [];
let active = null;
let dirty = false;
let selectedTheme = 'classic';
let selectedCover = 'split';
let toastTimer;

function toast(message) {
  const box = $('toast');
  box.textContent = message;
  box.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.remove('visible'), 4500);
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...options.headers } });
  let data;
  try { data = await response.json(); } catch { throw new Error('服务返回了无法读取的结果'); }
  if (!response.ok) throw new Error(data.error || '请求未完成');
  return data;
}

function setBusy(button, busy) { button.disabled = busy; button.setAttribute('aria-busy', String(busy)); }
function discardChanges() { return !dirty || confirm('简报中有未保存的修改，继续将放弃这些修改。'); }

function renderProjectList() {
  const list = $('sidebar-projects');
  list.replaceChildren();
  for (const project of projects) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = project.name;
    button.title = project.name;
    if (active && active.id === project.id) button.classList.add('selected');
    button.addEventListener('click', () => selectProject(project.id));
    list.append(button);
  }
}

function updateCounts(brief, images) {
  $('count-sources').textContent = Array.isArray(brief?.sources) ? brief.sources.length : 0;
  $('count-chapters').textContent = Array.isArray(brief?.chapters) ? brief.chapters.length : 0;
  $('count-images').textContent = images?.length || 0;
}

function selectDesign(theme, cover) {
  selectedTheme = ['classic', 'modern', 'bold'].includes(theme) ? theme : 'classic';
  selectedCover = cover === 'full_bleed' ? 'full_bleed' : 'split';
  document.querySelectorAll('.theme-option').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.theme === selectedTheme)));
  document.querySelectorAll('.cover-option').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.cover === selectedCover)));
}

function renderMessages() {
  const container = $('messages');
  container.replaceChildren();
  const entries = active.messages?.map(m => m.text) || [];
  if (!entries.length || !entries[0].includes(new URL(active.website).hostname)) {
    entries.unshift(`请为这家企业制作买手杂志\n${active.website}`);
  }
  for (const text of entries) {
    const bubble = document.createElement('div');
    bubble.className = 'user-turn';
    bubble.textContent = text;
    container.append(bubble);
  }
}

function renderActive(preserveEditor = false) {
  $('welcome').hidden = Boolean(active);
  $('project-view').hidden = !active;
  $('open-workspace-top').hidden = !active;
  $('refresh-project').hidden = !active;
  $('composer-input').placeholder = active ? '输入补充要求…' : '输入企业网址，开始制作杂志';
  renderProjectList();
  if (!active) { closeWorkspace(); return; }
  renderMessages();
  $('project-name').textContent = active.name;
  $('assistant-status').textContent = !active.has_brief
    ? '请将任务交给已连接的 Agent，完成研究和编辑。'
    : !active.has_build ? '资料已就绪，可以在工作区生成杂志。'
    : active.build_current ? '杂志已经生成，可以预览或调整设计。'
    : active.messages?.length ? '已记录要求，请交给 Agent 处理；当前预览为旧版本。' : '资料已更新，请重新生成杂志。';
  $('magazine-title').textContent = active.brief?.title || '杂志工作区';
  $('preview-label').textContent = active.has_build ? (active.build_current ? '已生成 · 待审核' : '旧版本 · 待更新') : '等待生成';
  $('panel-preview-status').textContent = $('preview-label').textContent;
  if (!preserveEditor) {
    $('brief').value = active.brief ? JSON.stringify(active.brief, null, 2) : '';
    dirty = false;
    const cover = active.brief?.chapters?.find(c => c.kind === 'cover');
    selectDesign(active.brief?.design?.theme || 'classic', cover?.layout || 'split');
  }
  let displayBrief = active.brief;
  try { if (dirty) displayBrief = JSON.parse($('brief').value); } catch {}
  updateCounts(displayBrief, active.images);
  const imageList = $('image-list');
  imageList.replaceChildren();
  for (const image of active.images) {
    const tag = document.createElement('span');
    tag.textContent = image;
    imageList.append(tag);
  }
  const preview = `/preview/${active.id}/index.html`;
  const download = `/api/projects/${active.id}/download`;
  $('preview-placeholder').hidden = active.has_build;
  $('preview-frame').hidden = !active.has_build;
  $('open-preview').hidden = !active.has_build;
  $('download').hidden = !active.has_build;
  $('panel-download').hidden = !active.has_build;
  if (active.has_build) {
    $('preview-frame').src = `${preview}?build=${active.build_revision}`;
    $('open-preview').href = preview;
    $('download').href = download;
    $('panel-download').href = download;
  } else $('preview-frame').removeAttribute('src');
  const cover = active.brief?.chapters?.find(c => c.kind === 'cover')?.image;
  $('cover-image').hidden = true;
  $('cover-fallback').hidden = false;
  if (active.has_build && cover) $('cover-image').src = `/preview/${active.id}/images/${encodeURIComponent(cover)}?build=${active.build_revision}`;
  else $('cover-image').removeAttribute('src');
}

$('cover-image').addEventListener('load', () => { $('cover-image').hidden = false; $('cover-fallback').hidden = true; });
$('cover-image').addEventListener('error', () => { $('cover-image').hidden = true; $('cover-fallback').hidden = false; });

async function refresh(id = active?.id, preserveEditor = false) {
  projects = (await api('/api/projects')).projects;
  const selected = projects.find(p => p.id === id) || projects[0];
  active = selected ? await api(`/api/projects/${selected.id}`) : null;
  renderActive(preserveEditor);
}

async function selectProject(id) {
  if (dirty && active?.id !== id && !discardChanges()) return;
  try { await refresh(id); $('sidebar').classList.remove('open'); } catch (e) { toast(e.message); }
}

function switchTab(tab) {
  document.querySelectorAll('.tab').forEach(button => {
    const selected = button.dataset.tab === tab;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-selected', String(selected));
  });
  for (const name of ['preview', 'design', 'brief']) $(`panel-${name}`).hidden = name !== tab;
}

function openWorkspace(tab = 'preview') {
  if (!active) { toast('请先输入企业网址，创建项目。'); return; }
  $('workspace-panel').hidden = false;
  document.querySelector('.app-shell').classList.add('panel-open');
  switchTab(tab);
}

function closeWorkspace() {
  $('workspace-panel').hidden = true;
  document.querySelector('.app-shell').classList.remove('panel-open');
}

for (const id of ['open-workspace', 'open-workspace-top', 'magazine-card', 'composer-add']) $(id).addEventListener('click', () => openWorkspace());
$('close-workspace').addEventListener('click', closeWorkspace);
document.querySelectorAll('.tab').forEach(button => button.addEventListener('click', () => switchTab(button.dataset.tab)));
document.querySelectorAll('.theme-option').forEach(button => button.addEventListener('click', () => selectDesign(button.dataset.theme, selectedCover)));
document.querySelectorAll('.cover-option').forEach(button => button.addEventListener('click', () => selectDesign(selectedTheme, button.dataset.cover)));

$('open-sidebar').addEventListener('click', () => $('sidebar').classList.add('open'));
$('close-sidebar').addEventListener('click', () => $('sidebar').classList.remove('open'));
$('new-chat').addEventListener('click', () => {
  if (!discardChanges()) return;
  dirty = false; active = null; renderActive(); $('sidebar').classList.remove('open');
  $('composer-input').value = ''; $('composer-input').focus();
});
$('refresh-project').addEventListener('click', async () => {
  if (dirty && !discardChanges()) return;
  try { await refresh(active?.id); toast('项目已刷新。'); } catch (e) { toast(e.message); }
});

function websiteFrom(text) {
  const match = text.match(/https?:\/\/[^\s<>"'，。！？]+/i);
  let value = match?.[0]?.replace(/[.,!?;:]+$/, '');
  if (!value) {
    const domain = text.match(/(?:^|[\s（(])((?:www\.)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:\/[^\s，。！？]*)?)/i);
    if (domain) value = `https://${domain[1].replace(/[.,!?;:]+$/, '')}`;
  }
  if (!value) throw new Error('请输入企业的 HTTPS 网址。');
  const url = new URL(value);
  if (url.protocol !== 'https:') throw new Error('请输入企业的 HTTPS 网址。');
  return url.href;
}

$('composer').addEventListener('submit', async event => {
  event.preventDefault();
  const input = $('composer-input');
  const text = input.value.trim();
  if (!text) return;
  setBusy($('send'), true);
  try {
    if (active) {
      await api(`/api/projects/${active.id}/messages`, { method: 'POST', body: JSON.stringify({text}) });
      await refresh(active.id, dirty);
      toast('要求已记录，请交给 Agent 处理。');
    } else {
      const website = websiteFrom(text);
      const hostname = new URL(website).hostname.replace(/^www\./i, '');
      const name = hostname.split('.')[0].replace(/[-_]+/g, ' ');
      const project = await api('/api/projects', { method: 'POST', body: JSON.stringify({name, website, request:text}) });
      await refresh(project.id);
    }
    input.value = ''; input.style.height = 'auto';
    $('conversation').scrollTop = $('conversation').scrollHeight;
  } catch (e) { toast(e.message); } finally { setBusy($('send'), false); }
});

$('composer-input').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('composer').requestSubmit(); }
});
$('composer-input').addEventListener('input', event => {
  event.target.style.height = 'auto';
  event.target.style.height = `${Math.min(event.target.scrollHeight, 150)}px`;
});

$('brief').addEventListener('input', () => {
  dirty = true;
  try { updateCounts(JSON.parse($('brief').value), active.images); } catch {}
});

$('save-brief').addEventListener('click', async event => {
  if (!active) return;
  let brief;
  try { brief = JSON.parse($('brief').value); } catch { toast('请检查简报 JSON 的格式。'); return; }
  const button = event.currentTarget; setBusy(button, true);
  try {
    await api(`/api/projects/${active.id}/brief`, { method: 'PUT', body: JSON.stringify({brief}) });
    await refresh(active.id); toast('简报已保存。');
  } catch (e) { toast(e.message); } finally { setBusy(button, false); }
});

$('load-example').addEventListener('click', async () => {
  if ($('brief').value && !confirm('示例会替换编辑框中的内容，不会直接覆盖已保存的简报。继续吗？')) return;
  try {
    const sample = await api('/api/example-brief');
    $('brief').value = JSON.stringify(sample, null, 2); dirty = true;
    selectDesign(sample.design?.theme || 'classic', sample.chapters?.find(c => c.kind === 'cover')?.layout || 'split');
    updateCounts(sample, active.images); toast('已加载虚构示例，请替换成真实资料。');
  } catch (e) { toast(e.message); }
});

function readBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`无法读取图片：${file.name}`));
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.readAsDataURL(file);
  });
}

$('files').addEventListener('change', async event => {
  const input = event.currentTarget; const files = [...input.files];
  if (!active || !files.length) return;
  try {
    for (const file of files) {
      if (file.size > 8 * 1024 * 1024) throw new Error(`${file.name} 超过 8 MB`);
      await api(`/api/projects/${active.id}/images`, { method: 'POST', body: JSON.stringify({filename:file.name,data_base64:await readBase64(file)}) });
    }
    await refresh(active.id, dirty); toast(`已上传 ${files.length} 张图片。`);
  } catch (e) { await refresh(active.id, dirty); toast(e.message); } finally { input.value = ''; }
});

$('build').addEventListener('click', async event => {
  if (!active) return;
  if (dirty) { toast('请先保存简报中的修改。'); return; }
  const button = event.currentTarget; setBusy(button, true);
  try {
    const result = await api(`/api/projects/${active.id}/build`, {method:'POST',body:'{}'});
    await refresh(active.id); toast(`生成成功，共 ${result.checks.chapters} 个章节。`);
  } catch (e) { toast(e.message); } finally { setBusy(button, false); }
});

$('apply-design').addEventListener('click', async event => {
  if (!active) return;
  let brief;
  try { brief = JSON.parse($('brief').value); } catch { toast('请先准备有效的编辑简报。'); return; }
  const cover = brief.chapters?.find(c => c.kind === 'cover');
  if (!cover) { toast('简报中尚未包含封面。'); return; }
  if (!brief.design || typeof brief.design !== 'object' || Array.isArray(brief.design)) brief.design = {};
  brief.design.theme = selectedTheme;
  cover.layout = selectedCover === 'full_bleed' ? 'full_bleed' : 'split';
  const button = event.currentTarget; setBusy(button, true); let saved = false;
  try {
    await api(`/api/projects/${active.id}/brief`, {method:'PUT',body:JSON.stringify({brief})}); saved = true; dirty = false;
    await api(`/api/projects/${active.id}/build`, {method:'POST',body:'{}'});
    await refresh(active.id); switchTab('preview'); toast('设计已应用，杂志已重新生成。');
  } catch (e) {
    if (saved) { await refresh(active.id); toast(`设计已保存，生成未完成：${e.message}`); }
    else toast(e.message);
  } finally { setBusy(button, false); }
});

$('copy-task').addEventListener('click', async () => {
  if (!active) return;
  const instructions = (active.messages || []).map(m => `- ${m.text}`).join('\n');
  const text = `请使用 Buyer Magazine Studio 的 MCP 工具完成项目 ${active.id}。\n企业官网：${active.website}\n${instructions ? `用户要求：\n${instructions}\n` : ''}请用你自己的研究工具核实公开资料，记录来源 URL、观察日期和不确定项；为图片记录来源与使用权限。先调用 get_project 和 get_editorial_example 查看格式，再使用 upload_image、save_editorial_brief 和 build_magazine。不要编造产品、工厂、价格、认证或市场数据。生成结果仅供内部审核。`;
  try { await navigator.clipboard.writeText(text); toast('任务已复制。'); }
  catch { window.prompt('请复制下面的任务内容：', text); }
});

refresh().catch(e => toast(`无法连接本地工作台：${e.message}`));

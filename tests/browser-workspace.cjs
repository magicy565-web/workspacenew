/* Synthetic end-to-end fixtures; never connect this test to a user's data directory. */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const net = require('node:net');
const http = require('node:http');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');

const repo = path.resolve(__dirname, '..');
const output = path.resolve(process.env.WORKSPACE_QA_OUTPUT || path.join(repo, '..', 'qa'));
const runId = new Date().toISOString().replace(/[:.]/g, '-');
const runDir = path.join(output, 'workspace-' + runId);
fs.mkdirSync(runDir, { recursive: true });
const dataDir = path.join(runDir, 'isolated-data');
const python = process.env.WORKSPACE_QA_PYTHON || path.join(repo, '..', 'studio-venv', 'Scripts', 'python.exe');
const moduleCandidates = [process.env.WORKSPACE_QA_PLAYWRIGHT, 'playwright', path.join(os.homedir(), '.cache', 'codex-runtimes', 'codex-primary-runtime', 'dependencies', 'node', 'node_modules', 'playwright')].filter(Boolean);
let playwright;
for (const candidate of moduleCandidates) {
  try { playwright = require(candidate); break; } catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND') throw error;
  }
}
assert(playwright, 'Install Playwright or set WORKSPACE_QA_PLAYWRIGHT to its module directory.');
const edge = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const executablePath = process.env.WORKSPACE_QA_BROWSER || (fs.existsSync(edge) ? edge : undefined);
const checks = [];
let service;
let browser;
let page;
let base;
let serverLog = '';
const pageErrors = [];
let testedAssets;
let testedExecutionRuntime;
let testedManagedRuntime;
let sharedAcceptance;
let collaborationAcceptance;
let executionAcceptance;
let teamAcceptance;
let messengerAcceptance;
const wantsCollaboration = process.argv.includes('--collaboration') || process.argv.includes('--collaboration-only') || process.argv.includes('--team-collaboration-only');
const wantsExecution = process.argv.includes('--execution') || process.argv.includes('--execution-only');
const wantsStreaming = wantsExecution || process.argv.includes('--streaming-only');
const wantsTeams = process.argv.includes('--teams') || process.argv.includes('--teams-only') || process.argv.includes('--team-boundaries-only') || process.argv.includes('--team-collaboration-only');
const wantsManaged = process.argv.includes('--managed') || process.argv.includes('--managed-only');
const wantsMessenger = process.argv.some(flag => ['--messenger', '--messenger-only', '--messenger-api-only', '--messenger-navigation-only', '--messenger-responsive-only'].includes(flag));
const attachmentMarker = 'QA_ATTACHMENT_SOURCE_8462';

function assetHashes() {
  const html = fs.readFileSync(path.join(repo, 'studio', 'static', 'workspace.html'), 'utf8');
  const names = ['workspace.html', ...new Set([...html.matchAll(/(?:src|href)="\/([^"?]+\.(?:css|js))"/g)].map(match => match[1]))];
  return Object.fromEntries(names.filter(name => fs.existsSync(path.join(repo, 'studio', 'static', name))).map(name => [
    name, crypto.createHash('sha256').update(fs.readFileSync(path.join(repo, 'studio', 'static', name))).digest('hex'),
  ]));
}

function executionRuntimeHashes() {
  return Object.fromEntries(['worker.py', 'hub_execution.py'].map(name => [name,
    crypto.createHash('sha256').update(fs.readFileSync(path.join(repo, 'studio', name))).digest('hex'),
  ]));
}

function managedRuntimeHashes() {
  return Object.fromEntries(['worker.py', 'executors.py'].map(name => [name,
    crypto.createHash('sha256').update(fs.readFileSync(path.join(repo, 'studio', name))).digest('hex'),
  ]));
}

async function check(name, fn) {
  const start = Date.now();
  try {
    await fn();
    checks.push({ name, passed: true, elapsed_ms: Date.now() - start });
    process.stdout.write('PASS ' + name + '\n');
  } catch (error) {
    checks.push({ name, passed: false, error: error.stack, elapsed_ms: Date.now() - start });
    throw error;
  }
}

async function freePort() {
  const listener = net.createServer();
  await new Promise((resolve, reject) => { listener.once('error', reject); listener.listen(0, '127.0.0.1', resolve); });
  const port = listener.address().port;
  await new Promise(resolve => listener.close(resolve));
  return port;
}

async function api(method, route, body, expected) {
  const response = await fetch(base + '/api/hub' + route, {
    method,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const raw = await response.text();
  let parsed;
  try { parsed = JSON.parse(raw); } catch { parsed = raw; }
  if (expected !== undefined) assert.equal(response.status, expected, route + ': ' + raw);
  else assert(response.ok, route + ': HTTP ' + response.status + ' ' + raw);
  return parsed;
}

async function until(fn, timeout = 12000, interval = 100) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const result = await fn();
    if (result) return result;
    await new Promise(resolve => setTimeout(resolve, interval));
  }
  throw new Error('Timed out waiting for persisted workspace state.');
}

async function claim(worker, roomId, agentId) {
  return api('POST', '/tasks/claim', { worker_id: worker, room_id: roomId, agent_ids: [agentId] });
}

async function complete(lease, worker, result, expected) {
  return api('POST', '/tasks/' + lease.task.id + '/complete', {
    worker_id: worker, lease_token: lease.lease_token, result,
  }, expected);
}

async function assertNoOverflow(target) {
  const dimensions = await target.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
  }));
  assert(dimensions.content <= dimensions.viewport + 1, JSON.stringify(dimensions));
}

async function assertControlNotClipped(control) {
  await control.scrollIntoViewIfNeeded();
  const visibility = await control.evaluate(element => {
    const rect = element.getBoundingClientRect();
    const clipping = [];
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      if (!['auto', 'scroll', 'hidden', 'clip'].includes(getComputedStyle(parent).overflowY)) continue;
      const bounds = parent.getBoundingClientRect();
      const top = bounds.top + parent.clientTop;
      const bottom = top + parent.clientHeight;
      if (rect.top < top - 1 || rect.bottom > bottom + 1) clipping.push({ parent: parent.id || parent.className, top, bottom });
    }
    return { top: rect.top, bottom: rect.bottom, viewport: innerHeight, clipping };
  });
  assert(visibility.top >= -1 && visibility.bottom <= visibility.viewport + 1 && !visibility.clipping.length,
    'An action is visibly clipped: ' + JSON.stringify(visibility));
}

async function mobileToastJourney(surface, controls) {
  const originalViewport = page.viewportSize();
  const evidence = [];
  for (const height of [844, 600]) {
    await page.setViewportSize({ width: 390, height });
    for (const control of controls) await assertControlNotClipped(control);
    // This explicit visual fixture exercises the production notification path;
    // it makes no assertion that a real task or model completed.
    await page.evaluate(() => window.workspaceUI.toast('布局验收提示：草稿仍然保留，请继续输入。'));
    const notice = page.locator('#toast');
    await notice.waitFor({ state: 'visible' });
    const toast = await notice.boundingBox();
    assert(toast && toast.x >= 0 && toast.y >= 0 && toast.x + toast.width <= 391 && toast.y + toast.height <= height + 1);
    const targets = [];
    for (const control of controls) {
      const bounds = await control.boundingBox();
      assert(bounds, 'The primary input/action is not visible while a notice is shown.');
      const overlap = Math.max(0, Math.min(toast.x + toast.width, bounds.x + bounds.width) - Math.max(toast.x, bounds.x)) * Math.max(0, Math.min(toast.y + toast.height, bounds.y + bounds.height) - Math.max(toast.y, bounds.y));
      assert.equal(overlap, 0, 'A mobile notice overlays the primary input/action: ' + JSON.stringify({ surface, height, toast, bounds }));
      targets.push(bounds);
    }
    await assertNoOverflow(page);
    await page.screenshot({ path: path.join(runDir, 'toast-' + surface + '-' + height + '.png'), fullPage: true, animations: 'disabled' });
    evidence.push({ surface, viewport: { width: 390, height }, toast, targets, overlap_area: 0, synthetic_visual_notice: true });
    await notice.waitFor({ state: 'hidden' });
  }
  fs.writeFileSync(path.join(runDir, 'toast-' + surface + '-geometry.json'), JSON.stringify(evidence, null, 2));
  await page.setViewportSize(originalViewport);
}

async function openArtifact(title) {
  const action = page.getByRole('button', { name: '打开成果：' + title, exact: true });
  await action.first().click();
  await page.getByLabel('成果版本', { exact: true }).waitFor({ state: 'visible' });
}

async function navigateWorkspace(name, target = page) {
  let action = target.getByRole('button', { name, exact: true }).first();
  if (!await action.isVisible()) {
    const more = target.getByRole('button', { name: '更多功能', exact: true });
    if (await more.isVisible()) await more.click();
    else await target.getByRole('button', { name: '打开侧栏', exact: true }).click();
    action = target.getByRole('button', { name, exact: true }).first();
  }
  await action.click();
}

async function messengerSummaryJourney() {
  let first, recent, group, artifact;
  const worker = 'qa-messenger-summary';
  const memberIds = ['writer', 'researcher', 'designer', 'developer', 'reviewer'];
  const unicodeReply = '真实摘要边界：' + '买手🙂'.repeat(65) + ' END_OF_REAL_MESSAGE';
  await check('Conversation summaries use actual messages and bounded current rosters, without fabricated greetings', async () => {
    const agents = (await api('GET', '/bootstrap')).agents;
    group = await api('POST', '/teams', { name: '真实空群与建立记录', description: '新建群聊只显示成员和创建记录。', member_ids: memberIds, routing_mode: 'mention', request_key: 'qa-messenger-empty-team' }, 201);
    const empty = await api('GET', '/rooms/' + group.primary_room_id);
    assert.deepEqual([empty.last_message, empty.last_message_at, empty.last_speaker, empty.messages.length, empty.runs.length], ['', null, null, 0, 0]);
    assert.equal(empty.team_member_count, 5);
    assert.deepEqual(empty.team_members_preview, group.member_ids.slice(0, 4).map(id => ({ agent_id: id, name: agents.find(agent => agent.id === id).name })));
    const creation = (await api('GET', '/rooms/' + empty.id + '/events')).events.filter(event => event.type === 'team.created');
    assert.equal(creation.length, 1, 'An empty group must have a persisted creation event instead of an invented Agent greeting.');
    first = await api('POST', '/rooms', { title: '较早发言的写作对话', text: '保存首版文档，再验证真实发言时间。', agent_id: 'writer' });
    const firstLease = await claim(worker, first.id, 'writer');
    const initial = await complete(firstLease, worker, { message: unicodeReply, artifacts: [{ title: '会话结构验收文档', kind: 'markdown', content: '# 会话结构验收文档\n\n首版正文需要在 Studio 中连续阅读。' }] });
    artifact = initial.artifacts[0];
    first = await api('GET', '/rooms/' + first.id);
    const actualLast = first.messages.at(-1);
    assert.equal(first.last_message, [...actualLast.text].slice(0, 160).join(''));
    assert.equal([...first.last_message].length, 160);
    assert.equal(first.last_message_at, actualLast.created_at);
    assert.deepEqual(first.last_speaker, { role: 'agent', agent_id: 'writer', author_id: null, name: agents.find(item => item.id === 'writer').name });
    assert.deepEqual(first.team_members_preview, []);
    assert.equal(first.team_member_count, 0);
    recent = await api('POST', '/rooms', { title: '最后真实发言的研究对话', text: '这条真人消息需要正确署名。', agent_id: 'researcher' });
    assert.deepEqual(recent.last_speaker, { role: 'user', agent_id: null, author_id: null, name: '本地用户' });
    assert.equal(recent.last_message_at, recent.messages.at(-1).created_at);
    const recentLease = await claim(worker, recent.id, 'researcher');
    await complete(recentLease, worker, { message: '最新真实回复：研究已完成，等待下一条需求。' });
    recent = await api('GET', '/rooms/' + recent.id);
    assert(Date.parse(recent.last_message_at) >= Date.parse(first.last_message_at));
    const privateContext = 'PRIVATE_CONTEXT_MUST_NOT_BECOME_A_CHAT_PREVIEW_0910';
    await api('PUT', '/rooms/' + first.id + '/context', { text: privateContext });
    const firstAfterMetadata = await api('GET', '/rooms/' + first.id);
    assert.equal(firstAfterMetadata.last_message_at, first.last_message_at);
    assert.equal(firstAfterMetadata.last_message, first.last_message);
    const boot = await api('GET', '/bootstrap');
    assert(boot.rooms.findIndex(item => item.id === first.id) < boot.rooms.findIndex(item => item.id === recent.id), 'Fixture did not exercise metadata order differing from real speaking time.');
    assert(!JSON.stringify(boot.rooms).includes(privateContext));
    for (const room of [firstAfterMetadata, recent, empty]) {
      const summary = boot.rooms.find(item => item.id === room.id);
      for (const key of ['last_message', 'last_message_at', 'last_speaker', 'team_members_preview', 'team_member_count']) assert.deepEqual(summary[key], room[key], key + ' differs between bootstrap and the full room.');
      assert(!('messages' in summary) && !('context' in summary) && !('sources' in summary) && !('lease_token' in summary));
    }
    messengerAcceptance = { first: firstAfterMetadata, recent, group, artifact };
    fs.writeFileSync(path.join(runDir, 'messenger-summary-evidence.json'), JSON.stringify({ synthetic_worker_results: true, summary_contract: 'workspace-room-summaries.md', creation_event: creation[0], rooms: boot.rooms.filter(item => [first.id, recent.id, empty.id].includes(item.id)), exact_messages: [first.messages.at(-1), recent.messages.at(-1)] }, null, 2));
  });

  await check('Read-only SQLite independently confirms message preview provenance and empty-group truth', async () => {
    const script = `import json, sqlite3, sys
from pathlib import Path
conn = sqlite3.connect(Path(sys.argv[1]).resolve().as_uri() + '?mode=ro', uri=True)
conn.row_factory = sqlite3.Row
rows = []
for room_id in sys.argv[2:]:
    message = conn.execute('SELECT id,role,agent_id,author_id,author_name,text,created_at FROM hub_messages WHERE room_id=? ORDER BY rowid DESC LIMIT 1', (room_id,)).fetchone()
    row = dict(conn.execute('SELECT id,created_at,updated_at,team_id FROM hub_rooms WHERE id=?', (room_id,)).fetchone())
    row['last_message'] = dict(message) if message else None
    row['run_count'] = conn.execute('SELECT count(*) FROM hub_runs WHERE room_id=?', (room_id,)).fetchone()[0]
    rows.append(row)
print(json.dumps({'integrity': conn.execute('PRAGMA integrity_check').fetchone()[0], 'rooms': rows}, ensure_ascii=False))
conn.close()
`;
    const evidence = await new Promise((resolve, reject) => {
      const child = spawn(python, ['-X', 'utf8', '-c', script, path.join(dataDir, 'studio.sqlite3'), first.id, recent.id, group.primary_room_id], { cwd: repo, windowsHide: true });
      let stdout = '', stderr = '';
      child.stdout.on('data', data => { stdout += data.toString(); });
      child.stderr.on('data', data => { stderr += data.toString(); });
      child.on('error', reject);
      child.on('exit', code => { if (code) reject(new Error('Read-only messenger query failed: ' + stderr)); else { try { resolve(JSON.parse(stdout)); } catch (error) { reject(error); } } });
    });
    assert.equal(evidence.integrity, 'ok');
    for (const room of [first, recent]) {
      const stored = evidence.rooms.find(item => item.id === room.id).last_message;
      assert.equal(stored.created_at, room.last_message_at);
      assert.equal([...stored.text].slice(0, 160).join(''), room.last_message);
      assert.equal(stored.agent_id, room.last_speaker.agent_id);
    }
    const empty = evidence.rooms.find(item => item.id === group.primary_room_id);
    assert.equal(empty.last_message, null);
    assert.equal(empty.run_count, 0);
    fs.writeFileSync(path.join(runDir, 'messenger-db-evidence.json'), JSON.stringify(evidence, null, 2));
  });
}

async function messengerEmptyJourney() {
  await check('Zero-history Agent selection uses the real catalog without creating rooms or claiming work', async () => {
    const initial = await api('GET', '/bootstrap');
    assert.equal(initial.rooms.length, 0);
    await page.goto(base);
    await page.locator('#initial-loading').waitFor({ state: 'hidden' });
    const contacts = page.locator('#contact-list [data-select-contact]');
    await until(async () => await contacts.count() === initial.agents.length);
    assert.deepEqual((await contacts.evaluateAll(elements => elements.map(item => item.dataset.selectContact))).sort(), initial.agents.map(item => item.id).sort());
    const writer = initial.agents.find(item => item.id === 'writer');
    await page.getByRole('button', { name: '选择对话 Agent：' + writer.name, exact: true }).click();
    assert.equal(await page.getByLabel('选择 Agent', { exact: true }).inputValue(), writer.id);
    assert.equal(await page.locator('#intro-name').innerText(), writer.name);
    assert.equal(await page.locator('#intro-description').innerText(), writer.description);
    const composer = page.getByLabel('需求输入', { exact: true });
    const draft = '仅选择真实 Agent 并保留草稿，此时不创建会话。';
    await composer.fill(draft);
    await composer.evaluate(element => { element.focus(); element.setSelectionRange(3, 7); });
    await page.waitForTimeout(3800);
    assert.equal(await composer.inputValue(), draft);
    assert.deepEqual(await composer.evaluate(element => [element === document.activeElement, element.selectionStart, element.selectionEnd]), [true, 3, 7]);
    await page.getByRole('button', { name: '创建团队', exact: true }).click();
    const editor = page.locator('#group-editor-dialog');
    await editor.waitFor({ state: 'visible' });
    await editor.getByLabel('团队名称', { exact: true }).fill('尚未提交的团队');
    await page.keyboard.press('Escape');
    await editor.waitFor({ state: 'hidden' });
    assert.equal(await composer.inputValue(), draft);
    assert.equal((await api('GET', '/bootstrap')).rooms.length, 0);
    assert.equal((await claim('qa-messenger-no-phantom-work', undefined, 'writer')).task, null);
    await page.screenshot({ path: path.join(runDir, 'messenger-empty-desktop.png'), fullPage: true, animations: 'disabled' });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: '打开侧栏', exact: true }).click();
    await page.getByRole('button', { name: '选择对话 Agent：' + writer.name, exact: true }).click();
    await assertControlNotClipped(composer);
    const input = await composer.boundingBox();
    assert(input && input.y > 844 / 2, 'The conversation composer is no longer at the bottom of the mobile reading area.');
    await assertNoOverflow(page);
    await page.screenshot({ path: path.join(runDir, 'messenger-empty-mobile.png'), fullPage: true, animations: 'disabled' });
    await page.setViewportSize({ width: 1440, height: 960 });
    await page.getByRole('button', { name: '新建对话', exact: true }).click();
    await composer.fill('');
    assert.equal((await api('GET', '/bootstrap')).rooms.length, 0);
  });

  await check('Labeled navigation and the More menu reach actual product pages with keyboard dismissal', async () => {
    for (const [name, id] of [['我的 Agent', 'agents'], ['协作团队', 'teams'], ['工作流', 'workflows'], ['技能库', 'skills'], ['周期任务', 'schedules']]) {
      await navigateWorkspace(name);
      await page.locator('#' + id + '-view').waitFor({ state: 'visible' });
      assert.equal(await page.evaluate(() => window.workspaceUI.getView()), id);
      assert(await page.locator('#' + id + '-view').innerText(), 'Navigation led to an empty placeholder.');
    }
    const more = page.getByRole('button', { name: '更多功能', exact: true });
    await more.click();
    const menu = page.locator('#more-menu');
    await menu.waitFor({ state: 'visible' });
    await page.keyboard.press('End');
    assert(await menu.locator('button,a').last().evaluate(element => element === document.activeElement));
    await page.keyboard.press('Home');
    assert(await menu.locator('button,a').first().evaluate(element => element === document.activeElement));
    await page.keyboard.press('Escape');
    await menu.waitFor({ state: 'hidden' });
    assert(await more.evaluate(element => element === document.activeElement));
    await navigateWorkspace('搜索空间');
    await page.getByLabel('搜索当前空间', { exact: true }).waitFor({ state: 'visible' });
    await page.keyboard.press('Escape');
    await more.click();
    const magazine = page.getByRole('link', { name: '杂志工作室', exact: true });
    assert.equal(await magazine.getAttribute('href'), '/magazines');
    await Promise.all([page.waitForURL('**/magazines'), magazine.click()]);
    await page.getByRole('heading', { name: '想为哪家企业制作杂志？', exact: true }).waitFor({ state: 'visible' });
    await page.getByLabel('输入企业网址或修改要求', { exact: true }).waitFor({ state: 'visible' });
    await page.goto(base + '/#new');
    await page.locator('#initial-loading').waitFor({ state: 'hidden' });
    await page.setViewportSize({ width: 390, height: 844 });
    for (const [name, id] of [['技能库', 'skills'], ['周期任务', 'schedules'], ['协作团队', 'teams']]) {
      await navigateWorkspace(name);
      await page.locator('#' + id + '-view').waitFor({ state: 'visible' });
      await assertNoOverflow(page);
    }
    await page.setViewportSize({ width: 1440, height: 960 });
    await navigateWorkspace('对话');
    assert.equal((await api('GET', '/bootstrap')).rooms.length, 0, 'Merely opening navigation destinations created a conversation.');
  });
}

async function messengerBrowserJourney() {
  const { first, recent, group } = messengerAcceptance;
  await check('Recent conversations display real speakers and speaking times, and explicit URLs override default restoration', async () => {
    await page.setViewportSize({ width: 1440, height: 960 });
    await page.goto(base);
    await page.waitForFunction(id => window.workspaceUI?.getCurrentRoom()?.id === id, recent.id);
    await until(async () => await page.locator('#room-list [data-room]').count() >= 3);
    assert.equal(await page.locator('#room-list [data-room]').first().getAttribute('data-room'), recent.id);
    for (const room of [first, recent]) {
      const row = page.locator('#room-list [data-room="' + room.id + '"]');
      assert.equal(await row.locator('time').getAttribute('datetime'), room.last_message_at);
      assert((await row.locator('.conversation-row-preview').innerText()).includes(room.last_speaker.name + '：' + room.last_message));
    }
    const header = await page.locator('#chat-identity').innerText();
    await page.getByLabel('选择 Agent', { exact: true }).selectOption('writer');
    await page.waitForTimeout(3800);
    assert.equal(await page.locator('#chat-identity').innerText(), header, 'Changing the next-message route rewrote the historical conversation identity.');
    await page.screenshot({ path: path.join(runDir, 'messenger-history-desktop.png'), fullPage: true, animations: 'disabled' });
    await page.goto(base + '/#room/' + first.id);
    await page.waitForFunction(id => window.workspaceUI?.getCurrentRoom()?.id === id, first.id);
    await page.reload();
    await page.waitForFunction(id => window.workspaceUI?.getCurrentRoom()?.id === id, first.id);
    await page.getByRole('button', { name: '新建对话', exact: true }).click();
    assert.equal(new URL(page.url()).hash, '#new');
    await page.reload();
    await page.locator('#initial-loading').waitFor({ state: 'hidden' });
    assert.equal(await page.evaluate(() => window.workspaceUI.getCurrentRoom()), null);
    assert.equal(new URL(page.url()).hash, '#new');
  });

  await check('Delayed initial reads cannot override New chat, an explicit room or user navigation', async () => {
    const evidence = [];
    for (const intent of ['new', 'teams', 'room']) {
      const delayedPage = await page.context().newPage();
      delayedPage.on('pageerror', error => pageErrors.push(error.message));
      let release;
      const gate = new Promise(resolve => { release = resolve; });
      let intercepted = false;
      await delayedPage.route('**/api/hub/rooms/' + recent.id, async route => {
        const response = await route.fetch();
        intercepted = true;
        await gate;
        await route.fulfill({ response });
      });
      try {
        await delayedPage.goto(base);
        await until(() => intercepted);
        if (intent === 'new') {
          await delayedPage.getByRole('button', { name: '新建对话', exact: true }).click();
          await delayedPage.getByLabel('需求输入', { exact: true }).fill('在迟到的旧会话返回前写下的新草稿。');
        } else if (intent === 'teams') await navigateWorkspace('协作团队', delayedPage);
        else await delayedPage.locator('[data-room="' + first.id + '"]').click();
        release();
        await delayedPage.unrouteAll({ behavior: 'wait' });
        await delayedPage.waitForTimeout(300);
        const state = await delayedPage.evaluate(() => ({ view: window.workspaceUI.getView(), room: window.workspaceUI.getCurrentRoom()?.id || null, draft: document.getElementById('composer-input').value, hash: location.hash }));
        if (intent === 'new') assert.deepEqual(state, { view: 'chat', room: null, draft: '在迟到的旧会话返回前写下的新草稿。', hash: '#new' });
        else if (intent === 'teams') assert.equal(state.view, 'teams');
        else { assert.equal(state.room, first.id); assert.equal(state.hash, '#room/' + first.id); }
        evidence.push({ intent, state, real_response_delayed: true });
      } finally {
        release();
        await delayedPage.unrouteAll({ behavior: 'wait' });
        await delayedPage.close();
      }
    }
    fs.writeFileSync(path.join(runDir, 'messenger-navigation-races.json'), JSON.stringify(evidence, null, 2));
  });
  await messengerExplicitNavigationJourney();

  await check('An empty persistent group shows its real creation context and returns to the same room on mobile', async () => {
    await navigateWorkspace('协作团队');
    await page.getByRole('button', { name: '打开群聊：' + group.name, exact: true }).click();
    await page.waitForFunction(id => window.workspaceUI?.getCurrentRoom()?.id === id, group.primary_room_id);
    const record = page.locator('[data-team-created="' + group.id + '"]');
    await record.waitFor({ state: 'visible' });
    assert.equal(await record.locator('time').getAttribute('datetime'), group.created_at);
    assert((await record.innerText()).includes((group.created_by_name || '空间成员') + '创建了团队'));
    const room = await api('GET', '/rooms/' + group.primary_room_id);
    for (const member of room.team.members) assert((await page.locator('#group-chat-empty').innerText()).includes(member.name));
    assert.equal(room.messages.length, 0);
    assert.equal(room.runs.length, 0);
    assert.equal(await page.locator('#messages .message').count(), 0);
    await page.screenshot({ path: path.join(runDir, 'messenger-empty-group-desktop.png'), fullPage: true, animations: 'disabled' });
    await page.reload();
    await page.waitForFunction(id => window.workspaceUI?.getCurrentRoom()?.id === id, group.primary_room_id);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: '团队信息', exact: true }).click();
    const dialog = page.locator('#group-info-dialog');
    await dialog.waitFor({ state: 'visible' });
    await assertControlNotClipped(dialog.getByRole('button', { name: '关闭', exact: true }));
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'hidden' });
    await assertNoOverflow(page);
    await assertControlNotClipped(page.getByLabel('需求输入', { exact: true }));
    await page.screenshot({ path: path.join(runDir, 'messenger-empty-group-mobile.png'), fullPage: true, animations: 'disabled' });
    assert.equal(await page.evaluate(() => window.workspaceUI.getCurrentRoom().id), group.primary_room_id);
    await page.setViewportSize({ width: 1440, height: 960 });
  });

  await check('The Messenger group keeps the real task, Studio discussion and linked revision continuous after refresh', async () => {
    const roomId = group.primary_room_id;
    const title = '秋季买手专刊提案';
    const firstContent = '# 秋季买手专刊提案\n\n## 本期方向\n\n以小型家居与礼品店为主要读者，围绕秋季陈列、选品组合和采购沟通组织内容。所有价格和供货周期均需依据企业资料核实。\n\n## 内容安排\n\n先介绍企业与产品系列，再按使用场景展示组合，最后给出需要向供应商确认的问题。';
    const secondContent = firstContent + '\n\n## 读者与采购问题\n\n读者包括精品家居店采购负责人和礼品渠道运营；重点确认最小起订量、样品政策与交付周期。';
    const writerName = (await api('GET', '/bootstrap')).agents.find(agent => agent.id === 'writer').name;
    await page.getByRole('button', { name: '点名 Agent', exact: true }).click();
    const mention = page.locator('#group-mention-dialog');
    await mention.getByLabel('点名：' + writerName, { exact: true }).check();
    await mention.getByRole('button', { name: '确认点名', exact: true }).click();
    await page.getByLabel('需求输入', { exact: true }).fill('请为秋季买手专刊整理一份可供团队讨论的选题提案。');
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await until(async () => (await api('GET', '/rooms/' + roomId)).runs.length === 1);
    const worker = 'qa-messenger-studio-fixture';
    const initial = await claim(worker, roomId, 'writer');
    assert(initial.task);
    const completed = await complete(initial, worker, { message: '选题提案已整理为文档，读者范围可以在评论中继续细化。', artifacts: [{ title, kind: 'markdown', content: firstContent }] });
    const artifact = completed.artifacts[0];
    await page.getByRole('button', { name: '打开成果：' + title, exact: true }).first().waitFor();
    await page.locator('#toast').waitFor({ state: 'hidden' });
    await page.screenshot({ path: path.join(runDir, 'messenger-group-conversation-desktop.png'), fullPage: true, animations: 'disabled' });
    await openArtifact(title);
    await page.getByRole('button', { name: '评论', exact: true }).click();
    const feedback = '请补充读者角色和需要确认的采购问题。';
    await page.getByLabel('评论内容', { exact: true }).fill(feedback);
    await page.getByRole('button', { name: '发表评论', exact: true }).click();
    const comment = await until(async () => (await api('GET', '/artifacts/' + artifact.id + '/comments?version=1')).comments.find(item => item.body === feedback));
    await page.getByLabel('选择讨论：' + feedback, { exact: true }).check();
    await page.getByRole('button', { name: '交给 Agent 修改', exact: true }).click();
    const modification = page.locator('#modification-dialog');
    await modification.getByLabel('修改说明', { exact: true }).fill('保留现有提案，并按讨论补充两类读者与采购确认清单。');
    await modification.getByLabel('负责修改的 Agent', { exact: true }).selectOption('writer');
    await modification.getByRole('button', { name: '提交修改请求', exact: true }).click();
    const request = await until(async () => (await api('GET', '/artifacts/' + artifact.id + '/modifications')).modifications[0]);
    const revision = await claim(worker, roomId, 'writer');
    assert.equal(revision.task.modification_request_id, request.id);
    assert.equal(revision.context.modification.baseline.content, firstContent);
    await complete(revision, worker, { message: '已按讨论补充读者与采购问题，修订保存在同一份提案中。', artifacts: [{ id: artifact.id, base_version: 1, title, kind: 'markdown', content: secondContent }] });
    const v2 = await api('GET', '/artifacts/' + artifact.id + '?version=2');
    assert.equal(v2.modification_request_id, request.id);
    assert.equal(v2.source_version, 1);
    assert.equal((await api('GET', '/comments/' + comment.id)).status, 'open');
    await page.reload();
    await page.waitForFunction(id => window.workspaceUI?.getCurrentRoom()?.id === id, roomId);
    await openArtifact(title);
    assert.equal(await page.getByLabel('成果版本', { exact: true }).inputValue(), '2');
    assert.equal(await page.locator('#artifact-list').isVisible(), false, 'One loaded artifact should not present a redundant file-switching tab.');
    assert((await page.locator('#artifact-version option:checked').innerText()).includes('最新'));
    const link = page.getByRole('link', { name: '下载成果', exact: true });
    const download = await fetch(new URL(await link.getAttribute('href'), base));
    assert.equal(download.status, 200);
    assert.equal(await download.text(), secondContent);
    await page.getByLabel('成果版本', { exact: true }).selectOption('1');
    await until(async () => (await page.locator('#artifact-version option:checked').innerText()).includes('历史'));
    await page.locator('#artifact-download[href$="version=1"]').waitFor({ state: 'visible' });
    const historyDownload = await fetch(new URL(await link.getAttribute('href'), base));
    assert.equal(await historyDownload.text(), firstContent);
    await page.getByRole('button', { name: '评论', exact: true }).click();
    await page.locator('[data-thread-id="' + comment.id + '"]').waitFor({ state: 'visible' });
    const draft = page.getByLabel('评论内容', { exact: true });
    await draft.fill('保留这个未发送的后续讨论草稿。');
    await draft.evaluate(element => { element.focus(); element.setSelectionRange(2, 8); });
    await page.waitForTimeout(3800);
    assert.equal(await draft.inputValue(), '保留这个未发送的后续讨论草稿。');
    assert.deepEqual(await draft.evaluate(element => [element === document.activeElement, element.selectionStart, element.selectionEnd]), [true, 2, 8]);
    assert.equal(await page.getByLabel('成果版本', { exact: true }).inputValue(), '1');
    assert.equal(await page.locator('#artifact-list').isVisible(), false);
    await page.screenshot({ path: path.join(runDir, 'messenger-group-studio-desktop.png'), fullPage: true, animations: 'disabled' });
    await page.setViewportSize({ width: 390, height: 844 });
    await assertControlNotClipped(draft);
    await assertControlNotClipped(page.getByRole('button', { name: '发表评论', exact: true }));
    await assertNoOverflow(page);
    await page.screenshot({ path: path.join(runDir, 'messenger-group-studio-mobile.png'), fullPage: true, animations: 'disabled' });
    const backToBody = page.getByRole('button', { name: '返回正文', exact: true });
    await assertControlNotClipped(backToBody);
    await backToBody.click();
    await page.locator('#artifact-body').waitFor({ state: 'visible' });
    assert((await page.locator('#artifact-body').innerText()).includes('本期方向'));
    assert.equal(await page.getByLabel('成果版本', { exact: true }).inputValue(), '1');
    await page.screenshot({ path: path.join(runDir, 'messenger-mobile-return-to-document.png'), fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: '评论', exact: true }).click();
    assert.equal(await draft.inputValue(), '保留这个未发送的后续讨论草稿。');
    await page.getByRole('button', { name: '关闭工作区', exact: true }).click();
    await page.getByLabel('需求输入', { exact: true }).waitFor({ state: 'visible' });
    assert.equal(await page.evaluate(() => window.workspaceUI.getCurrentRoom().id), roomId);
    await page.screenshot({ path: path.join(runDir, 'messenger-return-to-chat-mobile.png'), fullPage: true, animations: 'disabled' });
    await assertControlNotClipped(page.getByRole('button', { name: '打开成果：' + title, exact: true }).last());
    await page.screenshot({ path: path.join(runDir, 'messenger-group-conversation-mobile.png'), fullPage: true, animations: 'disabled' });
    await page.setViewportSize({ width: 1440, height: 960 });
    fs.writeFileSync(path.join(runDir, 'messenger-studio-evidence.json'), JSON.stringify({ synthetic_worker_results: true, room_id: roomId, artifact_id: artifact.id, request_id: request.id, versions: [1, 2], source_version: v2.source_version, comment_status: 'open', exact_download_verified: true, draft_preserved_during_polling: true }, null, 2));
  });
}

async function studioSwitchingJourney() {
  await check('A second real artifact restores file switching without ambiguous latest-version badges', async () => {
    const roomId = messengerAcceptance.group.primary_room_id;
    const firstTitle = '秋季买手专刊提案';
    const secondTitle = '采购沟通清单';
    await api('POST', '/rooms/' + roomId + '/messages', { text: '再整理一份独立的采购沟通清单。', target_agent_ids: ['writer'], request_key: 'qa-studio-second-artifact' });
    const lease = await claim('qa-studio-file-switching', roomId, 'writer');
    await complete(lease, 'qa-studio-file-switching', { message: '独立清单已保存。', artifacts: [{ title: secondTitle, kind: 'markdown', content: '# 采购沟通清单\n\n确认起订量、样品政策与交付周期。' }] });
    await page.getByRole('button', { name: '打开成果：' + secondTitle, exact: true }).first().waitFor({ state: 'visible' });
    await openArtifact(secondTitle);
    const list = page.locator('#artifact-list');
    await list.waitFor({ state: 'visible' });
    assert.equal(await list.locator('[data-artifact]').count(), 2);
    assert.deepEqual(await list.locator('small').allTextContents(), ['文档', '文档']);
    await list.getByRole('button', { name: '打开成果：' + firstTitle, exact: true }).click();
    await until(async () => await page.locator('#artifact-title').innerText() === firstTitle);
    assert.equal(await page.getByLabel('成果版本', { exact: true }).inputValue(), '2');
    await page.getByLabel('成果版本', { exact: true }).selectOption('1');
    await until(async () => (await page.locator('#artifact-version option:checked').innerText()).includes('历史'));
    await page.locator('#artifact-download[href$="version=1"]').waitFor({ state: 'visible' });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const geometry = await page.evaluate(title => {
      const card = [...document.querySelectorAll('#artifact-list [data-artifact]')].find(item => item.getAttribute('aria-label') === '打开成果：' + title);
      return { card: card?.getBoundingClientRect().toJSON(), heading: document.getElementById('artifact-title').getBoundingClientRect().toJSON() };
    }, firstTitle);
    fs.writeFileSync(path.join(runDir, 'studio-multiple-artifacts-geometry.json'), JSON.stringify(geometry, null, 2));
    assert(geometry.card && geometry.heading && geometry.heading.y >= geometry.card.bottom + 6, 'Multiple-artifact navigation overlaps the document heading: ' + JSON.stringify(geometry));
    await list.getByRole('button', { name: '打开成果：' + secondTitle, exact: true }).click();
    await until(async () => await page.locator('#artifact-title').innerText() === secondTitle);
    assert.equal(await page.getByLabel('成果版本', { exact: true }).inputValue(), '1');
    assert((await page.locator('#artifact-version option:checked').innerText()).includes('最新'));
    await assertNoOverflow(page);
    await page.screenshot({ path: path.join(runDir, 'studio-multiple-artifacts-mobile.png'), fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: '关闭工作区', exact: true }).click();
    await page.getByLabel('需求输入', { exact: true }).waitFor({ state: 'visible' });
    await page.setViewportSize({ width: 1440, height: 960 });
  });
}

async function messengerExplicitNavigationJourney() {
  await check('A delayed explicit history selection cannot undo a later directory navigation', async () => {
    const { first, recent } = messengerAcceptance;
    const delayedPage = await page.context().newPage();
    delayedPage.on('pageerror', error => pageErrors.push(error.message));
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    let intercepted = false;
    try {
      await delayedPage.goto(base + '/#room/' + recent.id);
      await delayedPage.waitForFunction(id => window.workspaceUI?.getCurrentRoom()?.id === id, recent.id);
      await delayedPage.route('**/api/hub/rooms/' + first.id, async route => {
        const response = await route.fetch();
        intercepted = true;
        await gate;
        await route.fulfill({ response });
      });
      await delayedPage.locator('#room-list [data-room="' + first.id + '"]').click();
      await until(() => intercepted);
      await navigateWorkspace('协作团队', delayedPage);
      await delayedPage.locator('#teams-view').waitFor({ state: 'visible' });
      release();
      await delayedPage.unrouteAll({ behavior: 'wait' });
      await delayedPage.waitForTimeout(300);
      const state = await delayedPage.evaluate(() => ({ view: window.workspaceUI.getView(), room_id: window.workspaceUI.getCurrentRoom()?.id || null, hash: location.hash }));
      fs.writeFileSync(path.join(runDir, 'messenger-explicit-navigation-race.json'), JSON.stringify({ delayed_room_id: first.id, prior_room_id: recent.id, user_intent: 'teams', after_real_response: state }, null, 2));
      await delayedPage.screenshot({ path: path.join(runDir, 'messenger-explicit-navigation-race.png'), fullPage: true, animations: 'disabled' });
      assert.equal(state.view, 'teams', 'A genuine late response for an explicitly clicked conversation undid the later directory navigation.');
      assert.equal(state.room_id, recent.id, 'A superseded conversation read replaced the current room after navigation.');
    } finally {
      release();
      await delayedPage.unrouteAll({ behavior: 'wait' });
      await delayedPage.close();
    }
  });
}

async function messengerResponsiveJourney() {
  const evidence = [];
  const originalViewport = page.viewportSize();
  await check('Conversation navigation has one coherent mode at tablet breakpoint boundaries', async () => {
    const { first } = messengerAcceptance;
    for (const width of [390, 760, 780, 800, 801, 820, 1440]) {
      await page.setViewportSize({ width, height: 960 });
      await page.goto(base + '/#room/' + first.id);
      await page.waitForFunction(id => window.workspaceUI?.getCurrentRoom()?.id === id, first.id);
      const railVisible = await page.locator('.app-rail').isVisible();
      const menuVisible = await page.getByRole('button', { name: '打开侧栏', exact: true }).isVisible();
      const secondaryNavVisible = await page.locator('.main-nav').isVisible();
      const state = { width, rail_visible: railVisible, menu_visible: menuVisible, duplicate_mobile_nav_visible: secondaryNavVisible };
      evidence.push(state);
      fs.writeFileSync(path.join(runDir, 'messenger-responsive-modes.json'), JSON.stringify(evidence, null, 2));
      await page.screenshot({ path: path.join(runDir, 'messenger-responsive-' + width + '.png'), fullPage: true, animations: 'disabled' });
      assert.notEqual(railVisible, menuVisible, 'Desktop rail and mobile menu must not be simultaneously shown or both missing: ' + JSON.stringify(state));
      assert.equal(secondaryNavVisible, false, 'The closed navigation surface contains duplicate mobile destinations: ' + JSON.stringify(state));
      await assertNoOverflow(page);
      await navigateWorkspace('协作团队');
      await page.locator('#teams-view').waitFor({ state: 'visible' });
      await assertNoOverflow(page);
      await navigateWorkspace('对话');
      await page.getByLabel('需求输入', { exact: true }).waitFor({ state: 'visible' });
      assert.equal(await page.evaluate(() => window.workspaceUI.getCurrentRoom().id), first.id);
      await openArtifact('会话结构验收文档');
      await assertNoOverflow(page);
      await assertControlNotClipped(page.getByRole('button', { name: '关闭工作区', exact: true }));
      await page.getByRole('button', { name: '关闭工作区', exact: true }).click();
      await assertControlNotClipped(page.getByLabel('需求输入', { exact: true }));
    }
  });
  await check('Resizing an open mobile drawer to desktop clears its overlay and keeps navigation usable', async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: '打开侧栏', exact: true }).click();
    await page.locator('#sidebar.open').waitFor({ state: 'visible' });
    assert(await page.locator('#sidebar-shade').isVisible());
    await page.setViewportSize({ width: 780, height: 844 });
    await until(async () => await page.locator('#sidebar.open').count() === 0);
    assert.equal(await page.locator('#sidebar-shade').isVisible(), false);
    await navigateWorkspace('我的 Agent');
    await page.locator('#agents-view').waitFor({ state: 'visible' });
    await navigateWorkspace('对话');
    const composer = page.getByLabel('需求输入', { exact: true });
    await composer.click();
    assert(await composer.evaluate(element => element === document.activeElement));
    await composer.fill('抽屉关闭后仍可继续编辑。');
    await page.keyboard.press('Tab');
    assert(await page.evaluate(() => document.activeElement.closest('#composer') !== null));
    await assertNoOverflow(page);
    await page.screenshot({ path: path.join(runDir, 'messenger-responsive-resize-open-drawer.png'), fullPage: true, animations: 'disabled' });
  });
  await page.setViewportSize(originalViewport);
}

async function teamBoundaryJourney() {
  let team, primaryRoom, source, artifact;
  let cancellationTeam, retainedArtifact, retainedComment;
  const sameMembers = (actual, expected) => assert.deepEqual([...actual].sort(), [...expected].sort());
  const teamSend = (current, body, expected) => api('POST', '/rooms/' + current.primary_room_id + '/messages', body, expected);

  await check('A persistent team has one empty room and structured routing preserves cross-message context', async () => {
    const before = await api('GET', '/bootstrap');
    const definition = { name: '持久团队边界验收', description: '仅用合成结果检查长期群聊与明确的成员边界。', member_ids: ['researcher', 'writer'], routing_mode: 'mention', request_key: 'qa-team-boundary-create' };
    team = await api('POST', '/teams', definition, 201);
    const duplicate = await api('POST', '/teams', definition, 201);
    assert.equal(duplicate.id, team.id);
    assert.equal(duplicate.primary_room_id, team.primary_room_id);
    await api('POST', '/teams', { ...definition, name: '相同键不能创建另一团队' }, 409);
    await api('POST', '/teams', { ...definition, member_ids: ['coordinator'], request_key: 'qa-team-invalid-coordinator' }, 400);
    assert.equal((await api('GET', '/bootstrap')).rooms.length, before.rooms.length + 1);
    assert.equal((await api('GET', '/bootstrap')).templates.length, before.templates.length, 'A team was silently converted into a workflow template.');
    for (let index = 0; index < 3; index++) assert.equal((await api('POST', '/teams/' + team.id + '/rooms', {})).id, team.primary_room_id);
    primaryRoom = await api('GET', '/rooms/' + team.primary_room_id);
    assert.equal(primaryRoom.messages.length, 0);
    assert.equal(primaryRoom.runs.length, 0);
    assert.equal(primaryRoom.team_id, team.id);
    const note = '@内容编辑 请保留这条群消息；文字提及本身不构成结构化点名。';
    primaryRoom = await teamSend(team, { text: note, request_key: 'qa-team-note' });
    assert.equal(primaryRoom.runs.length, 0);
    assert.equal(primaryRoom.messages.at(-1).run_id, null);
    assert.equal(primaryRoom.messages.at(-1).routing.mode, 'note');
    await teamSend(team, { text: '缺少幂等键不得写入。' }, 400);
    await teamSend(team, { text: '非成员不能被点名。', target_agent_ids: ['developer'], request_key: 'qa-team-outsider-message' }, 409);
    await teamSend(team, { text: '两套目标字段不应冲突。', target_agent_ids: ['writer'], agent_id: 'researcher', request_key: 'qa-team-conflicting-targets' }, 400);
    assert.equal((await api('GET', '/rooms/' + team.primary_room_id)).messages.length, 1);
    source = await api('POST', '/rooms/' + team.primary_room_id + '/sources', { title: '长期群共享资料', kind: 'text', content: 'TEAM_SHARED_SOURCE_0901：这条资料应出现在后续成员领取的上下文。' }, 201);
    const directBody = { text: '请整理这份共享资料。', target_agent_ids: ['writer'], request_key: 'qa-team-first-direct', base_team_version: team.version };
    primaryRoom = await teamSend(team, directBody);
    const directRun = primaryRoom.runs.at(-1);
    assert.equal(directRun.tasks.length, 1);
    assert.equal(directRun.tasks[0].agent_id, 'writer');
    assert.equal(directRun.routing.mode, 'direct');
    assert.equal(directRun.team_id, team.id);
    sameMembers(directRun.member_ids, team.member_ids);
    await teamSend(team, directBody);
    assert.equal((await api('GET', '/rooms/' + team.primary_room_id)).runs.length, 1);
    const writer = await claim('qa-team-context-writer', team.primary_room_id, 'writer');
    assert.equal(writer.context.team.id, team.id);
    sameMembers(writer.context.team.allowed_agent_ids, ['researcher', 'writer']);
    sameMembers(writer.context.agents.map(item => item.id), ['researcher', 'writer']);
    assert(writer.context.messages.some(item => item.text === note));
    assert(writer.context.sources.some(item => item.id === source.id && item.content.includes('TEAM_SHARED_SOURCE_0901')));
    primaryRoom = await complete(writer, 'qa-team-context-writer', { message: '团队首轮内容已保存。', artifacts: [{ title: '团队连续上下文成果', kind: 'markdown', content: '# 连续上下文\n\nTEAM_PRIOR_ARTIFACT_0901' }] });
    artifact = primaryRoom.artifacts.find(item => item.title === '团队连续上下文成果');
    team = await api('PUT', '/teams/' + team.id, { base_version: team.version, routing_mode: 'auto' });
    assert.deepEqual(team.cancelled_run_ids, []);
    assert.equal((await api('GET', '/rooms/' + team.primary_room_id)).runs.length, 1, 'Changing routing settings triggered unsolicited work.');
  });

  await check('A team coordinator cannot plan outside the frozen roster even when another member is added', async () => {
    const body = { text: '沿用上轮成果，为当前成员分配下一步。', request_key: 'qa-team-auto-plan', base_team_version: team.version };
    const dispatched = await teamSend(team, body);
    const run = dispatched.runs.at(-1);
    assert.equal(run.tasks.length, 1);
    assert.equal(run.tasks[0].agent_id, 'coordinator');
    assert.equal(run.team_version, team.version);
    const coordinator = await claim('qa-team-bounded-coordinator', team.primary_room_id, 'coordinator');
    assert.equal(coordinator.context.agent.id, 'coordinator');
    sameMembers(coordinator.context.agents.map(item => item.id), ['researcher', 'writer']);
    assert(JSON.stringify(coordinator.context.artifact_contents).includes('TEAM_PRIOR_ARTIFACT_0901'));
    assert(coordinator.context.messages.some(item => item.text === '团队首轮内容已保存。'));
    assert(coordinator.context.sources.some(item => item.id === source.id));
    const frozenVersion = team.version;
    team = await api('PUT', '/teams/' + team.id, { base_version: team.version, member_ids: ['researcher', 'writer', 'designer'] });
    assert.deepEqual(team.cancelled_run_ids, []);
    const invalid = id => ({ message: '错误计划不应部分写入。', artifacts: [{ title: '不应保存的越界成果', kind: 'markdown', content: 'DENIED_TEAM_PLAN_ARTIFACT' }], plan: [
      { key: 'allowed', agent_id: 'researcher', title: '允许的成员', instructions: '这一部分也不能单独写入。', depends_on: [] },
      { key: 'outside', agent_id: id, title: '不被当前run授权的成员', instructions: '这项必须被拒绝。', depends_on: [] },
    ] });
    for (const outsider of ['developer', 'designer']) await complete(coordinator, 'qa-team-bounded-coordinator', invalid(outsider), 409);
    const unchanged = await api('GET', '/rooms/' + team.primary_room_id);
    assert.equal(unchanged.runs.find(item => item.id === run.id).tasks.length, 1);
    assert.equal(unchanged.artifacts.length, 1);
    assert.equal(unchanged.runs.find(item => item.id === run.id).team_version, frozenVersion);
    sameMembers(unchanged.runs.find(item => item.id === run.id).member_ids, ['researcher', 'writer']);
    await complete(coordinator, 'qa-team-bounded-coordinator', { message: '只向本轮允许的两位成员分工。', plan: [
      { key: 'research', agent_id: 'researcher', title: '复核资料', instructions: '核对共享上下文。', depends_on: [] },
      { key: 'write', agent_id: 'writer', title: '更新结论', instructions: '根据资料补充结论。', depends_on: ['research'] },
    ] });
    assert.equal((await claim('qa-team-early-writer', team.primary_room_id, 'writer')).task, null);
    const researcher = await claim('qa-team-bounded-researcher', team.primary_room_id, 'researcher');
    await complete(researcher, 'qa-team-bounded-researcher', { message: '成员不得递归派发。', plan: [{ key: 'nested', agent_id: 'writer', title: '不应出现', instructions: '禁止递归委派。', depends_on: [] }] }, 400);
    await api('DELETE', '/sources/' + source.id);
    await complete(researcher, 'qa-team-bounded-researcher', { message: '资料复核已完成。' });
    const writer = await claim('qa-team-bounded-final', team.primary_room_id, 'writer');
    sameMembers(writer.context.team.allowed_agent_ids, ['researcher', 'writer']);
    assert.equal(writer.context.team.run_version, frozenVersion);
    assert.equal(writer.context.team.version, team.version);
    assert(!writer.context.sources.some(item => item.id === source.id));
    await complete(writer, 'qa-team-bounded-final', { message: '旧轮次仍只由冻结成员完成。' });
    const subsequent = await teamSend(team, { text: '现在允许新加入的设计师直接接手。', target_agent_ids: ['designer'], request_key: 'qa-team-new-member-fresh-run', base_team_version: team.version });
    sameMembers(subsequent.runs.at(-1).member_ids, team.member_ids);
    const designer = await claim('qa-team-new-designer', team.primary_room_id, 'designer');
    await complete(designer, 'qa-team-new-designer', { message: '新增成员只在新一轮获得授权。' });
    const history = await api('GET', '/teams/' + team.id + '/history?after=0&limit=1');
    assert.equal(history.history.length, 1);
    assert.equal(history.has_more, true);
    sameMembers(history.history[0].member_ids, ['researcher', 'writer']);
    teamAcceptance = { teamId: team.id, roomId: team.primary_room_id, version: team.version, artifactId: artifact.id };
    fs.writeFileSync(path.join(runDir, 'team-routing-evidence.json'), JSON.stringify({ synthetic: true, team, room: await api('GET', '/rooms/' + team.primary_room_id), initial_history: history }, null, 2));
  });

  await check('Removing a member atomically stops queued, running and failed runs and rejects every late lease path', async () => {
    cancellationTeam = await api('POST', '/teams', { name: '成员移除边界验收', member_ids: ['researcher', 'writer', 'reviewer'], routing_mode: 'auto', request_key: 'qa-team-removal-create' }, 201);
    // Room turns are serialized; create the retryable failed turn before the active plan.
    const failedRoom = await teamSend(cancellationTeam, { text: '失败但仍可重试的另一轮。', target_agent_ids: ['writer'], request_key: 'qa-team-removal-failed' });
    const runC = failedRoom.runs.at(-1);
    const failedLease = await claim('qa-remove-failed', cancellationTeam.primary_room_id, 'writer');
    assert.equal(failedLease.task.run_id, runC.id);
    await api('POST', '/tasks/' + failedLease.task.id + '/fail', { worker_id: 'qa-remove-failed', lease_token: failedLease.lease_token, error: '仅用于成员移除对失败任务的验收。' });
    const initial = await teamSend(cancellationTeam, { text: '保留已完成研究，后续工作接受成员变更控制。', request_key: 'qa-team-removal-plan' });
    const runA = initial.runs.at(-1);
    const coordinator = await claim('qa-remove-coordinator', cancellationTeam.primary_room_id, 'coordinator');
    await complete(coordinator, 'qa-remove-coordinator', { message: '三步合成分工已保存。', plan: [
      { key: 'research', agent_id: 'researcher', title: '已完成的研究', instructions: '输出需要保留的成果。', depends_on: [] },
      { key: 'write', agent_id: 'writer', title: '正在执行的写作', instructions: '成员变更后应停止。', depends_on: ['research'] },
      { key: 'staged', agent_id: 'writer', title: '结果已暂存的并行任务', instructions: '取消后不能发布已暂存结果。', depends_on: ['research'] },
      { key: 'review', agent_id: 'reviewer', title: '等待前序的审阅', instructions: '不能永久卡在等待中。', depends_on: ['write', 'staged'] },
    ] });
    const research = await claim('qa-remove-research', cancellationTeam.primary_room_id, 'researcher');
    const withArtifact = await complete(research, 'qa-remove-research', { message: '研究成果已真实保存。', artifacts: [{ title: '移除成员后仍保留的成果', kind: 'markdown', content: '# 原始成果\n\nTEAM_RETAIN_COMPLETED_0901' }] });
    retainedArtifact = withArtifact.artifacts[0];
    retainedComment = await api('POST', '/artifacts/' + retainedArtifact.id + '/comments', { artifact_version: 1, body: '成员变更不能自动解决这条意见。', request_key: 'qa-team-retained-comment' }, 201);
    const running = await claim('qa-remove-running', cancellationTeam.primary_room_id, 'writer');
    await appendExecution(running, 'qa-remove-running', 'team-removal-pending-call', 'tool_call', { call_id: 'team-removal-receipt', tool: 'qa_no_external_action', retry_safe: false, summary: '合成回执；没有执行实际外部动作，历史不能被成员变更抹去。' });
    const staged = await claim('qa-remove-staged', cancellationTeam.primary_room_id, 'writer');
    assert.equal(staged.task.run_id, runA.id);
    const stagedResult = { message: '取消后不应发布。', artifacts: [{ title: '不应发布的待提交成果', kind: 'markdown', content: '# 必须保持私有的合成候选' }] };
    await api('POST', '/tasks/' + staged.task.id + '/execution/result', { worker_id: 'qa-remove-staged', lease_token: staged.lease_token, result: stagedResult });
    const queuedRoom = await teamSend(cancellationTeam, { text: '保持排队的另一轮。', target_agent_ids: ['researcher'], request_key: 'qa-team-removal-queued' });
    const runB = queuedRoom.runs.at(-1);
    const priorVersion = cancellationTeam.version;
    cancellationTeam = await api('PUT', '/teams/' + cancellationTeam.id, { base_version: priorVersion, member_ids: ['writer', 'reviewer'] });
    sameMembers(cancellationTeam.cancelled_run_ids, [runA.id, runB.id, runC.id]);
    const stopped = await api('GET', '/rooms/' + cancellationTeam.primary_room_id);
    assert(stopped.runs.every(item => item.status === 'cancelled'));
    assert(stopped.runs.flatMap(item => item.tasks).every(item => ['completed', 'cancelled', 'failed'].includes(item.status)));
    assert(stopped.runs.find(item => item.id === runC.id).tasks.every(item => item.status === 'failed'), 'The prior failure evidence should remain attributable inside its cancelled run.');
    assert.equal(stopped.artifacts.length, 1);
    assert.equal((await api('GET', '/artifacts/' + retainedArtifact.id + '?version=1')).content, '# 原始成果\n\nTEAM_RETAIN_COMPLETED_0901');
    assert.equal((await api('GET', '/comments/' + retainedComment.id)).status, 'open');
    const owners = [[running, 'qa-remove-running'], [staged, 'qa-remove-staged']];
    for (const [lease, worker] of owners) {
      const owner = { worker_id: worker, lease_token: lease.lease_token };
      await api('POST', '/tasks/' + lease.task.id + '/heartbeat', owner, 409);
      await appendExecution(lease, worker, 'after-member-removal', 'progress', { message: '成员已变更，旧租约不应继续写。' }, undefined, 409);
      await api('POST', '/tasks/' + lease.task.id + '/execution/result', { ...owner, result: stagedResult }, 409);
      await api('POST', '/tasks/' + lease.task.id + '/execution/result/read', owner, 409);
      await api('POST', '/tasks/' + lease.task.id + '/fail', { ...owner, error: '旧租约不能改变取消状态。' }, 409);
      await complete(lease, worker, stagedResult, 409);
    }
    for (const run of [runA, runB, runC]) await api('POST', '/runs/' + run.id + '/retry', {}, 409);
    assert.equal((await api('POST', '/tasks/claim', { worker_id: 'qa-remove-no-reclaim', room_id: cancellationTeam.primary_room_id })).task, null);
    const ledger = await api('GET', '/executions/' + running.execution.id + '/events');
    assert(ledger.events.some(item => item.event_id === 'team-removal-pending-call'));
    cancellationTeam = await api('PUT', '/teams/' + cancellationTeam.id, { base_version: cancellationTeam.version, member_ids: ['researcher', 'writer', 'reviewer'] });
    assert.equal((await claim('qa-readded-member', cancellationTeam.primary_room_id, 'researcher')).task, null);
    await complete(running, 'qa-remove-running', stagedResult, 409);
    await teamSend(cancellationTeam, { text: '移除再加入后的新请求。', target_agent_ids: ['writer'], request_key: 'qa-team-after-readd' });
    const fresh = await claim('qa-readded-fresh', cancellationTeam.primary_room_id, 'writer');
    assert(fresh.task && ![running.task.id, staged.task.id].includes(fresh.task.id));
    await complete(fresh, 'qa-readded-fresh', { message: '仅新请求能够恢复协作。' });
    fs.writeFileSync(path.join(runDir, 'team-removal-evidence.json'), JSON.stringify({ synthetic: true, team: cancellationTeam, room: await api('GET', '/rooms/' + cancellationTeam.primary_room_id), preserved_events: ledger.events }, null, 2));
  });

  await check('Team artifact revisions respect current membership and archive preserves readable history', async () => {
    cancellationTeam = await api('PUT', '/teams/' + cancellationTeam.id, { base_version: cancellationTeam.version, routing_mode: 'mention', member_ids: ['writer', 'reviewer'] });
    const invalid = { base_version: 1, comment_ids: [retainedComment.id], instructions: '不能给已移除成员派修改任务。', agent_id: 'researcher', request_key: 'qa-team-invalid-modification' };
    await api('POST', '/artifacts/' + retainedArtifact.id + '/modifications', invalid, 409);
    assert.equal((await api('GET', '/artifacts/' + retainedArtifact.id + '/modifications')).modifications.length, 0);
    const modification = await api('POST', '/artifacts/' + retainedArtifact.id + '/modifications', { ...invalid, agent_id: 'writer', request_key: 'qa-team-valid-modification' }, 201);
    const lease = await claim('qa-team-revision-writer', cancellationTeam.primary_room_id, 'writer');
    assert.equal(lease.task.id, modification.task_id);
    assert.equal(lease.context.team.id, cancellationTeam.id);
    sameMembers(lease.context.team.allowed_agent_ids, ['writer', 'reviewer']);
    assert.equal(lease.context.modification.artifact_id, retainedArtifact.id);
    await complete(lease, 'qa-team-revision-writer', { message: '明确修改请求在仅点名模式仍可执行。', artifacts: [{ id: retainedArtifact.id, base_version: 1, title: retainedArtifact.title, kind: 'markdown', content: '# 团队修订\n\n基于未解决评论保存第二版。' }] });
    const revised = await api('GET', '/artifacts/' + retainedArtifact.id + '?version=2');
    assert.equal(revised.modification_request_id, modification.id);
    assert.equal((await api('GET', '/comments/' + retainedComment.id)).status, 'open');
    const queued = await teamSend(cancellationTeam, { text: '归档前尚未领取的工作。', target_agent_ids: ['reviewer'], request_key: 'qa-team-archive-queued' });
    const queuedId = queued.runs.at(-1).id;
    const archived = await api('DELETE', '/teams/' + cancellationTeam.id, { base_version: cancellationTeam.version });
    assert(archived.archived_at);
    assert(archived.cancelled_run_ids.includes(queuedId));
    assert.equal((await api('POST', '/teams/' + archived.id + '/rooms', {})).id, archived.primary_room_id);
    await teamSend(archived, { text: '已归档团队不能收到新任务。', request_key: 'qa-archived-team-message' }, 409);
    await api('PUT', '/teams/' + archived.id, { base_version: archived.version, name: '归档后不应可编辑' }, 409);
    assert.equal((await api('GET', '/artifacts/' + retainedArtifact.id)).version, 2);
    const history = await api('GET', '/teams/' + archived.id + '/history');
    assert(history.history.some(item => item.removed_member_ids.includes('researcher') && item.cancelled_run_ids.length === 3));
    assert(history.history.at(-1).archived_at);
    fs.writeFileSync(path.join(runDir, 'team-history-evidence.json'), JSON.stringify({ synthetic: true, history, artifact: revised }, null, 2));
  });

  await check('An oversized shared source is explicitly omitted without dropping the essential team boundary', async () => {
    const boundedTeam = await api('POST', '/teams', { name: '长资料团队权限验收', member_ids: ['researcher', 'writer'], routing_mode: 'auto', request_key: 'qa-team-bounded-context' }, 201);
    const largeSource = await api('POST', '/rooms/' + boundedTeam.primary_room_id + '/sources', { title: '超出单次上下文的资料', kind: 'text', content: 'BOUNDED_TEAM_SOURCE_'.repeat(22000) }, 201);
    await api('POST', '/rooms/' + boundedTeam.primary_room_id + '/messages', { text: '资料不能装入时，仍须保留完整团队授权成员。', request_key: 'qa-team-bounded-context-message' });
    const lease = await claim('qa-team-bounded-context-worker', boundedTeam.primary_room_id, 'coordinator');
    sameMembers(lease.context.team.member_ids, ['researcher', 'writer']);
    sameMembers(lease.context.team.allowed_agent_ids, ['researcher', 'writer']);
    sameMembers(lease.context.agents.map(item => item.id), ['researcher', 'writer']);
    assert.equal(lease.context.team.run_version, boundedTeam.version);
    assert(lease.context.omissions.some(item => item.kind === 'sources' && item.ids.includes(largeSource.id)));
    assert(lease.context.references.some(item => item.kind === 'source' && item.id === largeSource.id));
    assert(!lease.context.sources.some(item => item.id === largeSource.id));
    assert(Buffer.byteLength(JSON.stringify(lease.context), 'utf8') <= 256 * 1024);
    await complete(lease, 'qa-team-bounded-context-worker', { message: '合成验收确认资料省略是显式的，团队权限没有被省略。' });
    fs.writeFileSync(path.join(runDir, 'team-bounded-context-evidence.json'), JSON.stringify({ synthetic: true, source_id: largeSource.id, source_bytes: largeSource.content.length, context: lease.context }, null, 2));
  });

  await check('Read-only SQLite confirms one primary team room, frozen run rosters and preserved revision history', async () => {
    const script = `import json, sqlite3, sys
from pathlib import Path
database = Path(sys.argv[1]).resolve()
conn = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
conn.row_factory = sqlite3.Row
teams = []
for team_id in sys.argv[2:4]:
    row = dict(conn.execute('SELECT id,version,primary_room_id,member_ids_json,archived_at FROM hub_teams WHERE id=?', (team_id,)).fetchone())
    row['rooms'] = [dict(item) for item in conn.execute('SELECT id,team_id FROM hub_rooms WHERE team_id=?', (team_id,))]
    row['revisions'] = [dict(item) for item in conn.execute('SELECT version,snapshot_json,removed_json,cancelled_json FROM hub_team_revisions WHERE team_id=? ORDER BY version', (team_id,))]
    row['runs'] = [dict(item) for item in conn.execute('SELECT id,team_version,team_members_json,cancelled,cancel_reason FROM hub_runs WHERE team_id=? ORDER BY created_at,rowid', (team_id,))]
    teams.append(row)
result = {'integrity': conn.execute('PRAGMA integrity_check').fetchone()[0], 'teams': teams, 'deduplicated_requests': [dict(row) for row in conn.execute("SELECT request_key,count(*) AS records FROM hub_team_requests WHERE request_key IN ('qa-team-boundary-create','qa-team-first-direct') GROUP BY request_key")], 'artifact_versions': [dict(row) for row in conn.execute('SELECT version,modification_request_id FROM hub_artifact_versions WHERE artifact_id=? ORDER BY version', (sys.argv[4],))]}
conn.close()
print(json.dumps(result, ensure_ascii=False))
`;
    const evidence = await new Promise((resolve, reject) => {
      const child = spawn(python, ['-X', 'utf8', '-c', script, path.join(dataDir, 'studio.sqlite3'), team.id, cancellationTeam.id, retainedArtifact.id], { cwd: repo, windowsHide: true });
      let stdout = '', stderr = '';
      child.stdout.on('data', data => { stdout += data.toString(); });
      child.stderr.on('data', data => { stderr += data.toString(); });
      child.on('error', reject);
      child.on('exit', code => { if (code) reject(new Error('Read-only team database check failed: ' + stderr)); else { try { resolve(JSON.parse(stdout)); } catch (error) { reject(error); } } });
    });
    assert.equal(evidence.integrity, 'ok');
    for (const current of evidence.teams) {
      assert.deepEqual(current.rooms.map(item => item.id), [current.primary_room_id]);
      assert.deepEqual(current.revisions.map(item => item.version), Array.from({ length: current.version }, (_, index) => index + 1));
      for (const run of current.runs) {
        const revision = current.revisions.find(item => item.version === run.team_version);
        sameMembers(JSON.parse(run.team_members_json), JSON.parse(revision.snapshot_json).member_ids);
      }
    }
    assert(evidence.teams[1].runs.filter(item => item.cancelled).every(item => item.cancel_reason));
    assert.equal(evidence.deduplicated_requests.length, 2);
    assert(evidence.deduplicated_requests.every(item => item.records === 1));
    assert.deepEqual(evidence.artifact_versions.map(item => item.version), [1, 2]);
    assert(evidence.artifact_versions[1].modification_request_id);
    fs.writeFileSync(path.join(runDir, 'teams-db-evidence.json'), JSON.stringify(evidence, null, 2));
  });
}

async function teamBrowserJourney() {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(base);
  const agents = (await api('GET', '/agents')).agents;
  const nameOf = id => agents.find(item => item.id === id).name;
  const teamName = '产品协作常驻群';
  const sourceMarker = 'TEAM_BROWSER_SHARED_0901';
  let team, roomId, artifactId, activeLease;
  const editor = () => page.locator('#group-editor-dialog');
  async function selectTargets(ids) {
    await page.getByRole('button', { name: '点名 Agent', exact: true }).click();
    const dialog = page.locator('#group-mention-dialog');
    await dialog.getByRole('button', { name: '清除点名', exact: true }).click();
    for (const id of ids) await dialog.getByLabel('点名：' + nameOf(id), { exact: true }).check();
    await dialog.getByRole('button', { name: '确认点名', exact: true }).click();
  }
  async function sendText(text) {
    await page.getByLabel('需求输入', { exact: true }).fill(text);
    const [response] = await Promise.all([
      page.waitForResponse(item => item.url().endsWith('/api/hub/rooms/' + roomId + '/messages') && item.request().method() === 'POST'),
      page.getByRole('button', { name: '发送', exact: true }).click(),
    ]);
    assert.equal(response.status(), 200);
    const room = await response.json();
    await page.waitForFunction(value => window.workspaceUI?.getCurrentRoom()?.messages?.some(item => item.text === value), text);
    return room;
  }
  async function openEditor() {
    await page.getByRole('button', { name: '团队信息', exact: true }).click();
    await page.locator('#group-info-dialog').getByRole('button', { name: '编辑团队成员', exact: true }).click();
    await editor().waitFor({ state: 'visible' });
  }

  await check('The browser creates a persistent member group separately from the workflow editor', async () => {
    assert.equal(await page.getByRole('button', { name: '工作流', exact: true }).count(), 1);
    await page.getByRole('button', { name: '协作团队', exact: true }).first().click();
    await page.getByRole('button', { name: '创建团队', exact: true }).first().click();
    await editor().getByLabel('团队名称', { exact: true }).fill(teamName);
    await editor().getByLabel('团队说明', { exact: true }).fill('共享资料、持续对话、明确点名，所有输出均为合成验收结果。');
    for (const id of ['researcher', 'writer']) await editor().getByLabel('选择 Agent：' + nameOf(id), { exact: true }).check();
    assert.equal(await editor().getByLabel('选择 Agent：' + nameOf('coordinator'), { exact: true }).count(), 0);
    await editor().getByRole('switch', { name: '自动分工', exact: true }).uncheck();
    await page.setViewportSize({ width: 390, height: 844 });
    await assertNoOverflow(page);
    await assertControlNotClipped(editor().getByRole('button', { name: '创建并打开群聊', exact: true }));
    await page.screenshot({ path: path.join(runDir, 'team-mobile-create-members.png'), fullPage: true, animations: 'disabled' });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await editor().getByRole('button', { name: '创建并打开群聊', exact: true }).click();
    team = await until(async () => (await api('GET', '/teams')).teams.find(item => item.name === teamName));
    roomId = team.primary_room_id;
    await page.waitForFunction(id => window.workspaceUI?.getCurrentRoom()?.id === id, roomId);
    assert.equal(team.routing_mode, 'mention');
    assert.equal((await api('GET', '/rooms/' + roomId)).runs.length, 0);
    assert.equal((await api('GET', '/rooms/' + roomId)).messages.length, 0);
    const toggle = page.locator('#workspace-toggle');
    if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
    await page.getByRole('tab', { name: '资料', exact: true }).click();
    await page.getByRole('tabpanel', { name: '资料', exact: true }).getByRole('button', { name: '添加资料', exact: true }).click();
    const sourceDialog = page.getByRole('dialog').filter({ has: page.getByLabel('资料标题', { exact: true }) });
    await sourceDialog.getByLabel('资料标题', { exact: true }).fill('常驻群共享资料');
    await sourceDialog.getByLabel('资料类型', { exact: true }).selectOption('text');
    await sourceDialog.getByLabel('资料内容', { exact: true }).fill(sourceMarker + '：后续消息继续使用此资料。');
    await sourceDialog.getByRole('button', { name: '保存资料', exact: true }).click();
    await until(async () => (await api('GET', '/rooms/' + roomId + '/sources')).sources.length === 1);
    await page.getByRole('button', { name: '关闭工作区', exact: true }).click();
    const room = await sendText('@' + nameOf('writer') + ' 这是未选择目标的群消息，不应启动任务。');
    assert.equal(room.runs.length, 0);
    assert.equal(room.messages.at(-1).routing.mode, 'note');
    assert.equal(await page.locator('#run-summary').innerText(), '');
    await page.getByRole('button', { name: '点名 Agent', exact: true }).click();
    const mention = page.locator('#group-mention-dialog');
    assert.equal(await mention.getByLabel('点名：' + nameOf('developer'), { exact: true }).count(), 0);
    const writerChoice = mention.getByLabel('点名：' + nameOf('writer'), { exact: true });
    await writerChoice.focus();
    await page.keyboard.press('Space');
    assert(await writerChoice.isChecked());
    await mention.getByRole('button', { name: '确认点名', exact: true }).click();
    const direct = await sendText('请整理常驻群资料并生成第一版成果。');
    assert.equal(direct.runs.at(-1).tasks[0].agent_id, 'writer');
    const commandPath = path.join(runDir, 'team-browser-command.cjs');
    fs.writeFileSync(commandPath, `'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const envelope = JSON.parse(fs.readFileSync(0, 'utf8'));
fs.writeFileSync(${JSON.stringify(path.join(runDir, 'team-browser-worker-envelope.json'))}, JSON.stringify(envelope, null, 2));
assert.equal(envelope.context.team.id, ${JSON.stringify(team.id)});
assert.deepEqual([...envelope.context.team.allowed_agent_ids].sort(), ['researcher','writer']);
assert(envelope.context.sources.some(item => item.content.includes(${JSON.stringify(sourceMarker)})));
assert(envelope.context.messages.some(item => item.text === ${JSON.stringify('@' + nameOf('writer') + ' 这是未选择目标的群消息，不应启动任务。')}));
process.stdout.write(JSON.stringify({message:'常驻群第一轮成果已保存。',artifacts:[{title:'常驻群可修订成果',kind:'markdown',content:'# 常驻群成果\\n\\nTEAM_BROWSER_ARTIFACT_0901'}]}));
`);
    const worker = await runSyntheticWorker({ worker_id: 'qa-team-browser-host', name: '团队UI合成命令执行器', room_id: roomId, agent_ids: ['writer'], capabilities: ['synthetic-fixture'], command: [process.execPath, commandPath], cwd: runDir, timeout_seconds: 30 }, { label: 'team-browser-worker' });
    assert.equal(worker.exitCode, 0, worker.log);
    assert(worker.events.some(item => item.event === 'completed'), worker.log);
    const completed = await api('GET', '/rooms/' + roomId);
    artifactId = completed.artifacts[0].id;
    await page.locator('#messages').getByText('常驻群第一轮成果已保存。', { exact: true }).waitFor();
  });

  await check('A named follow-up keeps the same team room, sources and artifact lineage while polling preserves the composer', async () => {
    await selectTargets(['writer']);
    await sendText('沿用同一群的资料和第一版成果，继续修订。');
    const lease = await claim('qa-team-followup', roomId, 'writer');
    assert.equal(lease.context.team.id, team.id);
    assert(lease.context.sources.some(item => item.content.includes(sourceMarker)));
    assert(lease.context.artifact_contents.some(item => item.id === artifactId || item.artifact_id === artifactId));
    assert(JSON.stringify(lease.context.artifact_contents).includes('TEAM_BROWSER_ARTIFACT_0901'));
    assert(lease.context.messages.some(item => item.text === '常驻群第一轮成果已保存。'));
    const composer = page.getByLabel('需求输入', { exact: true });
    const draft = '轮询期间仍在写的群聊草稿。';
    await composer.fill(draft);
    await composer.evaluate(element => { element.focus(); element.setSelectionRange(3, 8); });
    await api('POST', '/tasks/' + lease.task.id + '/heartbeat', { worker_id: 'qa-team-followup', lease_token: lease.lease_token, progress: '正在验证同一群的第二轮上下文。' });
    await page.waitForFunction(id => window.workspaceUI.getCurrentRoom()?.runs?.flatMap(item => item.tasks).find(item => item.id === id)?.progress === '正在验证同一群的第二轮上下文。', lease.task.id);
    assert.equal(await composer.inputValue(), draft);
    assert.deepEqual(await composer.evaluate(element => ({ focused: document.activeElement === element, start: element.selectionStart, end: element.selectionEnd })), { focused: true, start: 3, end: 8 });
    await complete(lease, 'qa-team-followup', { message: '常驻群第二轮修订已保存。', artifacts: [{ id: artifactId, base_version: 1, title: '常驻群可修订成果', kind: 'markdown', content: '# 常驻群成果\n\n第二版保留同一成果ID。' }] });
    await page.locator('#messages').getByText('常驻群第二轮修订已保存。', { exact: true }).waitFor();
    assert.equal(await composer.inputValue(), draft);
    await composer.fill('');
    await page.reload();
    await page.waitForFunction(id => window.workspaceUI?.getCurrentRoom()?.id === id, roomId);
    await page.getByRole('button', { name: '协作团队', exact: true }).first().click();
    await page.getByRole('button', { name: '打开群聊：' + teamName, exact: true }).click();
    await page.waitForFunction(id => window.workspaceUI.getCurrentRoom()?.id === id, roomId);
    const restored = await api('GET', '/rooms/' + roomId);
    assert.equal(restored.runs.length, 2);
    assert.equal(restored.sources.length, 1);
    assert.equal(restored.artifacts[0].id, artifactId);
    assert.equal(restored.artifacts[0].version, 2);
    await assertNoOverflow(page);
    await page.screenshot({ path: path.join(runDir, 'team-persistent-conversation.png'), fullPage: true, animations: 'disabled' });
  });

  await check('Retrying a genuinely lost message response after a team-version change does not create a duplicate turn', async () => {
    const retryTeam = await api('POST', '/teams', { name: '消息确认丢失验收群', member_ids: ['writer', 'researcher'], routing_mode: 'mention', request_key: 'qa-browser-lost-message-team' }, 201);
    await page.goto(base + '/#room/' + retryTeam.primary_room_id);
    await page.waitForFunction(id => window.workspaceUI?.getCurrentRoom()?.id === id, retryTeam.primary_room_id);
    await selectTargets(['writer']);
    const text = '这条消息已经成功保存，确认丢失后重发不得产生第二轮。';
    await page.getByLabel('需求输入', { exact: true }).fill(text);
    const routeUrl = base + '/api/hub/rooms/' + retryTeam.primary_room_id + '/messages';
    let firstPayload, dropped = false, markCommitted;
    const committed = new Promise(resolve => { markCommitted = resolve; });
    await page.route(routeUrl, async route => {
      if (route.request().method() === 'POST' && !dropped) {
        firstPayload = route.request().postDataJSON();
        const reply = await route.fetch();
        assert.equal(reply.status(), 200);
        dropped = true;
        // A real backend commit occurred; discard the browser response only.
        await route.abort('connectionreset');
        markCommitted();
      } else await route.continue();
    });
    try {
      await page.getByRole('button', { name: '发送', exact: true }).click();
      await Promise.race([committed, new Promise((_, reject) => setTimeout(() => reject(new Error('Message did not reach the real acknowledgement-loss boundary.')), 12000))]);
      await until(async () => !await page.getByRole('button', { name: '发送', exact: true }).isDisabled());
      assert.equal(await page.getByLabel('需求输入', { exact: true }).inputValue(), text);
      const nextTeam = await api('PUT', '/teams/' + retryTeam.id, { base_version: retryTeam.version, description: '首次提交之后另一位成员更新了团队说明。' });
      await page.waitForFunction(data => window.workspaceUI?.getCurrentRoom()?.team?.id === data.id && window.workspaceUI.getCurrentRoom().team.version === data.version, nextTeam);
      const [response] = await Promise.all([
        page.waitForResponse(item => item.url() === routeUrl && item.request().method() === 'POST'),
        page.getByRole('button', { name: '发送', exact: true }).click(),
      ]);
      assert.equal(response.status(), 200);
      assert.deepEqual(response.request().postDataJSON(), firstPayload, 'A retry silently changed its key, baseline or explicit recipients.');
      const saved = await api('GET', '/rooms/' + retryTeam.primary_room_id);
      assert.equal(saved.messages.filter(item => item.role === 'user' && item.text === text).length, 1);
      assert.equal(saved.runs.length, 1);
      assert.equal(saved.runs[0].tasks.length, 1);
      const lease = await claim('qa-browser-lost-message-writer', retryTeam.primary_room_id, 'writer');
      await complete(lease, 'qa-browser-lost-message-writer', { message: '只处理了一条实际任务。' });
      fs.writeFileSync(path.join(runDir, 'team-message-ack-fault.json'), JSON.stringify({ synthetic: true, dropped_after_real_commit: dropped, original_team_version: retryTeam.version, changed_team_version: nextTeam.version, request_key_preserved: true, user_messages: 1, runs: 1 }, null, 2));
    } finally { await page.unroute(routeUrl); }
    await page.goto(base + '/#room/' + roomId);
    await page.waitForFunction(id => window.workspaceUI?.getCurrentRoom()?.id === id, roomId);
  });

  await check('Member edit conflicts preserve the exact draft and automatic routing changes do not create unsolicited work', async () => {
    await openEditor();
    await editor().getByLabel('选择 Agent：' + nameOf('designer'), { exact: true }).check();
    const description = editor().getByLabel('团队说明', { exact: true });
    await description.fill('冲突后也必须保留的团队说明。');
    await description.evaluate(element => { element.focus(); element.setSelectionRange(2, 8); });
    team = await api('GET', '/teams/' + team.id);
    const current = await api('PUT', '/teams/' + team.id, { base_version: team.version, member_ids: ['researcher', 'writer', 'reviewer'], description: '另一位成员先保存。' });
    await page.waitForFunction(data => window.workspaceUI.getTeams().find(item => item.id === data.id)?.version === data.version, current);
    assert.equal(await description.inputValue(), '冲突后也必须保留的团队说明。');
    assert.deepEqual(await description.evaluate(element => ({ focused: document.activeElement === element, start: element.selectionStart, end: element.selectionEnd })), { focused: true, start: 2, end: 8 });
    assert(await editor().getByLabel('选择 Agent：' + nameOf('designer'), { exact: true }).isChecked());
    assert(!await editor().getByLabel('选择 Agent：' + nameOf('reviewer'), { exact: true }).isChecked());
    const [conflict] = await Promise.all([
      page.waitForResponse(response => response.url().endsWith('/api/hub/teams/' + team.id) && response.request().method() === 'PUT'),
      editor().getByRole('button', { name: '保存成员', exact: true }).click(),
    ]);
    assert.equal(conflict.status(), 409);
    assert.equal(await description.inputValue(), '冲突后也必须保留的团队说明。');
    assert(await editor().getByRole('button', { name: '保存成员', exact: true }).isDisabled());
    await editor().getByRole('button', { name: '保留选择，读取最新成员', exact: true }).click();
    await page.locator('#group-conflict').getByText('已读取最新成员。你的选择与文字保持不变，核对后请再次保存。', { exact: true }).waitFor();
    assert.equal((await api('GET', '/teams/' + team.id)).version, current.version, 'Reading the new baseline implicitly saved the draft.');
    assert(await editor().getByLabel('选择 Agent：' + nameOf('designer'), { exact: true }).isChecked());
    assert(!await editor().getByLabel('选择 Agent：' + nameOf('reviewer'), { exact: true }).isChecked());
    await page.screenshot({ path: path.join(runDir, 'team-member-conflict-preserves-draft.png'), fullPage: true, animations: 'disabled' });
    await page.setViewportSize({ width: 390, height: 844 });
    await assertNoOverflow(page);
    await assertControlNotClipped(editor().getByRole('button', { name: '保存成员', exact: true }));
    await page.screenshot({ path: path.join(runDir, 'team-mobile-conflict-preserves-draft.png'), fullPage: true, animations: 'disabled' });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await editor().getByRole('button', { name: '保存成员', exact: true }).click();
    team = await until(async () => { const value = await api('GET', '/teams/' + team.id); return value.version > current.version && value; });
    assert.deepEqual([...team.member_ids].sort(), ['designer', 'researcher', 'writer']);
    await editor().waitFor({ state: 'hidden' });
    const info = page.locator('#group-info-dialog');
    await info.waitFor({ state: 'visible' });
    await info.getByRole('switch', { name: '自动分工', exact: true }).check();
    team = await until(async () => { const value = await api('GET', '/teams/' + team.id); return value.routing_mode === 'auto' && value; });
    assert.equal((await api('GET', '/rooms/' + roomId)).runs.length, 2);
    await page.keyboard.press('Escape');
    const routed = await sendText('请由当前团队自动分工，继续这份共同成果。');
    assert.equal(routed.runs.at(-1).tasks[0].agent_id, 'coordinator');
    const coordinator = await claim('qa-team-ui-coordinator', roomId, 'coordinator');
    assert.deepEqual([...coordinator.context.team.allowed_agent_ids].sort(), ['designer', 'researcher', 'writer']);
    await complete(coordinator, 'qa-team-ui-coordinator', { message: '已创建当前团队内的实际分工任务。', plan: [{ key: 'write', agent_id: 'writer', title: '团队内的下一步写作', instructions: '等待成员变更边界的合成验收。', depends_on: [] }] });
    activeLease = await claim('qa-team-ui-active-writer', roomId, 'writer');
    assert(activeLease.task);
  });

  await check('Removed pending targets cannot be silently rerouted and group controls remain usable on mobile and keyboard', async () => {
    await selectTargets(['designer']);
    const composer = page.getByLabel('需求输入', { exact: true });
    const draft = '这条尚未发送的消息原本只给设计师。';
    await composer.fill(draft);
    await composer.evaluate(element => { element.focus(); element.setSelectionRange(2, 7); });
    const removed = await api('PUT', '/teams/' + team.id, { base_version: team.version, member_ids: ['researcher', 'writer'] });
    await page.waitForFunction(data => window.workspaceUI.getCurrentRoom()?.team?.version === data.version, removed);
    assert.equal(await composer.inputValue(), draft);
    assert(await page.getByRole('button', { name: '发送', exact: true }).isDisabled());
    await page.locator('#run-summary').getByText('成员已变更，本轮已停止', { exact: true }).waitFor({ state: 'visible' });
    assert(removed.cancelled_run_ids.includes(activeLease.task.run_id));
    await complete(activeLease, 'qa-team-ui-active-writer', { message: '成员变更后旧工作不应回传。' }, 409);
    await page.screenshot({ path: path.join(runDir, 'team-removed-target-keeps-draft.png'), fullPage: true, animations: 'disabled' });
    await page.setViewportSize({ width: 390, height: 844 });
    await assertNoOverflow(page);
    await page.screenshot({ path: path.join(runDir, 'team-mobile-stale-target.png'), fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: '点名 Agent', exact: true }).click();
    const mention = page.locator('#group-mention-dialog');
    assert.equal(await mention.getByLabel('点名：' + nameOf('designer'), { exact: true }).count(), 0);
    await assertControlNotClipped(mention.getByRole('button', { name: '确认点名', exact: true }));
    await page.keyboard.press('Escape');
    assert(await page.getByRole('button', { name: '点名 Agent', exact: true }).evaluate(element => element === document.activeElement));
    await page.getByRole('button', { name: '清除点名', exact: true }).click();
    assert(!await page.getByRole('button', { name: '发送', exact: true }).isDisabled());
    assert.equal(await composer.inputValue(), draft);
    await page.getByRole('button', { name: '团队信息', exact: true }).click();
    const info = page.locator('#group-info-dialog');
    await info.getByRole('tab', { name: '成员', exact: true }).focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await info.getByRole('tab', { name: '变更记录', exact: true }).getAttribute('aria-selected'), 'true');
    await info.getByText('已停止 1 轮未完成任务', { exact: true }).waitFor();
    await assertNoOverflow(page);
    const closeBounds = await info.getByRole('button', { name: '关闭', exact: true }).boundingBox();
    assert(closeBounds && closeBounds.y >= 0 && closeBounds.y + closeBounds.height <= 844, 'Mobile history tab focus moved the dialog close control outside the viewport.');
    await page.screenshot({ path: path.join(runDir, 'team-mobile-history.png'), fullPage: true, animations: 'disabled' });
    await page.keyboard.press('Escape');
    assert(await page.getByRole('button', { name: '团队信息', exact: true }).evaluate(element => element === document.activeElement));
    await page.setViewportSize({ width: 1440, height: 1000 });
    await mobileToastJourney('team-chat', [composer, page.getByRole('button', { name: '发送', exact: true })]);
    await composer.fill('');
    assert.equal((await api('GET', '/artifacts/' + artifactId)).version, 2);
    fs.writeFileSync(path.join(runDir, 'team-browser-evidence.json'), JSON.stringify({ synthetic: true, team: await api('GET', '/teams/' + team.id), room: await api('GET', '/rooms/' + roomId), prior_artifact: await api('GET', '/artifacts/' + artifactId) }, null, 2));
    assert.deepEqual(pageErrors, []);
  });
}

async function teamCollaborationJourney() {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const title = '团队共同交付方案';
  const content = '# 团队共同交付方案\n\n交付内容由当前团队讨论并确认。\n\n## 协作方式\n\n保留共享资料、评论和每一次成果修订。';
  const body = '请明确这份成果的适用人群，并保留原来的协作方式。';
  const instructions = '成员变化后也必须保留这份修改说明，等待我重新确认负责人员。';
  const geometry = [];
  let team, artifactId, comment, request, coordinatorRoom, coordinatorArtifact;
  const dialog = () => page.locator('#modification-dialog');
  const agentSelect = () => dialog().getByLabel('负责修改的 Agent', { exact: true });
  const thread = () => page.locator('[data-thread-id="' + comment.id + '"]');
  async function openComments(version = 1) {
    await openArtifact(title);
    await page.getByLabel('成果版本', { exact: true }).selectOption(String(version));
    const toggle = page.getByRole('button', { name: '评论', exact: true });
    if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
    await page.locator('#collaboration-comments-tab').click();
    await page.getByLabel('评论内容', { exact: true }).waitFor();
  }
  async function enabledAgentValues() {
    return agentSelect().locator('option').evaluateAll(options => options.filter(item => !item.disabled && item.value).map(item => item.value).sort());
  }
  async function recordStudio(name) {
    await assertNoOverflow(page);
    const measured = await page.evaluate(label => {
      const ids = ['detail-panel', 'artifact-title', 'artifact-body', 'artifact-collaboration', 'comment-body', 'post-comment'];
      const documentText = document.querySelector('#artifact-body .prose') || document.getElementById('artifact-body');
      const firstLine = (documentText.firstElementChild || documentText).getBoundingClientRect();
      return { label, viewport: { width: innerWidth, height: innerHeight }, document_text: { font_size: getComputedStyle(documentText).fontSize, first_line_top: firstLine.top, first_line_bottom: firstLine.bottom }, elements: Object.fromEntries(ids.map(id => {
        const node = document.getElementById(id), rect = node.getBoundingClientRect(), style = getComputedStyle(node);
        return [id, { x: rect.x, y: rect.y, width: rect.width, height: rect.height, font_size: style.fontSize, line_height: style.lineHeight, color: style.color, display: style.display }];
      })) };
    }, name);
    geometry.push(measured);
    await page.screenshot({ path: path.join(runDir, name + '.png'), fullPage: true, animations: 'disabled' });
    return measured;
  }

  await check('Team artifact revisions offer only current members and preserve the draft when a selected member is removed', async () => {
    team = await api('POST', '/teams', { name: '团队成果协作验收', member_ids: ['writer', 'researcher'], routing_mode: 'mention', request_key: 'qa-team-collaboration' }, 201);
    await api('POST', '/rooms/' + team.primary_room_id + '/messages', { text: '生成团队共同交付方案，随后由成员批注修订。', target_agent_ids: ['writer'], request_key: 'qa-team-collaboration-initial' });
    const initial = await claim('qa-team-collaboration-initial', team.primary_room_id, 'writer');
    const done = await complete(initial, 'qa-team-collaboration-initial', { message: '团队共同方案已经保存，等待成员反馈。', artifacts: [{ title, kind: 'markdown', content }] });
    artifactId = done.artifacts[0].id;
    await page.goto(base + '/#room/' + team.primary_room_id);
    await openArtifact(title);
    const desktopReading = await recordStudio('team-studio-desktop-reading');
    assert(desktopReading.elements['artifact-body'].width >= 600, 'Closed comments left the document in an unused narrow half-column.');
    assert(parseFloat(desktopReading.document_text.font_size) >= 14);
    assert(desktopReading.document_text.first_line_top < desktopReading.viewport.height / 2);
    await page.setViewportSize({ width: 390, height: 844 });
    const mobileReading = await recordStudio('team-studio-mobile-reading');
    assert(mobileReading.document_text.first_line_top < mobileReading.viewport.height / 2, 'Mobile document begins below half the viewport because of controls above it.');
    await page.setViewportSize({ width: 1440, height: 1000 });
    await openComments();
    await page.getByLabel('评论内容', { exact: true }).fill(body);
    const commentFont = await page.getByLabel('评论内容', { exact: true }).evaluate(element => parseFloat(getComputedStyle(element).fontSize));
    assert(commentFont >= 14, 'Comment input is below the 14-pixel reading floor.');
    await assertControlNotClipped(page.getByRole('button', { name: '发表评论', exact: true }));
    await recordStudio('team-studio-desktop-comment');
    await page.getByRole('button', { name: '发表评论', exact: true }).click();
    comment = await until(async () => (await api('GET', '/artifacts/' + artifactId + '/comments?version=1')).comments.find(item => item.body === body));
    await thread().getByRole('checkbox').check();
    await page.setViewportSize({ width: 390, height: 600 });
    await assertControlNotClipped(page.getByRole('button', { name: '交给 Agent 修改', exact: true }));
    await assertControlNotClipped(page.getByRole('button', { name: '发表评论', exact: true }));
    await recordStudio('team-studio-compact-comment-controls');
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole('button', { name: '交给 Agent 修改', exact: true }).click();
    assert.deepEqual(await enabledAgentValues(), ['researcher', 'writer']);
    assert.equal(await agentSelect().locator('option[value="coordinator"]').count(), 0, 'A writer-origin artifact offered the internal coordinator.');
    await agentSelect().selectOption('researcher');
    const draft = dialog().getByLabel('修改说明', { exact: true });
    await draft.fill(instructions);
    await draft.evaluate(element => { element.focus(); element.setSelectionRange(3, 10); });
    await agentSelect().evaluate(element => {
      window.__qaModificationOptions = { changes: 0, observer: new MutationObserver(records => { window.__qaModificationOptions.changes += records.filter(record => record.type === 'childList').length; }) };
      window.__qaModificationOptions.observer.observe(element, { childList: true, subtree: true });
    });
    team = await api('PUT', '/teams/' + team.id, { base_version: team.version, description: '成员未改变，只更新团队说明。' });
    await page.waitForFunction(data => window.workspaceUI?.getCurrentRoom()?.team?.version === data.version, team);
    const optionChanges = await page.evaluate(() => { const probe = window.__qaModificationOptions; probe.observer.disconnect(); return probe.changes; });
    assert.equal(optionChanges, 0, 'A room update with unchanged members rebuilt the active recipient options.');
    assert.equal(await agentSelect().inputValue(), 'researcher');
    team = await api('PUT', '/teams/' + team.id, { base_version: team.version, member_ids: ['writer'] });
    await page.waitForFunction(data => window.workspaceUI?.getCurrentRoom()?.team?.version === data.version, team);
    assert.equal(await draft.inputValue(), instructions);
    assert.deepEqual(await draft.evaluate(element => ({ focused: document.activeElement === element, start: element.selectionStart, end: element.selectionEnd })), { focused: true, start: 3, end: 10 });
    assert.deepEqual(await enabledAgentValues(), ['writer']);
    assert(await dialog().getByRole('button', { name: '提交修改请求', exact: true }).isDisabled(), 'Removing the selected recipient must require an explicit new selection.');
    assert.equal((await api('GET', '/artifacts/' + artifactId + '/modifications')).modifications.length, 0);
    await recordStudio('team-modification-removed-recipient-desktop');
    await page.setViewportSize({ width: 390, height: 844 });
    await assertControlNotClipped(dialog().getByRole('button', { name: '提交修改请求', exact: true }));
    await recordStudio('team-modification-removed-recipient-mobile');
    await page.setViewportSize({ width: 1440, height: 1000 });
  });

  await check('Explicitly choosing a remaining member produces a linked revision that survives refresh and leaves resolution to the user', async () => {
    await agentSelect().selectOption('writer');
    const [response] = await Promise.all([
      page.waitForResponse(item => item.url().endsWith('/api/hub/artifacts/' + artifactId + '/modifications') && item.request().method() === 'POST'),
      dialog().getByRole('button', { name: '提交修改请求', exact: true }).click(),
    ]);
    assert.equal(response.status(), 201);
    request = await response.json();
    assert.equal(request.agent_id, 'writer');
    assert.equal(request.snapshot.instructions, instructions);
    const worker = 'qa-team-collaboration-revision';
    const lease = await claim(worker, team.primary_room_id, 'writer');
    assert.equal(lease.task.id, request.task_id);
    assert.equal(lease.context.modification.artifact_id, artifactId);
    assert.deepEqual(lease.context.team.allowed_agent_ids, ['writer']);
    await page.locator('#collaboration-comments-tab').click();
    const commentDraft = page.getByLabel('评论内容', { exact: true });
    const unsent = 'Agent 执行时仍然保留这条未发表的意见。';
    await commentDraft.fill(unsent);
    await commentDraft.evaluate(element => { element.focus(); element.setSelectionRange(2, 7); });
    await api('POST', '/tasks/' + lease.task.id + '/heartbeat', { worker_id: worker, lease_token: lease.lease_token, progress: '合成验收：正在处理团队内的修改请求。' });
    await page.waitForFunction(id => window.workspaceUI?.getCurrentRoom()?.runs?.flatMap(item => item.tasks).find(item => item.id === id)?.progress === '合成验收：正在处理团队内的修改请求。', lease.task.id);
    assert.equal(await commentDraft.inputValue(), unsent);
    assert.deepEqual(await commentDraft.evaluate(element => ({ focused: document.activeElement === element, start: element.selectionStart, end: element.selectionEnd })), { focused: true, start: 2, end: 7 });
    await page.setViewportSize({ width: 390, height: 844 });
    await assertControlNotClipped(commentDraft);
    await assertControlNotClipped(page.getByRole('button', { name: '发表评论', exact: true }));
    await recordStudio('team-studio-mobile-comment-draft');
    await mobileToastJourney('artifact-comments', [commentDraft, page.getByRole('button', { name: '发表评论', exact: true })]);
    const revised = content + '\n\n适用人群：采购负责人和渠道运营。';
    await complete(lease, worker, { message: '已依据团队讨论保存第二版。', artifacts: [{ id: artifactId, base_version: 1, title, kind: 'markdown', content: revised }] });
    await until(async () => (await api('GET', '/modifications/' + request.id)).status === 'completed');
    assert.equal((await api('GET', '/artifacts/' + artifactId + '?version=1')).content, content);
    assert.equal((await api('GET', '/artifacts/' + artifactId + '?version=2')).modification_request_id, request.id);
    assert.equal((await api('GET', '/comments/' + comment.id)).status, 'open');
    await page.setViewportSize({ width: 1440, height: 1000 });
    await commentDraft.fill('');
    await page.reload();
    await openComments(1);
    await thread().waitFor();
    await page.locator('#collaboration-requests-tab').click();
    await page.locator('[data-revision-request-id="' + request.id + '"]').getByRole('button', { name: '查看结果版本', exact: true }).click();
    await until(async () => (await page.getByLabel('成果版本', { exact: true }).inputValue()) === '2');
    await page.locator('#artifact-modification-origin').click();
    await page.locator('[data-modification-snapshot="' + request.id + '"]').waitFor();
    await page.keyboard.press('Escape');
    await openComments(1);
    await thread().getByRole('button', { name: '解决评论', exact: true }).click();
    await until(async () => (await api('GET', '/comments/' + comment.id)).status === 'resolved');
    await page.getByLabel('筛选评论', { exact: true }).selectOption('all');
    await recordStudio('team-studio-explicitly-resolved');
    collaborationAcceptance = { artifactId, commentId: comment.id, requestId: request.id, roomId: team.primary_room_id };
    fs.writeFileSync(path.join(runDir, 'team-collaboration-evidence.json'), JSON.stringify({ synthetic: true, team_id: team.id, room_id: team.primary_room_id, request: await api('GET', '/modifications/' + request.id), artifact: await api('GET', '/artifacts/' + artifactId), comment: await api('GET', '/comments/' + comment.id) }, null, 2));
  });

  await check('Only an artifact originally produced by the internal coordinator can send its revision back to that coordinator', async () => {
    const coordinatorTeam = await api('POST', '/teams', { name: '内部协调成果例外验收', member_ids: ['writer', 'researcher'], routing_mode: 'auto', request_key: 'qa-coordinator-artifact-team' }, 201);
    await api('POST', '/rooms/' + coordinatorTeam.primary_room_id + '/messages', { text: '由协调角色直接记录讨论结论。', request_key: 'qa-coordinator-artifact-first' });
    const first = await claim('qa-coordinator-artifact-first', coordinatorTeam.primary_room_id, 'coordinator');
    const completed = await complete(first, 'qa-coordinator-artifact-first', { message: '协调结论已保存。', artifacts: [{ title: '协调角色的共同结论', kind: 'markdown', content: '# 共同结论\n\n协调角色记录的第一版。' }] });
    const source = completed.artifacts[0];
    const root = await api('POST', '/artifacts/' + source.id + '/comments', { artifact_version: 1, body: '请原协调角色补充执行顺序。', request_key: 'qa-coordinator-artifact-comment' }, 201);
    await page.goto(base + '/#room/' + coordinatorTeam.primary_room_id);
    await openArtifact(source.title);
    const toggle = page.getByRole('button', { name: '评论', exact: true });
    if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
    await page.locator('#collaboration-comments-tab').click();
    await page.locator('[data-thread-id="' + root.id + '"]').getByRole('checkbox').check();
    await page.getByRole('button', { name: '交给 Agent 修改', exact: true }).click();
    assert.deepEqual(await enabledAgentValues(), ['coordinator', 'researcher', 'writer']);
    await agentSelect().selectOption('coordinator');
    await dialog().getByLabel('修改说明', { exact: true }).fill('仅修改本成果，不创建递归分工。');
    const [response] = await Promise.all([
      page.waitForResponse(item => item.url().endsWith('/api/hub/artifacts/' + source.id + '/modifications') && item.request().method() === 'POST'),
      dialog().getByRole('button', { name: '提交修改请求', exact: true }).click(),
    ]);
    assert.equal(response.status(), 201);
    const modification = await response.json();
    const lease = await claim('qa-coordinator-artifact-revision', coordinatorTeam.primary_room_id, 'coordinator');
    assert.equal(lease.task.id, modification.task_id);
    assert(!lease.context.team.member_ids.includes('coordinator'));
    await complete(lease, 'qa-coordinator-artifact-revision', { message: '原协调成果已完成修订。', artifacts: [{ id: source.id, base_version: 1, title: source.title, kind: 'markdown', content: '# 共同结论\n\n先确认资料，再由指定成员执行。' }] });
    const final = await api('GET', '/artifacts/' + source.id);
    assert.equal(final.version, 2);
    assert.equal(final.modification_request_id, modification.id);
    assert.deepEqual([...(await api('GET', '/teams/' + coordinatorTeam.id)).member_ids].sort(), ['researcher', 'writer']);
    coordinatorRoom = coordinatorTeam.primary_room_id;
    coordinatorArtifact = final;
    fs.writeFileSync(path.join(runDir, 'team-studio-geometry.json'), JSON.stringify(geometry, null, 2));
    assert.deepEqual(pageErrors, []);
  });

  await check('A delayed successful modification response cannot close or overwrite another artifact modification draft', async () => {
    const firstComment = await api('POST', '/artifacts/' + artifactId + '/comments', { artifact_version: 2, body: '第一份成果的延迟回包验收。', request_key: 'qa-team-late-revision-a' }, 201);
    const secondComment = await api('POST', '/artifacts/' + coordinatorArtifact.id + '/comments', { artifact_version: 2, body: '第二份成果的独立修改草稿。', request_key: 'qa-team-late-revision-b' }, 201);
    const secondCount = (await api('GET', '/artifacts/' + coordinatorArtifact.id + '/modifications')).modifications.length;
    await page.goto(base + '/#room/' + team.primary_room_id);
    await openComments(2);
    await page.locator('[data-thread-id="' + firstComment.id + '"]').getByRole('checkbox').check();
    await page.getByRole('button', { name: '交给 Agent 修改', exact: true }).click();
    await dialog().getByLabel('修改说明', { exact: true }).fill('第一份说明已经提交，不应覆盖另一份。');
    const url = base + '/api/hub/artifacts/' + artifactId + '/modifications';
    let markHeld, release, saved;
    const held = new Promise(resolve => { markHeld = resolve; });
    const gate = new Promise(resolve => { release = resolve; });
    await page.route(url, async route => {
      if (route.request().method() !== 'POST') return route.continue();
      const response = await route.fetch();
      assert.equal(response.status(), 201);
      saved = await response.json();
      markHeld();
      await gate;
      await route.fulfill({ response });
    });
    try {
      await dialog().getByRole('button', { name: '提交修改请求', exact: true }).click();
      await Promise.race([held, new Promise((_, reject) => setTimeout(() => reject(new Error('Modification response did not reach the delayed real-commit boundary.')), 12000))]);
      await page.keyboard.press('Escape');
      // Continue through real navigation controls while the first acknowledgement is held.
      await page.getByRole('button', { name: '关闭工作区', exact: true }).click();
      await page.getByRole('button', { name: '协作团队', exact: true }).first().click();
      await page.getByRole('button', { name: '打开群聊：内部协调成果例外验收', exact: true }).click();
      await page.waitForFunction(id => window.workspaceUI?.getCurrentRoom()?.id === id, coordinatorRoom);
      await openArtifact(coordinatorArtifact.title);
      await page.getByLabel('成果版本', { exact: true }).selectOption('2');
      const toggle = page.getByRole('button', { name: '评论', exact: true });
      if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
      await page.locator('#collaboration-comments-tab').click();
      await page.locator('[data-thread-id="' + secondComment.id + '"]').getByRole('checkbox').check();
      await page.getByRole('button', { name: '交给 Agent 修改', exact: true }).click();
      const otherDraft = dialog().getByLabel('修改说明', { exact: true });
      const text = '这是第二份成果尚未提交的说明，必须保持打开并完整保留。';
      await otherDraft.fill(text);
      await otherDraft.evaluate(element => { element.focus(); element.setSelectionRange(4, 9); });
      const returned = page.waitForResponse(item => item.url() === url && item.request().method() === 'POST');
      release();
      await returned;
      await until(async () => !await agentSelect().isDisabled());
      assert(await dialog().isVisible(), 'The response for artifact A closed artifact B\'s dialog.');
      assert.equal(await otherDraft.inputValue(), text);
      assert.deepEqual(await otherDraft.evaluate(element => ({ focused: document.activeElement === element, start: element.selectionStart, end: element.selectionEnd })), { focused: true, start: 4, end: 9 });
      assert((await dialog().locator('#modification-summary').innerText()).includes(coordinatorArtifact.title));
      assert.equal((await api('GET', '/artifacts/' + coordinatorArtifact.id + '/modifications')).modifications.length, secondCount);
      await page.screenshot({ path: path.join(runDir, 'team-modification-delayed-response-keeps-other-draft.png'), fullPage: true, animations: 'disabled' });
      fs.writeFileSync(path.join(runDir, 'team-modification-delayed-response.json'), JSON.stringify({ committed_request_id: saved.id, committed_artifact_id: saved.artifact_id, currently_open_artifact_id: coordinatorArtifact.id, other_draft_preserved: true, other_dialog_open: true, synthetic: true }, null, 2));
      await page.keyboard.press('Escape');
      await api('POST', '/runs/' + saved.run_id + '/cancel', {});
      assert.deepEqual(pageErrors, []);
    } finally {
      release();
      await page.unroute(url);
    }
  });

  await check('A newer artifact waits for its real producing task when the room history response is delayed', async () => {
    const delayedTeam = await api('POST', '/teams', { name: '成果来源时序验收', member_ids: ['writer', 'researcher'], routing_mode: 'auto', request_key: 'qa-team-origin-delay' }, 201);
    const roomId = delayedTeam.primary_room_id;
    const delayedTitle = '来源更新的团队成果';
    await api('POST', '/rooms/' + roomId + '/messages', { text: '先准备两份团队资料。', target_agent_ids: ['writer'], request_key: 'qa-team-origin-delay-first' });
    const first = await claim('qa-team-origin-delay-first', roomId, 'writer');
    const initial = await complete(first, 'qa-team-origin-delay-first', { message: '已保存两份真实合成资料。', artifacts: [{ title: delayedTitle, kind: 'markdown', content: '# 来源更新的团队成果\n\n第一版由内容编辑保存。' }, { title: '来源时序的参考资料', kind: 'markdown', content: '# 参考资料\n\n用于从另一份成果打开最新版本。' }] });
    const artifact = initial.artifacts.find(item => item.title === delayedTitle);
    await page.goto(base + '/#room/' + roomId);
    await openArtifact('来源时序的参考资料');
    const roomUrl = base + '/api/hub/rooms/' + roomId;
    let release, markHeld, heldCount = 0;
    const gate = new Promise(resolve => { release = resolve; });
    const held = new Promise(resolve => { markHeld = resolve; });
    await page.route(roomUrl, async route => {
      if (route.request().method() !== 'GET') return route.continue();
      const response = await route.fetch();
      assert.equal(response.status(), 200);
      heldCount += 1;
      markHeld();
      await gate;
      await route.fulfill({ response });
    });
    try {
      await Promise.race([held, new Promise((_, reject) => setTimeout(() => reject(new Error('A real room poll did not reach the source-ordering boundary.')), 12000))]);
      // A separate real client creates the new run and artifact version while
      // the browser still has its legitimate earlier room snapshot.
      await api('POST', '/rooms/' + roomId + '/messages', { text: '请协调角色直接补充同一成果的结论。', request_key: 'qa-team-origin-delay-second' });
      const coordinator = await claim('qa-team-origin-delay-coordinator', roomId, 'coordinator');
      await complete(coordinator, 'qa-team-origin-delay-coordinator', { message: '协调角色已补充第二版。', artifacts: [{ id: artifact.id, base_version: 1, title: delayedTitle, kind: 'markdown', content: '# 来源更新的团队成果\n\n第二版由协调角色保存。' }] });
      await openArtifact(delayedTitle);
      await page.waitForFunction(taskId => window.workspaceUI?.getSelectedArtifact()?.task_id === taskId, coordinator.task.id);
      const oldTasks = await page.evaluate(() => window.workspaceUI.getCurrentRoom().runs.flatMap(run => run.tasks.map(task => task.id)));
      assert(!oldTasks.includes(coordinator.task.id), 'The fixture did not preserve the real artifact/room response ordering.');
      const discussion = await api('POST', '/artifacts/' + artifact.id + '/comments', { artifact_version: 2, body: '请原协调角色补充最终执行顺序。', request_key: 'qa-team-origin-delay-comment' }, 201);
      const toggle = page.getByRole('button', { name: '评论', exact: true });
      if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
      await page.locator('#collaboration-comments-tab').click();
      await page.locator('[data-thread-id="' + discussion.id + '"]').getByRole('checkbox').check();
      await page.getByRole('button', { name: '交给 Agent 修改', exact: true }).click();
      const draft = dialog().getByLabel('修改说明', { exact: true });
      const text = '等待来源记录到达时，这份说明和光标都应保留。';
      await draft.fill(text);
      await draft.evaluate(element => { element.focus(); element.setSelectionRange(2, 8); });
      assert(await dialog().getByRole('button', { name: '提交修改请求', exact: true }).isDisabled());
      assert(await agentSelect().locator('option[value=""]').isDisabled());
      assert(!(await dialog().locator('#modification-agent-status').innerText()).includes('不在团队'), 'An unknown source was mislabeled as a removed member.');
      await page.screenshot({ path: path.join(runDir, 'team-modification-source-loading.png'), fullPage: true, animations: 'disabled' });
      release();
      await until(async () => (await enabledAgentValues()).includes('coordinator') && !await dialog().getByRole('button', { name: '提交修改请求', exact: true }).isDisabled());
      assert.deepEqual(await enabledAgentValues(), ['coordinator', 'researcher', 'writer']);
      assert.equal(await draft.inputValue(), text);
      assert.deepEqual(await draft.evaluate(element => ({ focused: document.activeElement === element, start: element.selectionStart, end: element.selectionEnd })), { focused: true, start: 2, end: 8 });
      await page.screenshot({ path: path.join(runDir, 'team-modification-source-confirmed.png'), fullPage: true, animations: 'disabled' });
      await agentSelect().selectOption('coordinator');
      const [response] = await Promise.all([
        page.waitForResponse(item => item.url().endsWith('/api/hub/artifacts/' + artifact.id + '/modifications') && item.request().method() === 'POST'),
        dialog().getByRole('button', { name: '提交修改请求', exact: true }).click(),
      ]);
      assert.equal(response.status(), 201);
      const modification = await response.json();
      assert.equal(modification.snapshot.source.agent_id, 'coordinator');
      const lease = await claim('qa-team-origin-delay-revision', roomId, 'coordinator');
      assert.equal(lease.task.id, modification.task_id);
      await complete(lease, 'qa-team-origin-delay-revision', { message: '正确的原协调角色完成了第三版。', artifacts: [{ id: artifact.id, base_version: 2, title: delayedTitle, kind: 'markdown', content: '# 来源更新的团队成果\n\n第三版已按讨论补充执行顺序。' }] });
      assert.equal((await api('GET', '/artifacts/' + artifact.id)).version, 3);
      fs.writeFileSync(path.join(runDir, 'team-modification-source-ordering.json'), JSON.stringify({ synthetic: true, delayed_real_room_reads: heldCount, earlier_browser_task_ids: oldTasks, new_producing_task_id: coordinator.task.id, producing_agent_id: 'coordinator', request_id: modification.id, result_version: 3, draft_preserved: true }, null, 2));
      assert.deepEqual(pageErrors, []);
    } finally {
      release();
      // Several genuine polls/source lookups can be in flight at this boundary.
      // Drain them before the next page navigation; unroute() does not wait.
      await page.unrouteAll({ behavior: 'wait' });
    }
  });
}

async function extendedJourney() {
  await page.setViewportSize({ width: 1440, height: 960 });
  let template;
  let teamRoom;
  let source;
  let producedArtifact;
  const teamName = '验收工作流';
  const prompt = '使用自建工作流核对资料并输出可编辑成果。';
  const sourceText = '这条合成资料有唯一标记 SOURCE-REVOKE-4729。';

  await check('Create an executable two-step workflow in the UI', async () => {
    await page.getByRole('button', { name: '工作流', exact: true }).first().click();
    await page.getByRole('button', { name: '创建工作流', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('工作流名称', { exact: true }).fill(teamName);
    await dialog.getByLabel('工作流说明', { exact: true }).fill('两步合成验收流程。');
    await dialog.getByLabel('步骤名称', { exact: true }).fill('核对资料');
    await dialog.getByLabel('步骤 Agent', { exact: true }).selectOption('researcher');
    await dialog.getByLabel('步骤指令', { exact: true }).fill('核对本次资料并返回摘要。');
    await dialog.getByRole('button', { name: '添加步骤', exact: true }).click();
    await dialog.getByLabel('步骤名称', { exact: true }).fill('撰写结论');
    await dialog.getByLabel('步骤 Agent', { exact: true }).selectOption('writer');
    await dialog.getByLabel('步骤指令', { exact: true }).fill('依据核对结果返回成果。');
    await dialog.getByLabel('前序任务', { exact: true }).selectOption({ label: '核对资料' });
    await page.screenshot({ path: path.join(runDir, 'team-workflow-editor.png'), fullPage: true, animations: 'disabled' });
    await dialog.getByRole('button', { name: '保存工作流', exact: true }).click();
    template = await until(async () => (await api('GET', '/templates')).templates.find(item => item.name === teamName));
    assert.equal(template.version, 1);
    assert.equal(template.steps.length, 2);
    assert.deepEqual(template.steps[1].depends_on, [template.steps[0].key]);
    await page.getByRole('button', { name: '使用工作流：' + teamName, exact: true }).click();
    await page.getByLabel('需求输入', { exact: true }).fill(prompt);
    await page.getByRole('button', { name: '发送', exact: true }).click();
    teamRoom = await until(async () => {
      const item = (await api('GET', '/bootstrap')).rooms.find(value => value.last_message === prompt);
      return item && api('GET', '/rooms/' + item.id);
    });
    assert.equal(teamRoom.runs[0].template_id, template.id);
    assert.equal(teamRoom.runs[0].template_version, 1);
    assert.equal(teamRoom.runs[0].tasks.length, 2);
  });

  await check('Add and revoke room knowledge through the UI', async () => {
    await page.getByRole('button', { name: '打开工作区', exact: true }).click();
    await page.getByRole('tab', { name: '资料', exact: true }).click();
    await page.getByRole('tabpanel', { name: '资料', exact: true }).getByRole('button', { name: '添加资料', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('资料标题', { exact: true }).fill('可撤销验收资料');
    await dialog.getByLabel('资料类型', { exact: true }).selectOption('text');
    await dialog.getByLabel('资料内容', { exact: true }).fill(sourceText);
    await dialog.getByRole('button', { name: '保存资料', exact: true }).click();
    source = await until(async () => (await api('GET', '/rooms/' + teamRoom.id + '/sources')).sources.find(item => item.title === '可撤销验收资料'));
    assert.equal(source.content, sourceText);
    assert.equal(source.retrieval_status, 'provided');
    await page.getByRole('button', { name: '查看资料：可撤销验收资料', exact: true }).click();
    await page.getByRole('dialog').getByText(sourceText, { exact: true }).waitFor();
    await page.keyboard.press('Escape');
    const researcher = await claim('qa-team-researcher', teamRoom.id, 'researcher');
    assert(JSON.stringify(researcher.context).includes(sourceText));
    await page.getByRole('button', { name: '移除资料：可撤销验收资料', exact: true }).click();
    await until(async () => !(await api('GET', '/rooms/' + teamRoom.id + '/sources')).sources.some(item => item.id === source.id));
    await complete(researcher, 'qa-team-researcher', { message: '资料核对阶段已完成。' });
    const writer = await claim('qa-team-writer', teamRoom.id, 'writer');
    assert(writer.task);
    assert(!JSON.stringify(writer.context).includes(sourceText), 'A revoked source leaked into a subsequent claim.');
    teamRoom = await complete(writer, 'qa-team-writer', {
      message: '团队验收成果已保存。', artifacts: [{ title: '团队验收成果', kind: 'markdown', content: '# 团队验收成果\n\n第一版。' }],
    });
    producedArtifact = teamRoom.artifacts.find(item => item.title === '团队验收成果');
    assert(producedArtifact);
  });

  await check('Editing a workflow preserves the definition used by an earlier run', async () => {
    const closeDrawer = page.getByRole('button', { name: '关闭工作区', exact: true });
    if (await closeDrawer.isVisible()) await closeDrawer.click();
    await page.getByRole('button', { name: '工作流', exact: true }).first().click();
    await page.getByRole('button', { name: '编辑工作流：' + teamName, exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('工作流说明', { exact: true }).fill('修订后的工作流定义。');
    await dialog.getByRole('button', { name: '选择步骤：核对资料', exact: true }).click();
    await dialog.getByLabel('步骤名称', { exact: true }).fill('核对新版资料');
    await dialog.getByRole('button', { name: '保存工作流', exact: true }).click();
    await until(async () => (await api('GET', '/templates/' + template.id)).version === 2);
    const savedRun = (await api('GET', '/rooms/' + teamRoom.id)).runs[0];
    assert.equal(savedRun.template_version, 1);
    assert(savedRun.tasks.some(item => item.title === '核对资料'));
    assert(!savedRun.tasks.some(item => item.title === '核对新版资料'));
    await api('PUT', '/templates/' + template.id, { base_version: 1, name: teamName, description: '过时的并发更新', steps: template.steps }, 409);
    await page.getByRole('button', { name: '对话', exact: true }).first().click();
    await page.locator('[data-room="' + teamRoom.id + '"]').click();
    await page.locator('#messages').getByText('团队验收成果已保存。', { exact: true }).waitFor();
    await openArtifact('团队验收成果');
  });

  await check('Human artifact edits, version-specific review, and conflict recovery', async () => {
    await page.getByRole('button', { name: '编辑成果', exact: true }).click();
    await page.getByLabel('成果内容', { exact: true }).fill('# 团队验收成果\n\n第二版由人工编辑。');
    await page.getByRole('button', { name: '保存成果', exact: true }).click();
    const second = await until(async () => {
      const current = await api('GET', '/artifacts/' + producedArtifact.id);
      return current.version === 2 && current;
    });
    assert.equal(second.author_type, 'human');
    await page.getByLabel('成果版本', { exact: true }).selectOption('1');
    await page.getByRole('button', { name: '通过审核', exact: true }).click();
    await page.getByLabel('审核意见', { exact: true }).fill('仅通过第一版，第二版须独立审核。');
    await page.getByRole('button', { name: '提交审核', exact: true }).click();
    await until(async () => (await api('GET', '/artifacts/' + producedArtifact.id + '?version=1')).reviews.some(item => item.version === 1 && item.decision === 'approved'));
    const secondAfterReview = await api('GET', '/artifacts/' + producedArtifact.id + '?version=2');
    assert(!secondAfterReview.reviews.some(item => item.version === 2 && item.decision === 'approved'));
    await page.getByLabel('成果版本', { exact: true }).selectOption('2');
    await page.getByRole('button', { name: '编辑成果', exact: true }).click();
    const unsaved = '# 不应丢失的编辑草稿\n\n并发保存失败也要保留。';
    await page.getByLabel('成果内容', { exact: true }).fill(unsaved);
    await api('PUT', '/artifacts/' + producedArtifact.id, { base_version: 2, title: '团队验收成果', kind: 'markdown', content: '# 外部并发修订\n\n这是第三版。' });
    const [conflict] = await Promise.all([
      page.waitForResponse(response => response.url().endsWith('/api/hub/artifacts/' + producedArtifact.id) && response.request().method() === 'PUT'),
      page.getByRole('button', { name: '保存成果', exact: true }).click(),
    ]);
    assert.equal(conflict.status(), 409);
    assert.equal(await page.getByLabel('成果内容', { exact: true }).inputValue(), unsaved);
    assert.equal((await api('GET', '/artifacts/' + producedArtifact.id)).version, 3);
    await page.screenshot({ path: path.join(runDir, 'artifact-conflict-preserves-draft.png'), fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: '基于最新版本继续', exact: true }).click();
    await page.locator('#artifact-edit-state').filter({ hasText: '草稿已保留，保存将新建版本 4' }).waitFor();
    assert.equal(await page.getByLabel('成果内容', { exact: true }).inputValue(), unsaved);
    await page.getByRole('button', { name: '保存成果', exact: true }).click();
    const recovered = await until(async () => {
      const current = await api('GET', '/artifacts/' + producedArtifact.id);
      return current.version === 4 && current;
    });
    assert.equal(recovered.content, unsaved);
  });

  await check('Executor presence is distinct from active tasks and can be disconnected', async () => {
    const registered = await api('POST', '/workers/register', { worker_id: 'qa-presence', name: '验收执行端', agent_ids: ['writer'], capabilities: ['synthetic-fixture'] });
    assert(registered.session_token);
    assert.equal(registered.worker.connected, true);
    const boot = await api('GET', '/bootstrap');
    assert.equal(boot.connection.connected_workers, 1);
    assert.equal(boot.connection.active_workers, 0);
    assert(!JSON.stringify(boot.workers).includes('session_token'));
    const closeDrawer = page.getByRole('button', { name: '关闭工作区', exact: true });
    if (await closeDrawer.isVisible()) await closeDrawer.click();
    await page.getByRole('button', { name: '连接 Agent', exact: true }).click();
    await page.getByRole('tab', { name: '执行端', exact: true }).click();
    await page.getByText('验收执行端', { exact: true }).waitFor();
    await page.getByText('验收执行端', { exact: true }).locator('..').getByText(/^已?连接$/).waitFor();
    await api('POST', '/workers/qa-presence/disconnect', { session_token: registered.session_token });
    await until(async () => (await api('GET', '/workers')).workers.find(item => item.worker_id === 'qa-presence').connected === false);
    await page.getByText('验收执行端', { exact: true }).locator('..').getByText(/^已?离线$/).waitFor();
    await page.screenshot({ path: path.join(runDir, 'executor-disconnected.png'), fullPage: true, animations: 'disabled' });
    await page.keyboard.press('Escape');
  });

  if (process.argv.includes('--expiry')) {
    await check('Executor presence expires without fabricated online status', async () => {
      const registered = await api('POST', '/workers/register', { worker_id: 'qa-expiring', name: '即将离线的验收执行端', agent_ids: ['writer'], capabilities: ['synthetic-fixture'] });
      await page.getByRole('button', { name: '连接 Agent', exact: true }).click();
      await page.getByRole('tab', { name: '执行端', exact: true }).click();
      await page.getByText('即将离线的验收执行端', { exact: true }).waitFor();
      const timeout = Math.max(10000, new Date(registered.worker.expires_at).getTime() - Date.now() + 12000);
      await until(async () => (await api('GET', '/workers')).workers.find(item => item.worker_id === 'qa-expiring').connected === false, timeout, 1000);
      assert.equal((await api('GET', '/bootstrap')).connection.connected_workers, 0);
      await page.getByText('即将离线的验收执行端', { exact: true }).locator('..').getByText(/^已?离线$/).waitFor();
      await page.keyboard.press('Escape');
    });
  }
}

async function artifactRebaseRaceJourney() {
  await check('Reading a newer artifact baseline locks save and cannot overwrite another artifact after switching', async () => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    const room = await api('POST', '/rooms', { title: '读取新版本的归属验收', text: '验证人工成果编辑的延迟读取边界。', agent_id: 'writer' });
    const worker = 'qa-artifact-rebase';
    const lease = await claim(worker, room.id, 'writer');
    await complete(lease, worker, { message: '两份合成成果已保存。', artifacts: [
      { title: '等待重读的成果A', kind: 'markdown', content: '# 成果A\n\n原始第一版。' },
      { title: '正在编辑的成果B', kind: 'markdown', content: '# 成果B\n\n原始第一版。' },
    ] });
    const saved = await api('GET', '/rooms/' + room.id);
    const first = saved.artifacts.find(item => item.title === '等待重读的成果A');
    const second = saved.artifacts.find(item => item.title === '正在编辑的成果B');
    await page.goto(base + '/#room/' + room.id);
    await page.waitForFunction(id => window.workspaceUI?.getCurrentRoom()?.id === id, room.id);
    await openArtifact(first.title);
    await page.getByRole('button', { name: '编辑成果', exact: true }).click();
    const firstDraft = '# 成果A的未保存草稿\n\n不能被异步响应丢失。';
    await page.getByLabel('成果内容', { exact: true }).fill(firstDraft);
    await api('PUT', '/artifacts/' + first.id, { base_version: 1, title: first.title, kind: 'markdown', content: '# 成果A\n\n其他成员保存了第二版。' });
    const [conflict] = await Promise.all([
      page.waitForResponse(response => response.url().endsWith('/api/hub/artifacts/' + first.id) && response.request().method() === 'PUT'),
      page.getByRole('button', { name: '保存成果', exact: true }).click(),
    ]);
    assert.equal(conflict.status(), 409);
    let release, entered;
    const held = new Promise(resolve => { release = resolve; });
    const received = new Promise(resolve => { entered = resolve; });
    let delayedReads = 0;
    const routeUrl = base + '/api/hub/artifacts/' + first.id;
    await page.route(routeUrl, async route => {
      if (route.request().method() === 'GET') { delayedReads++; entered(); await held; }
      await route.continue();
    });
    try {
      await page.getByRole('button', { name: '基于最新版本继续', exact: true }).click();
      await Promise.race([received, new Promise((_, reject) => setTimeout(() => reject(new Error('Rebase did not request the latest version.')), 12000))]);
      assert.equal(await page.getByRole('button', { name: '保存成果', exact: true }).isDisabled(), true, 'Save remained enabled while its baseline was being read.');
      assert.equal(await page.getByRole('button', { name: '基于最新版本继续', exact: true }).isDisabled(), true, 'Repeated baseline reads were not locked.');
      await page.locator('#artifact-list [data-artifact="' + second.id + '"]').click();
      await page.locator('#artifact-title').filter({ hasText: second.title }).waitFor();
      await page.getByRole('button', { name: '编辑成果', exact: true }).click();
      const secondDraft = '# 成果B的当前草稿\n\n旧成果A的读取响应不能影响这里。';
      await page.getByLabel('成果内容', { exact: true }).fill(secondDraft);
      const response = page.waitForResponse(result => result.url() === routeUrl && result.request().method() === 'GET');
      release();
      await (await response).finished();
      await page.waitForTimeout(250);
      assert.equal((await page.evaluate(() => window.workspaceUI.getSelectedArtifact())).id, second.id);
      assert.equal(await page.locator('#artifact-title').textContent(), second.title);
      assert.equal(await page.getByLabel('成果内容', { exact: true }).inputValue(), secondDraft);
      assert.equal(await page.getByRole('button', { name: '保存成果', exact: true }).isDisabled(), false, 'An old baseline request locked the newly selected artifact.');
      await page.getByRole('button', { name: '保存成果', exact: true }).click();
      const persistedSecond = await until(async () => { const result = await api('GET', '/artifacts/' + second.id); return result.version === 2 && result; });
      assert.equal(persistedSecond.content, secondDraft);
      assert.equal(persistedSecond.title, second.title);
      assert.equal((await api('GET', '/artifacts/' + first.id)).version, 2);
      assert.equal(delayedReads, 1);
      fs.writeFileSync(path.join(runDir, 'artifact-rebase-race.json'), JSON.stringify({ synthetic: true, delayed_reads: delayedReads, saved_artifact: persistedSecond.id, saved_version: persistedSecond.version, old_response_did_not_switch_artifact: true }, null, 2));
    } finally { release(); await page.unroute(routeUrl); }
  });
}

async function accountJourney() {
  const ownerContext = page.context();
  const password = 'Synthetic-QA-only-4729!';
  const originalRooms = (await api('GET', '/bootstrap')).rooms.map(item => item.id);
  let firstWorkspace;
  let secondWorkspace;
  let ownerReadToken;
  let viewerReadToken;
  let deniedControlToken;
  let viewerContext;
  let viewerPage;
  let accountDiscussion;
  let accountExecution;
  let executionAccessToken;
  let accountTeam;
  let teamAccessToken;

  async function request(context, method, route, body, expected) {
    const response = await context.request.fetch(base + route, { method, ...(body === undefined ? {} : { data: body }) });
    const raw = await response.text();
    let parsed;
    try { parsed = JSON.parse(raw); } catch { parsed = raw; }
    if (expected !== undefined) assert.equal(response.status(), expected, route + ': ' + raw);
    else assert(response.ok(), route + ': ' + response.status() + ' ' + raw);
    return parsed;
  }

  async function tokenRequest(token, method, route, body, expected = 200) {
    const response = await fetch(base + route, { method, headers: { Authorization: 'Bearer ' + token, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    assert.equal(response.status, expected, route + ': unexpected scoped-token response.');
    return response.json();
  }

  async function openAccount(target = page) {
    await target.getByRole('button', { name: '账户与空间', exact: true }).first().click();
    return target.getByRole('dialog');
  }

  async function navigateAction(action, target = page) {
    await Promise.all([target.waitForEvent('domcontentloaded'), action()]);
    await target.waitForFunction(() => window.workspaceAccount?.ready === true);
  }

  try {
    await check('Enable accounts through the UI while retaining the existing workspace', async () => {
      await page.setViewportSize({ width: 1440, height: 960 });
      const closeDrawer = page.getByRole('button', { name: '关闭工作区', exact: true });
      if (await closeDrawer.isVisible()) await closeDrawer.click();
      await openAccount();
      await page.getByRole('button', { name: '启用账户', exact: true }).click();
      await page.getByLabel('你的名字', { exact: true }).fill('验收拥有者');
      await page.getByLabel('邮箱', { exact: true }).fill('owner@example.test');
      await page.getByLabel('密码', { exact: true }).fill(password);
      await page.getByLabel('空间名称', { exact: true }).fill('原有验收空间');
      await navigateAction(() => page.getByRole('button', { name: '创建账户并启用登录', exact: true }).click());
      await page.getByLabel('需求输入', { exact: true }).waitFor();
      const session = await request(ownerContext, 'GET', '/api/account/session');
      assert.equal(session.mode, 'authenticated');
      assert.equal(session.workspace.role, 'owner');
      firstWorkspace = session.workspace;
      const kept = await request(ownerContext, 'GET', '/api/hub/bootstrap');
      assert.deepEqual(kept.rooms.map(item => item.id).sort(), [...originalRooms].sort());
      assert.equal((await fetch(base + '/api/hub/bootstrap')).status, 401);
      assert.equal((await fetch(base + '/api/projects')).status, 401);
      const cookies = await ownerContext.cookies();
      assert(cookies.some(cookie => cookie.httpOnly && cookie.sameSite === 'Strict'));
      if (sharedAcceptance) {
        assert((await request(ownerContext, 'GET', '/api/hub/search?q=SHARE_VERSION_TWO_PRIVATE')).results.some(item => item.type === 'artifact' && item.id === sharedAcceptance.artifactId));
        const publicResponse = await fetch(sharedAcceptance.url);
        assert.equal(publicResponse.status, 200);
        const sharedBody = await publicResponse.text();
        assert(sharedBody.includes('SHARE_VERSION_ONE'));
        assert(!sharedBody.includes('SHARE_VERSION_TWO_PRIVATE'));
        await request(ownerContext, 'DELETE', '/api/hub/artifacts/' + sharedAcceptance.artifactId + '/shares/' + sharedAcceptance.shareId);
        assert.equal((await fetch(sharedAcceptance.url)).status, 404);
      }
    });

    await check('Create and switch isolated workspaces through the UI', async () => {
      const dialog = await openAccount();
      await dialog.getByLabel('新空间名称', { exact: true }).fill('隔离验收空间');
      await navigateAction(() => dialog.getByRole('button', { name: '创建空间', exact: true }).click());
      secondWorkspace = await until(async () => {
        const session = await request(ownerContext, 'GET', '/api/account/session');
        return session.workspace?.name === '隔离验收空间' && session.workspace;
      });
      assert.notEqual(secondWorkspace.id, firstWorkspace.id);
      assert.equal((await request(ownerContext, 'GET', '/api/hub/bootstrap')).rooms.length, 0);
      if (process.argv.includes('--files')) assert.equal((await request(ownerContext, 'GET', '/api/hub/search?q=SHARE_VERSION_TWO_PRIVATE')).results.length, 0);
      if (originalRooms.length) await request(ownerContext, 'GET', '/api/hub/rooms/' + originalRooms[0], undefined, 404);
      await page.getByLabel('需求输入', { exact: true }).fill('只有第二个空间能够看到的验收会话。');
      await page.getByRole('button', { name: '发送', exact: true }).click();
      await until(async () => (await request(ownerContext, 'GET', '/api/hub/bootstrap')).rooms.length === 1);
      await openAccount();
      await navigateAction(() => page.getByRole('button', { name: '切换到 原有验收空间', exact: true }).click());
      await until(async () => (await request(ownerContext, 'GET', '/api/account/session')).workspace.id === firstWorkspace.id);
      assert.deepEqual((await request(ownerContext, 'GET', '/api/hub/bootstrap')).rooms.map(item => item.id).sort(), [...originalRooms].sort());
      await openAccount();
      await navigateAction(() => page.getByRole('button', { name: '切换到 隔离验收空间', exact: true }).click());
      await until(async () => (await request(ownerContext, 'GET', '/api/account/session')).workspace.id === secondWorkspace.id);
    });

    if (wantsCollaboration) {
      await check('Discussion and modification records are isolated by workspace, including direct IDs', async () => {
        assert(collaborationAcceptance);
        await request(ownerContext, 'GET', '/api/hub/artifacts/' + collaborationAcceptance.artifactId + '/comments', undefined, 404);
        await request(ownerContext, 'GET', '/api/hub/comments/' + collaborationAcceptance.commentId, undefined, 404);
        await request(ownerContext, 'GET', '/api/hub/modifications/' + collaborationAcceptance.requestId, undefined, 404);
        const visibleRoom = (await request(ownerContext, 'GET', '/api/hub/bootstrap')).rooms[0];
        const worker = 'qa-account-collaboration';
        const lease = await request(ownerContext, 'POST', '/api/hub/tasks/claim', { worker_id: worker, room_id: visibleRoom.id, agent_ids: ['coordinator'] });
        const completed = await request(ownerContext, 'POST', '/api/hub/tasks/' + lease.task.id + '/complete', { worker_id: worker, lease_token: lease.lease_token, result: { message: '成员权限验收首版已保存。', artifacts: [{ title: '成员权限验收成果', kind: 'markdown', content: '# 成员权限验收\n\n评论和修改遵守空间角色。' }] } });
        const artifact = completed.artifacts[0];
        const comment = await request(ownerContext, 'POST', '/api/hub/artifacts/' + artifact.id + '/comments', { artifact_version: 1, body: '查看者可以读取此讨论，但不能修改。', request_key: 'qa-member-comment' }, 201);
        const modification = await request(ownerContext, 'POST', '/api/hub/artifacts/' + artifact.id + '/modifications', { base_version: 1, comment_ids: [comment.id], instructions: '仅用于验证角色权限的合成任务。', agent_id: 'writer', request_key: 'qa-member-modification' }, 201);
        accountDiscussion = { artifact, comment, modification, roomId: visibleRoom.id };
      });
    }

    if (wantsExecution) {
      await check('Execution access is workspace-bound and revoking a credential rejects its still-live lease writes', async () => {
        assert(executionAcceptance);
        await request(ownerContext, 'GET', '/api/hub/tasks/' + executionAcceptance.taskId + '/execution', undefined, 404);
        await request(ownerContext, 'GET', '/api/hub/executions/' + executionAcceptance.executionId + '/events', undefined, 404);
        const token = await request(ownerContext, 'POST', '/api/account/tokens', { name: '待撤销的执行记录验收凭据', scopes: ['read', 'execute'] });
        executionAccessToken = token.token;
        const currentRoom = (await request(ownerContext, 'GET', '/api/hub/bootstrap')).rooms[0];
        const worker = 'qa-scoped-execution-owner';
        const lease = await tokenRequest(executionAccessToken, 'POST', '/api/hub/tasks/claim', { worker_id: worker, room_id: currentRoom.id, agent_ids: [accountDiscussion ? 'writer' : 'coordinator'], host_identity: 'qa-scoped-host' });
        assert(lease.task && lease.execution);
        const callId = 'scoped-unknown-tool';
        await tokenRequest(executionAccessToken, 'POST', '/api/hub/tasks/' + lease.task.id + '/execution/events', { worker_id: worker, lease_token: lease.lease_token, event_id: 'scoped-tool-start', kind: 'tool_call', payload: { call_id: callId, tool: 'fixture_operation', retry_safe: false, summary: '只用于权限验收，没有执行外部动作。' } }, 201);
        const beforeRevoke = await tokenRequest(executionAccessToken, 'GET', '/api/hub/tasks/' + lease.task.id + '/execution');
        assert.equal(beforeRevoke.active, true);
        await tokenRequest(executionAccessToken, 'POST', '/api/hub/executions/' + lease.execution.id + '/reconcile', { request_key: 'execute-token-cannot-review', expected_seq: beforeRevoke.last_seq, call_id: callId, decision: 'confirmed_not_executed', note: '执行范围凭据没有人工核对写权限。' }, 403);
        await request(ownerContext, 'DELETE', '/api/account/tokens/' + token.record.id);
        await tokenRequest(executionAccessToken, 'POST', '/api/hub/tasks/' + lease.task.id + '/execution/events', { worker_id: worker, lease_token: lease.lease_token, event_id: 'revoked-live-lease', kind: 'progress', payload: { message: '撤销后的凭据不能继续写入。' } }, 401);
        await tokenRequest(executionAccessToken, 'GET', '/api/hub/tasks/' + lease.task.id + '/execution', undefined, 401);
        // The account owner records the synthetic lost outcome; the revoked credential is never reused successfully.
        await request(ownerContext, 'POST', '/api/hub/tasks/' + lease.task.id + '/execution/events', { worker_id: worker, lease_token: lease.lease_token, event_id: 'scoped-tool-unknown', kind: 'tool_result', payload: { call_id: callId, outcome: 'unknown', summary: '合成事件：结果尚未核对。' } }, 201);
        const details = await request(ownerContext, 'GET', '/api/hub/tasks/' + lease.task.id + '/execution');
        assert.equal(details.recovery.decision, 'review_required');
        accountExecution = { lease, worker, callId, details, roomId: currentRoom.id };
      });
    }

    await check('UI-generated API credentials enforce scopes and remain workspace-bound', async () => {
      const dialog = await openAccount();
      await dialog.getByRole('button', { name: 'Agent 凭据', exact: true }).click();
      await dialog.getByLabel('凭据名称', { exact: true }).fill('只读验收凭据');
      await dialog.getByLabel('执行任务', { exact: true }).uncheck();
      await dialog.getByRole('button', { name: '创建凭据', exact: true }).click();
      ownerReadToken = await until(async () => {
        const input = dialog.getByLabel('新建凭据', { exact: true });
        return await input.count() ? input.inputValue() : false;
      });
      assert(ownerReadToken);
      assert.equal((await tokenRequest(ownerReadToken, 'GET', '/api/hub/bootstrap')).rooms.length, 1);
      await tokenRequest(ownerReadToken, 'POST', '/api/hub/rooms', { text: '不应被只读凭据创建。' }, 403);
      await tokenRequest(ownerReadToken, 'POST', '/api/hub/tasks/claim', { worker_id: 'qa-token-denied' }, 403);
      await tokenRequest(ownerReadToken, 'GET', '/api/account/members', undefined, 403);
      if (accountDiscussion) {
        await tokenRequest(ownerReadToken, 'GET', '/api/hub/modifications/' + accountDiscussion.modification.id);
        await tokenRequest(ownerReadToken, 'POST', '/api/hub/artifacts/' + accountDiscussion.artifact.id + '/comments', { artifact_version: 1, body: '只读凭据不能评论。', request_key: 'qa-read-token-comment' }, 403);
        await tokenRequest(ownerReadToken, 'PUT', '/api/hub/comments/' + accountDiscussion.comment.id, { base_version: 1, status: 'resolved' }, 403);
      }
      if (accountExecution) {
        const { lease, worker } = accountExecution;
        await tokenRequest(ownerReadToken, 'GET', '/api/hub/executions/' + lease.execution.id + '/events');
        await tokenRequest(ownerReadToken, 'POST', '/api/hub/tasks/' + lease.task.id + '/execution/result/read', { worker_id: worker, lease_token: lease.lease_token }, 403);
      }
      await dialog.getByRole('button', { name: '工作空间', exact: true }).click();
      await navigateAction(() => page.getByRole('button', { name: '切换到 原有验收空间', exact: true }).click());
      await until(async () => (await request(ownerContext, 'GET', '/api/account/session')).workspace.id === firstWorkspace.id);
      assert.equal((await tokenRequest(ownerReadToken, 'GET', '/api/hub/bootstrap')).rooms.length, 1);
      await openAccount();
      await navigateAction(() => page.getByRole('button', { name: '切换到 隔离验收空间', exact: true }).click());
      await until(async () => (await request(ownerContext, 'GET', '/api/account/session')).workspace.id === secondWorkspace.id);
    });

    if (wantsManaged) {
      await check('Authenticated local-owner control revokes its temporary execution credential on stop', async () => {
        assert.equal((await request(ownerContext, 'GET', '/api/hub/executors/local')).local_control, true);
        await request(ownerContext, 'POST', '/api/hub/executors/codex/start', {});
        const pid = Number(fs.readFileSync(path.join(runDir, 'managed-fixture.pid'), 'utf8'));
        try {
          const tokens = (await request(ownerContext, 'GET', '/api/account/tokens')).tokens;
          const executionCredential = tokens.find(item => item.name === '本机 Codex 执行器' && !item.revoked_at);
          assert(executionCredential);
          await request(ownerContext, 'POST', '/api/hub/executors/codex/stop', {});
          assert((await request(ownerContext, 'GET', '/api/account/tokens')).tokens.find(item => item.id === executionCredential.id).revoked_at);
          assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' });
          const tokenResult = await request(ownerContext, 'POST', '/api/account/tokens', { name: '控制器拒绝验收凭据', scopes: ['read', 'write', 'execute'] });
          deniedControlToken = tokenResult.token;
          assert.equal((await tokenRequest(deniedControlToken, 'GET', '/api/hub/executors/local')).local_control, false);
          await tokenRequest(deniedControlToken, 'POST', '/api/hub/executors/codex/start', {}, 403);
          await request(ownerContext, 'DELETE', '/api/account/tokens/' + tokenResult.record.id);
        } finally {
          await request(ownerContext, 'POST', '/api/hub/executors/codex/stop', {});
        }
      });
    }

    await check('Viewer invitation is usable and cannot grant write or execution access', async () => {
      const dialog = await openAccount();
      await dialog.getByRole('button', { name: '成员与邀请', exact: true }).click();
      await dialog.getByLabel('邀请邮箱', { exact: true }).fill('viewer@example.test');
      await dialog.getByLabel('邀请权限', { exact: true }).selectOption('viewer');
      await dialog.getByRole('button', { name: '创建邀请', exact: true }).click();
      const invite = await until(async () => {
        const input = dialog.getByLabel('邀请链接', { exact: true });
        return await input.count() ? input.inputValue() : false;
      });
      assert(invite.startsWith(base + '/join?token='));
      viewerContext = await browser.newContext({ viewport: { width: 1280, height: 900 } });
      viewerPage = await viewerContext.newPage();
      viewerPage.on('pageerror', error => pageErrors.push(error.message));
      await viewerPage.goto(invite);
      await viewerPage.getByLabel('你的名字', { exact: true }).fill('验收查看者');
      await viewerPage.getByLabel('邮箱', { exact: true }).fill('viewer@example.test');
      await viewerPage.getByLabel('密码', { exact: true }).fill(password);
      await navigateAction(() => viewerPage.getByRole('button', { name: '接受邀请', exact: true }).click(), viewerPage);
      await until(async () => (await request(viewerContext, 'GET', '/api/account/session')).workspace?.role === 'viewer');
      assert.equal((await request(viewerContext, 'GET', '/api/hub/bootstrap')).rooms.length, 1);
      await request(viewerContext, 'POST', '/api/hub/rooms', { text: '只读成员不应创建。' }, 403);
      await request(viewerContext, 'POST', '/api/hub/tasks/claim', { worker_id: 'qa-viewer-denied' }, 403);
      if (process.argv.includes('--files')) {
        const readable = (await request(viewerContext, 'GET', '/api/hub/bootstrap')).rooms[0];
        await request(viewerContext, 'POST', '/api/hub/rooms/' + readable.id + '/files', { filename: 'denied.txt', data_base64: Buffer.from('viewer cannot upload').toString('base64') }, 403);
        await request(viewerContext, 'POST', '/api/hub/artifacts/' + sharedAcceptance.artifactId + '/shares', { version: 1 }, 403);
      }
      await request(viewerContext, 'POST', '/api/account/tokens', { name: '不应提升权限', scopes: ['write'] }, 403);
      const viewerDialog = await openAccount(viewerPage);
      await viewerDialog.getByRole('button', { name: 'Agent 凭据', exact: true }).click();
      await viewerDialog.getByLabel('凭据名称', { exact: true }).fill('查看者只读凭据');
      assert(await viewerDialog.getByLabel('编辑', { exact: true }).isDisabled());
      assert(await viewerDialog.getByLabel('执行任务', { exact: true }).isDisabled());
      await viewerDialog.getByRole('button', { name: '创建凭据', exact: true }).click();
      viewerReadToken = await until(async () => {
        const input = viewerDialog.getByLabel('新建凭据', { exact: true });
        return await input.count() ? input.inputValue() : false;
      });
      assert.equal((await tokenRequest(viewerReadToken, 'GET', '/api/hub/bootstrap')).rooms.length, 1);
      await viewerPage.keyboard.press('Escape');
      assert.equal((await request(viewerContext, 'GET', '/api/hub/executors/local')).local_control, false);
      if (wantsManaged) {
        await request(viewerContext, 'POST', '/api/hub/executors/codex/start', {}, 403);
        await request(viewerContext, 'POST', '/api/account/workspaces', { name: '查看者自行创建的空间' });
        assert.equal((await request(viewerContext, 'GET', '/api/account/session')).workspace.role, 'owner');
        assert.equal((await request(viewerContext, 'GET', '/api/hub/executors/local')).local_control, false);
        await request(viewerContext, 'POST', '/api/hub/executors/codex/start', {}, 403);
        await request(viewerContext, 'POST', '/api/account/workspaces/' + secondWorkspace.id + '/select', {});
      }
      await viewerPage.screenshot({ path: path.join(runDir, 'viewer-workspace.png'), fullPage: true, animations: 'disabled' });
    });

    if (accountDiscussion) {
      await check('Viewer can read discussions and frozen requests while every mutation is denied', async () => {
        const { artifact, comment, modification, roomId } = accountDiscussion;
        const readable = await request(viewerContext, 'GET', '/api/hub/artifacts/' + artifact.id + '/comments?version=1');
        assert(readable.comments.some(item => item.id === comment.id));
        assert.equal((await request(viewerContext, 'GET', '/api/hub/modifications/' + modification.id)).snapshot.baseline.content, artifact.content || '# 成员权限验收\n\n评论和修改遵守空间角色。');
        await request(viewerContext, 'POST', '/api/hub/artifacts/' + artifact.id + '/comments', { artifact_version: 1, body: '查看者不应评论。', request_key: 'qa-viewer-comment' }, 403);
        await request(viewerContext, 'PUT', '/api/hub/comments/' + comment.id, { base_version: 1, status: 'resolved' }, 403);
        await request(viewerContext, 'POST', '/api/hub/artifacts/' + artifact.id + '/modifications', { base_version: 1, comment_ids: [comment.id], request_key: 'qa-viewer-modification' }, 403);
        await request(viewerContext, 'POST', '/api/hub/modifications/' + modification.id + '/apply', { base_version: 1, request_key: 'qa-viewer-apply' }, 403);
        await request(viewerContext, 'POST', '/api/hub/runs/' + modification.run_id + '/cancel', {}, 403);
        await request(viewerContext, 'GET', '/api/hub/modifications/' + collaborationAcceptance.requestId, undefined, 404);
        await viewerPage.goto(base + '/#room/' + roomId);
        await viewerPage.getByRole('button', { name: '打开成果：成员权限验收成果', exact: true }).first().click();
        await viewerPage.getByRole('button', { name: '评论', exact: true }).click();
        await viewerPage.locator('[data-thread-id="' + comment.id + '"]').waitFor();
        for (const locator of [viewerPage.getByLabel('评论内容', { exact: true }), viewerPage.getByRole('button', { name: '交给 Agent 修改', exact: true }), viewerPage.getByRole('button', { name: '解决评论', exact: true })]) {
          assert(await locator.count() === 0 || !await locator.first().isVisible() || await locator.first().isDisabled(), 'Viewer received an active discussion mutation control.');
        }
        await viewerPage.screenshot({ path: path.join(runDir, 'collaboration-viewer-readonly.png'), fullPage: true, animations: 'disabled' });
        await viewerPage.setViewportSize({ width: 390, height: 844 });
        await assertNoOverflow(viewerPage);
        await viewerPage.screenshot({ path: path.join(runDir, 'collaboration-viewer-mobile.png'), fullPage: true, animations: 'disabled' });
        await viewerPage.keyboard.press('Escape');
        await viewerPage.setViewportSize({ width: 1280, height: 900 });
      });
    }

    if (accountExecution) {
      await check('Viewer can inspect execution receipts but cannot append, read private results or reconcile outcomes', async () => {
        const { lease, worker, callId, roomId } = accountExecution;
        const details = await request(viewerContext, 'GET', '/api/hub/tasks/' + lease.task.id + '/execution');
        assert.equal(details.can_reconcile, false);
        const publicEvents = await request(viewerContext, 'GET', '/api/hub/executions/' + lease.execution.id + '/events');
        assert(publicEvents.events.some(item => item.kind === 'tool_result' && item.author_name === '验收拥有者'));
        assert(!JSON.stringify(publicEvents).includes(lease.lease_token));
        assert(!JSON.stringify(publicEvents).includes(executionAccessToken));
        await request(viewerContext, 'POST', '/api/hub/tasks/' + lease.task.id + '/execution/events', { worker_id: worker, lease_token: lease.lease_token, event_id: 'viewer-forged-progress', kind: 'progress', payload: { message: '查看者不应写执行记录。' } }, 403);
        await request(viewerContext, 'POST', '/api/hub/tasks/' + lease.task.id + '/execution/result/read', { worker_id: worker, lease_token: lease.lease_token }, 403);
        await request(viewerContext, 'POST', '/api/hub/executions/' + lease.execution.id + '/reconcile', { request_key: 'viewer-cannot-reconcile', expected_seq: details.last_seq, call_id: callId, decision: 'confirmed_not_executed', note: '查看者不能代替编辑者核对。' }, 403);
        await request(viewerContext, 'POST', '/api/hub/runs/' + details.run_id + '/retry', {}, 403);
        await request(viewerContext, 'GET', '/api/hub/executions/' + executionAcceptance.executionId + '/events', undefined, 404);
        await openExecutionPage(roomId, lease.task.id, viewerPage);
        await viewerPage.locator('#execution-task-status').filter({ hasText: '核对' }).waitFor();
        for (const label of ['重新排队', '核对外部调用', '核对调用', '保存核对结果', '停止本次协作']) {
          const control = viewerPage.getByRole('button', { name: label, exact: true });
          assert(await control.count() === 0 || !await control.isVisible() || await control.isDisabled(), 'Viewer received an active execution mutation control: ' + label);
        }
        await viewerPage.screenshot({ path: path.join(runDir, 'execution-viewer-readonly.png'), fullPage: true, animations: 'disabled' });
        await viewerPage.setViewportSize({ width: 390, height: 844 });
        await assertNoOverflow(viewerPage);
        await viewerPage.screenshot({ path: path.join(runDir, 'execution-viewer-mobile.png'), fullPage: true, animations: 'disabled' });
        await viewerPage.keyboard.press('Escape');
        await viewerPage.setViewportSize({ width: 1280, height: 900 });
      });
    }

    if (wantsTeams) {
      await check('Persistent teams are isolated by workspace and viewers can open the group without editing it', async () => {
        assert(teamAcceptance);
        for (const route of ['/teams/' + teamAcceptance.teamId, '/teams/' + teamAcceptance.teamId + '/history', '/rooms/' + teamAcceptance.roomId]) await request(ownerContext, 'GET', '/api/hub' + route, undefined, 404);
        await request(ownerContext, 'PUT', '/api/hub/teams/' + teamAcceptance.teamId, { base_version: teamAcceptance.version, name: '跨空间不得修改' }, 404);
        accountTeam = await request(ownerContext, 'POST', '/api/hub/teams', { name: '查看者可读的常驻群', member_ids: ['writer', 'researcher'], routing_mode: 'mention', request_key: 'qa-authenticated-team' }, 201);
        assert.equal(accountTeam.created_by_name, '验收拥有者');
        await request(ownerContext, 'POST', '/api/hub/rooms/' + accountTeam.primary_room_id + '/messages', { text: '这是同一空间成员可读的团队消息。', request_key: 'qa-authenticated-team-note' });
        assert((await request(viewerContext, 'GET', '/api/hub/teams')).teams.some(item => item.id === accountTeam.id));
        assert.equal((await tokenRequest(ownerReadToken, 'GET', '/api/hub/teams/' + accountTeam.id)).id, accountTeam.id);
        assert.equal((await request(viewerContext, 'GET', '/api/hub/teams/' + accountTeam.id + '/history')).history[0].actor_name, '验收拥有者');
        await request(viewerContext, 'GET', '/api/hub/teams/' + teamAcceptance.teamId, undefined, 404);
        await request(viewerContext, 'POST', '/api/hub/teams', { name: '查看者不能创建团队', member_ids: ['writer'], request_key: 'qa-viewer-team-create' }, 403);
        await request(viewerContext, 'PUT', '/api/hub/teams/' + accountTeam.id, { base_version: accountTeam.version, member_ids: ['writer'] }, 403);
        await request(viewerContext, 'DELETE', '/api/hub/teams/' + accountTeam.id, { base_version: accountTeam.version }, 403);
        await request(viewerContext, 'POST', '/api/hub/rooms/' + accountTeam.primary_room_id + '/messages', { text: '查看者不能派发群任务。', target_agent_ids: ['writer'], request_key: 'qa-viewer-team-message' }, 403);
        await tokenRequest(ownerReadToken, 'PUT', '/api/hub/teams/' + accountTeam.id, { base_version: accountTeam.version, name: '只读凭据不能修改' }, 403);
        if (await viewerPage.locator('dialog[open]').count()) await viewerPage.keyboard.press('Escape');
        await viewerPage.goto(base);
        await viewerPage.waitForFunction(() => !!window.workspaceUI?.getCurrentRoom());
        await viewerPage.getByRole('button', { name: '协作团队', exact: true }).first().click();
        const create = viewerPage.getByRole('button', { name: '创建团队', exact: true });
        assert(await create.count() === 0 || !await create.first().isVisible() || await create.first().isDisabled());
        await viewerPage.getByRole('button', { name: '打开群聊：' + accountTeam.name, exact: true }).click();
        const peerMessage = viewerPage.locator('#messages .message.user').filter({ hasText: '这是同一空间成员可读的团队消息。' });
        await peerMessage.waitFor({ state: 'visible' });
        assert((await peerMessage.innerText()).includes('验收拥有者'), 'The viewer lost the actual peer author identity.');
        await viewerPage.getByRole('button', { name: '团队信息', exact: true }).click();
        const info = viewerPage.locator('#group-info-dialog');
        const edit = info.getByRole('button', { name: '编辑团队成员', exact: true });
        assert(!await edit.isVisible() || await edit.isDisabled());
        assert(await info.getByRole('switch', { name: '自动分工', exact: true }).isDisabled());
        await viewerPage.setViewportSize({ width: 390, height: 844 });
        await assertNoOverflow(viewerPage);
        await viewerPage.screenshot({ path: path.join(runDir, 'team-viewer-mobile.png'), fullPage: true, animations: 'disabled' });
        await viewerPage.keyboard.press('Escape');
        await viewerPage.setViewportSize({ width: 1280, height: 900 });
      });

      await check('Viewer navigation remains on the team directory when an earlier automatic room read arrives late', async () => {
        const roomUrl = base + '/api/hub/rooms/' + accountTeam.primary_room_id;
        assert.equal((await request(viewerContext, 'GET', '/api/hub/bootstrap')).rooms[0].id, accountTeam.primary_room_id);
        let release, markHeld, delayed = false;
        const held = new Promise(resolve => { markHeld = resolve; });
        const gate = new Promise(resolve => { release = resolve; });
        await viewerPage.route(roomUrl, async route => {
          if (route.request().method() === 'GET' && !delayed) {
            delayed = true;
            const response = await route.fetch();
            assert.equal(response.status(), 200);
            markHeld();
            await gate;
            await route.fulfill({ response });
          } else await route.continue();
        });
        try {
          await viewerPage.goto(base);
          await Promise.race([held, new Promise((_, reject) => setTimeout(() => reject(new Error('Initial viewer room read was not intercepted.')), 12000))]);
          await viewerPage.getByRole('button', { name: '协作团队', exact: true }).first().click();
          assert.equal(await viewerPage.evaluate(() => window.workspaceUI.getView()), 'teams');
          const received = viewerPage.waitForResponse(item => item.url() === roomUrl && item.request().method() === 'GET');
          release();
          await received;
          await viewerPage.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
          assert.equal(await viewerPage.evaluate(() => window.workspaceUI.getView()), 'teams', 'A delayed initial room read replaced the user-selected directory.');
          assert(await viewerPage.getByRole('button', { name: '打开群聊：' + accountTeam.name, exact: true }).isVisible());
          await viewerPage.screenshot({ path: path.join(runDir, 'team-viewer-late-initial-room.png'), fullPage: true, animations: 'disabled' });
          fs.writeFileSync(path.join(runDir, 'team-navigation-race.json'), JSON.stringify({ delayed_real_room_response: true, selected_view: 'teams', view_after_response: 'teams', viewer: true }, null, 2));
        } finally {
          release();
          await viewerPage.unroute(roomUrl);
        }
      });

      await check('A revoked team executor credential cannot use its active lease or acquire membership authority', async () => {
        const token = await request(ownerContext, 'POST', '/api/account/tokens', { name: '团队执行撤销验收凭据', scopes: ['read', 'execute'] });
        teamAccessToken = token.token;
        await tokenRequest(teamAccessToken, 'PUT', '/api/hub/teams/' + accountTeam.id, { base_version: accountTeam.version, member_ids: ['writer', 'developer'] }, 403);
        await request(ownerContext, 'POST', '/api/hub/rooms/' + accountTeam.primary_room_id + '/messages', { text: '只用于验证凭据撤销的合成任务。', target_agent_ids: ['writer'], request_key: 'qa-authenticated-team-execution' });
        const lease = await tokenRequest(teamAccessToken, 'POST', '/api/hub/tasks/claim', { worker_id: 'qa-authenticated-team-worker', room_id: accountTeam.primary_room_id, agent_ids: ['writer'] });
        assert.equal(lease.context.team.id, accountTeam.id);
        assert.equal((await request(ownerContext, 'GET', '/api/hub/tasks/' + lease.task.id + '/execution')).active, true);
        await request(ownerContext, 'DELETE', '/api/account/tokens/' + token.record.id);
        const owner = { worker_id: 'qa-authenticated-team-worker', lease_token: lease.lease_token };
        await tokenRequest(teamAccessToken, 'POST', '/api/hub/tasks/' + lease.task.id + '/heartbeat', owner, 401);
        await tokenRequest(teamAccessToken, 'POST', '/api/hub/tasks/' + lease.task.id + '/execution/events', { ...owner, event_id: 'revoked-team-progress', kind: 'progress', payload: { message: '撤销后不得继续。' } }, 401);
        await tokenRequest(teamAccessToken, 'POST', '/api/hub/tasks/' + lease.task.id + '/complete', { ...owner, result: { message: '撤销后不应出现。' } }, 401);
        const archived = await request(ownerContext, 'DELETE', '/api/hub/teams/' + accountTeam.id, { base_version: accountTeam.version });
        assert(archived.cancelled_run_ids.includes(lease.task.run_id));
        assert.equal((await request(viewerContext, 'GET', '/api/hub/rooms/' + accountTeam.primary_room_id)).artifacts.length, 0);
      });
    }

    await check('Membership and credential revocation apply immediately and audit records omit secrets', async () => {
      const dialog = page.getByRole('dialog');
      await dialog.getByRole('button', { name: '成员与邀请', exact: true }).click();
      await dialog.getByRole('button', { name: '移除成员：viewer@example.test', exact: true }).click();
      await until(async () => !(await request(ownerContext, 'GET', '/api/account/members')).members.some(item => item.email === 'viewer@example.test'));
      await request(viewerContext, 'GET', '/api/hub/bootstrap', undefined, 403);
      await tokenRequest(viewerReadToken, 'GET', '/api/hub/bootstrap', undefined, 403);
      await dialog.getByRole('button', { name: 'Agent 凭据', exact: true }).click();
      await dialog.getByRole('button', { name: '撤销凭据：只读验收凭据', exact: true }).click();
      await until(async () => (await request(ownerContext, 'GET', '/api/account/tokens')).tokens.some(item => item.name === '只读验收凭据' && item.revoked_at));
      await tokenRequest(ownerReadToken, 'GET', '/api/hub/bootstrap', undefined, 401);
      await dialog.getByRole('button', { name: '操作记录', exact: true }).click();
      const audit = await request(ownerContext, 'GET', '/api/account/audit');
      assert(audit.events.length > 0);
      const encoded = JSON.stringify(audit);
      for (const secret of [password, ownerReadToken, viewerReadToken, deniedControlToken, executionAccessToken, teamAccessToken].filter(Boolean)) assert(!encoded.includes(secret), 'Audit included a credential.');
      await page.screenshot({ path: path.join(runDir, 'account-audit.png'), fullPage: true, animations: 'disabled' });
      await page.keyboard.press('Escape');
      assert.deepEqual(pageErrors, []);
    });
  } finally {
    if (viewerContext) await viewerContext.close();
  }
}

async function automationJourney() {
  await page.setViewportSize({ width: 1440, height: 960 });
  const closeDrawer = page.getByRole('button', { name: '关闭工作区', exact: true });
  if (await closeDrawer.isVisible()) await closeDrawer.click();
  const skillName = '周期资料核对技能';
  const scheduleName = '周期执行验收计划';
  let skill;
  let schedule;
  const fixturePath = path.join(runDir, 'synthetic-agent-command.cjs');
  fs.writeFileSync(fixturePath, `'use strict';
const fs = require('node:fs');
const assert = require('node:assert/strict');
const envelope = JSON.parse(fs.readFileSync(0, 'utf8'));
assert.equal(envelope.protocol, 'agent-workspace/1');
const expected = process.argv[2], skillId = process.argv[3];
const skill = (envelope.context.skills || []).find(item => item.id === skillId);
if (expected === 'archived') {
  assert(!skill, 'An archived skill reached the command.');
  assert(JSON.stringify(envelope.context.omissions).includes('skill_archived'));
} else {
  assert(skill, 'The bound skill was not supplied.');
  assert.equal(skill.version, Number(expected));
  assert(skill.content.includes('SKILL_QA_V' + expected));
}

process.stdout.write(JSON.stringify({message:'真实命令桥已完成合成验收，技能状态：' + expected,artifacts:[{title:'周期任务验收成果',kind:'markdown',content:'# 命令执行记录\\n\\n这是合成验收宿主返回的成果。技能状态：' + expected}]}));
`, 'utf8');

  async function runCommand(expected) {
    const configPath = path.join(runDir, 'worker-' + expected + '.json');
    fs.writeFileSync(configPath, JSON.stringify({
      worker_id: 'qa-command-worker', name: '合成验收命令执行端', agent_ids: ['researcher'], room_id: schedule.room_id,
      capabilities: ['synthetic-fixture'], command: [process.execPath, fixturePath, String(expected), skill.id], cwd: runDir, timeout_seconds: 30,
    }), 'utf8');
    const env = { ...process.env, STUDIO_DATA_DIR: dataDir, PYTHONUTF8: '1' };
    delete env.STUDIO_WORKER_TOKEN;
    const child = spawn(python, ['-X', 'utf8', '-m', 'studio.worker', '--config', configPath, '--server', base, '--once'], { cwd: repo, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let log = '';
    child.stdout.on('data', chunk => { log += chunk.toString(); });
    child.stderr.on('data', chunk => { log += chunk.toString(); });
    const exitCode = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => { child.kill(); reject(new Error('Synthetic command bridge timed out.')); }, 45000);
      child.once('error', error => { clearTimeout(timer); reject(error); });
      child.once('exit', code => { clearTimeout(timer); resolve(code); });
    });
    fs.writeFileSync(path.join(runDir, 'worker-' + expected + '.log'), log, 'utf8');
    assert.equal(exitCode, 0, log);
    assert(log.split(/\r?\n/).some(line => { try { return JSON.parse(line).event === 'completed'; } catch { return false; } }), log);
    schedule = await api('GET', '/schedules/' + schedule.id);
    assert.equal(schedule.last_run_status, 'completed');
  }

  async function makeDue() {
    const fresh = await api('GET', '/schedules/' + schedule.id);
    schedule = await api('PUT', '/schedules/' + schedule.id, {
      base_version: fresh.version, name: fresh.name, text: fresh.text, agent_id: fresh.agent_id,
      template_id: fresh.template_id, interval_seconds: 3600, next_run_at: new Date(Date.now() - 2000).toISOString(), room_id: fresh.room_id,
    });
  }

  await check('Create a versioned skill in the UI and bind it to an agent', async () => {
    await navigateWorkspace('技能库');
    await page.getByRole('button', { name: '新建技能', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('技能名称', { exact: true }).fill(skillName);
    await dialog.getByLabel('技能说明', { exact: true }).fill('提供可追溯的合成验收资料核对步骤。');
    await dialog.getByLabel('技能内容', { exact: true }).fill('# 周期资料核对\n\nSKILL_QA_V1\n\n检查任务和来源，返回有来源的结论。\n');
    await dialog.getByRole('button', { name: '保存技能', exact: true }).click();
    skill = await until(async () => (await api('GET', '/skills')).skills.find(item => item.name === skillName));
    assert.equal(skill.version, 1);
    assert.match(skill.sha256, /^[a-f0-9]{64}$/);
    await page.getByRole('button', { name: '绑定到 Agent', exact: true }).click();
    await Promise.all([
      page.waitForResponse(response => response.url().endsWith('/api/hub/agents/researcher/skills') && response.request().method() === 'GET'),
      page.getByLabel('绑定 Agent', { exact: true }).selectOption('researcher'),
    ]);
    await page.getByLabel(skillName, { exact: true }).check();
    await page.getByRole('button', { name: '保存绑定', exact: true }).click();
    await until(async () => (await api('GET', '/agents/researcher/skills')).skills.some(item => item.id === skill.id));
  });

  await check('A UI-created schedule uses the real command bridge and avoids overlapping runs', async () => {
    await navigateWorkspace('周期任务');
    await page.getByRole('button', { name: '新建计划', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('计划名称', { exact: true }).fill(scheduleName);
    await dialog.getByLabel('任务要求', { exact: true }).fill('读取绑定技能，执行合成验收并返回成果。');
    await dialog.getByLabel('执行方式', { exact: true }).selectOption('researcher');
    await dialog.getByLabel('重复间隔', { exact: true }).selectOption('3600');
    await dialog.getByRole('button', { name: '保存计划', exact: true }).click();
    schedule = await until(async () => (await api('GET', '/schedules')).schedules.find(item => item.name === scheduleName));
    assert.equal(schedule.interval_seconds, 3600);
    assert(new Date(schedule.next_run_at).getTime() > Date.now());
    await page.getByRole('button', { name: '立即运行：' + scheduleName, exact: true }).click();
    schedule = await until(async () => {
      const current = await api('GET', '/schedules/' + schedule.id);
      return current.last_run_id && current;
    });
    assert.equal(schedule.last_run_status, 'queued');
    const firstRun = schedule.last_run_id;
    await page.getByRole('button', { name: '立即运行：' + scheduleName, exact: true }).click();
    await until(async () => (await api('GET', '/schedules/' + schedule.id)).history.some(item => item.status === 'skipped'));
    assert.equal((await api('GET', '/rooms/' + schedule.room_id)).runs.length, 1);
    await runCommand(1);
    assert.equal(schedule.last_run_id, firstRun);
    const room = await api('GET', '/rooms/' + schedule.room_id);
    assert(room.artifacts.some(item => item.title === '周期任务验收成果'));
    await page.getByText(/最近运行.*已完成/).first().waitFor();
    await page.getByRole('button', { name: '查看记录：' + scheduleName, exact: true }).click();
    await page.getByRole('dialog').getByText('已跳过', { exact: true }).waitFor();
    await page.screenshot({ path: path.join(runDir, 'schedule-dispatch-history.png'), fullPage: true, animations: 'disabled' });
    await page.keyboard.press('Escape');
  });

  await check('Service-owned scheduling dispatches automatically with the latest skill version', async () => {
    await navigateWorkspace('技能库');
    await page.getByRole('button', { name: '编辑技能：' + skillName, exact: true }).click();
    await page.getByLabel('技能内容', { exact: true }).fill('# 周期资料核对\n\nSKILL_QA_V2\n\n新增一次独立核对步骤。\n');
    await page.getByRole('button', { name: '保存技能', exact: true }).click();
    await until(async () => (await api('GET', '/skills/' + skill.id)).version === 2);
    const oldRun = schedule.last_run_id;
    await makeDue();
    schedule = await until(async () => {
      const current = await api('GET', '/schedules/' + schedule.id);
      return current.last_run_id !== oldRun && current;
    }, 20000, 500);
    assert(schedule.history.some(item => item.trigger !== 'manual' && item.status === 'created'));
    assert(new Date(schedule.next_run_at).getTime() > Date.now());
    await runCommand(2);
    assert.equal((await api('GET', '/rooms/' + schedule.room_id)).runs.length, 2);
  });

  await check('Pause, skill archive, resume and schedule archive preserve truthful history', async () => {
    await navigateWorkspace('周期任务');
    await page.getByRole('button', { name: '暂停计划：' + scheduleName, exact: true }).click();
    await until(async () => !(await api('GET', '/schedules/' + schedule.id)).enabled);
    await makeDue();
    const lastRun = schedule.last_run_id;
    for (let index = 0; index < 3; index++) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      assert.equal((await api('GET', '/schedules/' + schedule.id)).last_run_id, lastRun);
    }
    await navigateWorkspace('技能库');
    await page.getByRole('button', { name: '归档技能：' + skillName, exact: true }).click();
    await until(async () => (await api('GET', '/skills/' + skill.id)).archived_at);
    assert((await api('GET', '/skills/' + skill.id + '?version=1')).content.includes('SKILL_QA_V1'));
    await navigateWorkspace('周期任务');
    await page.getByRole('button', { name: '恢复计划：' + scheduleName, exact: true }).click();
    schedule = await until(async () => {
      const current = await api('GET', '/schedules/' + schedule.id);
      return current.last_run_id !== lastRun && current;
    }, 20000, 500);
    await runCommand('archived');
    await page.getByRole('button', { name: '归档计划：' + scheduleName, exact: true }).click();
    const archived = await until(async () => {
      const current = await api('GET', '/schedules/' + schedule.id);
      return current.archived_at && current;
    });
    assert.equal(archived.enabled, false);
    const room = await api('GET', '/rooms/' + archived.room_id);
    assert.equal(room.runs.length, 3);
    assert(room.runs.every(run => run.status === 'completed'));
    assert.equal(room.artifacts.length, 3);
    assert(archived.history.length >= 4);
    assert.deepEqual(pageErrors, []);
  });
}

async function managedExecutorJourney() {
  await page.setViewportSize({ width: 1440, height: 960 });
  const pidFile = path.join(runDir, 'managed-fixture.pid');
  function assertExited(pid) {
    let exists = true;
    try { process.kill(pid, 0); } catch (error) { if (error.code === 'ESRCH') exists = false; else throw error; }
    assert.equal(exists, false, 'The controller left its owned fixture process running.');
  }
  await check('Local executor UI starts and stops one owned process without invoking a model', async () => {
    const closeDrawer = page.getByRole('button', { name: '关闭工作区', exact: true });
    if (await closeDrawer.isVisible()) await closeDrawer.click();
    await page.getByRole('button', { name: '连接 Agent', exact: true }).click();
    await page.getByRole('button', { name: '启动 Codex 执行器', exact: true }).click();
    const state = await until(async () => {
      const status = await api('GET', '/executors/local');
      return status.state === 'running' && status;
    });
    assert.equal(state.local_control, true);
    const pid = Number(fs.readFileSync(pidFile, 'utf8'));
    assert(Number.isInteger(pid) && pid > 0);
    process.kill(pid, 0);
    await api('POST', '/executors/codex/start', {});
    assert.equal(Number(fs.readFileSync(pidFile, 'utf8')), pid);
    assert.equal((await api('GET', '/workers')).workers.filter(item => item.worker_id === 'desktop-codex' && item.connected).length, 1);
    await page.getByRole('button', { name: '停止 Codex 执行器', exact: true }).click();
    await until(async () => (await api('GET', '/executors/local')).state === 'stopped');
    assertExited(pid);
    assert.equal((await api('GET', '/workers')).workers.find(item => item.worker_id === 'desktop-codex').connected, false);
    await page.keyboard.press('Escape');
  });

  await check('Managed executor crash is detected and a clean restart remains possible', async () => {
    await api('POST', '/executors/codex/start', {});
    const failedPid = Number(fs.readFileSync(pidFile, 'utf8'));
    process.kill(failedPid);
    await until(async () => (await api('GET', '/executors/local')).state !== 'running');
    assert.equal((await api('GET', '/workers')).workers.find(item => item.worker_id === 'desktop-codex').connected, false);
    await api('POST', '/executors/codex/start', {});
    const restartedPid = Number(fs.readFileSync(pidFile, 'utf8'));
    assert.notEqual(restartedPid, failedPid);
    assert.equal((await api('GET', '/executors/local')).state, 'running');
    await api('POST', '/executors/codex/stop', {});
    assertExited(restartedPid);
  });

  await check('A managed catalog discovers a new Agent without renewing its session or expanding a fixed external worker', async () => {
    await api('POST', '/executors/codex/start', {});
    const before = (await api('GET', '/workers')).workers.find(item => item.worker_id === 'desktop-codex');
    const fixed = await api('POST', '/workers/register', { worker_id: 'qa-fixed-catalog', name: '固定角色验收执行端', agent_ids: ['writer'], capabilities: ['synthetic-fixture'] });
    const created = await api('POST', '/agents', { name: '新增目录验收员', description: '仅用于目录刷新验收。', instructions: '按调用方授权核对提供的资料。' });
    const updated = await until(async () => {
      const worker = (await api('GET', '/workers')).workers.find(item => item.worker_id === 'desktop-codex');
      return worker.agent_ids.includes(created.id) && worker;
    });
    for (const key of ['registered_at', 'last_seen_at', 'expires_at', 'capabilities']) assert.deepEqual(updated[key], before[key], key + ' changed during catalog discovery.');
    assert.deepEqual((await api('GET', '/workers')).workers.find(item => item.worker_id === 'qa-fixed-catalog').agent_ids, ['writer']);
    assert.equal((await api('GET', '/executors/local')).state, 'running');
    await api('POST', '/executors/codex/stop', {});
    assert.equal((await api('GET', '/workers')).workers.find(item => item.worker_id === 'desktop-codex').connected, false);
    await api('POST', '/workers/qa-fixed-catalog/disconnect', { session_token: fixed.session_token });
    fs.writeFileSync(path.join(runDir, 'managed-catalog-evidence.json'), JSON.stringify({ synthetic_sleeping_child: true, model_invoked: false, before, updated, new_agent_id: created.id, fixed_worker_agent_ids: ['writer'], session_lifetime_unchanged: true }, null, 2));
  });
}

async function filesJourney() {
  await page.setViewportSize({ width: 1440, height: 960 });
  const filename = '品牌资料.txt';
  const fileBytes = Buffer.from('\ufeff品牌资料\r\n' + attachmentMarker + '\r\n这是保留原始字节的附件验收内容。\r\n', 'utf8');
  const imageBytes = fs.readFileSync(path.join(repo, 'examples', 'example.png'));
  const room = await api('POST', '/rooms', { title: '文件、检索与分享验收', text: '参考附件整理一份可以固定版本分享的方案。', agent_id: 'writer' });
  let textFile;
  let imageFile;
  let artifact;
  let publicUrl;

  await check('UI file upload preserves original bytes and distinguishes text from images', async () => {
    await page.goto(base + '/#room/' + room.id);
    await page.getByRole('button', { name: '打开工作区', exact: true }).click();
    await page.getByRole('tab', { name: '资料', exact: true }).click();
    await page.locator('#room-file-input').setInputFiles([
      { name: filename, mimeType: 'text/plain', buffer: fileBytes },
      { name: '图像样本.png', mimeType: 'image/png', buffer: imageBytes },
    ]);
    const files = await until(async () => {
      const data = await api('GET', '/rooms/' + room.id + '/files');
      return data.files.length === 2 && data.files;
    });
    textFile = files.find(item => item.filename === filename);
    imageFile = files.find(item => item.filename === '图像样本.png');
    assert(textFile.source_id);
    assert.equal(textFile.extraction_status, 'extracted');
    assert.equal(textFile.sha256, crypto.createHash('sha256').update(fileBytes).digest('hex'));
    assert.equal(imageFile.extraction_status, 'image_only');
    assert(!JSON.stringify(files).includes('data_base64'));
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('link', { name: '下载附件：' + filename, exact: true }).click(),
    ]);
    const downloaded = path.join(runDir, 'original-attachment.txt');
    await download.saveAs(downloaded);
    assert.equal(download.suggestedFilename(), filename);
    assert.deepEqual(fs.readFileSync(downloaded), fileBytes);
    assert.deepEqual(Buffer.from(await (await fetch(base + '/api/hub/files/' + imageFile.id + '/download')).arrayBuffer()), imageBytes);
    await page.getByText('图片原件 · 未识别文字', { exact: false }).waitFor();
    await page.getByRole('button', { name: '查看提取内容：' + filename, exact: true }).click();
    await page.getByRole('dialog').getByText(attachmentMarker, { exact: false }).waitFor();
    await page.keyboard.press('Escape');
    await page.screenshot({ path: path.join(runDir, 'files-extraction-and-download.png'), fullPage: true, animations: 'disabled' });
    const lease = await claim('qa-files-writer', room.id, 'writer');
    assert(JSON.stringify(lease.context).includes(attachmentMarker));
    const completed = await complete(lease, 'qa-files-writer', { message: '提取的资料已进入任务上下文，方案已保存。', artifacts: [{ title: '可分享验收方案', kind: 'markdown', content: '# 可分享验收方案\n\nSHARE_VERSION_ONE\n\n只公开这一份成果。' }] });
    artifact = completed.artifacts[0];
  });

  await check('Workspace search opens the matching source in its original conversation', async () => {
    const other = await api('POST', '/rooms', { title: '另一个检索入口', text: '这是用于验证跨会话查找的独立入口。', agent_id: 'reviewer' });
    await page.goto(base + '/#room/' + other.id);
    await navigateWorkspace('搜索空间');
    await page.getByLabel('搜索当前空间', { exact: true }).fill(attachmentMarker);
    await page.locator('#space-search-results [data-search-result]').first().waitFor();
    const result = await api('GET', '/search?q=' + attachmentMarker);
    assert(result.results.some(item => item.type === 'source' && item.id === textFile.source_id && item.room_id === room.id));
    await page.screenshot({ path: path.join(runDir, 'workspace-source-search.png'), fullPage: true, animations: 'disabled' });
    await page.getByLabel('搜索当前空间', { exact: true }).press('Enter');
    await page.getByRole('dialog').getByText(attachmentMarker, { exact: false }).waitFor();
    assert(page.url().endsWith('#room/' + room.id));
    await page.keyboard.press('Escape');
    await api('POST', '/runs/' + other.runs[0].id + '/cancel', {});
  });

  await check('Sharing pins the selected artifact version and revocation closes the public URL', async () => {
    await openArtifact('可分享验收方案');
    await page.getByRole('button', { name: '分享成果', exact: true }).click();
    await page.getByRole('dialog').getByText('固定分享版本 1', { exact: true }).waitFor();
    await page.getByLabel('链接有效期', { exact: true }).selectOption('1');
    await page.getByRole('button', { name: '生成查看链接', exact: true }).click();
    publicUrl = await until(async () => page.getByLabel('成果分享链接', { exact: true }).inputValue());
    assert(publicUrl.startsWith(base + '/share/'));
    await page.getByText('这是本机链接，只能在这台电脑上打开。', { exact: true }).waitFor();
    const initial = await fetch(publicUrl);
    assert.equal(initial.status, 200);
    assert(initial.headers.get('content-security-policy').includes('sandbox'));
    const initialContent = await initial.text();
    assert(initialContent.includes('SHARE_VERSION_ONE'));
    assert(!initialContent.includes(attachmentMarker));
    assert(!initialContent.includes(room.id));
    await page.screenshot({ path: path.join(runDir, 'exact-version-sharing.png'), fullPage: true, animations: 'disabled' });
    const guest = await browser.newContext();
    try {
      const publicPage = await guest.newPage();
      await publicPage.goto(publicUrl);
      await publicPage.getByText('SHARE_VERSION_ONE', { exact: false }).first().waitFor();
      await api('PUT', '/artifacts/' + artifact.id, { base_version: 1, title: artifact.title, kind: 'markdown', content: '# 新版方案\n\nSHARE_VERSION_TWO_PRIVATE' });
      await publicPage.reload();
      await publicPage.getByText('SHARE_VERSION_ONE', { exact: false }).first().waitFor();
      assert(!(await publicPage.textContent('body')).includes('SHARE_VERSION_TWO_PRIVATE'));
      await page.getByRole('button', { name: '撤销分享：版本 1', exact: true }).click();
      await until(async () => (await fetch(publicUrl)).status === 404);
      assert.equal((await publicPage.reload()).status(), 404);
    } finally { await guest.close(); }
    await page.keyboard.press('Escape');
    if (process.argv.includes('--accounts')) {
      const freshShare = await api('POST', '/artifacts/' + artifact.id + '/shares', { version: 1, expires_days: 1 });
      sharedAcceptance = { url: new URL(freshShare.url, base).href, artifactId: artifact.id, shareId: freshShare.share.id };
    }
  });

  await check('Removing a file revokes its download and searchable source', async () => {
    await page.getByRole('tab', { name: '资料', exact: true }).click();
    await page.getByRole('button', { name: '移除附件：' + filename, exact: true }).click();
    await until(async () => (await fetch(base + '/api/hub/files/' + textFile.id + '/download')).status === 410);
    assert.equal((await api('GET', '/search?q=' + attachmentMarker)).results.length, 0);
    await api('POST', '/rooms/' + room.id + '/messages', { text: '确认撤销资料不会进入新任务。', agent_id: 'writer' });
    const lease = await claim('qa-files-writer', room.id, 'writer');
    assert(!JSON.stringify(lease.context).includes(attachmentMarker));
    await complete(lease, 'qa-files-writer', { message: '新任务已确认不包含被撤销的资料。' });
    assert.equal((await fetch(base + '/api/hub/files/' + imageFile.id + '/download')).status, 200);
    assert.deepEqual(pageErrors, []);
  });
}

async function collaborationJourney() {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const title = '评论驱动的发布方案';
  const initialContent = '# 发布方案\n\n产品介绍需要补充适用人群。\n\n验收范围：评论、修改与版本回链。';
  const room = await api('POST', '/rooms', { title: '评论与修改闭环验收', text: '整理供协作者讨论和迭代的发布方案。', agent_id: 'writer' });
  const initialLease = await claim('qa-collaboration-initial', room.id, 'writer');
  const initial = await complete(initialLease, 'qa-collaboration-initial', { message: '首版已保存，等待协作者提出修改。', artifacts: [{ title, kind: 'markdown', content: initialContent }] });
  const artifactId = initial.artifacts[0].id;
  let rootComment;
  let firstRequest;
  let firstSnapshot;
  let firstSubmission;
  let conflictRequest;
  let conflictComment;
  let cancelRequest;
  let retryRequest;
  let currentVersion = 1;
  const firstBody = '请具体说明适用人群，并保留现有验收范围。';
  const firstReply = '补充两类读者：采购负责人和渠道运营。';
  const candidateContent = '# 发布方案\n\n候选修订 CANDIDATE_CONTENT_0731。\n\n补充渠道负责人和采购负责人。';

  const thread = id => page.locator('[data-thread-id="' + id + '"]');
  const requestCard = id => page.locator('[data-revision-request-id="' + id + '"]');
  async function collaborationScreenshot(name) {
    await page.locator('#toast').waitFor({ state: 'hidden' });
    await page.screenshot({ path: path.join(runDir, name), fullPage: true, animations: 'disabled' });
  }
  async function commentsAt(version) {
    const dialog = page.getByRole('dialog');
    if (await dialog.isVisible()) await page.keyboard.press('Escape');
    await openArtifact(title);
    await page.getByLabel('成果版本', { exact: true }).selectOption(String(version));
    const toggle = page.getByRole('button', { name: '评论', exact: true });
    if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
    await page.locator('#collaboration-comments-tab').click();
    await page.getByLabel('评论内容', { exact: true }).waitFor();
  }
  async function addRoot(body, quote = '') {
    await page.getByLabel('评论内容', { exact: true }).fill(body);
    await page.getByLabel('评论范围', { exact: true }).selectOption(quote ? 'quote' : 'document');
    if (quote) await page.getByLabel('引用文本', { exact: true }).fill(quote);
    await page.getByRole('button', { name: '发表评论', exact: true }).click();
    return until(async () => (await api('GET', '/artifacts/' + artifactId + '/comments')).comments.find(item => !item.parent_id && item.body === body));
  }
  async function sendRevision(comment, instructions, doubleClick = false) {
    await thread(comment.id).getByRole('checkbox').check();
    await page.getByRole('button', { name: '交给 Agent 修改', exact: true }).click();
    await page.getByLabel('修改说明', { exact: true }).fill(instructions);
    const send = page.getByRole('button', { name: '提交修改请求', exact: true });
    const [response] = await Promise.all([
      page.waitForResponse(result => result.url().endsWith('/api/hub/artifacts/' + artifactId + '/modifications') && result.request().method() === 'POST'),
      doubleClick ? send.dblclick({ delay: 30 }) : send.click(),
    ]);
    assert.equal(response.status(), 201);
    return { request: await response.json(), submission: response.request().postDataJSON() };
  }
  async function resultFor(lease, worker, content, expected) {
    return complete(lease, worker, { message: '已按冻结的讨论提交修改结果。', artifacts: [{ id: artifactId, base_version: lease.context.modification.base_version, title, kind: 'markdown', content }] }, expected);
  }

  await check('Quoted version-one discussion and its reply persist through the browser', async () => {
    await page.goto(base + '/#room/' + room.id);
    await commentsAt(1);
    rootComment = await addRoot(firstBody, '产品介绍需要补充适用人群。');
    assert.equal(rootComment.artifact_version, 1);
    assert.equal(rootComment.anchor.status, 'matched');
    assert.equal(rootComment.anchor.matches, 1);
    await thread(rootComment.id).getByRole('button', { name: '回复评论', exact: true }).click();
    await thread(rootComment.id).getByLabel('回复内容', { exact: true }).fill(firstReply);
    await thread(rootComment.id).getByRole('button', { name: '发送回复', exact: true }).click();
    const reply = await until(async () => (await api('GET', '/artifacts/' + artifactId + '/comments?version=1')).comments.find(item => item.parent_id === rootComment.id));
    assert.equal(reply.body, firstReply);
    assert.equal(reply.artifact_version, 1);
    assert.deepEqual(reply.anchor, rootComment.anchor);
  });

  await check('Submitting selected discussion is idempotent and freezes comments before later edits', async () => {
    const sent = await sendRevision(rootComment, '请按选定讨论修改同一份成果，保留版本记录。', true);
    firstRequest = sent.request;
    firstSubmission = sent.submission;
    firstSnapshot = firstRequest.snapshot;
    assert(firstRequest.task_id && firstRequest.run_id);
    assert.equal(firstRequest.base_version, 1);
    assert.equal(firstSnapshot.baseline.content, initialContent);
    assert(firstSnapshot.comments.some(item => item.body === firstBody));
    assert(firstSnapshot.comments.some(item => item.body === firstReply));
    await page.locator('#collaboration-comments-tab').click();
    await thread(rootComment.id).getByRole('button', { name: '编辑评论', exact: true }).click();
    await thread(rootComment.id).getByLabel('编辑评论内容', { exact: true }).fill('后续编辑 COMMENT_EDITED_AFTER_SEND_0731。');
    await thread(rootComment.id).getByRole('button', { name: '保存评论', exact: true }).click();
    const edited = await until(async () => {
      const current = await api('GET', '/comments/' + rootComment.id);
      return current.body.includes('COMMENT_EDITED_AFTER_SEND_0731') && current;
    });
    await api('POST', '/artifacts/' + artifactId + '/comments', { artifact_version: 1, parent_id: rootComment.id, body: '派发后补充的回复 AFTER_SEND_REPLY_0731。', request_key: 'qa-after-dispatch-reply' }, 201);
    assert.equal(edited.version, rootComment.version + 1);
    const replay = await api('POST', '/artifacts/' + artifactId + '/modifications', firstSubmission, 201);
    assert.equal(replay.id, firstRequest.id);
    assert.equal(replay.task_id, firstRequest.task_id);
    assert.deepEqual(replay.snapshot, firstSnapshot);
    assert.equal((await api('GET', '/artifacts/' + artifactId + '/modifications')).modifications.length, 1);
    assert.equal((await api('GET', '/rooms/' + room.id)).runs.filter(item => item.id === firstRequest.run_id).length, 1);
    await page.reload();
    await commentsAt(1);
    await page.locator('#collaboration-requests-tab').click();
    await requestCard(firstRequest.id).waitFor();
    const refreshed = await api('GET', '/modifications/' + firstRequest.id);
    assert.equal(refreshed.status, 'queued');
    assert.deepEqual(refreshed.snapshot, firstSnapshot);
    await page.locator('#collaboration-comments-tab').click();
  });

  await check('Real task completion links the same artifact version to its request and preserves discussion drafts', async () => {
    const worker = 'qa-comment-revision-worker';
    const lease = await claim(worker, room.id, firstRequest.agent_id);
    assert.equal(lease.task.id, firstRequest.task_id);
    assert.equal(lease.task.modification_request_id, firstRequest.id);
    assert.deepEqual(lease.context.modification, firstSnapshot);
    assert(!JSON.stringify(lease.context.modification).includes('COMMENT_EDITED_AFTER_SEND_0731'));
    assert(!JSON.stringify(lease.context.modification).includes('AFTER_SEND_REPLY_0731'));
    const draft = page.getByLabel('评论内容', { exact: true });
    const unsent = '这是轮询期间仍在编辑的评论草稿，不能被状态刷新替换。';
    await draft.fill(unsent);
    await draft.evaluate(element => { element.focus(); element.setSelectionRange(4, 11); });
    await api('POST', '/tasks/' + lease.task.id + '/heartbeat', { worker_id: worker, lease_token: lease.lease_token, progress: '正在根据已冻结的评论修改。' });
    await page.waitForTimeout(3400);
    assert.equal(await draft.inputValue(), unsent);
    assert.deepEqual(await draft.evaluate(element => ({ focused: document.activeElement === element, start: element.selectionStart, end: element.selectionEnd })), { focused: true, start: 4, end: 11 });
    await resultFor(lease, worker, '# 发布方案\n\n适用人群：采购负责人和渠道运营。\n\n验收范围：评论、修改与版本回链。');
    await until(async () => (await api('GET', '/modifications/' + firstRequest.id)).status === 'completed');
    const version = await api('GET', '/artifacts/' + artifactId + '?version=2');
    assert.equal(version.modification_request_id, firstRequest.id);
    assert.equal(version.source_version, 1);
    assert(version.versions.some(item => item.version === 2 && item.modification_request_id === firstRequest.id));
    assert.equal((await api('GET', '/modifications/' + firstRequest.id)).result_version, 2);
    assert.equal((await api('GET', '/comments/' + rootComment.id)).status, 'open');
    assert.equal((await api('GET', '/artifacts/' + artifactId + '?version=1')).content, initialContent);
    await page.waitForTimeout(3400);
    assert.equal(await draft.inputValue(), unsent);
    assert(await draft.evaluate(element => document.activeElement === element));
    await draft.fill('');
    await page.locator('#collaboration-requests-tab').click();
    await requestCard(firstRequest.id).getByRole('button', { name: '查看结果版本', exact: true }).click();
    await until(async () => (await page.getByLabel('成果版本', { exact: true }).inputValue()) === '2');
    await page.locator('#artifact-modification-origin').click();
    await page.locator('[data-modification-snapshot="' + firstRequest.id + '"]').waitFor();
    await collaborationScreenshot('collaboration-frozen-source.png');
    await commentsAt(1);
    await thread(rootComment.id).getByRole('button', { name: '解决评论', exact: true }).click();
    await until(async () => (await api('GET', '/comments/' + rootComment.id)).status === 'resolved');
    assert.equal((await api('GET', '/artifacts/' + artifactId + '?version=2')).reviews.length, 0);
    currentVersion = 2;
    await page.getByLabel('筛选评论', { exact: true }).selectOption('all');
    await collaborationScreenshot('collaboration-completed-discussion.png');
  });

  await check('A concurrent artifact edit retains the exact candidate without overwriting the newer version', async () => {
    await commentsAt(2);
    conflictComment = await addRoot('在并发修改时保留我的候选内容。', '采购负责人和渠道运营');
    conflictRequest = (await sendRevision(conflictComment, '补充渠道协作安排。')).request;
    const worker = 'qa-candidate-worker';
    const lease = await claim(worker, room.id, conflictRequest.agent_id);
    await api('PUT', '/artifacts/' + artifactId, { base_version: 2, title, kind: 'markdown', content: '# 人工第三版\n\nMANUAL_VERSION_THREE_0731。' });
    await resultFor(lease, worker, candidateContent);
    await resultFor(lease, worker, candidateContent);
    conflictRequest = await api('GET', '/modifications/' + conflictRequest.id);
    assert.equal(conflictRequest.status, 'conflict');
    assert.equal(conflictRequest.task_status, 'completed');
    assert.equal(conflictRequest.result_version, null);
    assert.equal(conflictRequest.candidate.content, candidateContent);
    assert.equal(conflictRequest.candidate.sha256, crypto.createHash('sha256').update(candidateContent).digest('hex'));
    const latest = await api('GET', '/artifacts/' + artifactId);
    assert.equal(latest.version, 3);
    assert(latest.content.includes('MANUAL_VERSION_THREE_0731'));
    await requestCard(conflictRequest.id).getByRole('button', { name: '对比候选版本', exact: true }).click();
    await page.getByRole('dialog').getByText('CANDIDATE_CONTENT_0731', { exact: false }).waitFor();
    await collaborationScreenshot('collaboration-candidate-comparison.png');
    await page.setViewportSize({ width: 390, height: 844 });
    await assertNoOverflow(page);
    const apply = await page.getByRole('button', { name: '应用候选版本', exact: true }).boundingBox();
    assert(apply && apply.x >= 0 && apply.x + apply.width <= 391, 'Candidate apply action overflows mobile viewport.');
    await collaborationScreenshot('collaboration-mobile-candidate.png');
    await page.setViewportSize({ width: 1440, height: 1000 });
  });

  await check('Applying a candidate rechecks the current version and requires a new explicit confirmation after a race', async () => {
    await api('PUT', '/artifacts/' + artifactId, { base_version: 3, title, kind: 'markdown', content: '# 人工第四版\n\nMANUAL_VERSION_FOUR_0731。' });
    const [response] = await Promise.all([
      page.waitForResponse(result => result.url().endsWith('/api/hub/modifications/' + conflictRequest.id + '/apply') && result.request().method() === 'POST'),
      page.getByRole('button', { name: '应用候选版本', exact: true }).click(),
    ]);
    assert.equal(response.status(), 409);
    assert.equal(response.request().postDataJSON().base_version, 3);
    assert.equal((await api('GET', '/artifacts/' + artifactId)).version, 4);
    assert.equal((await api('GET', '/modifications/' + conflictRequest.id)).candidate.content, candidateContent);
    assert(await page.getByRole('button', { name: '应用候选版本', exact: true }).isDisabled());
    await collaborationScreenshot('collaboration-apply-conflict.png');
    // Reading the new baseline is an explicit action; it must not apply the candidate automatically.
    await page.getByRole('button', { name: '读取最新版本重新对比', exact: true }).click();
    await page.getByLabel('当前版本内容', { exact: true }).filter({ hasText: 'MANUAL_VERSION_FOUR_0731' }).waitFor();
    assert.equal((await api('GET', '/artifacts/' + artifactId)).version, 4);
    const [appliedResponse] = await Promise.all([
      page.waitForResponse(result => result.url().endsWith('/api/hub/modifications/' + conflictRequest.id + '/apply') && result.request().method() === 'POST'),
      page.getByRole('button', { name: '应用候选版本', exact: true }).click(),
    ]);
    assert.equal(appliedResponse.status(), 200);
    const applyBody = appliedResponse.request().postDataJSON();
    assert.equal(applyBody.base_version, 4);
    await api('POST', '/modifications/' + conflictRequest.id + '/apply', applyBody);
    const final = await api('GET', '/artifacts/' + artifactId);
    assert.equal(final.version, 5);
    assert.equal(final.content, candidateContent);
    assert.equal(final.modification_request_id, conflictRequest.id);
    assert.equal(final.source_version, 2);
    assert.equal((await api('GET', '/modifications/' + conflictRequest.id)).result_version, 5);
    assert.equal((await api('GET', '/comments/' + conflictComment.id)).status, 'open');
    currentVersion = 5;
  });

  await check('Cancelling a modification rejects late worker output and cannot revive its request', async () => {
    await commentsAt(currentVersion);
    const comment = await addRoot('这条取消的修改不能改变已有成果。');
    cancelRequest = (await sendRevision(comment, '取消边界验收。')).request;
    const worker = 'qa-cancel-modification';
    const lease = await claim(worker, room.id, cancelRequest.agent_id);
    await requestCard(cancelRequest.id).getByRole('button', { name: '取消修改请求', exact: true }).click();
    await until(async () => (await api('GET', '/modifications/' + cancelRequest.id)).status === 'cancelled');
    await resultFor(lease, worker, '# 不允许保存的晚到内容', 409);
    assert.equal((await api('GET', '/artifacts/' + artifactId)).version, currentVersion);
    await api('POST', '/runs/' + cancelRequest.run_id + '/retry', {}, 409);
  });

  await check('Retrying a failed modification keeps the original snapshot and task without duplicating versions', async () => {
    await commentsAt(currentVersion);
    const comment = await addRoot('仅重试失败的这一次修改。');
    retryRequest = (await sendRevision(comment, '保留原请求与讨论快照。')).request;
    const worker = 'qa-retry-modification';
    const failedLease = await claim(worker, room.id, retryRequest.agent_id);
    await api('POST', '/tasks/' + failedLease.task.id + '/fail', { worker_id: worker, lease_token: failedLease.lease_token, error: '合成执行器失败，用于验收重试。' });
    await requestCard(retryRequest.id).getByRole('button', { name: '重试修改请求', exact: true }).click();
    const retried = await claim(worker, room.id, retryRequest.agent_id);
    assert.equal(retried.task.id, failedLease.task.id);
    assert.equal(retried.task.attempts, 2);
    assert.deepEqual(retried.context.modification, failedLease.context.modification);
    await resultFor(retried, worker, '# 发布方案\n\n重试后的第六版，原讨论仍需用户确认。');
    assert.equal((await api('GET', '/artifacts/' + artifactId)).version, 6);
    assert.equal((await api('GET', '/comments/' + comment.id)).status, 'open');
    await requestCard(retryRequest.id).getByRole('button', { name: '查看结果版本', exact: true }).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await assertNoOverflow(page);
    await collaborationScreenshot('collaboration-mobile-requests.png');
    await page.locator('#collaboration-comments-tab').click();
    const mobileDraft = page.getByLabel('评论内容', { exact: true });
    await mobileDraft.fill('手机上的讨论草稿。');
    await mobileDraft.click();
    assert(await mobileDraft.evaluate(element => document.activeElement === element), 'Mobile discussion composer is obscured.');
    await assertNoOverflow(page);
    await collaborationScreenshot('collaboration-mobile-discussion.png');
    await mobileDraft.fill('');
    await page.setViewportSize({ width: 1440, height: 960 });
    assert.deepEqual(pageErrors, []);
    collaborationAcceptance = { roomId: room.id, artifactId, commentId: rootComment.id, requestId: firstRequest.id };
    fs.writeFileSync(path.join(runDir, 'collaboration-evidence.json'), JSON.stringify({ synthetic_worker: true, room: await api('GET', '/rooms/' + room.id), artifact: await api('GET', '/artifacts/' + artifactId), root_comment: await api('GET', '/comments/' + rootComment.id), requests: await Promise.all([firstRequest, conflictRequest, cancelRequest, retryRequest].map(item => api('GET', '/modifications/' + item.id))) }, null, 2));
  });

  await check('Read-only SQLite inspection confirms durable snapshots, request bindings and artifact history', async () => {
    const script = `import json, sqlite3, sys
from pathlib import Path
database = Path(sys.argv[1]).resolve()
conn = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
conn.row_factory = sqlite3.Row
artifact_id, comment_id = sys.argv[2:4]
result = {
    'integrity': conn.execute('PRAGMA integrity_check').fetchone()[0],
    'modifications': [dict(row) for row in conn.execute('SELECT id,task_id,snapshot_json,result_version FROM hub_modifications WHERE artifact_id=? ORDER BY created_at', (artifact_id,))],
    'versions': [dict(row) for row in conn.execute('SELECT version,modification_request_id,source_version FROM hub_artifact_versions WHERE artifact_id=? ORDER BY version', (artifact_id,))],
    'history': [json.loads(row[0]) for row in conn.execute('SELECT snapshot_json FROM hub_comment_versions WHERE comment_id=? ORDER BY version', (comment_id,))],
}
conn.close()
print(json.dumps(result, ensure_ascii=False))
`;
    const evidence = await new Promise((resolve, reject) => {
      const child = spawn(python, ['-X', 'utf8', '-c', script, path.join(dataDir, 'studio.sqlite3'), artifactId, rootComment.id], { cwd: repo, windowsHide: true });
      let stdout = '', stderr = '';
      child.stdout.on('data', data => { stdout += data.toString(); });
      child.stderr.on('data', data => { stderr += data.toString(); });
      child.on('error', reject);
      child.on('exit', code => { if (code) reject(new Error('Read-only fixture database check failed: ' + stderr)); else { try { resolve(JSON.parse(stdout)); } catch (error) { reject(error); } } });
    });
    assert.equal(evidence.integrity, 'ok');
    assert.equal(evidence.modifications.length, 4);
    assert.equal(new Set(evidence.modifications.map(item => item.task_id)).size, 4);
    assert.deepEqual(JSON.parse(evidence.modifications.find(item => item.id === firstRequest.id).snapshot_json), firstSnapshot);
    assert.deepEqual(evidence.versions.map(item => item.version), [1, 2, 3, 4, 5, 6]);
    assert.equal(evidence.versions[1].modification_request_id, firstRequest.id);
    assert.equal(evidence.versions[4].modification_request_id, conflictRequest.id);
    assert.equal(evidence.versions[4].source_version, 2);
    assert(evidence.history.some(item => item.body === firstBody && item.status === 'open'));
    assert.equal(evidence.history.at(-1).status, 'resolved');
    fs.writeFileSync(path.join(runDir, 'collaboration-db-evidence.json'), JSON.stringify(evidence, null, 2));
  });
}

async function runSyntheticWorker(config, { server = base, label = config.worker_id, environment = {}, onChild, timeout = 45000 } = {}) {
  const configPath = path.join(runDir, label + '-config.json');
  fs.writeFileSync(configPath, JSON.stringify(config, null, 2));
  const env = { ...process.env, STUDIO_DATA_DIR: dataDir, PYTHONUTF8: '1' };
  delete env.STUDIO_WORKER_TOKEN;
  delete env.STUDIO_WORKER_SESSION;
  Object.assign(env, environment);
  const child = spawn(python, ['-X', 'utf8', '-m', 'studio.worker', '--config', configPath, '--server', server, '--once'], { cwd: repo, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  if (onChild) onChild(child);
  let log = '';
  child.stdout.on('data', chunk => { log += chunk.toString(); });
  child.stderr.on('data', chunk => { log += chunk.toString(); });
  const exitCode = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { child.kill(); reject(new Error('Synthetic worker exceeded its test timeout: ' + label)); }, timeout);
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('exit', code => { clearTimeout(timer); resolve(code); });
  });
  fs.writeFileSync(path.join(runDir, label + '.log'), log, 'utf8');
  const events = log.split(/\r?\n/).flatMap(line => { try { return [JSON.parse(line)]; } catch { return []; } });
  return { exitCode, log, events };
}

async function streamingWorkerJourney() {
  await check('The real JSONL worker retries a lost event acknowledgement without duplicating its public receipt', async () => {
    const room = await api('POST', '/rooms', { title: '流式事件确认丢失', text: '只验证合成命令的JSONL协议，不调用模型。', agent_id: 'writer' });
    const commandPath = path.join(runDir, 'streaming-success-command.cjs');
    const counterPath = path.join(runDir, 'streaming-success-counter.txt');
    fs.writeFileSync(commandPath, `'use strict';
const fs = require('node:fs');
const assert = require('node:assert/strict');
const envelope = JSON.parse(fs.readFileSync(0, 'utf8'));
assert.equal(envelope.event_protocol, 'agent-workspace/events-v1');
assert(envelope.execution.host_identity);
fs.appendFileSync(process.argv[2], envelope.task.id + '\\n');
const send = record => process.stdout.write(JSON.stringify(record) + '\\n');
const event = (event_id, kind, payload) => send({type:'execution_event',event_id,kind,payload});
event('stream-host', 'host_session', {host:'qa-stream',session_id:'synthetic-stream-session'});
event('stream-progress', 'progress', {message:'这是一条合成的公开进度回执。'});
event('stream-progress', 'progress', {message:'这是一条合成的公开进度回执。'});
event('stream-checkpoint', 'checkpoint', {host:'qa-stream',session_id:'synthetic-stream-session',checkpoint_id:'synthetic-stream-checkpoint'});
send({type:'result',result:{message:'合成流式协议验收已完成。',artifacts:[{title:'流式验收成果',kind:'markdown',content:'# 流式验收\\n\\n只有一个成果版本。'}]}});
`);
    const requests = [];
    let dropped = 0;
    const proxy = http.createServer(async (incoming, outgoing) => {
      try {
        if (!incoming.url.startsWith('/api/hub/')) { outgoing.writeHead(404); outgoing.end(); return; }
        const chunks = [];
        for await (const chunk of incoming) chunks.push(chunk);
        const body = Buffer.concat(chunks);
        const headers = {};
        for (const name of ['content-type', 'authorization']) if (incoming.headers[name]) headers[name] = incoming.headers[name];
        const upstream = await fetch(base + incoming.url, { method: incoming.method, headers, ...(body.length ? { body } : {}), redirect: 'error' });
        const reply = Buffer.from(await upstream.arrayBuffer());
        const eventId = incoming.url.endsWith('/execution/events') ? JSON.parse(body.toString('utf8')).event_id : undefined;
        const record = { path: incoming.url, status: upstream.status, event_id: eventId, payload_sha256: crypto.createHash('sha256').update(body).digest('hex'), dropped: false };
        requests.push(record);
        if (eventId === 'stream-progress' && upstream.ok && dropped === 0) {
          // Persist the event at the real backend, then lose only its acknowledgement.
          dropped++;
          record.dropped = true;
          outgoing.destroy();
          return;
        }
        outgoing.writeHead(upstream.status, { 'content-type': upstream.headers.get('content-type') || 'application/json', 'content-length': reply.length });
        outgoing.end(reply);
      } catch (error) {
        requests.push({ proxy_error: String(error) });
        if (!outgoing.destroyed) { outgoing.writeHead(502); outgoing.end(JSON.stringify({ error: 'Synthetic streaming fault proxy failed.' })); }
      }
    });
    await new Promise((resolve, reject) => { proxy.once('error', reject); proxy.listen(0, '127.0.0.1', resolve); });
    let worker;
    try {
      worker = await runSyntheticWorker({ worker_id: 'qa-stream-success', name: 'JSONL确认丢失合成执行器', room_id: room.id, agent_ids: ['writer'], capabilities: ['synthetic-fixture', 'continuation:qa-stream'], output_protocol: 'jsonl', command: [process.execPath, commandPath, counterPath], cwd: runDir, timeout_seconds: 30 }, { server: 'http://127.0.0.1:' + proxy.address().port, label: 'streaming-success-worker' });
    } finally {
      proxy.closeAllConnections();
      await new Promise(resolve => proxy.close(resolve));
      fs.writeFileSync(path.join(runDir, 'streaming-event-fault-proxy.json'), JSON.stringify({ synthetic: true, dropped_acknowledgements: dropped, requests }, null, 2));
    }
    assert.equal(worker.exitCode, 0, worker.log);
    assert(worker.events.some(item => item.event === 'completed'), worker.log);
    assert.equal(dropped, 1);
    assert(!requests.some(item => item.proxy_error));
    const repeated = requests.filter(item => item.event_id === 'stream-progress');
    assert.equal(repeated.length, 3, 'Expected one network retry and one deliberate duplicate event.');
    assert(repeated.every(item => item.status === 201));
    assert.equal(new Set(repeated.map(item => item.payload_sha256)).size, 1, 'Event retry changed its ownership, expected sequence or content.');
    assert.equal(fs.readFileSync(counterPath, 'utf8').trim().split(/\r?\n/).length, 1);
    const saved = await api('GET', '/rooms/' + room.id);
    const task = saved.runs[0].tasks[0];
    assert.equal(task.status, 'completed');
    assert.equal(task.attempts, 1);
    assert.equal(saved.artifacts.length, 1);
    assert.equal((await api('GET', '/artifacts/' + saved.artifacts[0].id)).versions.length, 1);
    const execution = await api('GET', '/tasks/' + task.id + '/execution');
    const ledger = await api('GET', '/executions/' + execution.id + '/events');
    assert.equal(ledger.events.filter(item => item.event_id === 'stream-progress').length, 1);
    const host = ledger.events.find(item => item.kind === 'host_session');
    const checkpoint = ledger.events.find(item => item.kind === 'checkpoint');
    assert(host.payload.host_identity, 'The worker did not bind the synthetic host receipt to its configuration.');
    assert.equal(checkpoint.payload.host_identity, host.payload.host_identity);
    assert.equal(checkpoint.payload.through_seq, checkpoint.seq - 1, 'The JSONL child must not guess the backend sequence.');
    await openExecutionPage(room.id, task.id);
    await page.locator('[data-execution-event-id="stream-progress"]').waitFor();
    assert.equal(await page.locator('[data-execution-event-id="stream-progress"]').count(), 1);
    await page.screenshot({ path: path.join(runDir, 'execution-jsonl-events.png'), fullPage: true, animations: 'disabled' });
    await page.keyboard.press('Escape');
    fs.writeFileSync(path.join(runDir, 'streaming-success-evidence.json'), JSON.stringify({ synthetic: true, command_invocations: 1, execution, events: ledger.events }, null, 2));
  });

  await check('An unknown JSONL tool result stops the real child process before its next side effect', async () => {
    const room = await api('POST', '/rooms', { title: '流式未知结果停止子进程', text: '合成外部动作仅写本次QA目录，未知回执后不得继续。', agent_id: 'writer' });
    const commandPath = path.join(runDir, 'streaming-unknown-command.cjs');
    const effectPath = path.join(runDir, 'streaming-unknown-effect.txt');
    const latePath = path.join(runDir, 'streaming-forbidden-late-effect.txt');
    fs.writeFileSync(commandPath, `'use strict';
const fs = require('node:fs');
const envelope = JSON.parse(fs.readFileSync(0, 'utf8'));
const event = (event_id, kind, payload) => process.stdout.write(JSON.stringify({type:'execution_event',event_id,kind,payload}) + '\\n');
event('unknown-stream-call', 'tool_call', {call_id:'unknown-stream-effect',tool:'qa_file_counter',retry_safe:false,summary:'仅在隔离QA目录中执行一次写入。'});
fs.appendFileSync(process.argv[2], envelope.task.id + '\\n');
event('unknown-stream-result', 'tool_result', {call_id:'unknown-stream-effect',outcome:'unknown',summary:'合成工具报告结果未知。'});
setTimeout(() => {
  fs.writeFileSync(process.argv[3], 'This write must never happen.');
  process.stdout.write(JSON.stringify({type:'result',result:{message:'This completion must never be submitted.'}}) + '\\n');
}, 4000);
`);
    const started = Date.now();
    const config = { worker_id: 'qa-stream-unknown', name: 'JSONL未知结果合成执行器', room_id: room.id, agent_ids: ['writer'], capabilities: ['synthetic-fixture'], output_protocol: 'jsonl', command: [process.execPath, commandPath, effectPath, latePath], cwd: runDir, timeout_seconds: 20 };
    const worker = await runSyntheticWorker(config, { label: 'streaming-unknown-worker' });
    assert.equal(worker.exitCode, 0, worker.log);
    assert(worker.events.some(item => item.event === 'rejected' && item.status === 409), worker.log);
    assert(!worker.events.some(item => item.event === 'completed'), worker.log);
    const stopElapsed = Date.now() - started;
    await new Promise(resolve => setTimeout(resolve, Math.max(0, 4500 - stopElapsed)));
    assert.equal(fs.existsSync(latePath), false, 'The child continued to an external side effect after reporting unknown.');
    assert.equal(fs.readFileSync(effectPath, 'utf8').trim().split(/\r?\n/).length, 1);
    const saved = await api('GET', '/rooms/' + room.id);
    const task = saved.runs[0].tasks[0];
    assert.equal(task.status, 'failed');
    assert.equal(saved.artifacts.length, 0);
    const execution = await api('GET', '/tasks/' + task.id + '/execution');
    assert.equal(execution.recovery.decision, 'review_required');
    assert.equal(execution.recovery.result_available, false);
    assert.equal(execution.can_retry, false);
    assert(execution.recovery.calls.some(item => item.call_id === 'unknown-stream-effect' && item.state === 'unknown'));
    const replay = await runSyntheticWorker({ ...config, worker_id: 'qa-stream-no-replay' }, { label: 'streaming-unknown-reclaim-worker' });
    assert.equal(replay.exitCode, 0, replay.log);
    assert(!replay.events.some(item => item.event === 'claimed'), 'An automatic worker reclaimed an unresolved tool result.');
    assert.equal(fs.readFileSync(effectPath, 'utf8').trim().split(/\r?\n/).length, 1);
    const ledger = await api('GET', '/executions/' + execution.id + '/events');
    assert.equal(ledger.events.filter(item => item.kind === 'tool_call').length, 1);
    assert.equal(ledger.events.filter(item => item.kind === 'tool_result').length, 1);
    assert(!ledger.events.some(item => item.kind === 'result.ready' || item.kind === 'task.completed'));
    fs.writeFileSync(path.join(runDir, 'streaming-unknown-evidence.json'), JSON.stringify({ synthetic: true, command_effects: 1, forbidden_late_effect: false, worker_stop_elapsed_ms: stopElapsed, execution, events: ledger.events }, null, 2));
  });
}

async function completionAckFaultProbe() {
  const room = await api('POST', '/rooms', { title: '完成确认丢失验收', text: '用合成执行器验证完成回传，不调用任何模型。', agent_id: 'writer' });
  const counterPath = path.join(runDir, 'completion-fault-command-count.txt');
  const commandPath = path.join(runDir, 'completion-fault-command.cjs');
  fs.writeFileSync(commandPath, `'use strict';
const fs = require('node:fs');
const envelope = JSON.parse(fs.readFileSync(0, 'utf8'));
fs.appendFileSync(process.argv[2], envelope.task.id + '\\n');
process.stdout.write(JSON.stringify({message:'合成命令已返回，验收HTTP确认丢失。',artifacts:[{title:'完成回传验收成果',kind:'markdown',content:'# 完成回传验收\\n\\n只有一个持久版本。'}]}));
`);
  const requests = [];
  let dropped = 0;
  const proxy = http.createServer(async (incoming, outgoing) => {
    try {
      if (!incoming.url.startsWith('/api/hub/')) { outgoing.writeHead(404); outgoing.end(); return; }
      const chunks = [];
      for await (const chunk of incoming) chunks.push(chunk);
      const body = Buffer.concat(chunks);
      const headers = {};
      for (const name of ['content-type', 'authorization']) if (incoming.headers[name]) headers[name] = incoming.headers[name];
      const upstream = await fetch(base + incoming.url, { method: incoming.method, headers, ...(body.length ? { body } : {}), redirect: 'error' });
      const reply = Buffer.from(await upstream.arrayBuffer());
      const record = { method: incoming.method, path: incoming.url, status: upstream.status, payload_sha256: crypto.createHash('sha256').update(body).digest('hex'), dropped: false };
      requests.push(record);
      if (incoming.url.endsWith('/complete') && upstream.status === 200 && dropped === 0) {
        // The backend has committed and generated a successful response; only the acknowledgement is lost.
        dropped++;
        record.dropped = true;
        outgoing.destroy();
        return;
      }
      outgoing.writeHead(upstream.status, { 'content-type': upstream.headers.get('content-type') || 'application/json', 'content-length': reply.length });
      outgoing.end(reply);
    } catch (error) {
      requests.push({ proxy_error: String(error) });
      if (!outgoing.destroyed) { outgoing.writeHead(502); outgoing.end(JSON.stringify({ error: 'Synthetic local fault proxy failed.' })); }
    }
  });
  await new Promise((resolve, reject) => { proxy.once('error', reject); proxy.listen(0, '127.0.0.1', resolve); });
  let worker;
  try {
    worker = await runSyntheticWorker({ worker_id: 'qa-completion-fault', name: '完成确认丢失合成执行器', room_id: room.id, agent_ids: ['writer'], capabilities: ['synthetic-fixture'], command: [process.execPath, commandPath, counterPath], cwd: runDir, timeout_seconds: 30 }, { server: 'http://127.0.0.1:' + proxy.address().port, label: 'completion-fault-worker' });
  } finally {
    proxy.closeAllConnections();
    await new Promise(resolve => proxy.close(resolve));
    fs.writeFileSync(path.join(runDir, 'completion-fault-proxy.json'), JSON.stringify({ synthetic: true, dropped_acknowledgements: dropped, requests }, null, 2));
  }
  assert.equal(worker.exitCode, 0, worker.log);
  assert(worker.events.some(item => item.event === 'completed'), worker.log);
  assert.equal(dropped, 1);
  assert(!requests.some(item => item.proxy_error), 'The fault proxy itself failed.');
  const completions = requests.filter(item => item.path?.endsWith('/complete'));
  assert.equal(completions.length, 2, 'Exactly the dropped completion should be retried once.');
  assert(completions.every(item => item.status === 200));
  assert.equal(new Set(completions.map(item => item.payload_sha256)).size, 1, 'Completion retry changed its payload.');
  assert.equal(requests.filter(item => item.path?.endsWith('/fail')).length, 0, 'A lost acknowledgement was incorrectly reported as task failure.');
  assert.equal(fs.readFileSync(counterPath, 'utf8').trim().split(/\r?\n/).length, 1, 'External command was executed more than once.');
  const saved = await api('GET', '/rooms/' + room.id);
  assert.equal(saved.runs[0].tasks[0].attempts, 1);
  assert.equal(saved.runs[0].tasks[0].status, 'completed');
  assert.equal(saved.artifacts.length, 1);
  assert.equal((await api('GET', '/artifacts/' + saved.artifacts[0].id)).versions.length, 1);
  fs.writeFileSync(path.join(runDir, 'completion-fault-result.json'), JSON.stringify({ synthetic: true, room: saved, command_invocations: 1, dropped_acknowledgements: dropped }, null, 2));
  return saved;
}

async function crashAfterResultStaging() {
  const room = await api('POST', '/rooms', { title: '执行器崩溃后的结果恢复', text: '仅用合成命令验证持久结果恢复，不调用模型。', agent_id: 'writer' });
  const counterPath = path.join(runDir, 'staged-result-command-count.txt');
  const commandPath = path.join(runDir, 'staged-result-command.cjs');
  const content = '# 持久结果\n\nPRIVATE_STAGED_RESULT_0842\n\n这是进程崩溃前已准备好的唯一成果。';
  fs.writeFileSync(commandPath, `'use strict';
const fs = require('node:fs');
const envelope = JSON.parse(fs.readFileSync(0, 'utf8'));
fs.appendFileSync(process.argv[2], envelope.task.id + '\\n');
process.stdout.write(JSON.stringify({message:'合成执行器准备完成结果。',artifacts:[{title:'崩溃恢复验收成果',kind:'markdown',content:${JSON.stringify(content)}}]}));
`);
  let child;
  let lease;
  let stagingReceipt;
  let interrupted = false;
  let completionPayload;
  const requests = [];
  const proxy = http.createServer(async (incoming, outgoing) => {
    try {
      if (!incoming.url.startsWith('/api/hub/')) { outgoing.writeHead(404); outgoing.end(); return; }
      const chunks = [];
      for await (const chunk of incoming) chunks.push(chunk);
      const body = Buffer.concat(chunks);
      if (incoming.url.endsWith('/complete')) {
        completionPayload = JSON.parse(body.toString('utf8'));
        interrupted = true;
        requests.push({ path: incoming.url, action: 'killed_worker_before_forwarding_completion', staged_result_seen: !!stagingReceipt });
        child.kill();
        outgoing.destroy();
        return;
      }
      const headers = {};
      for (const name of ['content-type', 'authorization']) if (incoming.headers[name]) headers[name] = incoming.headers[name];
      const upstream = await fetch(base + incoming.url, { method: incoming.method, headers, ...(body.length ? { body } : {}), redirect: 'error' });
      const reply = Buffer.from(await upstream.arrayBuffer());
      requests.push({ path: incoming.url, status: upstream.status });
      if (incoming.url.endsWith('/tasks/claim') && upstream.ok) lease = JSON.parse(reply.toString('utf8'));
      if (incoming.url.endsWith('/heartbeat') && upstream.ok && lease) lease.lease_expires_at = JSON.parse(reply.toString('utf8')).lease_expires_at || lease.lease_expires_at;
      if (incoming.url.endsWith('/execution/result') && upstream.ok) stagingReceipt = JSON.parse(reply.toString('utf8'));
      outgoing.writeHead(upstream.status, { 'content-type': upstream.headers.get('content-type') || 'application/json', 'content-length': reply.length });
      outgoing.end(reply);
    } catch (error) {
      requests.push({ proxy_error: String(error) });
      if (!outgoing.destroyed) { outgoing.writeHead(502); outgoing.end(JSON.stringify({ error: 'Synthetic staging fault proxy failed.' })); }
    }
  });
  await new Promise((resolve, reject) => { proxy.once('error', reject); proxy.listen(0, '127.0.0.1', resolve); });
  const config = { worker_id: 'qa-result-crash', name: '结果落盘后崩溃的合成执行器', room_id: room.id, agent_ids: ['writer'], capabilities: ['synthetic-fixture'], command: [process.execPath, commandPath, counterPath], cwd: runDir, timeout_seconds: 30 };
  try {
    await runSyntheticWorker(config, { server: 'http://127.0.0.1:' + proxy.address().port, label: 'staged-result-crashed-worker', onChild: process => { child = process; } });
  } finally {
    proxy.closeAllConnections();
    await new Promise(resolve => proxy.close(resolve));
    fs.writeFileSync(path.join(runDir, 'staged-result-fault-proxy.json'), JSON.stringify({ synthetic: true, interrupted_before_completion: interrupted, staged_result_seen: !!stagingReceipt, requests }, null, 2));
  }
  assert(interrupted, 'The worker did not reach the completion fault boundary.');
  assert(stagingReceipt?.sha256, 'The worker attempted completion before persisting its final result.');
  assert(lease?.execution?.id);
  assert.equal((await api('GET', '/rooms/' + room.id)).artifacts.length, 0);
  const details = await api('GET', '/tasks/' + lease.task.id + '/execution');
  assert.equal(details.recovery.result_available, true);
  const publicEvents = await api('GET', '/executions/' + lease.execution.id + '/events');
  assert(publicEvents.events.some(event => event.kind === 'result.ready'));
  assert(!JSON.stringify(publicEvents).includes('PRIVATE_STAGED_RESULT_0842'), 'Private final result content leaked into the public event ledger.');
  return { room, config, lease, stagingReceipt, completionPayload, counterPath, content };
}

async function recoverStagedResult(crashed) {
  const { room, config, lease, completionPayload, counterPath, content } = crashed;
  const detail = await api('GET', '/tasks/' + lease.task.id + '/execution');
  assert.notEqual(detail.task_status, 'completed');
  const expiry = Date.parse(lease.lease_expires_at);
  await until(async () => Date.now() > expiry + 300, Math.max(15000, expiry - Date.now() + 15000), 1000);
  // Real elapsed lease expiration, with no test clock or shortened production timeout.
  await api('POST', '/tasks/' + lease.task.id + '/execution/events', { worker_id: config.worker_id, lease_token: lease.lease_token, event_id: 'stale-after-crash', kind: 'progress', payload: { message: '旧执行器不应继续写入。' } }, 409);
  await api('POST', '/tasks/' + lease.task.id + '/complete', completionPayload, 409);
  const recovered = await runSyntheticWorker({ ...config, worker_id: 'qa-result-recover', name: '读取持久结果的恢复执行器' }, { label: 'staged-result-recovered-worker' });
  assert.equal(recovered.exitCode, 0, recovered.log);
  assert(recovered.events.some(item => item.event === 'completed'), recovered.log);
  assert.equal(fs.readFileSync(counterPath, 'utf8').trim().split(/\r?\n/).length, 1, 'The resumed worker reran a command that already had a staged result.');
  const saved = await api('GET', '/rooms/' + room.id);
  assert.equal(saved.runs[0].tasks[0].attempts, 2);
  assert.equal(saved.runs[0].tasks[0].status, 'completed');
  assert.equal(saved.artifacts.length, 1);
  const artifact = await api('GET', '/artifacts/' + saved.artifacts[0].id);
  assert.equal(artifact.content, content);
  assert.equal(artifact.versions.length, 1);
  const execution = await api('GET', '/tasks/' + lease.task.id + '/execution');
  assert.equal(execution.attempts.at(-1).decision, 'submit_result');
  assert.equal(execution.attempts.at(-1).worker_id, 'qa-result-recover');
  await api('POST', '/tasks/' + lease.task.id + '/execution/events', { worker_id: config.worker_id, lease_token: lease.lease_token, event_id: 'stale-after-new-owner', kind: 'progress', payload: { message: '旧租约不能写入新一轮尝试。' } }, 409);
  fs.writeFileSync(path.join(runDir, 'staged-result-recovery.json'), JSON.stringify({ synthetic: true, lease_expiry: 'real elapsed time', command_invocations: 1, execution, artifact }, null, 2));
  return { room: saved, execution, taskId: lease.task.id };
}

async function appendExecution(lease, worker, eventId, kind, payload, expectedSeq, expectedStatus) {
  return api('POST', '/tasks/' + lease.task.id + '/execution/events', { worker_id: worker, lease_token: lease.lease_token, event_id: eventId, kind, payload, ...(expectedSeq === undefined ? {} : { expected_seq: expectedSeq }) }, expectedStatus);
}

async function openExecutionPage(roomId, taskId, target = page) {
  if (await target.getByRole('dialog').isVisible()) await target.keyboard.press('Escape');
  await target.goto(base + '/#room/' + roomId);
  await target.waitForFunction(id => window.workspaceUI?.getCurrentRoom()?.id === id, roomId);
  const toggle = target.getByRole('button', { name: '打开工作区', exact: true });
  if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
  await target.getByRole('tab', { name: '任务', exact: true }).click();
  await target.locator('[data-open-execution="' + taskId + '"]').click();
  await target.locator('#execution-dialog[data-execution-task-id="' + taskId + '"]').waitFor();
  await target.locator('#execution-current-attempt').filter({ hasText: '第 ' }).waitFor();
}

async function executionJourney() {
  await page.setViewportSize({ width: 1440, height: 1000 });
  let crashed;
  let continuingRoom;
  let continuingLease;
  let unknownRoom;
  let unknownLease;
  const continuationWorker = 'qa-continuing-executor';
  const unknownWorker = 'qa-uncertain-executor';
  const hostIdentity = 'qa-execution-host-namespace-0842';
  const continuation = ['continuation:qa-host'];
  const counterPath = path.join(runDir, 'unsafe-effect-count.txt');
  const callId = 'effect-with-lost-receipt';
  const secondaryCall = 'operation-never-performed';
  const claimFor = (roomId, worker) => api('POST', '/tasks/claim', { room_id: roomId, worker_id: worker, agent_ids: ['writer'], capabilities: continuation, host_identity: hostIdentity });
  const detailFor = lease => api('GET', '/tasks/' + lease.task.id + '/execution');
  async function checkpoint(lease, worker, prefix) {
    const host = await appendExecution(lease, worker, prefix + '-host', 'host_session', { host: 'qa-host', session_id: prefix + '-session', host_identity: hostIdentity });
    return appendExecution(lease, worker, prefix + '-checkpoint', 'checkpoint', { host: 'qa-host', session_id: prefix + '-session', host_identity: hostIdentity, checkpoint_id: prefix + '-checkpoint-handle', through_seq: host.event.seq });
  }

  await check('A real worker process is killed only after its final result was durably staged', async () => {
    crashed = await crashAfterResultStaging();
  });

  await check('Execution event retries are idempotent and the browser shows an actual continued attempt', async () => {
    continuingRoom = await api('POST', '/rooms', { title: '继续已有会话的执行验收', text: '验证真实持久执行记录，宿主会话为显式合成回执。', agent_id: 'writer' });
    continuingLease = await claimFor(continuingRoom.id, continuationWorker);
    assert.equal(continuingLease.execution.recovery.decision, 'start');
    const first = await appendExecution(continuingLease, continuationWorker, 'stable-progress-key', 'progress', { message: '这是已记录的合成执行进度。' }, continuingLease.execution.last_seq);
    const duplicate = await appendExecution(continuingLease, continuationWorker, 'stable-progress-key', 'progress', { message: '这是已记录的合成执行进度。' }, continuingLease.execution.last_seq);
    assert.equal(duplicate.event.seq, first.event.seq);
    await appendExecution(continuingLease, continuationWorker, 'stable-progress-key', 'progress', { message: '同一键不能改成另一段内容。' }, undefined, 409);
    await appendExecution(continuingLease, continuationWorker, 'stale-sequence-key', 'progress', { message: '过时序号不应写入。' }, continuingLease.execution.last_seq, 409);
    await checkpoint(continuingLease, continuationWorker, 'continuation');
    await openExecutionPage(continuingRoom.id, continuingLease.task.id);
    await page.locator('[data-execution-event-id="stable-progress-key"]').waitFor();
    assert.equal(await page.locator('[data-execution-event-id="stable-progress-key"]').count(), 1);
    await page.locator('#execution-current-attempt').filter({ hasText: '第 1 次 · 首次执行' }).waitFor();
    await api('POST', '/tasks/' + continuingLease.task.id + '/fail', { worker_id: continuationWorker, lease_token: continuingLease.lease_token, error: '合成宿主中止；已有可续接的宿主回执。' });
    await page.getByRole('button', { name: '重新排队', exact: true }).click();
    await until(async () => (await detailFor(continuingLease)).task_status === 'queued');
    const next = await claimFor(continuingRoom.id, continuationWorker);
    assert.equal(next.task.id, continuingLease.task.id);
    assert.equal(next.execution.id, continuingLease.execution.id);
    assert.equal(next.execution.attempt, 2);
    assert.equal(next.execution.recovery.decision, 'continue');
    assert.equal(next.execution.recovery.checkpoint.session_id, 'continuation-session');
    // Even an exact previously accepted event is rejected once its lease no longer owns the task.
    await appendExecution(continuingLease, continuationWorker, 'stable-progress-key', 'progress', { message: '这是已记录的合成执行进度。' }, undefined, 409);
    await api('POST', '/tasks/' + continuingLease.task.id + '/heartbeat', { worker_id: continuationWorker, lease_token: continuingLease.lease_token }, 409);
    await complete(continuingLease, continuationWorker, { message: '旧执行器不应覆盖第二次尝试。' }, 409);
    await appendExecution(next, continuationWorker, 'continued-progress', 'progress', { message: '合成宿主已接到同一会话的继续指令。' });
    await page.getByRole('button', { name: '刷新执行记录', exact: true }).click();
    await page.locator('#execution-current-attempt').filter({ hasText: '第 2 次 · 继续已有宿主会话' }).waitFor();
    await page.getByRole('tab', { name: '第 1 次执行', exact: true }).click();
    await page.locator('[data-execution-event-id="stable-progress-key"]').waitFor();
    await page.getByRole('tab', { name: '第 2 次执行', exact: true }).click();
    await page.screenshot({ path: path.join(runDir, 'execution-continued-attempt.png'), fullPage: true, animations: 'disabled' });
    await complete(next, continuationWorker, { message: '已完成合成继续流程；这不是对远端宿主真实性的证明。' });
    const all = [];
    let cursor = 0;
    for (;;) {
      const chunk = await api('GET', '/executions/' + next.execution.id + '/events?after=' + cursor + '&limit=2');
      all.push(...chunk.events);
      cursor = chunk.cursor;
      if (!chunk.has_more) break;
    }
    assert.equal(all.filter(item => item.event_id === 'stable-progress-key').length, 1);
    assert.deepEqual(all.map(item => item.seq), all.map((_, index) => index + 1));
    assert(!JSON.stringify(all).includes(continuingLease.lease_token));
    fs.writeFileSync(path.join(runDir, 'execution-continuation-events.json'), JSON.stringify({ synthetic_host_receipt: true, events: all }, null, 2));
  });

  await check('Unknown unsafe tool outcomes block replay and require an explicit human check', async () => {
    unknownRoom = await api('POST', '/rooms', { title: '外部调用未知结果核对', text: '合成故障：隔离计数已经改变，但工具结果回执丢失。', agent_id: 'writer' });
    unknownLease = await claimFor(unknownRoom.id, unknownWorker);
    await checkpoint(unknownLease, unknownWorker, 'before-uncertain-call');
    await appendExecution(unknownLease, unknownWorker, 'secondary-tool-call', 'tool_call', { call_id: secondaryCall, tool: 'fixture_operation', retry_safe: false, summary: '合成调用已排入宿主，但尚未实际执行。' });
    await appendExecution(unknownLease, unknownWorker, 'unsafe-tool-call', 'tool_call', { call_id: callId, tool: 'fixture_counter', retry_safe: false, summary: '合成操作只写入本次隔离QA目录中的计数文件。' });
    fs.appendFileSync(counterPath, 'one-real-fixture-effect\n');
    await openExecutionPage(unknownRoom.id, unknownLease.task.id);
    const active = await detailFor(unknownLease);
    assert.equal(active.active, true);
    assert.equal(active.can_reconcile, false);
    assert.notEqual(active.recovery.decision, 'review_required', 'An active pending tool call was incorrectly declared interrupted.');
    await api('POST', '/executions/' + active.id + '/reconcile', { request_key: 'cannot-review-live-lease', expected_seq: active.last_seq, call_id: callId, decision: 'confirmed_succeeded', note: '活跃租约期间不能核对覆盖执行状态。' }, 409);
    await complete(unknownLease, unknownWorker, { message: '尚有未知调用，不能宣称完成。' }, 409);
    await appendExecution(unknownLease, unknownWorker, 'unsafe-tool-unknown', 'tool_result', { call_id: callId, outcome: 'unknown', summary: '合成故障已丢失调用结果回执，需核对实际状态。' });
    const blocked = await detailFor(unknownLease);
    assert.equal(blocked.task_status, 'failed');
    assert.equal(blocked.recovery.decision, 'review_required');
    assert.equal(blocked.can_retry, false);
    assert(blocked.recovery.calls.some(item => item.call_id === callId && item.state === 'unknown'));
    await api('POST', '/runs/' + unknownRoom.runs[0].id + '/retry', {}, 409);
    await appendExecution(unknownLease, unknownWorker, 'after-unknown', 'progress', { message: '被中止的租约不能继续写入。' }, undefined, 409);
    const forbiddenCommand = path.join(runDir, 'must-not-replay-unknown.cjs');
    fs.writeFileSync(forbiddenCommand, "require('node:fs').appendFileSync(process.argv[2], 'UNSAFE_REPLAY\\n'); process.stdout.write(JSON.stringify({message:'This command must never run.'}));");
    const probe = await runSyntheticWorker({ worker_id: 'qa-no-unsafe-replay', name: '未知调用禁止重放验收', room_id: unknownRoom.id, agent_ids: ['writer'], capabilities: ['synthetic-fixture'], command: [process.execPath, forbiddenCommand, counterPath], cwd: runDir, timeout_seconds: 10 }, { label: 'unknown-replay-probe' });
    assert.equal(probe.exitCode, 0, probe.log);
    assert(!probe.events.some(item => item.event === 'claimed'), 'Automatic worker reclaimed a task with an unresolved external outcome.');
    assert.equal(fs.readFileSync(counterPath, 'utf8'), 'one-real-fixture-effect\n');
    await page.getByRole('button', { name: '刷新执行记录', exact: true }).click();
    await page.locator('#execution-task-status').filter({ hasText: '核对' }).waitFor();
    assert(!await page.getByRole('button', { name: '重新排队', exact: true }).isVisible());
    await page.getByRole('button', { name: '核对外部调用', exact: true }).click();
    await page.getByLabel('需要核对的调用', { exact: true }).selectOption(callId);
    await page.locator('input[name="execution-decision"][value="confirmed_succeeded"]').check();
    const note = page.getByLabel('核对说明', { exact: true });
    const evidence = '已检查隔离目录的计数文件，只有一次真实合成写入；不重复执行该操作。';
    await note.fill(evidence);
    await note.evaluate(element => { element.focus(); element.setSelectionRange(4, 11); });
    const beforeReview = await detailFor(unknownLease);
    await api('POST', '/executions/' + beforeReview.id + '/reconcile', { request_key: 'secondary-call-confirmed-absent', expected_seq: beforeReview.last_seq, call_id: secondaryCall, decision: 'confirmed_not_executed', note: '另一位合成验收操作者核对后确认第二项没有发生外部动作。' });
    await page.locator('[data-execution-event-kind="recovery.reviewed"]').filter({ hasText: '另一位合成验收操作者核对后确认第二项没有发生外部动作。' }).waitFor();
    assert.equal(await note.inputValue(), evidence);
    assert.deepEqual(await note.evaluate(element => ({ focused: document.activeElement === element, start: element.selectionStart, end: element.selectionEnd })), { focused: true, start: 4, end: 11 });
    await assertControlNotClipped(page.getByRole('button', { name: '保存核对结果', exact: true }));
    await page.screenshot({ path: path.join(runDir, 'execution-unknown-tool-review.png'), fullPage: true, animations: 'disabled' });
    await page.setViewportSize({ width: 390, height: 844 });
    await note.scrollIntoViewIfNeeded();
    await assertNoOverflow(page);
    await assertControlNotClipped(page.getByRole('button', { name: '保存核对结果', exact: true }));
    await page.screenshot({ path: path.join(runDir, 'execution-mobile-unknown-review.png'), fullPage: true, animations: 'disabled' });
    await page.setViewportSize({ width: 1440, height: 1000 });
    const [reviewResponse] = await Promise.all([
      page.waitForResponse(response => response.url().endsWith('/api/hub/executions/' + beforeReview.id + '/reconcile') && response.request().method() === 'POST'),
      page.getByRole('button', { name: '保存核对结果', exact: true }).click(),
    ]);
    assert.equal(reviewResponse.status(), 200);
    const reviewPayload = reviewResponse.request().postDataJSON();
    const reviewed = await reviewResponse.json();
    assert(reviewed.recovery.calls.some(item => item.call_id === callId && item.state === 'succeeded'));
    assert.equal(reviewed.can_retry, true);
    await api('POST', '/executions/' + reviewed.id + '/reconcile', reviewPayload);
    await api('POST', '/executions/' + reviewed.id + '/reconcile', { ...reviewPayload, note: '同一键不允许换另一份核对说明。' }, 409);
    const ledger = await api('GET', '/executions/' + reviewed.id + '/events');
    const savedReview = ledger.events.find(item => item.kind === 'recovery.reviewed' && JSON.stringify(item.payload).includes(evidence));
    assert(savedReview && 'author_id' in savedReview && 'author_name' in savedReview);
    await page.getByRole('button', { name: '重新排队', exact: true }).click();
    await until(async () => (await detailFor(unknownLease)).task_status === 'queued');
    const restarted = await claimFor(unknownRoom.id, unknownWorker);
    assert.equal(restarted.execution.attempt, 2);
    assert.equal(restarted.execution.recovery.decision, 'restart', 'A checkpoint preceding the unknown call and human review was incorrectly reused.');
    await page.getByRole('button', { name: '刷新执行记录', exact: true }).click();
    await page.locator('#execution-current-attempt').filter({ hasText: '第 2 次 · 重新执行' }).waitFor();
    assert.equal(fs.readFileSync(counterPath, 'utf8'), 'one-real-fixture-effect\n');
    await complete(restarted, unknownWorker, { message: '已读取人工核对结果；合成外部动作没有被再次执行。' });
    executionAcceptance = { taskId: unknownLease.task.id, executionId: unknownLease.execution.id, roomId: unknownRoom.id };
    fs.writeFileSync(path.join(runDir, 'execution-reviewed-restart.json'), JSON.stringify({ synthetic: true, counter: 1, execution: await detailFor(restarted), review_note: evidence, events: ledger.events }, null, 2));
  });

  await check('Another member resolving the selected call cannot transfer its draft decision onto a different call', async () => {
    const room = await api('POST', '/rooms', { title: '并发核对的目标归属', text: '两项未执行的合成调用，用于验证核对目标不能被轮询替换。', agent_id: 'writer' });
    const worker = 'qa-review-selection-race';
    const lease = await claimFor(room.id, worker);
    const firstCall = 'selection-call-a', nextCall = 'selection-call-b';
    for (const id of [firstCall, nextCall]) await appendExecution(lease, worker, 'start-' + id, 'tool_call', { call_id: id, tool: 'fixture_not_executed', retry_safe: false, summary: '此合成调用没有执行实际外部动作。' });
    await appendExecution(lease, worker, 'unknown-selection-call', 'tool_result', { call_id: nextCall, outcome: 'unknown', summary: '合成回执丢失，两个调用都仍待核对。' });
    await openExecutionPage(room.id, lease.task.id);
    await page.getByRole('button', { name: '核对外部调用', exact: true }).click();
    const selection = page.getByLabel('需要核对的调用', { exact: true });
    const note = page.getByLabel('核对说明', { exact: true });
    await selection.selectOption(firstCall);
    await page.locator('input[name="execution-decision"][value="confirmed_not_executed"]').check();
    const firstNote = '仅属于调用A的核对草稿，不能自动用于调用B。';
    await note.fill(firstNote);
    const before = await detailFor(lease);
    await api('POST', '/executions/' + before.id + '/reconcile', { request_key: 'other-member-reviewed-call-a', expected_seq: before.last_seq, call_id: firstCall, decision: 'confirmed_not_executed', note: '另一位合成操作者已经核对A没有执行。' });
    await page.locator('[data-execution-event-kind="recovery.reviewed"]').filter({ hasText: '另一位合成操作者已经核对A没有执行。' }).waitFor();
    assert.equal(await selection.inputValue(), '', 'Polling silently changed the selected call to another unresolved call.');
    assert.equal(await page.locator('input[name="execution-decision"]:checked').count(), 0, 'A decision for the previous call remained selected.');
    assert.equal(await note.inputValue(), firstNote, 'The unsent explanation was lost.');
    let unexpectedSubmissions = 0;
    const capture = request => { if (request.method() === 'POST' && request.url().endsWith('/api/hub/executions/' + before.id + '/reconcile')) unexpectedSubmissions++; };
    page.on('request', capture);
    try {
      const submit = page.getByRole('button', { name: '保存核对结果', exact: true });
      if (!await submit.isDisabled()) await submit.click();
      await page.waitForTimeout(150);
      assert.equal(unexpectedSubmissions, 0, 'The previous draft was submitted against a different call.');
    } finally { page.off('request', capture); }
    await page.screenshot({ path: path.join(runDir, 'execution-resolved-selection-preserves-draft.png'), fullPage: true, animations: 'disabled' });
    await selection.selectOption(nextCall);
    await page.locator('input[name="execution-decision"][value="confirmed_not_executed"]').check();
    const secondNote = '已重新选择调用B并独立核对，没有执行外部动作。';
    await note.fill(secondNote);
    const [response] = await Promise.all([
      page.waitForResponse(result => result.url().endsWith('/api/hub/executions/' + before.id + '/reconcile') && result.request().method() === 'POST'),
      page.getByRole('button', { name: '保存核对结果', exact: true }).click(),
    ]);
    assert.equal(response.status(), 200);
    assert.equal(response.request().postDataJSON().call_id, nextCall);
    assert.equal(response.request().postDataJSON().note, secondNote);
    await api('POST', '/runs/' + room.runs[0].id + '/cancel', {});
  });

  await check('After real lease expiry a new worker submits the staged result without running the command again', async () => {
    const recovered = await recoverStagedResult(crashed);
    await openExecutionPage(recovered.room.id, recovered.taskId);
    await page.locator('#execution-current-attempt').filter({ hasText: '第 2 次 · 提交已保存的结果' }).waitFor();
    await page.screenshot({ path: path.join(runDir, 'execution-staged-result-recovery.png'), fullPage: true, animations: 'disabled' });
    await page.keyboard.press('Escape');
    assert.deepEqual(pageErrors, []);
  });

  await check('A committed result survives a genuinely lost HTTP acknowledgement without replaying the command', completionAckFaultProbe);

  await streamingWorkerJourney();

  await check('Read-only SQLite inspection confirms durable execution attempts, event ordering and a single staged result', async () => {
    const sessionIds = [continuingLease.execution.id, unknownLease.execution.id, crashed.lease.execution.id];
    const script = `import json, sqlite3, sys
from pathlib import Path
database = Path(sys.argv[1]).resolve()
conn = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
conn.row_factory = sqlite3.Row
sessions = []
for sid in sys.argv[2:]:
    sessions.append({
        'id': sid,
        'attempts': [dict(row) for row in conn.execute('SELECT attempt,worker_id,decision,reason FROM hub_execution_attempts WHERE session_id=? ORDER BY attempt', (sid,))],
        'events': [dict(row) for row in conn.execute('SELECT seq,event_id,kind,attempt FROM hub_execution_events WHERE session_id=? ORDER BY seq', (sid,))],
        'calls': [dict(row) for row in conn.execute('SELECT call_id,state,call_seq,result_seq,review_seq FROM hub_execution_calls WHERE session_id=? ORDER BY call_seq', (sid,))],
        'results': [dict(row) for row in conn.execute('SELECT sha256,bytes,event_seq FROM hub_execution_results WHERE session_id=?', (sid,))],
        'reviews': [dict(row) for row in conn.execute('SELECT request_key,event_seq FROM hub_execution_reviews WHERE session_id=? ORDER BY event_seq', (sid,))],
    })
result = {'integrity': conn.execute('PRAGMA integrity_check').fetchone()[0], 'sessions': sessions}
conn.close()
print(json.dumps(result, ensure_ascii=False))
`;
    const evidence = await new Promise((resolve, reject) => {
      const child = spawn(python, ['-X', 'utf8', '-c', script, path.join(dataDir, 'studio.sqlite3'), ...sessionIds], { cwd: repo, windowsHide: true });
      let stdout = '', stderr = '';
      child.stdout.on('data', data => { stdout += data.toString(); });
      child.stderr.on('data', data => { stderr += data.toString(); });
      child.on('error', reject);
      child.on('exit', code => { if (code) reject(new Error('Read-only execution database check failed: ' + stderr)); else { try { resolve(JSON.parse(stdout)); } catch (error) { reject(error); } } });
    });
    assert.equal(evidence.integrity, 'ok');
    assert.deepEqual(evidence.sessions.map(item => item.attempts.map(attempt => attempt.decision)), [['start', 'continue'], ['start', 'restart'], ['start', 'submit_result']]);
    for (const session of evidence.sessions) {
      assert.deepEqual(session.events.map(item => item.seq), session.events.map((_, index) => index + 1));
      assert.equal(new Set(session.events.map(item => item.event_id)).size, session.events.length);
      assert.equal(session.events.filter(item => item.kind === 'task.completed').length, 1);
    }
    assert.equal(evidence.sessions[0].events.filter(item => item.event_id === 'stable-progress-key').length, 1);
    assert.equal(evidence.sessions[1].reviews.length, 2);
    assert(evidence.sessions[1].calls.every(item => item.review_seq && ['succeeded', 'not_executed'].includes(item.state)));
    assert.equal(evidence.sessions[2].results.length, 1);
    assert.equal(evidence.sessions[2].results[0].sha256, crashed.stagingReceipt.sha256);
    assert.equal(evidence.sessions[2].events.filter(item => item.kind === 'result.ready').length, 1);
    fs.writeFileSync(path.join(runDir, 'execution-db-evidence.json'), JSON.stringify(evidence, null, 2));
  });
}

function serverCommand(port) {
  if (!wantsManaged) return ['-X', 'utf8', '-m', 'studio.server', '--port', String(port)];
  const wrapper = path.join(runDir, 'managed-fixture-server.py');
  fs.writeFileSync(wrapper, `import os, subprocess, sys, threading, types
from pathlib import Path
sys.path.insert(0, os.environ['WORKSPACE_QA_SOURCE'])
from studio import executors, server
real_subprocess = subprocess
class FixtureSubprocess:
    CREATE_NO_WINDOW = getattr(real_subprocess, 'CREATE_NO_WINDOW', 0)
    DEVNULL = real_subprocess.DEVNULL
    TimeoutExpired = real_subprocess.TimeoutExpired
    def run(self, args, **kwargs):
        if args[1:] != ['login', 'status']:
            raise RuntimeError('Unexpected command in executor-control fixture')
        return real_subprocess.CompletedProcess(args, 0, b'Synthetic login fixture', b'')
    def Popen(self, args, **kwargs):
        if '-m' not in args or args[args.index('-m') + 1] != 'studio.worker':
            raise RuntimeError('Unexpected managed process command')
        child = real_subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'], **kwargs)
        Path(os.environ['WORKSPACE_QA_PID']).write_text(str(child.pid), encoding='utf-8')
        return child
executors.subprocess = FixtureSubprocess()
executors.shutil = types.SimpleNamespace(which=lambda name: sys.executable if name == 'codex' else None)
class FixtureHandler(server.Handler):
    def do_POST(self):
        if self.path == '/__qa_shutdown':
            if not self._allowed(writing=True):
                return
            self._send(200, {'ok': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            super().do_POST()
server.Handler = FixtureHandler
server.main()
`, 'utf8');
  return ['-X', 'utf8', wrapper, '--port', String(port)];
}

async function visualJourney() {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(base);
  await page.getByLabel('需求输入', { exact: true }).waitFor();
  await page.screenshot({ path: path.join(runDir, 'reference-home.png'), fullPage: true, animations: 'disabled' });
  await api('POST', '/agents', { name: '视觉审校', description: '检查版面层级、可读性与不同屏幕的使用体验。', instructions: '根据实际预览核对视觉层级与交互；记录可复现的问题。' });
  await page.reload();
  await check('Reference-sized agent directory has working search and three readable columns', async () => {
    await page.getByRole('button', { name: '我的 Agent', exact: true }).first().click();
    await page.getByRole('heading', { name: '视觉审校', exact: true }).waitFor();
    await assertNoOverflow(page);
    const columns = await page.locator('#agent-grid').evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length);
    assert.equal(columns, 3);
    await page.screenshot({ path: path.join(runDir, 'reference-agents.png'), fullPage: true, animations: 'disabled' });
    await page.getByLabel('搜索 Agent', { exact: true }).fill('视觉审校');
    assert.equal(await page.locator('#agent-grid > article').count(), 1);
    await page.getByLabel('搜索 Agent', { exact: true }).fill('');
  });
  const template = await api('POST', '/templates', {
    name: '产品上新协作', description: '研究、内容和视觉设计并行推进，统一审阅交付。',
    steps: [
      { key: 'research', agent_id: 'researcher', title: '梳理产品与用户', instructions: '整理产品资料与目标用户的问题。', depends_on: [] },
      { key: 'write', agent_id: 'writer', title: '编写产品叙事', instructions: '根据研究结论完成内容初稿。', depends_on: ['research'] },
      { key: 'design', agent_id: 'designer', title: '设计视觉结构', instructions: '根据资料确定版式和信息层级。', depends_on: ['research'] },
      { key: 'review', agent_id: 'reviewer', title: '完成联合审阅', instructions: '核对内容和视觉成果，记录交付建议。', depends_on: ['write', 'design'] },
    ],
  });
  await check('Workflow library and editable dependency diagram match their saved data', async () => {
    await page.getByRole('button', { name: '工作流', exact: true }).first().click();
    await page.getByRole('button', { name: '编辑工作流：产品上新协作', exact: true }).waitFor();
    await page.screenshot({ path: path.join(runDir, 'reference-teams.png'), fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: '编辑工作流：产品上新协作', exact: true }).click();
    await page.getByRole('dialog').waitFor();
    assert.equal(await page.locator('#workflow-canvas [data-select-step]').count(), template.steps.length);
    await page.screenshot({ path: path.join(runDir, 'reference-flow-editor.png'), fullPage: true, animations: 'disabled' });
    await page.keyboard.press('Escape');
  });
  const content = '# 春季新品上市计划\n\n以一份共同维护的计划，串联产品研究、内容制作和视觉交付。\n\n## 目标与交付\n\n明确新品的核心价值，形成可审阅的产品叙事、页面结构和渠道内容。每项成果保留来源与版本，便于继续修改。\n\n## 研究先行\n\n- 整理产品规格、材料和适用场景。\n- 收集目标用户常见的问题。\n- 区分已确认信息和待补充的假设。\n\n## 内容与设计并行\n\n内容编辑完成标题、产品介绍和行动提示；设计师同步整理页面层级、图片位置与移动端布局。两条工作线共享研究结论。\n\n## 审阅与下一步\n\n联合核对事实、语言和版面。确认具体成果版本后，再决定是否分享给其他成员。\n';
  const room = await api('POST', '/rooms', { title: '春季新品上市计划', text: '整理一份新品上市计划，让研究、内容和视觉设计一起推进。', agent_id: 'writer' });
  const lease = await claim('qa-visual-fixture', room.id, 'writer');
  const completed = await complete(lease, 'qa-visual-fixture', { message: '方案已保存。可以在右侧查看内容、继续编辑，或对这个版本提出审阅意见。', artifacts: [{ title: '春季新品上市计划', kind: 'markdown', content }] });
  const artifact = completed.artifacts[0];
  await api('POST', '/artifacts/' + artifact.id + '/reviews', { version: 1, decision: 'approved', comment: '此处为界面验收资料，用于检查预览、版本和审阅布局。' });
  await check('Studio and mobile layouts keep the conversation and artifact usable', async () => {
    await page.goto(base + '/#room/' + room.id);
    await openArtifact('春季新品上市计划');
    await page.getByRole('heading', { name: '目标与交付', exact: true }).waitFor();
    await assertNoOverflow(page);
    await page.screenshot({ path: path.join(runDir, 'reference-studio.png'), fullPage: true, animations: 'disabled' });
    await page.setViewportSize({ width: 390, height: 844 });
    await assertNoOverflow(page);
    const mobileArtifactTitle = await page.locator('#artifact-title').boundingBox();
    assert.equal(await page.locator('#artifact-list').isVisible(), false, 'The single loaded document has a redundant file-switching row.');
    assert(mobileArtifactTitle && mobileArtifactTitle.y >= 0 && mobileArtifactTitle.y + mobileArtifactTitle.height < 844);
    await page.screenshot({ path: path.join(runDir, 'reference-mobile-studio.png'), fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: '关闭工作区', exact: true }).click();
    await page.getByLabel('需求输入', { exact: true }).click();
    await page.screenshot({ path: path.join(runDir, 'reference-mobile-chat.png'), fullPage: true, animations: 'disabled' });
  });
  await check('Readonly members receive an understandable existing-workspace entry', async () => {
    await page.context().request.post(base + '/api/account/setup', { data: { email: 'visual-owner@example.test', name: '产品负责人', password: 'Synthetic-visual-only-4729!', workspace_name: '产品协作空间' } });
    const invitationResponse = await page.context().request.post(base + '/api/account/invitations', { data: { email: 'visual-viewer@example.test', role: 'viewer' } });
    assert(invitationResponse.ok());
    const invitation = await invitationResponse.json();
    const readonlyContext = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    try {
      const acceptance = await readonlyContext.request.post(base + '/api/account/invitations/accept', { data: { token: invitation.token, email: 'visual-viewer@example.test', name: '审阅成员', password: 'Synthetic-visual-only-4729!' } });
      assert(acceptance.ok());
      const readonlyPage = await readonlyContext.newPage();
      readonlyPage.on('pageerror', error => pageErrors.push(error.message));
      await readonlyPage.goto(base);
      await readonlyPage.getByText('方案已保存。可以在右侧查看内容、继续编辑，或对这个版本提出审阅意见。', { exact: false }).waitFor();
      assert.equal(await readonlyPage.getByRole('button', { name: '新建对话', exact: true }).isVisible(), false);
      await assertNoOverflow(readonlyPage);
      await readonlyPage.screenshot({ path: path.join(runDir, 'reference-readonly-member.png'), fullPage: true, animations: 'disabled' });
    } finally { await readonlyContext.close(); }
    assert.deepEqual(pageErrors, []);
  });
}

async function main() {
  testedAssets = assetHashes();
  if (wantsStreaming) testedExecutionRuntime = executionRuntimeHashes();
  if (wantsManaged) testedManagedRuntime = managedRuntimeHashes();
  const port = await freePort();
  base = 'http://127.0.0.1:' + port;
  service = spawn(python, serverCommand(port), {
    cwd: repo,
    env: { ...process.env, STUDIO_DATA_DIR: dataDir, PYTHONUTF8: '1', WORKSPACE_QA_SOURCE: repo, WORKSPACE_QA_PID: path.join(runDir, 'managed-fixture.pid') },
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  service.on('error', error => { serverLog += error.stack + '\n'; });
  service.stdout.on('data', chunk => { serverLog += chunk.toString(); });
  service.stderr.on('data', chunk => { serverLog += chunk.toString(); });
  await until(async () => {
    if (service.exitCode !== null) throw new Error('Test server exited: ' + serverLog);
    try { return (await fetch(base + '/api/hub/bootstrap')).ok; } catch { return false; }
  });
  browser = await playwright.chromium.launch({ executablePath, headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, acceptDownloads: true });
  page = await context.newPage();
  page.on('pageerror', error => pageErrors.push(error.message));
  page.setDefaultTimeout(15000);
  if (process.argv.includes('--managed-only')) {
    await page.goto(base);
    await page.locator('#initial-loading').waitFor({ state: 'hidden' });
    await managedExecutorJourney();
    if (process.argv.includes('--accounts')) await accountJourney();
    await check('Managed controller resources stayed unchanged during verification', async () => {
      assert.deepEqual(assetHashes(), testedAssets);
      assert.deepEqual(managedRuntimeHashes(), testedManagedRuntime);
      assert.deepEqual(pageErrors, []);
    });
    return;
  }
  if (process.argv.includes('--messenger-api-only')) {
    await messengerSummaryJourney();
    return;
  }
  if (process.argv.includes('--messenger-navigation-only')) {
    await messengerSummaryJourney();
    await messengerExplicitNavigationJourney();
    await check('Messenger navigation assets stayed unchanged during verification', async () => {
      assert.deepEqual(assetHashes(), testedAssets);
      assert.deepEqual(pageErrors, []);
    });
    return;
  }
  if (process.argv.includes('--messenger-responsive-only')) {
    await messengerSummaryJourney();
    await messengerResponsiveJourney();
    await check('Messenger responsive assets stayed unchanged during verification', async () => {
      assert.deepEqual(assetHashes(), testedAssets);
      assert.deepEqual(pageErrors, []);
    });
    return;
  }
  if (process.argv.includes('--messenger-only')) {
    await messengerEmptyJourney();
    await messengerSummaryJourney();
    await messengerBrowserJourney();
    await studioSwitchingJourney();
    await messengerResponsiveJourney();
    if (wantsManaged) await managedExecutorJourney();
    if (process.argv.includes('--accounts')) await accountJourney();
    await check('Messenger assets stayed unchanged during browser verification', async () => {
      assert.deepEqual(assetHashes(), testedAssets);
      if (testedManagedRuntime) assert.deepEqual(managedRuntimeHashes(), testedManagedRuntime);
      assert.deepEqual(pageErrors, []);
    });
    return;
  }
  if (process.argv.includes('--visual-only')) {
    await visualJourney();
    await check('Visual resources stayed unchanged during browser verification', async () => assert.deepEqual(assetHashes(), testedAssets));
    return;
  }
  if (process.argv.includes('--team-boundaries-only')) {
    await teamBoundaryJourney();
    return;
  }
  if (process.argv.includes('--team-collaboration-only')) {
    await teamBoundaryJourney();
    await teamCollaborationJourney();
    if (process.argv.includes('--accounts')) await accountJourney();
    await check('Team collaboration resources stayed unchanged during browser verification', async () => assert.deepEqual(assetHashes(), testedAssets));
    return;
  }
  if (process.argv.includes('--teams-only')) {
    await teamBoundaryJourney();
    await teamBrowserJourney();
    if (wantsCollaboration) await teamCollaborationJourney();
    if (process.argv.includes('--accounts')) await accountJourney();
    await check('Team UI resources stayed unchanged during browser verification', async () => assert.deepEqual(assetHashes(), testedAssets));
    return;
  }
  if (process.argv.includes('--artifact-rebase-only')) {
    await artifactRebaseRaceJourney();
    await check('Artifact editing resources stayed unchanged during verification', async () => {
      assert.deepEqual(assetHashes(), testedAssets);
      assert.deepEqual(pageErrors, []);
    });
    return;
  }
  if (process.argv.includes('--completion-fault-only')) {
    await check('A committed result survives a genuinely lost HTTP acknowledgement without replaying the command', completionAckFaultProbe);
    return;
  }
  if (process.argv.includes('--streaming-only')) {
    await streamingWorkerJourney();
    await check('Streaming runtime and visual resources stayed unchanged during verification', async () => {
      assert.deepEqual(executionRuntimeHashes(), testedExecutionRuntime);
      assert.deepEqual(assetHashes(), testedAssets);
      assert.deepEqual(pageErrors, []);
    });
    return;
  }
  if (process.argv.includes('--execution-only')) {
    await executionJourney();
    if (process.argv.includes('--accounts')) await accountJourney();
    await check('Execution resources stayed unchanged during browser verification', async () => {
      assert.deepEqual(assetHashes(), testedAssets);
      assert.deepEqual(executionRuntimeHashes(), testedExecutionRuntime);
    });
    return;
  }
  if (process.argv.includes('--collaboration-only')) {
    await collaborationJourney();
    if (process.argv.includes('--accounts')) await accountJourney();
    await check('Collaboration resources stayed unchanged during browser verification', async () => assert.deepEqual(assetHashes(), testedAssets));
    return;
  }

  let room;
  let coordinator;
  let artifact;
  const title = '协作方案';
  const firstContent = '# 协作方案\n\n这是第一版合成验收资料。\n';
  const secondContent = '# 协作方案\n\n这是第二版合成验收资料。\n';
  const thirdContent = '# 协作方案\n\n这是第三版合成验收资料。\n';

  if (wantsMessenger) await messengerEmptyJourney();

  await check('Blank workspace and external connection truth', async () => {
    const initial = await api('GET', '/bootstrap');
    assert.equal(initial.rooms.length, 0);
    assert.equal(initial.connection.mode, 'external');
    assert.equal(initial.connection.active_workers, 0);
    await page.goto(base);
    await page.getByLabel('需求输入', { exact: true }).waitFor();
    await assertNoOverflow(page);
    const mainArea = await page.locator('.main-area').boundingBox();
    assert(mainArea && mainArea.width >= 700, 'Desktop conversation area is unexpectedly compressed.');
    const sidebar = await page.locator('#sidebar').boundingBox();
    assert(!sidebar || sidebar.width <= 320, 'Desktop conversation sidebar is unexpectedly wide.');
    await page.screenshot({ path: path.join(runDir, 'desktop-empty.png'), fullPage: true, animations: 'disabled' });
  });

  await check('Chinese input composition does not submit prematurely', async () => {
    const composer = page.getByLabel('需求输入', { exact: true });
    await composer.fill('正在选择输入法候选词');
    await composer.evaluate(element => element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, isComposing: true })));
    assert.equal((await api('GET', '/bootstrap')).rooms.length, 0);
    assert.equal(await composer.inputValue(), '正在选择输入法候选词');
    await composer.fill('');
  });

  await check('Create conversation through UI and persist a queued coordinator', async () => {
    const prompt = '请把测试需求分成研究与写作两步，并返回协作方案。';
    await page.getByLabel('需求输入', { exact: true }).fill(prompt);
    await page.getByRole('button', { name: '发送', exact: true }).click();
    room = await until(async () => {
      const state = await api('GET', '/bootstrap');
      return state.rooms[0] && api('GET', '/rooms/' + state.rooms[0].id);
    });
    assert.equal(room.messages.filter(item => item.role === 'user').at(-1).text, prompt);
    assert.equal(room.runs[0].tasks[0].agent_id, 'coordinator');
    assert.equal(room.runs[0].tasks[0].status, 'queued');
    await page.locator('#run-summary').getByText('等待 Agent 接手', { exact: true }).waitFor({ state: 'visible' });
    assert(!JSON.stringify(room).includes('lease_token'));
  });

  await check('Exclusive claim, live progress, and draft focus preservation', async () => {
    const sharedContext = '只使用合成测试资料，不访问网络。';
    await api('PUT', '/rooms/' + room.id + '/context', { text: sharedContext });
    coordinator = await claim('qa-coordinator', room.id, 'coordinator');
    assert(coordinator.task);
    assert(JSON.stringify(coordinator.context).includes(sharedContext));
    assert.equal((await claim('qa-other-coordinator', room.id, 'coordinator')).task, null);
    const composer = page.getByLabel('需求输入', { exact: true });
    await composer.fill('尚未发送的草稿，不应因任务更新而消失。');
    await composer.evaluate(element => { element.focus(); element.setSelectionRange(4, 9); });
    const progress = '测试更新不会清除草稿';
    await api('POST', '/tasks/' + coordinator.task.id + '/heartbeat', {
      worker_id: 'qa-coordinator', lease_token: coordinator.lease_token, progress,
    });
    await page.locator('#run-summary').getByText(progress, { exact: true }).waitFor({ state: 'visible' });
    assert.equal(await composer.inputValue(), '尚未发送的草稿，不应因任务更新而消失。');
    assert.deepEqual(await composer.evaluate(element => ({ focused: document.activeElement === element, start: element.selectionStart, end: element.selectionEnd })), { focused: true, start: 4, end: 9 });
    await composer.fill('');
  });

  await check('Coordinator plan honors dependencies and completion persistence', async () => {
    room = await complete(coordinator, 'qa-coordinator', {
      message: '合成验收计划：先研究，再写作。',
      plan: [
        { key: 'research', agent_id: 'researcher', title: '核对测试资料', instructions: '核对本次合成资料并回传摘要。', depends_on: [] },
        { key: 'write', agent_id: 'writer', title: '撰写协作方案', instructions: '基于研究摘要生成方案。', depends_on: ['research'] },
      ],
    });
    assert.equal(room.runs[0].tasks.length, 3);
    assert.equal((await claim('qa-writer', room.id, 'writer')).task, null);
    const researcher = await claim('qa-researcher', room.id, 'researcher');
    assert(researcher.task);
    await complete(researcher, 'qa-researcher', { message: '合成资料已核对，来源是验收脚本内的固定样本。' });
    const writer = await claim('qa-writer', room.id, 'writer');
    assert(writer.task);
    assert(JSON.stringify(writer.context).includes('合成资料已核对'));
    room = await complete(writer, 'qa-writer', {
      message: '协作方案第一版已保存。',
      artifacts: [{ title, kind: 'markdown', content: firstContent }],
    });
    assert.equal(room.runs[0].status, 'completed');
    artifact = room.artifacts.find(item => item.title === title);
    assert(artifact);
    assert.equal(artifact.version, 1);
    await page.locator('#messages').getByText('协作方案第一版已保存。', { exact: true }).waitFor({ state: 'visible' });
    await openArtifact(title);
    const persisted = await api('GET', '/artifacts/' + artifact.id + '?version=1');
    assert.equal(persisted.content, firstContent);
  });

  await check('Artifact revision, historical selection, and exact browser download', async () => {
    await api('POST', '/rooms/' + room.id + '/messages', { text: '请修订为第二版。', agent_id: 'writer' });
    const writer = await claim('qa-reviser', room.id, 'writer');
    await complete(writer, 'qa-reviser', {
      message: '第二版已保存。', artifacts: [{ id: artifact.id, base_version: 1, title, kind: 'markdown', content: secondContent }],
    });
    const versions = page.getByLabel('成果版本', { exact: true });
    await until(() => versions.locator('option[value="2"]').count());
    await versions.selectOption('1');
    await api('POST', '/rooms/' + room.id + '/messages', { text: '请修订为第三版。', agent_id: 'writer' });
    const writer3 = await claim('qa-reviser', room.id, 'writer');
    await complete(writer3, 'qa-reviser', {
      message: '第三版已保存。', artifacts: [{ id: artifact.id, base_version: 2, title, kind: 'markdown', content: thirdContent }],
    });
    await until(() => versions.locator('option[value="3"]').count());
    assert.equal(await versions.inputValue(), '1');
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('link', { name: '下载成果', exact: true }).click(),
    ]);
    const downloaded = path.join(runDir, 'downloaded-version-1.txt');
    await download.saveAs(downloaded);
    assert.equal(fs.readFileSync(downloaded, 'utf8'), firstContent);
    const response = await fetch(base + '/api/hub/artifacts/' + artifact.id + '/download?version=2');
    assert(response.headers.get('content-disposition').includes('attachment'));
    assert.equal(await response.text(), secondContent);
    const panel = await page.locator('#detail-panel').boundingBox();
    assert(panel && panel.y <= 100 && panel.width >= 300, 'Desktop artifact detail is outside the expected side panel.');
    await page.locator('#messages').getByText('第三版已保存。', { exact: true }).waitFor({ state: 'visible' });
    await page.screenshot({ path: path.join(runDir, 'desktop-conversation-artifact.png'), fullPage: true, animations: 'disabled' });
  });

  await check('Cancellation rejects a late result', async () => {
    room = await api('POST', '/rooms/' + room.id + '/messages', { text: '这项任务用于验证取消。', agent_id: 'reviewer' });
    const reviewer = await claim('qa-reviewer', room.id, 'reviewer');
    assert(reviewer.task);
    const run = room.runs.find(item => item.tasks.some(task => task.id === reviewer.task.id));
    assert(run);
    const closeDrawer = page.getByRole('button', { name: '关闭工作区', exact: true });
    if (await closeDrawer.isVisible()) await closeDrawer.click();
    await page.getByRole('button', { name: '查看任务', exact: true }).click();
    await page.getByRole('button', { name: '取消任务', exact: true }).last().click();
    await until(async () => (await api('GET', '/rooms/' + room.id)).runs.find(item => item.id === run.id).status === 'cancelled');
    await complete(reviewer, 'qa-reviewer', { message: '这条晚到回复不应进入会话。' }, 409);
    const fresh = await api('GET', '/rooms/' + room.id);
    assert(!fresh.messages.some(item => item.text.includes('这条晚到回复')));
    assert.equal(fresh.runs.find(item => item.id === run.id).status, 'cancelled');
  });

  await check('Failed task can be retried through the UI', async () => {
    room = await api('POST', '/rooms/' + room.id + '/messages', { text: '这项任务用于验证失败重试。', agent_id: 'reviewer' });
    const reviewer = await claim('qa-reviewer', room.id, 'reviewer');
    await api('POST', '/tasks/' + reviewer.task.id + '/fail', {
      worker_id: 'qa-reviewer', lease_token: reviewer.lease_token, error: '合成验收故障，可以安全重试。',
    });
    await page.getByRole('button', { name: '重试任务', exact: true }).last().click();
    const retry = await until(async () => {
      const result = await claim('qa-reviewer', room.id, 'reviewer');
      return result.task ? result : false;
    });
    assert.equal(retry.task.id, reviewer.task.id);
    assert(retry.task.attempts >= 2);
    room = await complete(retry, 'qa-reviewer', { message: '重试已成功，前序方案仍然保留。' });
    assert.equal(room.artifacts.find(item => item.id === artifact.id).version, 3);
    await page.locator('#messages').getByText('重试已成功，前序方案仍然保留。', { exact: true }).waitFor({ state: 'visible' });
  });

  await check('Connection dialog keyboard behavior and mobile layout', async () => {
    const closeDrawer = page.getByRole('button', { name: '关闭工作区', exact: true });
    if (await closeDrawer.isVisible()) await closeDrawer.click();
    const connection = page.getByRole('button', { name: '连接 Agent', exact: true });
    await connection.click();
    const dialog = page.getByRole('dialog');
    await dialog.waitFor();
    for (let step = 0; step < 8; step++) {
      await page.keyboard.press('Tab');
      assert(await dialog.evaluate(element => element.contains(document.activeElement)), 'Dialog focus escaped.');
    }
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'hidden' });
    assert(await connection.evaluate(element => element === document.activeElement), 'Dialog did not restore focus.');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => {
      const sidebar = document.getElementById('sidebar');
      return getComputedStyle(sidebar).display === 'none' || sidebar.getBoundingClientRect().right <= 1;
    }, null, { timeout: 3000 });
    await assertNoOverflow(page);
    const composer = page.getByLabel('需求输入', { exact: true });
    await composer.scrollIntoViewIfNeeded();
    assert(await composer.isVisible());
    await composer.click();
    assert(await composer.evaluate(element => element === document.activeElement), 'Mobile composer is obscured.');
    const send = await page.getByRole('button', { name: '发送', exact: true }).boundingBox();
    assert(send && send.x >= 0 && send.x + send.width <= 391, 'Send action is outside the mobile viewport.');
    await page.screenshot({ path: path.join(runDir, 'mobile-conversation.png'), fullPage: true, animations: 'disabled' });
  });

  await check('Conversation survives browser reload and emits no JavaScript errors', async () => {
    await page.reload();
    const fresh = await api('GET', '/rooms/' + room.id);
    assert.equal(fresh.artifacts[0].version, 3);
    assert.equal((await api('GET', '/bootstrap')).connection.active_workers, 0);
    assert.deepEqual(pageErrors, []);
  });
  if (wantsMessenger) {
    await messengerSummaryJourney();
    await messengerBrowserJourney();
    await studioSwitchingJourney();
    await messengerResponsiveJourney();
  }
  if (process.argv.includes('--extended')) {
    await extendedJourney();
    await artifactRebaseRaceJourney();
  }
  if (process.argv.includes('--automation')) await automationJourney();
  if (process.argv.includes('--files')) await filesJourney();
  if (wantsTeams) {
    await teamBoundaryJourney();
    await teamBrowserJourney();
    if (wantsCollaboration) await teamCollaborationJourney();
  }
  if (wantsCollaboration) await collaborationJourney();
  if (wantsExecution) await executionJourney();
  if (wantsManaged) await managedExecutorJourney();
  if (process.argv.includes('--accounts')) await accountJourney();
  await check('Visual resources stayed unchanged during browser verification', async () => {
    assert.deepEqual(assetHashes(), testedAssets, 'UI resources changed during the run; rerun against a stable saved build.');
    if (wantsExecution) assert.deepEqual(executionRuntimeHashes(), testedExecutionRuntime);
    if (testedManagedRuntime) assert.deepEqual(managedRuntimeHashes(), testedManagedRuntime);
  });
}

(async () => {
  let failed;
  try { await main(); } catch (error) {
    failed = error;
    process.stderr.write(error.stack + '\n');
    if (page) {
      await page.screenshot({ path: path.join(runDir, 'failure.png'), fullPage: true, animations: 'disabled' }).catch(() => {});
      fs.writeFileSync(path.join(runDir, 'failure-dom.html'), await page.content().catch(() => ''), 'utf8');
    }
  } finally {
    if (browser) await browser.close();
    if (service && service.exitCode === null && wantsManaged) {
      await fetch(base + '/__qa_shutdown', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).catch(() => {});
      await new Promise(resolve => { if (service.exitCode !== null) return resolve(); service.once('exit', resolve); setTimeout(resolve, 5000); });
    }
    if (service && service.exitCode === null) {
      service.kill();
      await new Promise(resolve => { service.once('exit', resolve); setTimeout(resolve, 3000); });
    }
    fs.writeFileSync(path.join(runDir, 'server.log'), serverLog, 'utf8');
    fs.writeFileSync(path.join(runDir, 'report.json'), JSON.stringify({
      run_id: runId, synthetic_fixtures: true, isolated_data_dir: dataDir,
      scope: { core: !process.argv.some(flag => ['--visual-only', '--collaboration-only', '--completion-fault-only', '--execution-only', '--streaming-only', '--artifact-rebase-only', '--team-boundaries-only', '--teams-only', '--team-collaboration-only', '--messenger-only', '--messenger-api-only', '--messenger-navigation-only', '--messenger-responsive-only', '--managed-only'].includes(flag)), messenger: wantsMessenger, messenger_api_only: process.argv.includes('--messenger-api-only'), messenger_explicit_navigation_only: process.argv.includes('--messenger-navigation-only'), messenger_responsive_only: process.argv.includes('--messenger-responsive-only'), visual_only: process.argv.includes('--visual-only'), artifact_rebase_race: process.argv.includes('--artifact-rebase-only') || process.argv.includes('--extended'), completion_ack_fault: process.argv.includes('--completion-fault-only') || wantsExecution, execution: wantsExecution, real_time_execution_lease_expiry: wantsExecution, jsonl_worker: wantsStreaming, extended: process.argv.includes('--extended'), real_time_presence_expiry: process.argv.includes('--expiry'), accounts: process.argv.includes('--accounts'), automation: process.argv.includes('--automation'), files_search_sharing: process.argv.includes('--files'), collaboration: wantsCollaboration, teams: wantsTeams, team_collaboration: wantsTeams && wantsCollaboration, team_boundaries_api_only: process.argv.includes('--team-boundaries-only'), managed_process_fixture: wantsManaged },
      passed: !failed, checks, tested_ui_assets: testedAssets, tested_execution_runtime: testedExecutionRuntime, tested_managed_runtime: testedManagedRuntime, page_errors: pageErrors, error: failed?.stack,
    }, null, 2), 'utf8');
    process.stdout.write('Report: ' + path.join(runDir, 'report.json') + '\n');
  }
  process.exitCode = failed ? 1 : 0;
})();

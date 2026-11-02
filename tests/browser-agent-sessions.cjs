/* Synthetic HTTP protocol fixtures in a real browser; no model or user's data is used. */
'use strict';
const fs = require('fs');
const path = require('path');
const os = require('os');
const assert = require('assert/strict');
const repo = path.resolve(__dirname, '..'), assets = path.join(repo, 'studio', 'static');
const moduleCandidates = [process.env.WORKSPACE_QA_PLAYWRIGHT, 'playwright', path.join(os.homedir(), '.cache', 'codex-runtimes', 'codex-primary-runtime', 'dependencies', 'node', 'node_modules', 'playwright')].filter(Boolean);
let playwright;
for (const candidate of moduleCandidates) {
  try { playwright = require(candidate); break; } catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; }
}
assert(playwright, 'Install Playwright or set WORKSPACE_QA_PLAYWRIGHT to its module directory.');
const { chromium } = playwright;
const edge = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const executablePath = process.env.WORKSPACE_QA_BROWSER || (fs.existsSync(edge) ? edge : undefined);
const output = path.resolve(process.env.WORKSPACE_QA_OUTPUT || path.join(repo, '..', 'qa'));
const reportDir = path.join(output, 'agent-sessions-' + new Date().toISOString().replace(/[:.]/g, '-'));
fs.mkdirSync(reportDir, {recursive:true});
const stamp = '2026-11-02T21:40:00Z';
const session = (id, turn, decision, reason) => ({id, room_id:'room', agent_id:'writer', generation:1, turn_index:turn, continuation:{decision,reason,from_task_id:decision==='continue'?'t1':null}});
const records = {};
function execution(id, status, info, review=false) {return {id:'exec_'+id,task_id:id,run_id:'run_'+id,task_status:status,active:status==='running',attempts:info?[{attempt:1,worker_id:'fixture-worker',decision:'start',started_at:stamp}]:[],last_seq:1,recovery:{decision:status==='cancelled'?'cancelled':status==='completed'?'completed':'start',calls:review?[{call_id:'external',tool:'fixture.publish',state:'unknown',retry_safe:false,call_seq:1}]:[]},can_reconcile:review,can_retry:false,can_cancel:false,agent_session:info};}
records.t1=execution('t1','completed',session('ags_writer',1,'fresh','first_turn'));
records.t2=execution('t2','running',session('ags_writer',2,'continue','matching_completed_turn'));
records.t3=execution('t3','queued',null);
records.t4=execution('t4','cancelled',session('ags_writer',3,'task_recovery','task_recovery_priority'),true);
const turns = [1,2].map(n=>({turn_index:n,task_id:'t'+n,execution_id:'exec_t'+n,run_id:'run_t'+n,title:n===1?'第一轮产品简报':'根据反馈继续简报',status:n===1?'completed':'running',created_at:stamp,latest_attempt:{continuation:records['t'+n].agent_session.continuation}}));
const history={session:{id:'ags_writer',agent_name:'撰稿人',status:'running',turn_count:2},turns,delegations:[{id:'edge1',parent_task_id:'t1',child_task_id:'t2',parent_session_id:'ags_coordinator',child_session_id:'ags_writer',run_id:'run_t2'}],cursor:2,has_more:false};
const html=`<!doctype html><html lang="zh-CN"><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/workspace.css"><link rel="stylesheet" href="/execution.css"></head><body><script>window.workspaceUI={getAgents:()=>[{id:'writer',name:'撰稿人'}],getCurrentRoom:()=>({runs:[{id:'run',tasks:[{id:'t1',title:'第一轮产品简报'},{id:'t2',title:'根据反馈继续简报'},{id:'t3',title:'尚未领取的工作'},{id:'t4',title:'已取消的外部操作'}]}]}),refreshRoom:async()=>{},toast:()=>{}};document.addEventListener('click',e=>{if(e.target.closest('[data-close-dialog]'))document.querySelector('dialog[open]')?.close()})</script><script src="/execution.js"></script></body></html>`;
(async()=>{
  const browser=await chromium.launch({executablePath,headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}), errors=[], checks=[]; let delayed=null, hold=false, notFound=false, posted=null;
  page.on('pageerror',e=>{errors.push(e.message);console.error('PAGE ERROR',e.message,e.stack)});
  page.on('console',m=>{if(m.type()==='error')console.error('CONSOLE',m.text())});
  const json=(route,obj,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(obj)});
  await page.route('http://127.0.0.1:59876/**',async route=>{
    const url=new URL(route.request().url()), p=url.pathname;
    if(p==='/')return route.fulfill({contentType:'text/html; charset=utf-8',body:html});
    if(p==='/workspace.css')return route.fulfill({contentType:'text/css',body:fs.readFileSync(path.join(assets,'workspace.css'),'utf8')});
    if(['/execution.css','/execution.js'].includes(p))return route.fulfill({contentType:p.endsWith('css')?'text/css; charset=utf-8':'text/javascript; charset=utf-8',body:fs.readFileSync(path.join(assets,p.slice(1)),'utf8')});
    if(p.startsWith('/api/hub/tasks/'))return json(route,records[p.split('/')[4]]);
    if(p.startsWith('/api/hub/agent-sessions/')){if(hold)await new Promise(r=>delayed=r);return json(route,notFound?{error:'不可用'}:history,notFound?404:200);}
    if(p.endsWith('/events'))return json(route,{events:[],cursor:0,has_more:false});
    if(p.endsWith('/reconcile')){posted=route.request().postDataJSON();records.t4={...records.t4,can_reconcile:false,recovery:{decision:'cancelled',calls:[]},last_seq:2};return json(route,records.t4);}
    return route.abort();
  });
  try{
    await page.goto('http://127.0.0.1:59876');
    await page.evaluate(()=>window.workspaceExecution.open('t2'));
    await page.getByLabel('查看 Agent 会话历程').click();
    await page.locator('[data-agent-session-turn-index="2"]').waitFor();
    assert.match(await page.locator('#execution-agent-session-heading').innerText(),/第 2 次工作/);
    assert.equal(await page.locator('#execution-agent-session-mode').innerText(),'接着上次工作');
    assert.equal(await page.locator('[data-delegation-id="edge1"]').count(),1);
    checks.push('Server-shaped claimed session, work turn, history and exact delegation IDs render');
    await page.screenshot({path:path.join(reportDir,'session-desktop.png')});
    await page.setViewportSize({width:390,height:844});
    assert(await page.locator('#execution-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
    await page.screenshot({path:path.join(reportDir,'session-mobile.png')});checks.push('390px expanded session has no horizontal overflow');
    await page.evaluate(()=>window.workspaceExecution.open('t1'));hold=true;
    await page.getByLabel('查看 Agent 会话历程').click();
    await page.waitForFunction(()=>document.querySelector('#refresh-agent-session').disabled);
    await page.evaluate(()=>window.workspaceExecution.open('t3'));
    hold=false;delayed();await page.waitForTimeout(80);
    assert(await page.locator('#execution-agent-session').isHidden());assert.equal(await page.locator('#execution-dialog').getAttribute('data-execution-task-id'),'t3');checks.push('Late session response does not replace newly opened unclaimed task');
    await page.evaluate(()=>window.workspaceExecution.open('t4'));
    await page.getByText('核对外部调用',{exact:true}).click();
    const note=page.getByLabel('核对说明');await note.fill('已在外部系统核对，操作没有执行。');await note.focus();
    await page.waitForTimeout(2500);
    assert.equal(await note.inputValue(),'已在外部系统核对，操作没有执行。');assert(await note.evaluate(el=>document.activeElement===el));
    await page.locator('input[value="confirmed_not_executed"]').check();
    await page.getByLabel('保存核对结果').click();await page.waitForTimeout(150);
    assert.equal(posted.decision,'confirmed_not_executed');assert.equal(posted.call_id,'external');
    assert.match(await page.locator('#execution-task-status').innerText(),/已取消/);assert(await page.getByLabel('重新排队',{exact:true}).isHidden());assert(await page.locator('#execution-review-banner').isHidden());
    checks.push('Cancelled unknown outcome remains reviewable; polling retains draft/focus; reconciliation keeps task cancelled');
    await page.evaluate(()=>window.workspaceExecution.open('t1'));notFound=true;await page.getByLabel('查看 Agent 会话历程').click();
    await page.locator('#execution-agent-session-error').waitFor();assert.match(await page.locator('#execution-agent-session-error').innerText(),/不可用/);checks.push('404 history shows recoverable error without hiding task ledger');
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(reportDir,'ui-check-report.json'),JSON.stringify({scope:'Synthetic HTTP protocol fixture in a real browser; not live backend/model validation',checks,errors},null,2));
    console.log(JSON.stringify({checks:checks.length,errors,reportDir}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});

/* Default: synthetic HTTP fixtures. --live URL --room ID --model-report PATH:
 * inspect existing model evidence without sending messages or starting a model. */
'use strict';
const fs = require('fs');
const path = require('path');
const os = require('os');
const assert = require('assert/strict');
const crypto = require('crypto');
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
function option(name) { const i=process.argv.indexOf(name); if(i<0)return null; const value=process.argv[i+1]; assert(value&&!value.startsWith('--'),name+' requires a value');return value; }
const live=option('--live'), roomId=option('--room'), modelReportPath=option('--model-report');
const reportDir = path.join(output, (live?'live-ui-':'synthetic-ui-') + new Date().toISOString().replace(/[:.]/g, '-'));
fs.mkdirSync(reportDir, {recursive:true});
const digest=value=>crypto.createHash('sha256').update(value).digest('hex');
const assetHashes=()=>Object.fromEntries(fs.readdirSync(assets).filter(n=>/\.(js|css|html)$/.test(n)).sort().map(n=>[n,digest(fs.readFileSync(path.join(assets,n)))]));
const initialAssets=assetHashes();
async function poll(fn,message,timeout=12000){const end=Date.now()+timeout;do{const v=await fn();if(v)return v;await new Promise(r=>setTimeout(r,75));}while(Date.now()<end);throw Error(message);}
async function notClipped(locator){await locator.scrollIntoViewIfNeeded();const geometry=await locator.evaluate(el=>{const r=el.getBoundingClientRect(),failures=[];if(r.width<=0||r.height<=0||r.left<-.5||r.right>innerWidth+.5||r.top<-.5||r.bottom>innerHeight+.5)failures.push('viewport');for(let p=el.parentElement;p;p=p.parentElement){const s=getComputedStyle(p),b=p.getBoundingClientRect();if(/hidden|auto|scroll|clip/.test(s.overflowY)&&(r.top<b.top-1||r.bottom>b.bottom+1))failures.push(p.id||p.tagName);}return {x:r.x,y:r.y,width:r.width,height:r.height,failures};});assert.deepEqual(geometry.failures,[],'Clipped control: '+JSON.stringify(geometry));return geometry;}
const stamp = '2026-11-02T21:40:00Z';
const checkpoint={host:'codex',session_id:'11111111-2222-4333-8444-555555555555',host_identity:'fixture-host',worker_id:'fixture-worker',checkpoint_id:'fixture-checkpoint'};
const session = (id, turn, decision, reason) => ({id, room_id:'room', agent_id:'writer', generation:1, turn_index:turn, continuation:{decision,reason,from_task_id:decision==='continue'?'t1':null,checkpoint:decision==='continue'?checkpoint:null}});
const records = {};
function execution(id, status, info, review=false) {return {id:'exec_'+id,task_id:id,run_id:'run_'+id,task_status:status,active:status==='running',attempts:info?[{attempt:1,worker_id:'fixture-worker',decision:'start',started_at:stamp}]:[],last_seq:1,recovery:{decision:status==='cancelled'?'cancelled':status==='completed'?'completed':'start',calls:review?[{call_id:'external',tool:'fixture.publish',state:'unknown',retry_safe:false,call_seq:1}]:[]},can_reconcile:review,can_retry:false,can_cancel:false,agent_session:info};}
records.t1=execution('t1','completed',session('ags_writer',1,'fresh','first_turn'));
records.t2=execution('t2','running',session('ags_writer',2,'continue','matching_completed_turn'));
records.t3=execution('t3','queued',null);
records.t4=execution('t4','cancelled',session('ags_writer',3,'task_recovery','task_recovery_priority'),true);
const turns = [1,2].map(n=>({turn_index:n,task_id:'t'+n,execution_id:'exec_t'+n,run_id:'run_t'+n,title:n===1?'第一轮产品简报':'根据反馈继续简报',status:n===1?'completed':'running',created_at:stamp,latest_attempt:{continuation:records['t'+n].agent_session.continuation}}));
const history={session:{id:'ags_writer',agent_name:'撰稿人',status:'running',turn_count:2},turns,delegations:[{id:'edge1',parent_task_id:'t1',child_task_id:'t2',parent_session_id:'ags_coordinator',child_session_id:'ags_writer',run_id:'run_t2'}],cursor:2,has_more:false};
const html=`<!doctype html><html lang="zh-CN"><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/workspace.css"><link rel="stylesheet" href="/execution.css"></head><body><script>window.workspaceUI={getAgents:()=>[{id:'writer',name:'撰稿人'}],getCurrentRoom:()=>({runs:[{id:'run',tasks:[{id:'t1',title:'第一轮产品简报'},{id:'t2',title:'根据反馈继续简报'},{id:'t3',title:'尚未领取的工作'},{id:'t4',title:'已取消的外部操作'}]}]}),refreshRoom:async()=>{},toast:()=>{}};document.addEventListener('click',e=>{if(e.target.closest('[data-close-dialog]'))document.querySelector('dialog[open]')?.close()})</script><script src="/execution.js"></script></body></html>`;
async function runLive(){
  assert(roomId&&modelReportPath,'--live requires --room and --model-report. No evidence is created automatically.');
  const base=new URL(live), model=JSON.parse(fs.readFileSync(path.resolve(modelReportPath),'utf8'));
  assert(['http:','https:'].includes(base.protocol));assert.equal(model.synthetic_fixtures,false);assert.equal(new URL(model.server).origin,base.origin);assert(Object.values(model.checks).every(v=>v===true));
  const report={scope:'Read-only UI/API inspection of existing real model evidence; no new model invocation',synthetic_fixtures:false,model_evidence:path.resolve(modelReportPath),base:base.origin,room_id:roomId,checks:[],errors:[],assets_before:initialAssets,started_at:new Date().toISOString(),limitations:['Completed real tasks are inspected; a new streaming invocation is not started.','The next input is prepared but no third model request is sent.','Readonly authorization and errors are checked separately.','Geometry and screenshots are review evidence, not Teamily equivalence or design acceptance.']};
  const browser=await chromium.launch({executablePath,headless:true});const page=await browser.newPage({viewport:{width:1440,height:1000},acceptDownloads:true});page.setDefaultTimeout(12000);page.on('pageerror',e=>report.errors.push(e.message));
  const check=async(name,fn)=>{const start=Date.now();try{const evidence=await fn();report.checks.push({name,passed:true,ms:Date.now()-start,evidence});console.log('PASS '+name);}catch(error){report.checks.push({name,passed:false,error:error.stack});throw error;}};
  const shot=name=>page.screenshot({path:path.join(reportDir,name+'.png')});
  const get=async p=>{const response=await fetch(new URL(p,base));assert(response.ok,`${response.status} ${p}`);return response.json();};
  try{
    const room=await get('/api/hub/rooms/'+roomId),tasks=room.runs.flatMap(r=>r.tasks),ids=model.turns.map(t=>t.task_id);assert.equal(ids.length,2);assert(ids.every(id=>tasks.some(t=>t.id===id&&t.status==='completed')));
    const executions=await Promise.all(ids.map(id=>get('/api/hub/tasks/'+id+'/execution'))),sessionId=executions[0].agent_session.id;
    const history=await get('/api/hub/agent-sessions/'+sessionId+'?after=0&limit=100');
    const readEvents=async ex=>{let after=0,events=[];for(let n=0;n<100;n++){const data=await get(`/api/hub/executions/${ex.id}/events?after=${after}&limit=100`);events.push(...data.events);if(!data.has_more)return events;assert(data.cursor>after);after=data.cursor;}throw Error('Event pagination exceeded bound');};
    const events=await Promise.all(executions.map(readEvents)),outputs=[];
    for(const a of room.artifacts){const metadata=await get('/api/hub/artifacts/'+a.id);for(const v of metadata.versions||[]){if(ids.includes(v.task_id)&&v.author_type!=='human'){const exact=await get(`/api/hub/artifacts/${a.id}?version=${v.version}`);outputs.push({task_id:v.task_id,id:a.id,version:v.version,title:exact.title,content:exact.content});}}}
    await check('Real saved tasks have separate ledgers and one exact logical/native session',async()=>{
      assert.notEqual(ids[0],ids[1]);assert.equal(executions[1].agent_session.id,sessionId);assert.equal(executions[1].agent_session.continuation.decision,'continue');assert.equal(executions[1].agent_session.continuation.from_task_id,ids[0]);const cp=executions[1].agent_session.continuation.checkpoint;
      for(let i=0;i<2;i++){const attempt=Math.max(...executions[i].attempts.map(a=>a.attempt));assert(events[i].some(e=>e.kind==='host_session'&&e.attempt===attempt&&e.payload.session_id===cp.session_id&&e.payload.host===cp.host&&e.payload.host_identity===cp.host_identity));assert(events[i].some(e=>e.kind==='task.completed'));}
      assert(ids.every(id=>history.turns.some(t=>t.task_id===id)));assert.equal(outputs.length,2);assert.equal(outputs[0].id,outputs[1].id);return {task_ids:ids,session_id:sessionId,native_session_id:cp.session_id,outputs:outputs.map(({content,...o})=>({...o,sha256:digest(Buffer.from(content))}))};
    });
    const mutations=[];page.on('request',r=>{if(new URL(r.url()).origin===base.origin&&!['GET','HEAD','OPTIONS'].includes(r.method()))mutations.push({method:r.method(),url:r.url()});});
    await page.goto(base.origin+'/#room/'+roomId);await page.waitForFunction(id=>window.workspaceUI?.getCurrentRoom()?.id===id,roomId);
    const openTask=async id=>{if(await page.locator('#execution-dialog').isVisible())await page.getByLabel('关闭执行记录').click();if(await page.getByLabel('关闭工作区',{exact:true}).isVisible())await page.getByLabel('关闭工作区',{exact:true}).click();await page.getByLabel('查看任务',{exact:true}).click();await page.locator(`[data-task-id="${id}"]`).getByLabel('查看执行记录',{exact:true}).click();await page.waitForFunction(id=>document.querySelector('#execution-dialog')?.dataset.executionTaskId===id&&document.querySelector('#execution-task-status')?.textContent.includes('已完成'),id);};
    for(const view of [{width:1440,height:1000,label:'desktop'},{width:390,height:844,label:'mobile'}]){
      await page.setViewportSize(view);
      for(let i=0;i<ids.length;i++){const id=ids[i],output=outputs.find(o=>o.task_id===id);await check(`${view.label}: real turn ${i+1} → history → exact result → downloaded bytes`,async()=>{
        await openTask(id);await poll(async()=>await page.locator('#execution-agent-session-mode').getAttribute('data-continuation-state')===(i===1?'confirmed':'fresh'),'Native receipt state not reflected');await page.getByLabel('查看 Agent 会话历程').click();await page.locator(`[data-agent-session-task-id="${id}"]`).waitFor();assert.equal(await page.locator('#execution-agent-session').getAttribute('data-agent-session-id'),sessionId);for(const other of ids)assert.equal(await page.locator(`[data-agent-session-task-id="${other}"]`).count(),1);if(i===1)assert.match(await page.locator('#execution-agent-session-mode').innerText(),/已收到续接回执/);await shot(`${view.label}-turn-${i+1}-history`);
        const button=page.locator(`[data-execution-artifact="${output.id}"][data-artifact-version="${output.version}"]`);await button.waitFor();assert((await button.innerText()).includes(output.title));await page.getByLabel('查看 Agent 会话历程').click();const geometry=await notClipped(button);await shot(`${view.label}-turn-${i+1}-result-entry`);await button.click();
        await page.waitForFunction(v=>document.querySelector('#artifact-download')?.getAttribute('href')?.endsWith(`version=${v}`)&&document.querySelector('#artifact-version')?.value===String(v),output.version);assert.equal(await page.locator('#artifact-title').innerText(),output.title);const selected=await page.evaluate(()=>window.workspaceUI.getSelectedArtifact());assert.equal(selected.id,output.id);assert.equal(selected.version,output.version);assert.equal(selected.task_id,id);assert((await page.locator("#artifact-body").innerText()).includes(output.title));
        const pending=page.waitForEvent('download');await page.getByLabel('下载成果',{exact:true}).click();const download=await pending,file=path.join(reportDir,`${view.label}-turn-${i+1}-download.md`);await download.saveAs(file);assert.deepEqual(fs.readFileSync(file),Buffer.from(output.content));assert(await page.locator('html').evaluate(el=>el.scrollWidth<=innerWidth+1));await shot(`${view.label}-turn-${i+1}-studio`);return {task_id:id,artifact_id:output.id,version:output.version,download_sha256:digest(fs.readFileSync(file)),button_geometry:geometry};
      });}
      await check(`${view.label}: prepare next step with draft/focus intact after polling`,async()=>{
        await page.getByLabel('关闭工作区',{exact:true}).click();const input=page.getByLabel('需求输入',{exact:true});const text='下一步：请继续检查这份验收表。（QA 未发送草稿）';await input.fill(text);await input.focus();await input.evaluate(el=>el.setSelectionRange(4,8));await page.waitForTimeout(2500);assert.equal(await input.inputValue(),text);assert(await input.evaluate(el=>document.activeElement===el&&el.selectionStart===4&&el.selectionEnd===8));const geometry=await notClipped(page.getByLabel('发送',{exact:true}));await shot(`${view.label}-next-step-draft`);await input.fill('');return geometry;
      });
    }
    await check('History links open the real earlier task and Escape returns to its workspace',async()=>{
      await openTask(ids[1]);await page.getByLabel('查看 Agent 会话历程').click();await page.locator(`#execution-agent-session-turns [data-session-task="${ids[0]}"]`).click();await page.waitForFunction(id=>document.querySelector('#execution-dialog')?.dataset.executionTaskId===id&&document.querySelector('#execution-agent-session-heading')?.textContent.includes('第 1 次工作'),ids[0]);
      assert.equal(await page.locator('#execution-agent-session').getAttribute('data-agent-session-id'),sessionId);await page.keyboard.press('Escape');assert(await page.locator('#execution-dialog').isHidden());
    });
    await check('Read-only inspection preserves messages, results and runs',async()=>{const after=await get('/api/hub/rooms/'+roomId);assert.deepEqual(after.messages,room.messages);assert.deepEqual(after.artifacts,room.artifacts);assert.deepEqual(after.runs,room.runs);assert.deepEqual(mutations,[]);});
    assert.deepEqual(report.errors,[]);assert.deepEqual(assetHashes(),initialAssets,'Assets changed during QA');report.status='passed';
  }catch(error){report.status='failed';report.failure=error.stack;await shot('failure').catch(()=>{});process.exitCode=1;console.error(error.stack);}
  finally{report.assets_after=assetHashes();report.finished_at=new Date().toISOString();fs.writeFileSync(path.join(reportDir,'report.json'),JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify({status:report.status,checks:report.checks.length,reportDir}));}
}
(async()=>{
  if(live){await runLive();return;}
  const browser=await chromium.launch({executablePath,headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}), errors=[], checks=[]; let delayed=null, hold=false, notFound=false, posted=null, events=[], paged=false;
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
    if(p.endsWith('/events'))return json(route,{events:paged?[]:events,cursor:paged?Number(url.searchParams.get('after'))+1:events.length,has_more:paged});
    if(p.endsWith('/reconcile')){posted=route.request().postDataJSON();records.t4={...records.t4,can_reconcile:false,outcome_review_required:false,unresolved_outcome_count:0,recovery:{decision:'cancelled',calls:[]},last_seq:2};return json(route,records.t4);}
    return route.abort();
  });
  try{
    await page.goto('http://127.0.0.1:59876');
    await page.evaluate(()=>window.workspaceExecution.open('t2'));
    await page.getByLabel('查看 Agent 会话历程').click();
    await page.locator('[data-agent-session-turn-index="2"]').waitFor();
    assert.match(await page.locator('#execution-agent-session-heading').innerText(),/第 2 次工作/);
    assert.equal(await page.locator('#execution-agent-session-mode').getAttribute('data-continuation-state'),'awaiting');
    assert.match(await page.locator('#execution-agent-session-mode').innerText(),/已选择续接/);
    assert.equal(await page.locator('[data-delegation-id="edge1"]').count(),1);
    checks.push('Server-shaped claimed session, work turn, history and exact delegation IDs render');
    const mode=()=>page.locator('#execution-agent-session-mode').getAttribute('data-continuation-state');
    const receipt=(attempt,payload)=>[{event_id:'receipt',kind:'host_session',seq:1,attempt,payload,created_at:stamp}];
    events=receipt(1,checkpoint);await page.evaluate(()=>window.workspaceExecution.open('t2'));assert.equal(await mode(),'confirmed');
    events=receipt(1,{...checkpoint,session_id:'different-native'});await page.evaluate(()=>window.workspaceExecution.open('t2'));assert.equal(await mode(),'awaiting');
    events=receipt(1,{...checkpoint,host_identity:'different-host'});await page.evaluate(()=>window.workspaceExecution.open('t2'));assert.equal(await mode(),'awaiting');
    events=receipt(1,{...checkpoint,host:'different-protocol'});await page.evaluate(()=>window.workspaceExecution.open('t2'));assert.equal(await mode(),'awaiting');
    events=receipt(0,checkpoint);await page.evaluate(()=>window.workspaceExecution.open('t2'));assert.equal(await mode(),'awaiting');
    events=[];records.t2.active=false;records.t2.task_status='completed';await page.evaluate(()=>window.workspaceExecution.open('t2'));assert.equal(await mode(),'not_reported');records.t2.active=true;records.t2.task_status='running';
    checks.push('Only exact current-attempt host/native/identity receipt confirms continuation; absent, wrong and old receipts do not');
    paged=true;await page.evaluate(()=>window.workspaceExecution.open('t2'));assert.equal(await mode(),'loading');paged=false;await page.evaluate(()=>window.workspaceExecution.open('t2'));
    checks.push('Incomplete event pagination remains loading');
    await page.getByLabel('查看 Agent 会话历程').click();await page.locator('[data-agent-session-turn-index="2"]').waitFor();
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
    records.t4.outcome_review_required=true;records.t4.unresolved_outcome_count=1;records.t4.outcome_review_terminated=false;records.t4.can_reconcile=false;
    await page.evaluate(()=>document.documentElement.dataset.access='viewer');await page.evaluate(()=>window.workspaceExecution.open('t4'));
    assert.match(await page.locator('#execution-task-status').innerText(),/已取消.*待核对/);assert(await page.locator('#execution-review-banner').isVisible());assert(await page.getByText('核对外部调用',{exact:true}).isHidden());assert(await page.getByLabel('重新排队',{exact:true}).isHidden());assert.match(await page.locator('#execution-recovery-description').innerText(),/编辑权限/);
    await page.screenshot({path:path.join(reportDir,'readonly-unknown-mobile.png')});checks.push('Read-only cancelled task shows unknown outcome but offers no mutation; presentation fixture only');
    records.t4.can_reconcile=true;await page.evaluate(()=>document.documentElement.dataset.access='owner');await page.evaluate(()=>window.workspaceExecution.open('t4'));
    await page.getByText('核对外部调用',{exact:true}).click();
    const note=page.getByLabel('核对说明');await note.fill('已在外部系统核对，操作没有执行。');await note.focus();
    await page.waitForTimeout(2500);
    assert.equal(await note.inputValue(),'已在外部系统核对，操作没有执行。');assert(await note.evaluate(el=>document.activeElement===el));
    await page.locator('input[value="confirmed_not_executed"]').check();
    await page.getByLabel('保存核对结果').click();await page.waitForTimeout(150);
    assert.equal(posted.decision,'confirmed_not_executed');assert.equal(posted.call_id,'external');
    assert.match(await page.locator('#execution-task-status').innerText(),/已取消/);assert(await page.getByLabel('重新排队',{exact:true}).isHidden());assert(await page.locator('#execution-review-banner').isHidden());
    checks.push('Cancelled unknown outcome remains reviewable; polling retains draft/focus; reconciliation keeps task cancelled');
    records.t4={...records.t4,outcome_review_required:false,unresolved_outcome_count:1,outcome_review_terminated:true};await page.evaluate(()=>document.documentElement.dataset.access='viewer');await page.evaluate(()=>window.workspaceExecution.open('t4'));
    assert.equal(await page.locator('#execution-recovery-title').innerText(),'已记录终止决定');assert.match(await page.locator('#execution-recovery-description').innerText(),/外部结果仍未确认/);assert(await page.locator('#execution-review-banner').isHidden());assert(await page.getByLabel('重新排队',{exact:true}).isHidden());
    await page.screenshot({path:path.join(reportDir,'readonly-terminated-mobile.png')});checks.push('Audited termination does not claim that an unknown external action was undone or never executed');
    await page.evaluate(()=>window.workspaceExecution.open('t1'));notFound=true;await page.getByLabel('查看 Agent 会话历程').click();
    await page.locator('#execution-agent-session-error').waitFor();assert.match(await page.locator('#execution-agent-session-error').innerText(),/不可用/);checks.push('404 history shows recoverable error without hiding task ledger');
    notFound=false;await page.getByLabel('刷新会话历程').click();await page.locator('[data-agent-session-turn-index="1"]').waitFor();assert(await page.locator('#execution-agent-session-error').isHidden());await page.keyboard.press('Escape');assert(await page.locator('#execution-dialog').isHidden());checks.push('History refresh recovers from 404 and keyboard Escape closes the dialog');
    assert.deepEqual(errors,[]);
    assert.deepEqual(assetHashes(),initialAssets,'Assets changed during QA');
    fs.writeFileSync(path.join(reportDir,'ui-check-report.json'),JSON.stringify({scope:'Synthetic HTTP protocol fixture in a real browser; not live backend/model/authorization validation',checks,errors,assets_before:initialAssets,assets_after:assetHashes()},null,2));
    console.log(JSON.stringify({checks:checks.length,errors,reportDir}));
  }catch(error){await page.screenshot({path:path.join(reportDir,'failure.png')}).catch(()=>{});fs.writeFileSync(path.join(reportDir,'failed-report.json'),JSON.stringify({scope:'Synthetic HTTP fixture; not real model/backend authorization',checks,errors,failure:error.stack,assets_before:initialAssets,assets_after:assetHashes()},null,2));throw error;}
  finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});

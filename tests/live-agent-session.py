"""Opt-in real caller-model acceptance against a caller-selected local test service."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.request import ProxyHandler, Request, build_opener
from urllib.parse import urlsplit

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--server', required=True, help='An isolated local test service with its own STUDIO_DATA_DIR')
parser.add_argument('--allow-model-calls', action='store_true', help='Explicitly run two tasks with the caller-owned Codex model')
parser.add_argument('--output', type=Path, help='A new directory for private receipts and actual model artifacts')
args = parser.parse_args()
if not args.allow_model_calls:
    parser.error('This test creates a room and invokes your model twice; pass --allow-model-calls explicitly')
target = urlsplit(args.server)
if target.scheme != 'http' or target.hostname not in ('127.0.0.1', 'localhost', '::1') or target.username or target.password or target.query or target.fragment or target.path not in ('', '/'):
    parser.error('--server must be a local HTTP origin without credentials, paths or query parameters')
BASE = args.server.rstrip('/')
OUT = (args.output or Path(__file__).resolve().parents[2] / 'qa' / ('live-agent-session-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))).resolve()
OUT.mkdir(parents=True, exist_ok=False)
opener = build_opener(ProxyHandler({}))
report = {'server': BASE, 'started_at': datetime.now(timezone.utc).isoformat(), 'synthetic_fixtures': False,
          'model_source': 'caller-owned logged-in Codex', 'checks': {}, 'turns': [], 'observations': []}

def request(path, body=None, method=None, raw=False):
    payload = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
    with opener.open(Request(BASE + path, data=payload, method=method, headers={'Content-Type': 'application/json'}), timeout=15) as response:
        return response.read() if raw else json.load(response)

def save():
    (OUT / 'live-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

def emit(stage, **values):
    print(json.dumps({'stage': stage, **values}, ensure_ascii=False), flush=True)
    save()

def finish_run(room, label):
    run_id = room['runs'][-1]['id']
    emit('submitted', label=label, room_id=room['id'], run_id=run_id)
    previous = None
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        latest = request('/api/hub/rooms/' + room['id'])
        run = next(item for item in latest['runs'] if item['id'] == run_id)
        state = [(task['id'], task['agent_id'], task['status'], task.get('attempts')) for task in run['tasks']]
        if state != previous:
            report['observations'].append({'label': label, 'at': datetime.now(timezone.utc).isoformat(), 'tasks': state})
            emit('task_state', label=label, tasks=state)
            previous = state
        if run['status'] in ('completed', 'failed', 'cancelled'):
            break
        time.sleep(1)
    else:
        request('/api/hub/runs/' + run_id + '/cancel', {})
        raise RuntimeError('Real model verification reached its bounded deadline')
    if len(run['tasks']) != 1:
        raise RuntimeError('Expected exactly one writer task')
    task = run['tasks'][0]
    execution = request('/api/hub/tasks/' + task['id'] + '/execution')
    events = request('/api/hub/executions/' + execution['id'] + '/events?limit=100')['events']
    native = [event['payload']['session_id'] for event in events if event['kind'] == 'host_session']
    turn = {'label': label, 'task_id': task['id'], 'run_id': run_id, 'status': task['status'],
            'attempts': task['attempts'], 'execution_id': execution['id'], 'agent_session': execution['agent_session'],
            'native_sessions': native, 'event_kinds': sorted(set(event['kind'] for event in events))}
    report['turns'].append(turn)
    save()
    if task['status'] != 'completed':
        raise RuntimeError('Actual model task did not complete: ' + task['status'])
    return latest, turn

try:
    report['worker'] = request('/api/hub/executors/codex/start', {})
    assert report['worker']['state'] == 'running'
    first_prompt = '''请生成一份标题为《买手开发任务验收表》的 Markdown 成果，供我们检查平台功能，明确标注“功能验收文档，不是已经完成的买手研究”。
只依据这些已确认的产品要求：买手研究应保留来源链接与采集日期；供应商资料要区分企业声明与核实事实；产出名单和排除理由；用户在同一会话可继续修改成果。
正文只需“研究证据”和“交付要求”两小节，总字数控制在350字内。不要虚构公司、联系人、已完成研究或发送行为。
无需访问文件、网络或任何工具，不执行外部动作。返回一个真实完整 markdown 成果。'''
    room = request('/api/hub/rooms', {'text': first_prompt, 'title': '买手开发验收 · 连续工作', 'agent_id': 'writer'})
    report['room_id'] = room['id']
    room, first = finish_run(room, 'first')
    artifacts = room['artifacts']
    assert len(artifacts) == 1
    artifact = request('/api/hub/artifacts/' + artifacts[0]['id'])
    report['artifact_id'] = artifact['id']
    report['first_artifact_sha256'] = hashlib.sha256(artifact['content'].encode()).hexdigest()
    (OUT / 'first-real-model-artifact.md').write_text(artifact['content'], encoding='utf-8')
    report['checks']['first_real_task_and_file'] = first['attempts'] == 1 and artifact['version'] == 1
    second_prompt = '''继续修改刚才那份《买手开发任务验收表》，保留原有“研究证据”和“交付要求”，新增“执行前审批”一节，写清报价、发信、联系客户、产生费用前需要用户批准确切对象、内容和预算。
这是对已有成果的第二版修改，不要新建另一份成果。请使用当前上下文中的原成果 id 与当前 base_version，返回完整的新正文；不要声称平台已实现这项审批能力。
继续仅整理本对话已提供要求；不访问文件、网络、工具或执行任何外部动作。'''
    room = request('/api/hub/rooms/' + room['id'] + '/messages', {'text': second_prompt, 'agent_id': 'writer'})
    room, second = finish_run(room, 'second')
    final = request('/api/hub/artifacts/' + artifact['id'])
    old = request('/api/hub/artifacts/' + artifact['id'] + '?version=1')
    final_download = request('/api/hub/artifacts/' + artifact['id'] + '/download?version=2', raw=True)
    report['checks'].update({
        'two_distinct_tasks_and_ledgers': first['task_id'] != second['task_id'] and first['execution_id'] != second['execution_id'],
        'same_persistent_agent_session': first['agent_session']['id'] == second['agent_session']['id'],
        'claim_selected_cross_task_continuation': second['agent_session']['continuation']['decision'] == 'continue' and second['agent_session']['continuation']['from_task_id'] == first['task_id'],
        'actual_exact_native_session_reused': len(first['native_sessions']) == 1 and first['native_sessions'] == second['native_sessions'],
        'same_artifact_became_v2': len(room['artifacts']) == 1 and final['version'] == 2 and final['id'] == artifact['id'],
        'original_version_preserved': old['content'] == artifact['content'] and old['version'] == 1,
        'new_section_and_original_sections': all(section in final['content'] for section in ('研究证据', '交付要求', '执行前审批')),
        'exact_v2_download': final_download == final['content'].encode('utf-8'),
        'real_completed_receipts_both_turns': all({'host_session', 'result.ready', 'task.completed'}.issubset(turn['event_kinds']) for turn in (first, second)),
    })
    report['final_artifact_version'] = final['version']
    report['final_artifact_sha256'] = hashlib.sha256(final_download).hexdigest()
    report['session_detail'] = request('/api/hub/agent-sessions/' + first['agent_session']['id'])
    (OUT / 'second-real-model-artifact.md').write_text(final['content'], encoding='utf-8')
    report['status'] = 'passed' if all(report['checks'].values()) else 'failed'
    emit('finished', status=report['status'], room_id=report['room_id'], artifact_id=report['artifact_id'], checks=report['checks'])
    if report['status'] != 'passed':
        raise SystemExit(1)
except Exception as exc:
    report['status'] = 'failed'
    report['error'] = str(exc)
    emit('failed', error=str(exc))
    raise
finally:
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    save()

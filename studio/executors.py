"""Explicit local-owner controls for a host-installed executor process."""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading

from . import accounts, core, hub
from .worker import stop_process

_lock = threading.RLock()
_managed = {}
CATALOG_REFRESH_SECONDS = 3


def allowed(principal):
    if os.environ.get('STUDIO_PUBLIC_ORIGIN') or not principal or principal.get('token_id'):
        return False
    if principal.get('local'):
        return True
    if not principal.get('workspace') or principal['workspace']['role'] != 'owner':
        return False
    with contextlib.closing(accounts.db()) as conn:
        return bool(conn.execute("SELECT 1 FROM members m JOIN workspaces w ON w.id=m.workspace_id WHERE w.storage_key='.' AND m.role='owner' AND m.user_id=?", (principal['user']['id'],)).fetchone())


def _key():
    return str(core.data_root())


def _catalog_principal(record):
    """Recheck the original local grant, never a caller-supplied workspace."""
    if os.environ.get('STUDIO_PUBLIC_ORIGIN'):
        raise hub.HubError('本机执行器管理权限已撤销', 403)
    if record.get('api_token_id'):
        principal = {'token_id': record['api_token_id']}
        accounts.authorize(principal, 'POST', '/api/hub/tasks/claim')
        if (principal['user']['id'] != record.get('owner_id')
                or principal['workspace']['id'] != record.get('workspace_id')
                or Path(accounts.data_directory(principal)).resolve() != Path(record['data_root']).resolve()):
            raise hub.HubError('本机执行器工作空间授权已变更', 403)
        # A token can execute as an editor; extending a managed catalog still
        # requires the original local owner's current management authority.
        management = dict(principal, token_id=None)
        if not allowed(management):
            raise hub.HubError('本机执行器管理权限已撤销', 403)
        return principal
    principal = accounts.identify({})
    if not principal or not principal.get('local') or Path(record['data_root']).resolve() != core.base_data_root():
        raise hub.HubError('本机执行器身份已变更，请重新连接', 403)
    return principal


def _catalog_agent_ids(conn):
    return [row['id'] for row in conn.execute('SELECT id FROM hub_agents ORDER BY builtin DESC,rowid')]


def _refresh_catalog(record):
    """Refresh only an explicitly managed registration; do not renew it."""
    if not record.get('refresh_agent_catalog'):
        return False
    _catalog_principal(record)
    binding = core.REQUEST_DATA_ROOT.set(record['data_root'])
    actor = core.REQUEST_ACTOR.set(None)
    try:
        with hub._transaction() as conn:
            current = hub._worker_owned(conn, record['worker_id'], record['session_token'])
            agent_ids = _catalog_agent_ids(conn)
            if len(agent_ids) > 100:
                raise hub.HubError('本机执行器最多注册 100 个 Agent，请调整目录后重新连接', 409)
            if json.loads(current['agent_ids_json']) == agent_ids:
                return False
            # Session hash, heartbeat expiry, capabilities, tasks and leases
            # deliberately remain unchanged. Removed roles affect future claims.
            conn.execute('UPDATE hub_workers SET agent_ids_json=? WHERE id=?', (hub._json(agent_ids), record['worker_id']))
            return True
    finally:
        core.REQUEST_ACTOR.reset(actor)
        core.REQUEST_DATA_ROOT.reset(binding)


def _catalog_loop(record):
    while not record['catalog_stop'].wait(CATALOG_REFRESH_SECONDS):
        with _lock:
            if _managed.get(str(record['data_root'])) is not record or record['process'].poll() is not None:
                return
            try:
                _refresh_catalog(record)
            except hub.HubError as exc:
                # Never re-register after revocation, expiry or replacement.
                # Stop this process only; _revoke revalidates its old session.
                record['catalog_error'] = str(exc)
                record['catalog_stop'].set()
                stop_process(record['process'])
                _revoke(record)
                record['log'].close()
                record['cleaned'] = True
                return
            except (sqlite3.Error, OSError):
                # Temporary storage contention may delay discovery, but cannot
                # extend the registration lifetime or trigger re-registration.
                continue


def _revoke(record):
    if record.get('catalog_stop'):
        record['catalog_stop'].set()
    if record.get('api_token_id'):
        with accounts.transaction() as conn:
            conn.execute('UPDATE api_tokens SET revoked_at=? WHERE id=?', (accounts.now(), record['api_token_id']))
    binding = core.REQUEST_DATA_ROOT.set(record['data_root'])
    try:
        try:
            hub.disconnect_worker(record['worker_id'], record['session_token'])
            with hub._transaction() as conn:
                tasks = conn.execute('SELECT t.id,t.run_id,r.room_id FROM hub_tasks t JOIN hub_runs r ON r.id=t.run_id WHERE t.worker_id=? AND t.status=? AND r.cancelled=0', (record['worker_id'], 'running')).fetchall()
                for task in tasks:
                    conn.execute('UPDATE hub_tasks SET status=?,worker_id=NULL,lease_token=NULL,lease_expires_at=NULL,progress=NULL,updated_at=? WHERE id=?', ('queued', hub.now(), task['id']))
                    hub._event(conn, task['room_id'], task['run_id'], task['id'], 'task.requeued', '执行器已停止，等待重新接手')
        except hub.HubError:
            pass
    finally:
        core.REQUEST_DATA_ROOT.reset(binding)


def status(principal):
    can_manage = allowed(principal)
    with _lock:
        record = _managed.get(_key()) if can_manage else None
        running = record is not None and record['process'].poll() is None
        if record and not running and not record.get('cleaned'):
            _revoke(record)
            record['log'].close()
            record['cleaned'] = True
        return {'local_control': can_manage, 'codex_available': bool(shutil.which('codex')) if can_manage else False, 'state': 'running' if running else 'failed' if record and record['process'].returncode else 'stopped', 'worker_id': record['worker_id'] if record else None, 'started_at': record['started_at'] if record else None, 'exit_code': record['process'].returncode if record and not running else None}


def start(principal, server_url):
    if not allowed(principal):
        raise hub.HubError('只有本机所有者可以启动本机执行器；远程空间请使用独立执行端', 403)
    with _lock:
        if status(principal)['state'] == 'running':
            return status(principal)
        executable = shutil.which('codex')
        if not executable:
            raise hub.HubError('未找到本机 Codex，请先安装并登录 Codex CLI', 409)
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        try:
            auth = subprocess.run([executable, 'login', 'status'], capture_output=True, timeout=15, **options)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise hub.HubError('无法检查本机 Codex 登录状态', 409) from exc
        if auth.returncode:
            raise hub.HubError('请先在本机运行 codex login，登录后再连接', 409)
        folder = core.data_root() / 'executor'
        folder.mkdir(parents=True, exist_ok=True)
        worker_id = 'desktop-codex'
        agents = [agent['id'] for agent in hub.list_agents()]
        capabilities = ['codex-cli', 'read-only-sandbox', 'structured-artifacts', 'execution-events-v1', 'continuation:codex']
        registration = hub.register_worker(worker_id, agents, '本机 Codex', capabilities)
        token_id = None
        env = dict(os.environ, PYTHONPATH=str(core.ROOT), PYTHONUTF8='1', STUDIO_WORKER_SESSION=registration['session_token'])
        env.pop('STUDIO_WORKER_TOKEN', None)
        if not principal.get('local'):
            issued = accounts.dispatch('POST', '/api/account/tokens', {'name': '本机 Codex 执行器', 'scopes': ['read', 'write', 'execute']}, principal)[1]
            env['STUDIO_WORKER_TOKEN'] = issued['token']
            token_id = issued['record']['id']
        config = {'worker_id': worker_id, 'name': '本机 Codex', 'follow_registered_agents': True, 'command': [sys.executable, '-X', 'utf8', '-m', 'studio.codex_executor', '--codex', executable], 'timeout_seconds': 960, 'output_protocol': 'jsonl', 'capabilities': capabilities}
        config_path = folder / 'codex-worker.json'
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
        log = (folder / 'codex-worker.log').open('ab', buffering=0)
        try:
            process = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'studio.worker', '--config', str(config_path), '--server', server_url], cwd=core.ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, shell=False, **options)
        except OSError:
            log.close()
            hub.disconnect_worker(worker_id, registration['session_token'])
            if token_id:
                with accounts.transaction() as conn:
                    conn.execute('UPDATE api_tokens SET revoked_at=? WHERE id=?', (accounts.now(), token_id))
            raise
        record = {'process': process, 'log': log, 'worker_id': worker_id, 'session_token': registration['session_token'], 'data_root': core.data_root(), 'started_at': accounts.now(), 'api_token_id': token_id, 'owner_id': principal['user']['id'], 'workspace_id': principal['workspace']['id'], 'refresh_agent_catalog': True, 'catalog_stop': threading.Event()}
        _managed[_key()] = record
        record['catalog_thread'] = threading.Thread(target=_catalog_loop, args=(record,), name='studio-managed-catalog', daemon=True)
        record['catalog_thread'].start()
        return status(principal)


def stop(principal):
    if not allowed(principal):
        raise hub.HubError('没有本机执行器管理权限', 403)
    with _lock:
        record = _managed.get(_key())
        if record:
            stop_process(record['process'])
            _revoke(record)
            record['log'].close()
            _managed.pop(_key(), None)
        return status(principal)


def shutdown():
    with _lock:
        for record in list(_managed.values()):
            stop_process(record['process'])
            _revoke(record)
            record['log'].close()
        _managed.clear()

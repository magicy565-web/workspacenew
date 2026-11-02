"""Single-use approval ledger; NOT an authentication or tool-enforcement layer.

The host must supply a live authorizer, use a server-selected database path, and
route every protected action through consume(). See docs/action-approvals.md.
Consumption means permission was used, never that an external action succeeded.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import Callable
from uuid import uuid4

MAX_PAYLOAD_BYTES = 64 * 1024
MAX_TTL_MS = 24 * 60 * 60 * 1000
MAX_INTEGER = 2 ** 53 - 1
OPERATIONS = frozenset({'request', 'read', 'decide', 'revoke', 'consume'})


class ApprovalError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Authority:
    """Server-derived identity, grants and current resource-authority revision.

    revision is an opaque, non-secret fence for the action's effective authority,
    not merely the approver's role. The host must change it (or deny access) when
    the task, lease, tools, membership, source grants or budget policy changes.
    Never construct this object from an Agent's JSON or a browser request body.
    """
    subject_id: str
    workspace_id: str
    kind: str
    revision: str
    grants: frozenset[str]


def _text(value, label: str, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ApprovalError(f'Invalid {label}')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise ApprovalError(f'Invalid {label}') from exc
    return value  # Never silently normalize recipients, content or identifiers.


def _canonical(value) -> str:
    def check(item, depth=0):
        if depth > 32:
            raise ApprovalError('JSON nesting exceeds 32 levels')
        if item is None or type(item) in (str, bool):
            return
        if type(item) is int:
            if abs(item) > MAX_INTEGER:
                raise ApprovalError('JSON integer is outside the interoperable range')
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                check(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child, depth + 1)
            return
        raise ApprovalError('Only finite JSON values and string object keys are allowed')

    check(value)
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False)
        size = len(encoded.encode('utf-8'))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ApprovalError('Invalid JSON payload') from exc
    if size > MAX_PAYLOAD_BYTES:
        raise ApprovalError('Approval payload exceeds 64 KiB')
    return encoded


def _action(value) -> dict:
    keys = {'workspace_id', 'task_id', 'execution_id', 'executor_id',
            'action', 'parameters', 'budget'}
    if type(value) is not dict or set(value) != keys:
        raise ApprovalError('Action fields must match the approval contract')
    for key in keys - {'parameters', 'budget'}:
        _text(value[key], key)
    if type(value['parameters']) is not dict:
        raise ApprovalError('parameters must be an object')
    budget = value['budget']
    if type(budget) is not dict or set(budget) != {'currency', 'max_cost_minor'}:
        raise ApprovalError('An explicit currency and maximum cost are required')
    if not isinstance(budget['currency'], str) or not re.fullmatch(r'[A-Z]{3}', budget['currency']):
        raise ApprovalError('currency must be a three-letter uppercase code')
    if type(budget['max_cost_minor']) is not int or not 0 <= budget['max_cost_minor'] <= MAX_INTEGER:
        raise ApprovalError('max_cost_minor must be a non-negative integer')
    return json.loads(_canonical(value))


class ApprovalLedger:
    """Durable approval state in a host-selected SQLite database.

    authorize(operation, action) must consult current server-side authentication
    and authorization and return Authority. A missing/denying authorizer is an
    error, never implicit approval. No network, model, or tool calls are made here.
    """

    def __init__(self, database: str | Path,
                 authorize: Callable[[str, dict], Authority], *, clock_ms=None):
        if not callable(authorize):
            raise ApprovalError('A live authorizer is required', 403)
        self.database = Path(database)
        self.authorize = authorize
        self.clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS hub_action_approvals (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    action_key TEXT NOT NULL,
                    envelope_json TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    requester_id TEXT NOT NULL,
                    expires_at_ms INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK(state IN
                        ('pending','approved','rejected','revoked','consumed','expired')),
                    created_at_ms INTEGER NOT NULL,
                    decision_by TEXT,
                    consumption_id TEXT UNIQUE,
                    consumed_at_ms INTEGER,
                    UNIQUE(workspace_id, action_key)
                );
                CREATE TABLE IF NOT EXISTS hub_action_approval_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    approval_id TEXT NOT NULL REFERENCES hub_action_approvals(id),
                    event TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS hub_action_approval_events_lookup
                    ON hub_action_approval_events(approval_id,id);
            ''')

    @contextmanager
    def _connection(self, *, write=False):
        conn = sqlite3.connect(self.database, timeout=20, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('PRAGMA busy_timeout=20000')
            if write:
                conn.execute('BEGIN IMMEDIATE')
            else:
                conn.execute('BEGIN')
            yield conn
            if conn.in_transaction:
                conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def _time(self):
        stamp = self.clock_ms()
        if type(stamp) is not int or not 0 <= stamp <= MAX_INTEGER:
            raise ApprovalError('Invalid server clock')
        return stamp

    def _authorize(self, operation, action, *, revision=None):
        # The callback receives a deep copy and cannot rewrite the stored action.
        actor = self.authorize(operation, json.loads(_canonical(action)))
        if not isinstance(actor, Authority):
            raise ApprovalError('Authorization denied', 403)
        _text(actor.subject_id, 'authenticated subject')
        _text(actor.revision, 'authority revision')
        if (actor.kind not in ('human', 'worker') or
                actor.workspace_id != action['workspace_id'] or
                not isinstance(actor.grants, frozenset) or
                operation not in actor.grants):
            raise ApprovalError('Authorization denied', 403)
        if operation in ('decide', 'revoke') and actor.kind != 'human':
            raise ApprovalError('Human approval is required', 403)
        if operation == 'consume' and (actor.kind != 'worker' or
                                      actor.subject_id != action['executor_id']):
            raise ApprovalError('Approval is bound to a different executor', 403)
        if revision is not None and actor.revision != revision:
            raise ApprovalError('Action authority changed; a new approval is required', 409)
        return actor

    @staticmethod
    def _load(conn, approval_id):
        _text(approval_id, 'approval ID')
        row = conn.execute('SELECT * FROM hub_action_approvals WHERE id=?', (approval_id,)).fetchone()
        if row is None:
            raise ApprovalError('Approval not found', 404)
        return row, json.loads(row['envelope_json'])

    @staticmethod
    def _match(row, expected_digest):
        if not isinstance(expected_digest, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_digest):
            raise ApprovalError('An exact approval digest is required')
        if not secrets.compare_digest(row['digest'], expected_digest):
            raise ApprovalError('Action does not match the reviewed approval', 409)

    def _expire(self, conn, row, stamp):
        # Expiration is durable once observed; a later wall-clock correction must
        # not resurrect a permission. The host still needs a trustworthy clock.
        if row['state'] in ('pending', 'approved') and stamp >= row['expires_at_ms']:
            conn.execute("UPDATE hub_action_approvals SET state='expired' WHERE id=?", (row['id'],))
            self._event(conn, row['id'], 'expired', 'system:expiry', '', stamp)
            row, _ = self._load(conn, row['id'])
        return row

    @staticmethod
    def _event(conn, approval_id, event, actor_id, note, stamp):
        conn.execute('''INSERT INTO hub_action_approval_events
            (approval_id,event,actor_id,note,created_at_ms) VALUES (?,?,?,?,?)''',
                     (approval_id, event, actor_id, note, stamp))

    def _view(self, row, stamp):
        return {'id': row['id'], 'digest': row['digest'],
                'envelope': json.loads(row['envelope_json']),
                'state': row['state'], 'requester_id': row['requester_id'],
                'decision_by': row['decision_by'], 'consumption_id': row['consumption_id'],
                'consumed_at_ms': row['consumed_at_ms']}

    def request(self, action: dict, action_key: str, expires_at_ms: int) -> dict:
        action = _action(action)
        _text(action_key, 'action key')
        if type(expires_at_ms) is not int or not 0 <= expires_at_ms <= MAX_INTEGER:
            raise ApprovalError('Invalid approval expiry')
        with self._connection(write=True) as conn:
            actor = self._authorize('request', action)
            envelope = {'schema': 1, 'action': action, 'action_key': action_key,
                        'authority_revision': actor.revision, 'expires_at_ms': expires_at_ms}
            encoded = _canonical(envelope)
            digest = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
            stamp = self._time()
            row = conn.execute('''SELECT * FROM hub_action_approvals
                WHERE workspace_id=? AND action_key=?''', (action['workspace_id'], action_key)).fetchone()
            if row is not None:
                if row['requester_id'] != actor.subject_id:
                    raise ApprovalError('Action key belongs to another requester', 409)
                self._match(row, digest)
                row = self._expire(conn, row, stamp)
                return self._view(row, stamp)
            if not stamp < expires_at_ms <= stamp + MAX_TTL_MS:
                raise ApprovalError('Expiry must be in the next 24 hours')
            approval_id = uuid4().hex
            conn.execute('''INSERT INTO hub_action_approvals
                (id,workspace_id,action_key,envelope_json,digest,requester_id,
                 expires_at_ms,state,created_at_ms) VALUES (?,?,?,?,?,?,?,'pending',?)''',
                         (approval_id, action['workspace_id'], action_key, encoded, digest,
                          actor.subject_id, expires_at_ms, stamp))
            self._event(conn, approval_id, 'requested', actor.subject_id, '', stamp)
            row, _ = self._load(conn, approval_id)
            return self._view(row, stamp)

    def inspect(self, approval_id: str) -> dict:
        with self._connection(write=True) as conn:
            row, envelope = self._load(conn, approval_id)
            self._authorize('read', envelope['action'])
            stamp = self._time()
            row = self._expire(conn, row, stamp)
            result = self._view(row, stamp)
            result['events'] = [dict(event) for event in conn.execute('''SELECT id,event,actor_id,note,created_at_ms
                FROM hub_action_approval_events WHERE approval_id=? ORDER BY id''', (approval_id,))]
            return result

    def decide(self, approval_id: str, expected_digest: str,
               decision: str, note: str = '') -> dict:
        if decision not in ('approve', 'reject'):
            raise ApprovalError('Decision must be approve or reject')
        return self._transition(approval_id, expected_digest, 'decide', decision, note)

    def revoke(self, approval_id: str, expected_digest: str, note: str) -> dict:
        _text(note, 'revocation reason', 2000)
        return self._transition(approval_id, expected_digest, 'revoke', 'revoke', note)

    def consume(self, approval_id: str, expected_digest: str) -> dict:
        """Atomically use approval once. A receipt is NOT evidence of execution.

        Never retry an external call by replaying this receipt or creating a new
        action key. A crash after consumption requires external-result review.
        """
        return self._transition(approval_id, expected_digest, 'consume', 'consume', '')

    def _transition(self, approval_id, expected_digest, operation, decision, note):
        if not isinstance(note, str) or len(note) > 2000:
            raise ApprovalError('Decision note must be at most 2000 characters')
        _canonical(note)
        with self._connection(write=True) as conn:
            row, envelope = self._load(conn, approval_id)
            actor = self._authorize(operation, envelope['action'],
                                    revision=None if operation == 'revoke' else envelope['authority_revision'])
            self._match(row, expected_digest)
            stamp = self._time()  # Check again after a potentially slow authorizer.
            row = self._expire(conn, row, stamp)
            state = row['state']
            allowed = {'decide': ('pending',), 'revoke': ('pending', 'approved'), 'consume': ('approved',)}
            error = None
            result = None
            if state not in allowed[operation]:
                # Commit observed expiration before returning the rejection.
                error = ApprovalError(f'Cannot {operation} an approval in state {state}', 409)
            else:
                next_state = {'approve': 'approved', 'reject': 'rejected',
                              'revoke': 'revoked', 'consume': 'consumed'}[decision]
                if operation == 'consume':
                    conn.execute('''UPDATE hub_action_approvals SET state=?,consumption_id=?,consumed_at_ms=?
                        WHERE id=?''', (next_state, uuid4().hex, stamp, approval_id))
                else:
                    conn.execute('UPDATE hub_action_approvals SET state=?,decision_by=? WHERE id=?',
                                 (next_state, actor.subject_id, approval_id))
                self._event(conn, approval_id, next_state, actor.subject_id, note, stamp)
                row, _ = self._load(conn, approval_id)
                result = self._view(row, stamp)
        if error is not None:
            raise error
        return result

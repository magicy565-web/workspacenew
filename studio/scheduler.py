"""Tick every registered workspace without inheriting HTTP request identity."""
from __future__ import annotations

import contextlib
import sqlite3

from . import accounts, core, hub, hub_extensions


def tick_all(now_iso=None):
    output = {'workspaces': [], 'errors': []}
    roots = {'local': core.base_data_root()}
    try:
        if accounts.has_users():
            with contextlib.closing(accounts.db()) as conn:
                registered = [dict(row) for row in conn.execute('SELECT id,storage_key FROM workspaces')]
            for workspace in registered:
                try:
                    root = accounts.data_directory({'workspace': workspace})
                    if root == core.base_data_root():
                        roots.pop('local', None)
                    roots[workspace['id']] = root
                except hub.HubError:
                    output['errors'].append({'workspace_id': workspace['id'], 'error': 'invalid_workspace_storage'})
    except (OSError, sqlite3.Error):
        output['errors'].append({'workspace_id': 'registry', 'error': 'workspace_registry_unavailable'})
        return output
    actor_var = getattr(core, 'REQUEST_ACTOR', None)
    visited = set()
    for workspace_id, root in roots.items():
        if root in visited:
            continue
        visited.add(root)
        data_binding = core.REQUEST_DATA_ROOT.set(root)
        actor_binding = actor_var.set(None) if actor_var is not None else None
        try:
            result = hub_extensions.tick_due(now_iso)
            output['workspaces'].append({'workspace_id': workspace_id, **result})
        except (OSError, sqlite3.Error, hub.HubError):
            output['errors'].append({'workspace_id': workspace_id, 'error': 'scheduler_storage_or_configuration_error'})
        finally:
            if actor_var is not None:
                actor_var.reset(actor_binding)
            core.REQUEST_DATA_ROOT.reset(data_binding)
    return output

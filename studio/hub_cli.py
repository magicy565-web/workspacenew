"""JSON interface for agent hosts with shell access; shares the web workspace database."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import parse_qs, urlsplit

from . import hub, hub_http


def main(argv=None):
    parser = argparse.ArgumentParser(description='Agent Workspace JSON CLI (same operations as /api/hub)')
    parser.add_argument('method', choices=['GET', 'POST', 'PUT', 'DELETE'])
    parser.add_argument('path', help='For example /bootstrap, /rooms, /tasks/claim, /artifacts/ID/download')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--json', dest='json_text', help='JSON request object; prefer --file for larger requests')
    source.add_argument('--file', help='UTF-8 JSON request file, or - for stdin')
    parser.add_argument('--out', help='Save exact response bytes to this file (required for downloads)')
    args = parser.parse_args(argv)
    try:
        url = urlsplit(args.path)
        if url.scheme or url.netloc or not url.path.startswith('/'):
            raise ValueError('path must be a local API path beginning with /')
        path = url.path if url.path.startswith('/api/hub/') else '/api/hub' + url.path
        raw = args.json_text
        if args.file:
            raw = sys.stdin.read() if args.file == '-' else Path(args.file).read_text(encoding='utf-8-sig')
        body = json.loads(raw) if raw else {}
        if not isinstance(body, dict):
            raise ValueError('request must be a JSON object')
        if args.method == 'GET' and path == '/api/hub/connection':
            from .server import connection_config
            response = (200, connection_config(), 'application/json', {})
        else:
            response = hub_http.dispatch(args.method, path, body, parse_qs(url.query, keep_blank_values=True))
        if response is None:
            raise ValueError('API operation not found')
        status, payload, mime, _headers = response
        if status >= 400:
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
            return 1
        if isinstance(payload, (dict, list)):
            rendered = json.dumps(payload, ensure_ascii=False)
            data = (rendered + '\n').encode('utf-8')
        else:
            data = payload.encode('utf-8') if isinstance(payload, str) else payload
            rendered = None
        if args.out:
            target = Path(args.out)
            target.write_bytes(data)
            print(json.dumps({'file': str(target.resolve()), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}, ensure_ascii=False))
        elif rendered is not None:
            print(rendered)
        else:
            raise ValueError('Use --out to save this content response')
    except (hub.HubError, ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

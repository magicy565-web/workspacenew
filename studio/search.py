"""Literal, workspace-scoped keyword lookup with source identifiers."""
import contextlib
from . import hub


def _snippet(text, query):
    position = text.casefold().find(query.casefold())
    start = max(0, position - 55)
    return ('…' if start else '') + text[start:start + 240] + ('…' if len(text) > start + 240 else '')


def find(query):
    query = hub._text(query, '搜索词', 200)
    escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    like = '%' + escaped + '%'
    results = []
    with contextlib.closing(hub._db()) as conn:
        conn.execute('BEGIN')
        rooms = conn.execute("SELECT r.id,r.title,(SELECT text FROM hub_messages m WHERE m.room_id=r.id AND m.text LIKE ? ESCAPE '\\' ORDER BY rowid DESC LIMIT 1) AS matched FROM hub_rooms r WHERE r.title LIKE ? ESCAPE '\\' OR EXISTS(SELECT 1 FROM hub_messages m WHERE m.room_id=r.id AND m.text LIKE ? ESCAPE '\\') ORDER BY r.updated_at DESC LIMIT 20", (like, like, like))
        for row in rooms:
            results.append({'type': 'room', 'id': row['id'], 'room_id': row['id'], 'title': row['title'], 'snippet': _snippet(row['matched'] or row['title'], query)})
        sources = conn.execute("WITH active_sources AS (SELECT id,room_id,title,origin,source_url,version,created_at,CASE WHEN origin='uploaded_file' THEN CASE WHEN instr(content,char(10)||char(10))>0 THEN substr(content,instr(content,char(10)||char(10))+2) ELSE '' END ELSE content END AS body FROM hub_sources WHERE revoked_at IS NULL) SELECT * FROM active_sources WHERE title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\' ORDER BY created_at DESC LIMIT 20", (like, like))
        for row in sources:
            results.append({'type': 'source', 'id': row['id'], 'room_id': row['room_id'], 'title': row['title'], 'snippet': _snippet(row['body'] or row['title'], query), 'version': row['version'], 'source_url': row['source_url']})
        artifacts = conn.execute("SELECT a.id,a.room_id,a.title,a.version,v.content FROM hub_artifacts a JOIN hub_artifact_versions v ON v.artifact_id=a.id AND v.version=a.version WHERE a.title LIKE ? ESCAPE '\\' OR v.content LIKE ? ESCAPE '\\' ORDER BY a.updated_at DESC LIMIT 20", (like, like))
        for row in artifacts:
            results.append({'type': 'artifact', 'id': row['id'], 'room_id': row['room_id'], 'title': row['title'], 'snippet': _snippet(row['content'], query), 'version': row['version']})
        skills = conn.execute("SELECT s.id,s.name,s.version,v.content,s.source_url FROM hub_skills s JOIN hub_skill_versions v ON v.skill_id=s.id AND v.version=s.version WHERE s.archived_at IS NULL AND (s.name LIKE ? ESCAPE '\\' OR v.content LIKE ? ESCAPE '\\') ORDER BY s.updated_at DESC LIMIT 20", (like, like))
        for row in skills:
            results.append({'type': 'skill', 'id': row['id'], 'title': row['name'], 'snippet': _snippet(row['content'], query), 'version': row['version'], 'source_url': row['source_url']})
    return {'query': query, 'results': results[:50]}

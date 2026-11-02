# Files, search and exact-version sharing

All authenticated API paths below are relative to `/api/hub` and use the selected workspace.

- `POST /rooms/{id}/files` `{filename,data_base64}` -> File (201). Accept UTF-8 text/Markdown/CSV/JSON, PDF, DOCX and PNG/JPEG/WebP images, max 8 MiB. Preserve original bytes. Text extraction has resource and size limits, never claims OCR. Source metadata records exact file hash and extraction status; extracted text enters the room's sources. Images and scanned PDFs remain downloadable with explicit `image_only`/`no_text` status.
- `GET /rooms/{id}/files` -> `{files:[File]}`; `GET /files/{id}` -> File; `GET /files/{id}/download` -> exact attachment bytes. `DELETE /files/{id}` -> File with revoked_at; also revokes its source. Download after revocation returns 410.
- File: `{id,room_id,filename,mime,bytes,sha256,source_id,extraction_status,warning,created_at,revoked_at,download_url}`. No raw base64 in ordinary responses.
- `GET /search?q=...` -> `{query,results:[{type:'room'|'source'|'artifact'|'skill',id,room_id?,title,snippet,version?,source_url?}]}`. Searches only the current workspace, max 50 matches, active sources/skills and current artifact versions. Simple keyword lookup, not a claimed vector memory or external web search.
- `POST /artifacts/{id}/shares` `{version,expires_days?:7}` -> `{share,token,url}`. Creates an unguessable view link for exactly that saved version, valid 1–30 days. The raw token is returned only at creation. Later edits do not change shared content. `GET /artifacts/{id}/shares` -> `{shares:[Share]}`; `DELETE /artifacts/{id}/shares/{share_id}` -> `{ok:true}` revokes it.
- Share: `{id,artifact_id,version,title,kind,created_at,expires_at,revoked_at}`. Only an editor/owner can create or revoke; read-only members can see metadata. API tokens need write scope for creation/revocation.
- Public `GET /share/{token}` displays the pinned content with restrictive sandbox CSP; expired/revoked/unknown tokens return 404. URL uses the configured HTTPS public origin when deployed, or the actual loopback origin locally. Local links only work on that machine; UI must communicate this at copy time. No source files, conversation, worker details or credentials are exposed.

Interface: room sources offers a real file picker and extraction feedback; keep original download and revoke controls. Workspace-wide search opens the result's original room/artifact/skill. Artifact sharing lives alongside download/version controls and always names the selected version. These are persisted actions, not cosmetic buttons.

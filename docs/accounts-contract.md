# Accounts and workspace isolation

Local use remains available without an account until the owner enables accounts. `STUDIO_AUTH_MODE=required` requires authentication from startup. `STUDIO_PUBLIC_ORIGIN=https://your-domain` also requires accounts; public startup must have an initialized owner. The service still binds loopback unless explicitly configured for a trusted reverse proxy.

All paths below are under `/api/account`. Errors are `{error}` with 400/401/403/404/409. Mutations require same-origin JSON. HTTP session cookies are HttpOnly, SameSite=Strict and Secure when public HTTPS is configured. Passwords are salted scrypt hashes. API tokens and invitations are stored as hashes.

- `GET /session` -> `{mode:'local'|'authenticated',setup_available,user:null|{id,email,name},workspaces:[{id,name,role}],workspace:null|{id,name,role}}`. Anonymous authenticated mode is a login gate. Local mode continues existing usage.
- `POST /setup` `{email,name,password,workspace_name?}` -> session plus session cookie. Only the first user; available from loopback without a public origin. Enables accounts persistently and keeps existing data in the first workspace. Password >=12 characters.
- `POST /login` `{email,password}` -> session plus cookie.
- `POST /logout` `{}` -> `{ok:true}` and expired cookie.
- `POST /workspaces` `{name}` -> workspace, selects it for the current session. Each workspace has isolated durable data.
- `POST /workspaces/{id}/select` `{}` -> session.
- `GET /members` -> `{members:[{id,email,name,role}],invitations:[{id,email,role,expires_at,accepted_at,revoked_at}]}` for current workspace owner.
- `POST /invitations` `{email,role:'editor'|'viewer'}` -> `{invitation,token}`. Token is returned once; UI creates a local `/join?token=...` link; no email is sent.
- `POST /invitations/accept` `{token,email,name?,password}` -> session. New members set their password; existing members verify their current password. Selects invited workspace.
- `DELETE /invitations/{id}` -> `{ok:true}` (owner only).
- `PUT /members/{id}` `{role:'editor'|'viewer'}` -> member; cannot change owner through this route.
- `DELETE /members/{id}` -> `{ok:true}`; cannot remove owner. Membership revocation immediately prevents that user's sessions and API tokens from accessing this workspace.
- `GET /tokens` -> `{tokens:[{id,name,scopes,created_at,last_used_at,revoked_at}]}` for own tokens in current workspace.
- `POST /tokens` `{name,scopes:['read','write','execute']}` -> `{token,record}`. Token returned once; scope cannot exceed membership. Use Authorization: Bearer token. execute permits claim/heartbeat/results and worker lifecycle; write permits ordinary workspace mutations. Neither grants account administration.
- `DELETE /tokens/{id}` -> `{ok:true}` for own token.
- `GET /audit?after=0` -> `{events,cursor}` (owner). Stores actor, method, route, HTTP status and timestamp, never request bodies or credentials.

Normal UI and API calls use the selected workspace. Viewer can read, editor can operate, owner can manage members. Account/API sessions are separate from task leases. Public API tokens are workspace-scoped. Local stdio MCP and CLI processes are privileged filesystem clients; use a remote MCP adapter with a scoped token when sharing.

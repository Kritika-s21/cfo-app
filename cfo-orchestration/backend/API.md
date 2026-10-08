# CFO Back Office API (default port: `8766`; base: `/api/v1`, docs: `/docs`, spec: `/openapi.json`)

Auth: send `X-API-Key: <key>` (or `Authorization: Bearer <key>`). Keys come from env `CFO_API_KEYS` (comma-separated).
All protected `/api/v1` routes require a valid browser account session or configured API key; anonymous access is rejected. Browser sessions use an HttpOnly, SameSite=Strict cookie, expire after 12 hours, and are revoked at logout. `CFO_CORS_ORIGINS` must list the exact frontend origins; set `AUTH_COOKIE_SECURE=1` when TLS terminates at a reverse proxy. `GET /health` is open.

| Method | Path | Purpose |
|---|---|---|
| POST | /auth/register | Create account (email, name, password of 12+ characters) and start a session |
| POST | /auth/login | Validate account credentials and start a session |
| GET | /auth/me | Return the current session's account |
| POST | /auth/logout | Revoke the current session |

Passwords are stored as salted PBKDF2-HMAC-SHA256 hashes; raw passwords and session tokens are not persisted. Set `CFO_AUTH_SQL_CONNECTION_STRING` to the existing SQL Server database (or use `SQL_CONNECTION_STRING`) to store accounts in separate `dbo.CFOAgentUsers`, `dbo.CFOAgentSessions`, and `dbo.CFOAgentLoginFailures` tables, isolated from `dbo.FileIngestion`. The value may be a SQLAlchemy URL or ODBC-style connection string. Without a SQL Server connection, local development falls back to SQLite at `auth.sqlite3` beside `main.py` (override with `CFO_AUTH_DATABASE_PATH`). Back up the account database securely.

| Method | Path | Purpose |
|---|---|---|
| GET | /agents | Agents with their EzCoworker skills and applicable policies |
| GET | /agents/{id} | One agent |
| POST | /agents/{id}/run | Run synchronously: policy retrieval -> EzCoworker (agent skills) -> JSON result |
| POST | /agents/{id}/runs | Start async run (202) -> `run_id` |
| POST | /agents/{id}/context | Preview the policy context the agent would receive |
| GET | /runs, /runs/{run_id} | Run history / status / result (audit trail) |
| GET | /runs/latest | Latest successful run per agent (result, alerts, files); feeds the dashboard cards |
| POST | /files | Upload a data file to the workspace; records columns and detected reporting period (`date_range`) |
| GET | /files?q=&source= | Every workspace file with `columns` and `date_range`; `q` searches names and column headers |
| DELETE | /files/{name} | Hide a file from the picker (workspace copy untouched) |
| GET | /skills, /skills/{id} | Skill registry (markdown-defined) |
| PUT | /skills/{id} | Create/update a skill definition |
| POST | /skills/route | Which skill the router would pick for an agent + text |
| POST | /skills/reload | Re-read skills/*.md |
| GET | /runs/{id}/events | Pipeline events for one run |
| GET | /logs | Log registry; filter `agent_id`, `type` prefix (e.g. `alert`) |
| GET/POST | /schedules | List / create cron schedules |
| PUT/DELETE | /schedules/{id} | Update / delete |
| POST | /schedules/{id}/run-now | Queue a schedule immediately |
| GET | /knowledge/status | LanceDB / graph / policy-doc counts |
| GET | /policies | List; filters `category`, `agent_id`, `critical` |
| GET | /policies/{id} | One policy (frontmatter + markdown body) |
| POST | /policies/{id} | Create (409 if exists) |
| PUT | /policies/{id} | Update; previous version archived |
| DELETE | /policies/{id} | Delete (archived first) |
| GET | /policies/{id}/versions | Revision history |
| GET | /policies/{id}/impact | Agents + policies affected by a change |
| POST | /policies/search | Vector search + graph expansion |
| GET | /policies/graph | Nodes/edges for a graph view |
| POST | /policies/reindex | Rebuild chunks, vectors, graph from markdown |

Example
```
curl -X POST localhost:8765/api/v1/agents/cash_forecaster/run -H "X-API-Key: $K" -H "Content-Type: application/json" \
  -d '{"text":"Do wires above $50K need approval?"}'
# -> {"skill":{"id":"budget-management","routed_by":"trigger"},"structured_output":{"summary","report","alerts","files"},"run_id":"…","result":{"answer":"…","policy_cited":"POL-007, POL-003"},"policies_used":["POL-007","POL-003"],"skills":[…]}
```

# CFO orchestration backend

```
pip install -r requirements.txt
cp .env.example .env     # set EZCOWORKER_API_KEY; configure origins and secure cookies for deployment
uvicorn main:app --port 8766      # docs: http://localhost:8766/docs   (or: python main.py)
python3 test_api.py               # offline tests (EzCoworker mocked)
python3 test_auth.py              # focused account/session tests
```

Browser accounts use salted PBKDF2-HMAC-SHA256 password hashes. Login sessions are random,
HttpOnly cookies that expire after 12 hours and can be revoked at logout. For shared hosted
accounts, set `CFO_AUTH_SQL_CONNECTION_STRING` (or `SQL_CONNECTION_STRING`) to the existing
SQL Server database. The auth store creates separate `dbo.CFOAgentUsers`,
`dbo.CFOAgentSessions`, and `dbo.CFOAgentLoginFailures` tables; it does not use or modify
`dbo.FileIngestion`. Locally, SQLite is used only when no SQL Server connection is configured.

For production, set `CFO_CORS_ORIGINS` to the exact frontend origins and
`AUTH_COOKIE_SECURE=1` when HTTPS is provided by a reverse proxy. Do not set browser-exposed
`VITE_*` API keys; `CFO_API_KEYS` is only for trusted server clients.

## Flow (matches the architecture diagram)

```
CFO User / System (React UI, API clients, Scheduler)
        |
EzCoworker orchestration  (orchestrator.py)
  1. Skill Router ........ skill_registry.py  + skills/*.md  (Financial Analysis, Budget Management,
                            Compliance Policy Check, Reporting Generator, File Management)
  2. Knowledge layer ..... LanceDB vector store (policy/vectors.py) + Graph KB (policy relations, policy_graph.json)
                            + Policy Docs (policies/*.md)
  3. EzCoworker call ..... /chat/stream with enabledSkills from the skill file, skill instructions + policies in the prompt
  4. Structured Output ... report / alerts / files (+ summary)
  5. Log Registry ........ events.jsonl (run.started, skill.routed, knowledge.queried, ezcoworker.called, alert.critical, run.succeeded/failed)
  6. Scheduler ........... scheduler_service.py (cron + worker pool); same orchestrate() as users
```
Skills, policies and schedules are data (markdown / JSON), editable through the API without code changes.
`ezcoworker_skills` in each skill file must name skills that exist in your EzCoworker workspace.
Use EMBEDDINGS=openai or sentence-transformers in real use; `hash` is only a dev stand-in.

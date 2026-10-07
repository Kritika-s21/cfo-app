# CFO Orchestration (merged)

```
backend/         CFO Back Office API  (FastAPI, port 8765)  - agents, policies, skills, EzCoworker, cron schedules
file-scheduler/  File Pickup Scheduler (FastAPI, port 8766) - scans sources, ingests files, hands them to the CFO agents
cfo-app/         React UI (Vite, port 5173) - chat/agents + Scheduler pages
```

## Run (three terminals)
```bash
# 1. CFO backend
cd backend && cp .env.example .env      # set EZCOWORKER_API_KEY, CFO_API_KEYS
pip install -r requirements.txt && python main.py          # http://localhost:8765/docs, logs in ./logs

# 2. File scheduler
cd file-scheduler && cp .env.example .env && cp cfo_rules.example.json cfo_rules.json
pip install -r requirements.txt && python api_server.py    # http://localhost:8766

# 3. UI
cd cfo-app && cp .env.example .env      # VITE_API_BASE=:8766, VITE_CFO_API_BASE=:8765, VITE_CFO_API_KEY
npm install && npm run dev              # http://localhost:5173
```

## Data flow
Scheduled/triggered scan -> new/modified file -> extract + classify + ingest (SQL / vector) ->
`cfo_bridge` uploads it to EzCoworker as `input/<name>` via backend `POST /api/v1/files` ->
rules in `cfo_rules.json` run the matching agent/skill -> result shows in the CFO UI / run logs.
Rules match on filename (`match`), on column headers (`columns_all` / `columns_any`), or both, so a file can have any name.
The backend reads each file's header row and reporting period when it arrives, so the chat picker and the rules both see them.
The dashboard cards show each agent's latest real run (`GET /api/v1/runs/latest`). If the backend is unreachable the UI says so;
sample figures appear only with `VITE_ALLOW_DEMO_DATA=1`, and are labelled as demo data.
In the chat UI, attaching a file does the same upload, so users can run any skill on it manually.

## Notes
- `file-scheduler/api_server.py` imports `presentation_agent` - copy your `presentation_agent.py` into `file-scheduler/`.
- Each service has its own `.env` (LOG_DIR means different folders for the two backends: `logs/` vs `agent_logs/`).

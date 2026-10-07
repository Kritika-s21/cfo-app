# CFO orchestration backend

```
pip install -r requirements.txt
cp .env.example .env     # set EZCOWORKER_API_KEY and CFO_API_KEYS
uvicorn main:app --port 8766      # docs: http://localhost:8766/docs   (or: python main.py)
python3 test_api.py               # offline tests (EzCoworker mocked)
```

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

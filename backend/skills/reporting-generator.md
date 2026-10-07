---
id: reporting-generator
name: Reporting Generator
description: Builds structured management / board reports (P&L, cash flow, close status) from verified numbers.
agents: [financial_analyst, close_orchestrator, review, cash_forecaster, entity_consolidator, dispatch]
ezcoworker_skills: [data-analyst]
priority: 3
triggers: [report, board pack, board-level, summary, statement, cash flow statement, mis, dashboard, close status, presentation, executive]
policies: [POL-005]
knowledge: [policy_docs, vector]
outputs: [report, files]
---
## Purpose
Assemble a clear, structured report. Do not invent numbers: use figures from the request, uploaded data or earlier agent results.

## Steps
1. Choose the layout: P&L (IAS 1), cash flow (IAS 7 indirect), board pack, or close status.
2. Fill each section with figures plus one-line commentary.
3. List assumptions and data gaps at the end.

## Output
JSON: `answer`, `analysis`, `report` = {title, period, sections: [{heading, body, table?}]}, `files` (list of {name, type} the UI may offer to export), `policy_cited`, `alerts`.

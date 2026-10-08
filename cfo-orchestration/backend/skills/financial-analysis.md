---
id: financial-analysis
name: Financial Analysis
description: Variance, ratio, reconciliation and working-capital analysis on ledger / uploaded data.
agents: [financial_analyst, reconciliation, rev_recognition, cash_forecaster, wc_optimizer, ar_engine, ap_engine, entity_consolidator, segment_mapper, gl_harmonizer, close_orchestrator, dispatch]
ezcoworker_skills: [data-analyst]
priority: 1
triggers: [variance, margin, p&l, pnl, profit, revenue, sales, cogs, reconcil, ratio, dso, dpo, dio, working capital, aging, anomal, trend, consolidat, forecast]
policies: [POL-001, POL-005]
knowledge: [vector, graph, policy_docs]
outputs: [report, alerts]
---
## Purpose
Turn ledger data or an uploaded file into verified numbers with a short explanation.

## Steps
1. Detect period / currency / entity from the data. State them in the answer.
2. Compute only from supplied data; if a figure is missing, say so — never estimate silently.
3. Compare to prior period and budget when available; flag outliers.
4. Check results against the cited policies and list any breach under `alerts`.

## Formulas (editable by Finance)
- Variance = Actual − Budget; Variance % = Variance ÷ Budget × 100
- Gross margin % = (Net sales − COGS) ÷ Net sales × 100
- DSO = Receivables ÷ Credit sales × days in period; DPO = Payables ÷ COGS × days; DIO = Inventory ÷ COGS × days
- Cash conversion cycle = DSO + DIO − DPO
- Reconciliation: items match when |A − B| ≤ tolerance (default ₹1 unless a policy says otherwise)
- Anomaly: value beyond 2 standard deviations of the trailing 6-period mean

## Output
JSON: `answer`, `analysis`, `metrics` (object), `policy_cited`, `alerts` (list of {severity, message, policy}), `report` (optional {title, sections[]}).

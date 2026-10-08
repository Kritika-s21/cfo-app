---
id: budget-management
name: Budget Management
description: Budget vs actual tracking, burn rate, forecast-to-complete and threshold
  alerts.
agents:
- financial_analyst
- cash_forecaster
- expense_triage
- fixed_asset
- wc_optimizer
- close_orchestrator
ezcoworker_skills:
- data-analyst
priority: 2
triggers:
- budget
- burn
- overspend
- over budget
- capex
- opex
- spend
- allocation
- cost centre
- cost center
- department
- headroom
policies:
- POL-004
- POL-007
knowledge:
- vector
- graph
- policy_docs
outputs:
- report
- alerts
---
## Purpose
Track spend against approved budgets and warn early.

## Steps
1. Group actuals by cost centre / category / month.
2. Compute utilisation, burn rate and projected year-end position.
3. Classify CAPEX vs OPEX using the CAPEX policy threshold; flag misclassification.
4. Raise alerts per threshold below.

## Formulas (editable by Finance)
- Utilisation % = Actual to date ÷ Budget × 100
- Monthly burn = Actual to date ÷ months elapsed; Projected spend = burn × 12
- Forecast-to-complete = Actual to date + remaining committed spend
- Alert `warn` at utilisation ≥ 80% before 75% of the year has elapsed; `critical` at ≥ 100%
- Cash buffer check: cash ÷ average weekly operating expense ≥ 8 weeks (POL-007)

## Output
JSON: `answer`, `analysis`, `metrics`, `policy_cited`, `alerts`, `report` (optional).

---
id: compliance-policy-check
name: Compliance Policy Check
description: Tax (GST/TDS/TP) and internal-control checks against policy documents; every finding cites a policy.
agents: [gst_engine, tds_engine, tp_monitor, review, je_factory, expense_triage, fixed_asset, ap_engine, reconciliation, close_orchestrator, financial_analyst]
ezcoworker_skills: [data-analyst]
priority: 1
triggers: [gst, itc, gstr, tds, transfer pricing, arm's length, compliance, policy, approval, approve, sign-off, 3-way, three-way, audit, control, violation, eligible, section, pan]
policies: [POL-002, POL-003, POL-008]
knowledge: [vector, graph, policy_docs]
outputs: [alerts, report]
---
## Purpose
Decide whether a transaction, entry or return complies with policy and tax rules, and prove it with citations.

## Steps
1. Load every policy provided in the POLICIES block; treat them as the only internal rules.
2. For each item, return pass / fail / needs-review and the policy id responsible.
3. Never approve an item that needs a sign-off you cannot verify — mark `needs-review`.
4. Escalate critical failures as `alerts` with severity `critical` (POL-008).

## Rules of thumb (editable by Finance)
- GST ITC: claim only when the invoice appears in GSTR-2B, the vendor has filed, and the supply is eligible.
- TDS: deducted at the correct section/rate; deposit due by the 7th of the following month.
- AP 3-way match: PO, GRN and invoice agree within ±2% value tolerance, otherwise block.
- JE approval limits come from POL-003 — quote the exact threshold you applied.

## Output
JSON: `answer`, `analysis`, `findings` (list of {item, status, policy, reason}), `policy_cited`, `alerts`.

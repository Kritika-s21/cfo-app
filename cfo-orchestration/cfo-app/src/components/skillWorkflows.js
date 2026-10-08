// Maps every CFO skill to the agent that runs it
export const SKILL_AGENT = {
  "GL Harmonization": "gl_harmonizer",
  "Entity Consolidation": "entity_consolidator",
  "Segment Mapping": "segment_mapper",
  "IC Elimination": "entity_consolidator",
  "GST Reconciliation": "gst_engine",
  "TDS Classification": "tds_engine",
  "Transfer Pricing": "tp_monitor",
  "ITC Validation": "gst_engine",
  "GSTR Filing": "gst_engine",
  "WHT Compliance": "tds_engine",
  "Bank Reconciliation": "reconciliation",
  "Expense Triage": "expense_triage",
  "Revenue Recognition": "rev_recognition",
  "Fixed Asset Intel": "fixed_asset",
  "AP 3-Way Match": "ap_engine",
  "AR Prediction": "ar_engine",
  "Close Orchestration": "close_orchestrator",
  "JE Factory": "je_factory",
  "P&L Summary": "financial_analyst",
  "Variance Analysis": "financial_analyst",
  "Board Pack": "financial_analyst",
  "Cash Flow": "financial_analyst",
  "13-Week Forecast": "cash_forecaster",
  "Working Capital CCC": "wc_optimizer",
  "Compliance QA Gate": "review",
};

// Per-skill rich content (optional). Skills not listed use the generic config.
// A table needs "Confidence" and "Status" columns for the bar/badge styling.
export const SKILL_OVERRIDES = {
  "GL Harmonization": {
    title: "GL Harmonization Engine",
    subtitle:
      "Semantic mapping of GL accounts across ERP systems. Reads all charts of accounts, applies embedding-based similarity matching, flags conflicts and unmapped accounts, and produces the unified uCOA mapping table.",
    inputs: ["QuickBooks CoA", "SAP GL master", "Tally ledgers", "Xero accounts"],
    systems: [
      { name: "QuickBooks Online", entity: "Meridian Logistics LLC", cur: "USD", accts: 847 },
      { name: "SAP S/4HANA", entity: "TechForge Solutions GmbH", cur: "EUR", accts: 2341 },
      { name: "Tally Prime", entity: "GreenWave India Pvt Ltd", cur: "INR", accts: 312 },
      { name: "Xero", entity: "Heritage Building AU Pty", cur: "AUD", accts: 189 },
    ],
    stats: [
      ["Systems connected", 4, "#1d4ed8"],
      ["Total mappings", 14, "#1e293b"],
      ["Auto-matched", 9, "#047857"],
      ["Needs review", 2, "#b45309"],
      ["Conflicts", 1, "#dc2626"],
      ["Unmapped", 2, "#dc2626"],
    ],
    columns: ["uCOA Code", "Unified Name", "QuickBooks", "SAP S/4", "Tally", "Xero", "Confidence", "Status"],
    rows: [
      ["4100", "Service Revenue", "4000 · Sales Income", "800000 · Revenue from Services", "Sales A/c", "200 · Revenue", 97, "MATCHED"],
      ["4300", "Freight Revenue", "4010 · Freight Income", "800100 · Freight Revenue", "Freight Charges Rec.", "220 · Freight Income", 91, "MATCHED"],
      ["5100", "Direct Labor", "5000 · Payroll Expenses", "600000 · Wages & Salaries", "Salary A/c", "477 · Wages", 88, "MATCHED"],
      ["5200", "Fuel Costs", "5020 · Fuel & Gas", "610000 · Fuel Expenses", "Petrol & Diesel", "453 · Motor Vehicle Exp", 74, "REVIEW"],
      ["5400", "Insurance — Ops", "5040 · Insurance", "620000 · Insurance Premiums", "Insurance Charges", "461 · Insurance", 95, "MATCHED"],
      ["6100", "Mgmt Salaries", "6000 · Officer Compensation", "700000 · Executive Remuneration", "Directors Remun.", "477 · Wages", 61, "CONFLICT"],
      ["6300", "Software & Tech", "6070 · Computer Expenses", "720000 · IT Costs", "Computer Expenses", "489 · Subscriptions", 82, "MATCHED"],
      ["6500", "Professional Fees", "6020 · Professional Fees", "730000 · Legal & Consulting", "Audit & Legal Fees", "404 · Accountants Fees", 93, "MATCHED"],
      ["1200", "Accounts Receivable", "1100 · Accounts Receivable", "130000 · Trade Receivables", "Sundry Debtors", "120 · Trade Debtors", 99, "MATCHED"],
    ],
    pipeline: [
      ["POL-001 + POL-008", "Load policies", "POL-001 v1.3 (uCOA definitions) · POL-008 v1.0 (unmapped escalation rules)"],
      ["Ingest", "Ingest QuickBooks CoA", "847 accounts loaded"],
      ["Ingest", "Ingest SAP GL master", "2,341 accounts loaded · cost centers included"],
      ["Ingest", "Ingest Tally ledgers", "312 ledgers loaded"],
      ["Ingest", "Fetch Xero via API", "189 accounts fetched"],
      ["POL-001 §1", "Semantic match pass 1 (name embedding)", "Cosine similarity ≥ 0.90 → 9 exact semantic matches"],
      ["POL-001 §2", "Fuzzy match pass 2 (string similarity)", "SequenceMatcher ≥ 0.70 → 5 additional matches"],
      ["POL-008", "Flag conflicts & unmapped accounts", "1 conflict · 2 unmapped → review gate"],
    ],
  },
};

// Builds a full workflow definition for ANY skill
export function buildWorkflow(skill, agents, skillsByAgent) {
  const agentId = SKILL_AGENT[skill.name];
  const agent = agents.find((a) => a.id === agentId) || agents[0];
  const ov = SKILL_OVERRIDES[skill.name] || {};
  const genericPipeline = [
    ["POL-008", "Load policies", agent.policies.join(" · ")],
    ["Ingest", "Read uploaded file & detect period", "Excel / CSV parsed in-browser"],
    ...(skillsByAgent[agent.id] || []).slice(0, 3).map((s) => ["Skill", `Execute ${s}`, agent.name]),
    ["POL-008", "Apply policy checks", "policy_cited attached to output"],
  ];
  return {
    skill,
    agent,
    title: skill.name,
    subtitle: agent.desc,
    inputs: ["Excel / CSV ledger export"],
    pipeline: genericPipeline,
    ...ov,
  };
}

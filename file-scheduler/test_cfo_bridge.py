"""Offline test for cfo_bridge rule matching (no network, no EzCoworker). Run:  python test_cfo_bridge.py"""
import json, os, sys, tempfile
from pathlib import Path

d = tempfile.mkdtemp()
rules_path = Path(d) / "rules.json"
os.environ.update(CFO_BRIDGE_ENABLED="1", CFO_RULES_FILE=str(rules_path), CFO_UPLOAD_ALWAYS="0", CFO_API_BASE="http://cfo.test")
sys.path.insert(0, str(Path(__file__).parent))
import cfo_bridge as b

# ── pure matching ──
r = {"agent_id": "x", "columns_all": ["PO Number", "Vendor"]}
assert b.rule_matches(r, "anything.csv", ["Invoice No", "vendor", "po_number"])          # case / punctuation insensitive
assert not b.rule_matches(r, "anything.csv", ["Invoice No", "vendor"])
assert b.rule_matches({"agent_id": "x", "columns_any": ["Days Overdue", "90+"]}, "n.csv", ["a", "days overdue"])
assert not b.rule_matches({"agent_id": "x", "match": "ap_*.csv", "columns_all": ["a"]}, "other.csv", ["a"])   # name AND columns
assert b.rule_matches({"agent_id": "x", "match": "AP_*.CSV"}, "ap_jan.csv", [])                                # filename-only, case-insensitive

# ── end to end with a fake backend ──
rules_path.write_text(json.dumps([
    {"_comment": "ignored"},
    {"columns_all": ["Invoice No", "Vendor", "PO Number"], "agent_id": "ap_engine", "text": "ap"},
    {"columns_all": ["Bank Amount", "GL Amount"], "agent_id": "reconciliation", "text": "rec"},
    {"match": "*.csv", "agent_id": "ap_engine", "text": "ap"},                              # same job as rule 1 -> must run once
    {"columns_any": ["nope"], "agent_id": "ar_engine", "enabled": False},
]))
posted, ran = [], []
class R:
    def __init__(s, j): s._j, s.ok, s.status_code, s.text = j, True, 200, ""
    def json(s): return s._j
def fake_post(url, headers=None, json=None, timeout=None):
    if url.endswith("/api/v1/files"):
        posted.append(json["filename"])
        cols = {"Q3 payables (final) v2.csv": ["Invoice No", "Vendor", "PO Number", "Amount"],
                "Random Name 7.csv": ["x", "y"]}[json["filename"]]
        return R({"remote_path": "input/" + json["filename"], "file": {"columns": cols}})
    ran.append(url.split("/agents/")[1].split("/")[0])
    return R({"structured_output": {"alerts": []}, "skill": {"name": "s"}})
b.requests.post = fake_post
b.DOWNLOADERS = {"local": lambda fi: b"data"}
b._download_local = lambda fi: b"data"

out = b.handle_file({"file_name": "Q3 payables (final) v2.csv", "path": "/drop/x.csv"})
assert ran == ["ap_engine"], ran                                   # content rule + name rule collapsed into one run
assert out["uploaded"] == "input/Q3 payables (final) v2.csv" and len(out["runs"]) == 1, out

ran.clear()
out = b.handle_file({"file_name": "Random Name 7.csv", "path": "/drop/y.csv"})
assert ran == ["ap_engine"], ran                                   # only the *.csv name rule matches; columns don't match any content rule

rules_path.write_text(json.dumps([{"columns_all": ["Invoice No"], "agent_id": "ap_engine"}]))
ran.clear(); posted.clear()
out = b.handle_file({"file_name": "Random Name 7.csv", "path": "/drop/y.csv"})
assert ran == [] and posted == ["Random Name 7.csv"] and "no rule matched" in out["note"], (ran, out)   # uploaded, nothing run

rules_path.write_text(json.dumps([{"match": "ap_*.csv", "agent_id": "ap_engine"}]))
posted.clear()
out = b.handle_file({"file_name": "Random Name 7.csv", "path": "/drop/y.csv"})
assert posted == [] and out["uploaded"] is None                    # UPLOAD_ALWAYS=0 + nothing could match -> not even uploaded
print("cfo_bridge rules OK")

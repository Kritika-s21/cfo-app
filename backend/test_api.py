import os, shutil, tempfile, time
d = tempfile.mkdtemp(); shutil.copytree("policies", d + "/p"); shutil.copytree("skills", d + "/s")
os.environ.update(POLICY_DIR=d + "/p", RUNS_FILE=d + "/runs.jsonl", EVENTS_FILE=d + "/events.jsonl",
                  SCHEDULES_FILE=d + "/schedules.json", LANCEDB_URI=d + "/lance", GRAPH_FILE=d + "/graph.json",
                  VECTOR_BACKEND="lancedb", CFO_API_KEYS="k1", SKILLS_DIR=d + "/s", FILES_REGISTRY=d + "/files.json")
from fastapi.testclient import TestClient
import main, ezcoworker_client as ez

calls = []
def fake(m, c=None, s=None):
    calls.append((m, s))
    return {"reply": '{"answer":"ok","alerts":[{"severity":"critical","message":"limit breached","policy":"POL-003"}],"report":{"title":"R"}}', "conversationId": "conv1"}
ez.chat_stream = fake

with TestClient(main.app) as c:
    H = {"X-API-Key": "k1"}
    assert c.get("/api/v1/agents").status_code == 401
    h = c.get("/health").json(); assert h["vector_backend"] == "lancedb" and h["skills"] == 5 and h["scheduler_running"], h
    assert h["unknown_ezcoworker_skills"] == [], h
    assert ez.split_known(["data-analyst", "gst-compliance"]) == (["data-analyst"], ["gst-compliance"])
    assert len(c.get("/api/v1/skills", headers=H).json()) == 5
    assert c.post("/api/v1/skills/route", headers=H, json={"agent_id": "gst_engine", "text": "check ITC eligibility and policy"}).json()["skill"] == "compliance-policy-check"
    assert c.post("/api/v1/skills/route", headers=H, json={"agent_id": "financial_analyst", "text": "board pack summary"}).json()["skill"] == "reporting-generator"

    r = c.post("/api/v1/agents/cash_forecaster/run", headers=H, json={"text": "budget burn and wire transfer approval"}).json()
    assert r["skill"]["id"] == "budget-management" and r["structured_output"]["alerts"][0]["severity"] == "critical", r
    assert "POL-007" in r["policies_used"] and "POL-004" in r["policies_used"], r["policies_used"]
    assert calls[-1][1] == ["data-analyst"] and "SKILL INSTRUCTIONS" in calls[-1][0] and "Utilisation" in calls[-1][0]
    ev = [e["type"] for e in c.get(f"/api/v1/runs/{r['run_id']}/events", headers=H).json()]
    assert ev == ["run.started", "skill.routed", "knowledge.queried", "ezcoworker.called", "alert.critical", "run.succeeded"], ev
    assert c.get("/api/v1/logs?type=alert", headers=H).json()

    rid = c.post("/api/v1/agents/gst_engine/runs", headers=H, json={"text": "itc mismatch"}).json()["run_id"]
    assert c.get(f"/api/v1/runs/{rid}", headers=H).json()["status"] == "succeeded"

    c.post("/api/v1/agents/ap_engine/run", headers=H, json={"text": "check invoices", "file_context": "Uploaded data files (read these with your tools):\n- input/Random Name 7.csv"})
    lt = c.get("/api/v1/runs/latest", headers=H).json()
    by = {x["agent_id"]: x for x in lt}
    assert {"cash_forecaster", "gst_engine", "ap_engine"} <= set(by) and len(by) == len(lt), list(by)   # one row per agent
    assert by["cash_forecaster"]["alerts"][0]["severity"] == "critical" and by["ap_engine"]["files"] == ["input/Random Name 7.csv"], by["ap_engine"]
    assert by["ap_engine"]["result"]["answer"] == "ok"

    c.post("/api/v1/agents/ar_engine/run", headers=H, json={"text": "run AR workflow"})          # no file attached
    assert "do NOT use the AskUserQuestion tool" in calls[-1][0] and "needs_file" in calls[-1][0], calls[-1][0][:600]
    c.post("/api/v1/agents/ar_engine/run", headers=H, json={"text": "run AR", "file_context": "Uploaded data files:\n- input/x.csv"})
    assert "do NOT use the AskUserQuestion tool" in calls[-1][0] and "needs_file" not in calls[-1][0]
    dg = c.get("/api/v1/ezcoworker/diagnose?agent_id=je_factory", headers=H).json()
    assert dg["probes"][0]["probe"].startswith("message only") and len(dg["probes"]) == 3, dg
    # scheduler
    assert len(c.get("/api/v1/schedules", headers=H).json()) == 9
    assert c.post("/api/v1/schedules", headers=H, json={"agent_id": "gst_engine", "cron": "bad", "text": "x"}).status_code == 422
    s = c.post("/api/v1/schedules", headers=H, json={"agent_id": "cash_forecaster", "cron": "0 9 * * MON", "text": "update cash forecast"}).json()
    assert s["next_run"], s
    assert c.put("/api/v1/schedules/sch_recon", headers=H, json={"agent_id": "reconciliation", "cron": "0 0 L * *", "text": "recon", "enabled": True}).json()["next_run"]
    c.post(f"/api/v1/schedules/{s['id']}/run-now", headers=H)
    for _ in range(40):
        sch = [x for x in c.get("/api/v1/schedules", headers=H).json() if x["id"] == s["id"]][0]
        if sch.get("last_status"): break
        time.sleep(0.1)
    assert sch["last_status"] == "succeeded", sch
    assert c.get("/api/v1/logs?type=schedule", headers=H).json()

    # policies still work, incl. skill-required policy preview and graph persistence
    assert c.post("/api/v1/agents/gst_engine/context", headers=H, json={"query": "itc policy"}).json()["policies_used"]
    assert os.path.exists(d + "/graph.json") and c.get("/api/v1/knowledge/status", headers=H).json()["chunks"] > 5
    body = {"name": "Vendor Onboarding", "category": "Controls", "agents": ["ap_engine"], "body": "## Rules\n- KYC first, see [[POL-003]]\n"}
    assert c.post("/api/v1/policies/POL-009", headers=H, json=body).status_code == 201
    assert c.post("/api/v1/policies/search", headers=H, json={"query": "KYC vendor", "agent_id": "ap_engine"}).json()["hits"]
    assert "POL-009" in c.get("/api/v1/policies/POL-003/impact", headers=H).json()["referenced_by"]
    assert c.delete("/api/v1/policies/POL-009", headers=H).status_code == 200
    up = c.post("/api/v1/policies/upload", headers=H, json={"filename": "vendor kyc.md", "content": "---\nname: Vendor KYC\nagents: [ap_engine, nope]\n---\n## Rules\n- KYC before first payment\n"}).json()
    assert up["policy"] == "POL-009" and up["unknown_agents"] == ["nope"] and not up["updated"], up
    assert c.post("/api/v1/policies/upload", headers=H, json={"filename": "x.md", "content": "# Loose\n- no agents"}).json()["warning"]
    c.delete("/api/v1/policies/POL-009", headers=H); c.delete("/api/v1/policies/POL-010", headers=H)
    # edit a skill via API
    sk = c.get("/api/v1/skills/budget-management", headers=H).json(); body_ = sk.pop("body")
    assert c.put("/api/v1/skills/budget-management", headers=H, json={"meta": sk, "body": body_}).json()["skills"] == 5

    # ── files registry: any filename, listed for the chat picker ──
    import base64 as _b64, io as _io
    ez.upload_file = lambda name, data: {"filename": name, "remote_path": f"input/{name}", "response": {}}
    upf = lambda n, b, **kw: c.post("/api/v1/files", headers=H, json={"filename": n, "content_b64": _b64.b64encode(b).decode(), **kw})
    assert upf("Q3 payables (final) v2.csv", b"Invoice No,Vendor,PO Number,Amount\n1,A,PO1,10\n", source="scheduler", source_ref="/drop/x.csv", change="new").status_code == 201
    assert upf("weird_name.csv", b"a;b;c\n1;2;3\n").status_code == 201
    lst = c.get("/api/v1/files", headers=H).json()
    assert {f["name"] for f in lst} == {"Q3 payables (final) v2.csv", "weird_name.csv"}, lst
    q3 = next(f for f in lst if f["name"].startswith("Q3"))
    assert q3["source"] == "scheduler" and q3["change"] == "new" and q3["columns"] == ["Invoice No", "Vendor", "PO Number", "Amount"] and q3["remote_path"] == "input/Q3 payables (final) v2.csv", q3
    assert next(f for f in lst if f["name"] == "weird_name.csv")["columns"] == ["a", "b", "c"]
    assert [f["name"] for f in c.get("/api/v1/files?q=po number", headers=H).json()] == ["Q3 payables (final) v2.csv"]   # find by column, not name
    assert [f["name"] for f in c.get("/api/v1/files?source=scheduler", headers=H).json()] == ["Q3 payables (final) v2.csv"]
    upf("weird_name.csv", b"a,b\n"); assert next(f for f in c.get("/api/v1/files", headers=H).json() if f["name"] == "weird_name.csv")["versions"] == 2
    assert c.delete("/api/v1/files/weird_name.csv", headers=H).status_code == 200 and c.delete("/api/v1/files/weird_name.csv", headers=H).status_code == 404
    assert c.get("/api/v1/files").status_code == 401
    # ── date range is detected server-side, so files picked from the workspace get it too ──
    import openpyxl, datetime as _d
    from file_registry import sniff_date_range
    dr = lambda n, b: sniff_date_range(n, b)
    r1 = dr("any name.csv", b"Invoice No,Invoice Date,Amount\n2025-11,2026-01-15,10\nINV-2026-03,2026-06-30,5\n")
    assert r1["label"] == "Jan\u2013Jun 2026" and r1["months"] == [1, 6], r1            # invoice no "2025-11" ignored: date column preferred
    assert dr("x.csv", b"Period,COGS,Month\nJan-2013,100,1\nDec 2014,50,6\n")["label"] == "Jan 2013\u2013Dec 2014"   # multi-year, correct start/end
    assert dr("x.csv", b"Date,Amt\n31/03/2026,1\n15/04/2026,2\n")["label"] == "Mar\u2013Apr 2026"                   # day-first
    assert dr("x.csv", b"Month,COGS\n6,10\n6,5\n")  is None                                                        # no dates -> None
    assert dr("x.csv", b"Period,COGS,Month\nJan-2013,100,1\nJun-2013,50,6\n")["juneCOGS"] == 50
    wbk = openpyxl.Workbook(); ws = wbk.active; ws.append(["Posting Date", "Amount"])
    ws.append([_d.datetime(2025, 4, 3), 1]); ws.append([_d.datetime(2025, 9, 20), 2])
    buf = _io.BytesIO(); wbk.save(buf)
    assert dr("Q3 payables (final) v2.xlsx", buf.getvalue())["label"] == "Apr\u2013Sep 2025"
    upf("Random Name 7.csv", b"Date,Amt\n2026-02-01,1\n2026-03-09,2\n")
    rn = next(f for f in c.get("/api/v1/files", headers=H).json() if f["name"] == "Random Name 7.csv")
    assert rn["date_range"]["label"] == "Feb\u2013Mar 2026" and rn["date_range"]["endMonth"] == 3, rn
    assert next(f for f in c.get("/api/v1/files", headers=H).json() if f["name"].startswith("Q3"))["date_range"] is None
    print("files registry OK")
print("all passed;", len(main.app.openapi()["paths"]), "paths")

"""CFO Back Office API. Run: uvicorn main:app --port 8766 -> docs at /docs (OpenAPI at /openapi.json)."""
import os, re, base64, time, uuid, logging
from dotenv import load_dotenv
load_dotenv()  # reads backend/.env before the modules below read os.environ
from logging_setup import setup_logging
setup_logging()  # console + logs/app.log + logs/error.log, before the other modules create loggers
log = logging.getLogger("cfo.api")
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from contextlib import asynccontextmanager
import yaml
import auth_store as accounts
import ezcoworker_client as ez
import orchestrator as orch
from orchestrator import store, runs, logs, registry
from scheduler_service import SchedulerService
from file_registry import FileRegistry, sniff_columns, sniff_date_range

# Server-to-server keys clients may send as `X-API-Key` or `Authorization: Bearer`.
API_KEYS = {k.strip() for k in os.environ.get("CFO_API_KEYS", "").split(",") if k.strip()}
AUTH_COOKIE = "cfo_session"
AUTH_ORIGINS = {
    origin.strip().rstrip("/")
    for origin in os.environ.get(
        "CFO_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173",
    ).split(",")
    if origin.strip()
}
if "*" in AUTH_ORIGINS:
    raise RuntimeError("CFO_CORS_ORIGINS must contain explicit origins, not '*'.")
accounts.initialize()


def auth(
    request: Request,
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    token = x_api_key or (authorization or "").removeprefix("Bearer ").strip()
    if token and token in API_KEYS:
        return
    if accounts.user_for_session(request.cookies.get(AUTH_COOKIE)):
        return
    raise HTTPException(401, "Authentication required")


files = FileRegistry(os.environ.get("FILES_REGISTRY", "files_registry.json"))
scheduler = SchedulerService(os.environ.get("SCHEDULES_FILE", "schedules.json"))


@asynccontextmanager
async def lifespan(app):
    if os.environ.get("SCHEDULER_ENABLED", "1") == "1":
        scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="CFO Back Office API", version="2.0.0", lifespan=lifespan,
              description="EzCoworker orchestration: skill router -> knowledge layer (LanceDB + graph + policy docs) -> structured output, with log registry and scheduler.")
app.add_middleware(CORSMiddleware, allow_origins=sorted(AUTH_ORIGINS),
                   allow_methods=["*"], allow_headers=["*"], allow_credentials=True)


@app.middleware("http")
async def access_log(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:8]
    t0 = time.perf_counter()
    client = request.client.host if request.client else "-"
    log.info("[%s] --> %s %s from %s (body=%s bytes)", rid, request.method, request.url.path,
             client, request.headers.get("content-length", "0"))
    try:
        resp = await call_next(request)
    except Exception:
        log.exception("[%s] !! %s %s crashed after %.0f ms", rid, request.method, request.url.path, (time.perf_counter() - t0) * 1000)
        raise
    ms = (time.perf_counter() - t0) * 1000
    lvl = logging.ERROR if resp.status_code >= 500 else logging.WARNING if resp.status_code >= 400 else logging.INFO
    log.log(lvl, "[%s] <-- %s %s %s in %.0f ms", rid, request.method, request.url.path, resp.status_code, ms)
    resp.headers["X-Request-ID"] = rid
    return resp


v1 = APIRouter(prefix="/api/v1", dependencies=[Depends(auth)])


class RegisterReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=12, max_length=128)


class LoginReq(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=128)


def _validate_auth_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in AUTH_ORIGINS:
        raise HTTPException(403, "Origin is not allowed")


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        key=AUTH_COOKIE,
        value=token,
        max_age=accounts.SESSION_TTL_SECONDS,
        httponly=True,
        secure=os.environ.get("AUTH_COOKIE_SECURE", "").lower() in {"1", "true", "yes"}
        or request.url.scheme == "https",
        samesite="strict",
        path="/api/v1",
    )


@app.post("/api/v1/auth/register", tags=["auth"])
def register_user(payload: RegisterReq, request: Request, response: Response):
    _validate_auth_origin(request)
    email = payload.email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(422, "Enter a valid email address")
    name = " ".join(payload.name.split())
    if not name:
        raise HTTPException(422, "Name is required")
    try:
        user = accounts.create_user(name, email, payload.password)
    except accounts.DuplicateAccountError:
        raise HTTPException(409, "An account with this email already exists")
    token, _ = accounts.issue_session(user["id"])
    _set_session_cookie(response, request, token)
    user["initials"] = "".join(part[0] for part in name.split() if part)[:2].upper()
    return user


@app.post("/api/v1/auth/login", tags=["auth"])
def login_user(payload: LoginReq, request: Request, response: Response):
    _validate_auth_origin(request)
    ip_address = request.client.host if request.client else "unknown"
    if accounts.login_is_limited(ip_address):
        raise HTTPException(429, "Too many login attempts. Please try again later")
    email = payload.email.strip().lower()
    user = accounts.verify_user(email, payload.password)
    if not user:
        accounts.record_login_failure(ip_address)
        raise HTTPException(401, "Invalid email or password")
    accounts.clear_login_failures(ip_address)
    token, _ = accounts.issue_session(user["id"])
    _set_session_cookie(response, request, token)
    user["initials"] = "".join(part[0] for part in user["name"].split() if part)[:2].upper()
    return user


@app.get("/api/v1/auth/me", tags=["auth"])
def current_user(request: Request):
    user = accounts.user_for_session(request.cookies.get(AUTH_COOKIE))
    if not user:
        raise HTTPException(401, "Not signed in")
    return user


@app.post("/api/v1/auth/logout", tags=["auth"])
def logout_user(request: Request, response: Response):
    _validate_auth_origin(request)
    accounts.revoke_session(request.cookies.get(AUTH_COOKIE))
    response.delete_cookie(key=AUTH_COOKIE, path="/api/v1", httponly=True, samesite="strict")
    return {"ok": True}


# ───────────────────────── models ─────────────────────────
class RunReq(BaseModel):
    text: str = Field(..., description="User request for the agent")
    skill: str = Field("", description="Primary skill hint (e.g. gst_reconcile_itc)")
    file_context: str = ""
    conversation_id: Optional[str] = None
    policy_ids: Optional[List[str]] = Field(None, description="Force-include these policies in addition to retrieval")
    top_k: int = 4


class PolicyIn(BaseModel):
    name: str
    version: str = "v1.0"
    category: str = "Governance"
    owner: str = ""
    critical: bool = False
    agents: List[str] = []
    related: List[str] = []
    always_load: bool = False
    body: str = Field(..., description="Markdown body: '## Section' headings; [[POL-003]] creates graph links")


class SearchReq(BaseModel):
    query: str
    agent_id: Optional[str] = None
    k: int = 4
    graph_hops: bool = True


# ───────────────────────── health / agents / skills ─────────────────────────
@app.get("/health", tags=["system"])
def health():
    wanted = {x for v in ez.AGENT_SKILLS.values() for x in v} | {x for s in registry.skills.values() for x in s.ezcoworker_skills}
    return {"unknown_ezcoworker_skills": sorted(ez.split_known(sorted(wanted))[1]), "status": "ok", "ezcoworker_configured": bool(ez.API_KEY), "policies": len(store.policies),
            "skills": len(registry.skills), "vector_backend": store.vectors.name, "scheduler_running": scheduler.sched.running}


def _agent_view(a: str):
    return {"id": a, "skills": [s.id for s in registry.for_agent(a)],
            "policies": sorted(p for p, pol in store.policies.items() if a in (pol.meta.get("agents") or []))}


@v1.get("/agents", tags=["agents"])
def list_agents():
    return [_agent_view(a) for a in ez.AGENT_SKILLS]


@v1.get("/agents/{agent_id}", tags=["agents"])
def get_agent(agent_id: str):
    if agent_id not in ez.AGENT_SKILLS:
        raise HTTPException(404, "Unknown agent")
    return _agent_view(agent_id)


class SkillIn(BaseModel):
    meta: Dict[str, Any]
    body: str


class RouteReq(BaseModel):
    agent_id: str
    text: str


@v1.get("/skills", tags=["skills"], summary="Skill registry (markdown-defined)")
def list_skills():
    return [{k: getattr(s, k) for k in ("id", "name", "description", "agents", "ezcoworker_skills", "policies", "knowledge", "outputs", "priority")}
            for s in registry.skills.values()]


@v1.post("/skills/route", tags=["skills"], summary="Which skill would the router pick?")
def route_skill(r: RouteReq):
    s, how = registry.resolve(r.agent_id, "", r.text)
    return {"skill": s.id if s else None, "routed_by": how}


@v1.post("/skills/reload", tags=["skills"])
def reload_skills():
    return {"skills": registry.reload()}


@v1.get("/skills/{sid}", tags=["skills"])
def get_skill(sid: str):
    if sid not in registry.skills:
        raise HTTPException(404, "Skill not found")
    s = registry.skills[sid]
    return {**s.meta, "body": s.body}


@v1.put("/skills/{sid}", tags=["skills"], summary="Create/update a skill definition (writes markdown, reloads)")
def put_skill(sid: str, p: SkillIn):
    meta = {**p.meta, "id": sid}
    (registry.dir / f"{sid}.md").write_text(f"---\n{yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)}---\n{p.body}\n", encoding="utf-8")
    return {"skills": registry.reload()}


@v1.get("/ezcoworker/diagnose", tags=["system"], summary="Probe EzCoworker: which part of the call is rejected?")
def ezcoworker_diagnose(agent_id: str = "je_factory"):
    return ez.diagnose(agent_id)


# ───────────────────────── runs ─────────────────────────
def _execute(agent_id: str, req: RunReq, run_id: str, source: str = "user") -> Dict[str, Any]:
    return orch.orchestrate(agent_id, req.text, req.skill, req.file_context, req.conversation_id,
                            req.policy_ids, req.top_k, source, run_id)


@v1.post("/agents/{agent_id}/run", tags=["runs"], summary="Run an agent synchronously")
def run_sync(agent_id: str, req: RunReq):
    if agent_id not in ez.AGENT_SKILLS:
        raise HTTPException(404, "Unknown agent")
    rec = runs.create(agent_id, req.model_dump())
    try:
        out = _execute(agent_id, req, rec["run_id"])
        rec.update(status="succeeded", result=out)
    except Exception as e:
        rec.update(status="failed", error=str(e)); runs.update(rec)
        logs.event("run.failed", rec["run_id"], agent_id, error=str(e))
        raise HTTPException(502, f"EzCoworker error: {e}")
    runs.update(rec)
    return {"run_id": rec["run_id"], **out}


def _bg(run_id: str, agent_id: str, req: RunReq):
    rec = runs.get(run_id); rec["status"] = "running"; runs.update(rec)
    try:
        rec.update(status="succeeded", result=_execute(agent_id, req, run_id))
    except Exception as e:
        rec.update(status="failed", error=str(e))
        logs.event("run.failed", run_id, agent_id, error=str(e))
    runs.update(rec)


@v1.post("/agents/{agent_id}/runs", tags=["runs"], status_code=202, summary="Start an async run (poll GET /runs/{id})")
def run_async(agent_id: str, req: RunReq, bg: BackgroundTasks):
    if agent_id not in ez.AGENT_SKILLS:
        raise HTTPException(404, "Unknown agent")
    rec = runs.create(agent_id, req.model_dump())
    bg.add_task(_bg, rec["run_id"], agent_id, req)
    return {"run_id": rec["run_id"], "status": "queued"}


@v1.get("/runs", tags=["runs"])
def list_runs(agent_id: Optional[str] = None, limit: int = Query(50, le=500)):
    return [{k: r[k] for k in ("run_id", "agent_id", "status", "created_at")} for r in runs.list(agent_id, limit)]

@v1.get("/runs/count", tags=["runs"], summary="Total number of recorded agent runs")
def run_count():
    by_agent = runs.counts_by_agent()
    return {"total_runs": sum(by_agent.values()), "by_agent": by_agent}


@v1.get("/runs/latest", tags=["runs"], summary="Most recent successful run of every agent (feeds the dashboard cards)")
def latest_runs():
    seen: Dict[str, Any] = {}
    for r in runs.list(None, 5000):                                   # newest first
        if r["status"] != "succeeded" or not r.get("result") or r["agent_id"] in seen or (r["result"].get("result") or {}).get("needs_file"):
            continue
        out = r["result"]
        alerts = (out.get("structured_output") or {}).get("alerts") or []
        files = re.findall(r"^- (input/.+)$", (r.get("request") or {}).get("file_context") or "", re.M)
        seen[r["agent_id"]] = {"agent_id": r["agent_id"], "run_id": r["run_id"], "created_at": r["created_at"],
                               "result": out.get("result") or {}, "skill": (out.get("skill") or {}).get("name"),
                               "alerts": alerts, "files": files}
    return list(seen.values())


@v1.get("/runs/{run_id}", tags=["runs"])
def get_run(run_id: str):
    r = runs.get(run_id)
    if not r:
        raise HTTPException(404, "Run not found")
    return r


@v1.get("/runs/{run_id}/events", tags=["logs"], summary="Pipeline events for one run (audit trail)")
def run_events(run_id: str):
    return logs.query(run_id=run_id)


@v1.get("/logs", tags=["logs"], summary="Log registry: filter by agent / event type prefix")
def get_logs(agent_id: Optional[str] = None, type: Optional[str] = None, limit: int = Query(200, le=2000)):
    return logs.query(agent_id=agent_id, type_prefix=type, limit=limit)


# ───────────────────────── scheduler ─────────────────────────
class ScheduleIn(BaseModel):
    agent_id: str
    name: str = ""
    cron: str = Field(..., description="5-field cron, e.g. '0 9 * * MON'; 'L' in day-of-month = last day")
    text: str
    skill: str = ""
    timezone: str = "Asia/Kolkata"
    enabled: bool = True
    watch_path: str = Field("", description="File or glob the scheduler picks up each run, e.g. '/data/drop/ap_*.csv'. "
                                            "Matching files are uploaded to EzCoworker (input/<name>) before the agent runs.")
    only_if_changed: bool = Field(True, description="Cron runs are skipped when the watched files have not changed since the last successful run")


@v1.get("/schedules", tags=["scheduler"])
def list_schedules():
    return scheduler.list()


@v1.post("/schedules", tags=["scheduler"], status_code=201)
def create_schedule(s: ScheduleIn):
    if s.agent_id not in ez.AGENT_SKILLS:
        raise HTTPException(404, "Unknown agent")
    try:
        return scheduler.upsert(None, s.model_dump())
    except ValueError as e:
        raise HTTPException(422, str(e))


@v1.put("/schedules/{sid}", tags=["scheduler"])
def update_schedule(sid: str, s: ScheduleIn):
    if sid not in scheduler.items:
        raise HTTPException(404, "Schedule not found")
    try:
        return scheduler.upsert(sid, s.model_dump())
    except ValueError as e:
        raise HTTPException(422, str(e))


@v1.delete("/schedules/{sid}", tags=["scheduler"])
def delete_schedule(sid: str):
    scheduler.delete(sid)
    return {"deleted": sid}


@v1.post("/schedules/{sid}/run-now", tags=["scheduler"], status_code=202)
def run_schedule_now(sid: str):
    if sid not in scheduler.items:
        raise HTTPException(404, "Schedule not found")
    return scheduler.run_now(sid)


# ───────────────────────── knowledge layer ─────────────────────────
@v1.get("/knowledge/status", tags=["policies"], summary="LanceDB / graph / policy-doc status")
def knowledge_status():
    return {"vector_backend": store.vectors.name, "policies": len(store.policies), "chunks": len(store.chunks),
            "graph_edges": len(store.graph.edges)}


# ───────────────────────── policies ─────────────────────────
def _pol(p) -> Dict[str, Any]:
    return {**p.meta, "body": p.body}


@v1.get("/policies", tags=["policies"])
def list_policies(category: Optional[str] = None, agent_id: Optional[str] = None, critical: Optional[bool] = None):
    out = [_pol(p) for p in store.policies.values()]
    if category: out = [p for p in out if p.get("category") == category]
    if agent_id: out = [p for p in out if agent_id in (p.get("agents") or [])]
    if critical is not None: out = [p for p in out if bool(p.get("critical")) == critical]
    return out


@v1.get("/policies/graph", tags=["policies"], summary="Nodes/edges for graph view")
def policy_graph():
    return store.graph.to_json()


@v1.post("/policies/search", tags=["policies"], summary="Vector search + graph expansion")
def policy_search(req: SearchReq):
    return store.search(req.query, req.agent_id, req.k, req.graph_hops)


@v1.post("/policies/reindex", tags=["policies"])
def policy_reindex():
    return store.reindex()


class FileUpload(BaseModel):
    filename: str
    content_b64: str = Field(..., description="Base64 file bytes")
    source: str = Field("chat", description="Where the file came from: 'chat' (paperclip) or 'scheduler' (file pickup)")
    source_ref: str = Field("", description="Original location (path / bucket key) for scheduler pickups")
    change: str = Field("", description="'new' or 'modified' for scheduler pickups")


class PolicyUpload(BaseModel):
    filename: str = ""
    content: str = Field(..., description="Raw .md text. Optional YAML frontmatter (id, name, version, critical, agents, related, always_load)")


@v1.post("/files", tags=["files"], status_code=201,
         summary="Upload a data file to the EzCoworker workspace (becomes input/<filename>) so agents can read it")
def upload_data_file(u: FileUpload):
    name = os.path.basename(u.filename)
    if not name:
        raise HTTPException(422, "filename required")
    try:
        data = base64.b64decode(u.content_b64, validate=True)
    except Exception:
        raise HTTPException(422, "content_b64 is not valid base64")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "File too large (25 MB max)")
    try:
        out = ez.upload_file(name, data)
    except RuntimeError as e:
        raise HTTPException(502, f"EzCoworker upload failed: {e}")
    entry = files.record(name, out.get("remote_path") or f"input/{name}", len(data), u.source, u.source_ref,
                         u.change, sniff_columns(name, data), sniff_date_range(name, data))
    return {**out, "file": entry}


@v1.get("/files", tags=["files"],
        summary="Every file in the workspace (chat attachments + scheduler pickups), newest first - names are never filtered by convention")
def list_data_files(source: str = "", q: str = "", limit: int = Query(200, le=1000)):
    return files.list(source, q, limit)


@v1.delete("/files/{name}", tags=["files"], summary="Remove a file from the picker list (workspace copy is untouched)")
def forget_data_file(name: str):
    if not files.remove(os.path.basename(name)):
        raise HTTPException(404, "Unknown file")
    return {"removed": name}


@v1.post("/policies/upload", tags=["policies"], status_code=201,
         summary="Upload a policy .md (create or new version); re-chunks, re-embeds and rebuilds the graph")
def upload_policy(u: PolicyUpload):
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", u.content, re.S)
    try:
        meta, body = (yaml.safe_load(m.group(1)) or {}, m.group(2).strip()) if m else ({}, u.content.strip())
    except yaml.YAMLError as e:
        raise HTTPException(422, f"Invalid frontmatter: {e}")
    if not isinstance(meta, dict) or not body:
        raise HTTPException(422, "Policy needs a markdown body (and frontmatter must be a mapping)")
    pid = str(meta.get("id") or u.filename.rsplit(".", 1)[0]).upper()
    if not re.fullmatch(r"POL-\d{3,}", pid):  # also blocks path tricks in filenames
        pid = "POL-%03d" % (1 + max([int(p[4:]) for p in store.policies if p[4:].isdigit()] or [0]))
    title = re.search(r"(?m)^#\s+(.+)$", body)
    meta.setdefault("name", title.group(1).strip() if title else pid)
    meta.setdefault("version", "v1.0"); meta.setdefault("category", "Governance")
    agents = meta.get("agents") or []
    existed = pid in store.policies
    idx = store.save(pid, meta, body)
    return {"policy": pid, "updated": existed, "index": idx,
            "unknown_agents": [a for a in agents if a not in ez.AGENT_SKILLS],
            "warning": None if agents or meta.get("always_load") else
            "No agents listed: this policy will not be retrieved by any agent until you assign some."}


@v1.get("/policies/{pid}", tags=["policies"])
def get_policy(pid: str):
    if pid not in store.policies:
        raise HTTPException(404, "Policy not found")
    return _pol(store.policies[pid])


@v1.post("/policies/{pid}", tags=["policies"], status_code=201)
def create_policy(pid: str, p: PolicyIn):
    pid = pid.upper()
    if pid in store.policies:
        raise HTTPException(409, "Policy exists; use PUT to update")
    return {"index": store.save(pid, p.model_dump(exclude={"body"}), p.body), "policy": pid}


@v1.put("/policies/{pid}", tags=["policies"], summary="Update (previous version is archived)")
def update_policy(pid: str, p: PolicyIn):
    if pid not in store.policies:
        raise HTTPException(404, "Policy not found")
    return {"index": store.save(pid, p.model_dump(exclude={"body"}), p.body), "policy": pid}


@v1.delete("/policies/{pid}", tags=["policies"])
def delete_policy(pid: str):
    if pid not in store.policies:
        raise HTTPException(404, "Policy not found")
    return store.delete(pid)


@v1.get("/policies/{pid}/versions", tags=["policies"])
def policy_versions(pid: str):
    return store.versions(pid)


@v1.get("/policies/{pid}/impact", tags=["policies"], summary="Which agents/policies are affected by a change")
def policy_impact(pid: str):
    if pid not in store.policies:
        raise HTTPException(404, "Policy not found")
    return store.impact(pid)


@v1.post("/agents/{agent_id}/context", tags=["agents"], summary="Preview the policy context an agent would receive")
def preview_context(agent_id: str, req: SearchReq):
    s, _ = registry.resolve(agent_id, "", req.query)
    c = store.build_context(req.query, agent_id, req.k, s.policies if s else None)
    return {"policies_used": c["policies_used"], "context": c["context"]}


app.include_router(v1)


if __name__ == "__main__":      # python main.py  -> same as: uvicorn main:app, with all logs in terminal + logs/
    import uvicorn
    uvicorn.run("main:app", host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8766")),
                log_config=None, reload=os.environ.get("RELOAD", "0") == "1")

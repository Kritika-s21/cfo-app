"""The flow: User/System -> route skill -> query knowledge layer -> EzCoworker (skill) -> structured output + logs."""
import os, re
from typing import Any, Dict, List, Optional

import ezcoworker_client as ez
from log_registry import LogRegistry
from policy.embeddings import get_embedder
from policy.store import PolicyStore
from policy.vectors import make_backend
from run_store import RunStore
from skill_registry import SkillRegistry

registry = SkillRegistry(os.environ.get("SKILLS_DIR", "skills"))
store = PolicyStore(
    os.environ.get("POLICY_DIR", "policies"), get_embedder(),
    make_backend(os.environ.get("VECTOR_BACKEND", "lancedb"), os.environ.get("LANCEDB_URI", "lancedb")),
    graph_file=os.environ.get("GRAPH_FILE", "policy_graph.json"))
store.reindex()
runs = RunStore(os.environ.get("RUNS_FILE", "runs.jsonl"))
logs = LogRegistry(os.environ.get("EVENTS_FILE", "events.jsonl"))


_POL = re.compile(r"POL[\-\u2010-\u2015]?\s?(\d{3})")


def _pol_id(v: Any) -> Optional[str]:
    """'POL-003#0', 'POL‑003 (rule 2)' -> 'POL-003' (models cite chunk ids and use look-alike hyphens)."""
    m = _POL.search(str(v or ""))
    return f"POL-{m.group(1)}" if m else None


def _infer_policy(result: Dict[str, Any]) -> Optional[str]:
    """Best single policy for alerts that name none: the failed findings' policy, else the model's own policy_cited."""
    failed = {_pol_id(f.get("policy")) for f in (result.get("findings") or [])
              if isinstance(f, dict) and str(f.get("status", "")).lower() in ("fail", "failed", "needs-review", "violation")}
    failed.discard(None)
    if len(failed) == 1:
        return failed.pop()
    cited = set(_POL.findall(str(result.get("policy_cited") or "")))
    return f"POL-{cited.pop()}" if len(cited) == 1 else None


def to_structured(result: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise to the 'Structured Output' box: reports, alerts, files."""
    alerts = result.get("alerts") or []
    alerts = [a if isinstance(a, dict) else {"severity": "info", "message": str(a)} for a in alerts]
    for a in alerts:                                    # every alert must point at a real policy id
        a["policy"] = _pol_id(a.get("policy")) or _pol_id(a.get("message")) or _infer_policy(result) or a.get("policy")
    return {"summary": result.get("answer"), "report": result.get("report"),
            "alerts": alerts, "files": result.get("files") or []}


def orchestrate(agent_id: str, text: str, skill_hint: str = "", file_context: str = "",
                conversation_id: Optional[str] = None, policy_ids: Optional[List[str]] = None,
                top_k: int = 4, source: str = "user", run_id: Optional[str] = None) -> Dict[str, Any]:
    logs.event("run.started", run_id, agent_id, source=source, text=text[:300])
    skill, how = registry.resolve(agent_id, skill_hint, text)                    # 1. Skill Router
    logs.event("skill.routed", run_id, agent_id, skill=skill.id if skill else None, method=how)
    required = list(dict.fromkeys((skill.policies if skill else []) + (policy_ids or [])))
    ctx = store.build_context(text, agent_id, top_k, required)                   # 2. Knowledge layer
    logs.event("knowledge.queried", run_id, agent_id, vector_backend=store.vectors.name,
               policies=ctx["policies_used"], graph_expanded=ctx["retrieval"]["graph_expanded"])
    enabled = (skill.ezcoworker_skills if skill and skill.ezcoworker_skills else None)
    logs.event("ezcoworker.called", run_id, agent_id, enabled_skills=enabled or ez.AGENT_SKILLS.get(agent_id))
    out = ez.run_agent(agent_id, skill.name if skill else skill_hint, text, ctx["context"], file_context,
                       conversation_id, skill.body if skill else "", enabled)   # 3. Skill execution
    if out.get("skills_dropped"):
        logs.event("ezcoworker.skills_dropped", run_id, agent_id, dropped=out["skills_dropped"])
    res = out["result"]
    model_cited = bool(res.get("policy_cited"))
    res.setdefault("policy_cited", ", ".join(ctx["policies_used"]))
    if isinstance(res.get("policy_cited"), str):
        res["policy_cited"] = re.sub(r"(POL-\d{3})#\d+", r"\1", res["policy_cited"])
    structured = to_structured(res if model_cited else {**res, "policy_cited": ""})                                              # 4. Structured output
    for a in structured["alerts"]:
        if str(a.get("severity", "")).lower() == "critical":
            logs.event("alert.critical", run_id, agent_id, message=a.get("message"), policy=a.get("policy"))
    logs.event("run.succeeded", run_id, agent_id, skill=skill.id if skill else None, alerts=len(structured["alerts"]))
    return {"result": res, "structured_output": structured, "conversationId": out["conversationId"],
            "skill": {"id": skill.id, "name": skill.name, "routed_by": how} if skill else None,
            "ezcoworker_skills": out["skills"], "policies_used": ctx["policies_used"]}

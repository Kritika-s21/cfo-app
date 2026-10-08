"""EzCoworker client + per-agent skill orchestration (server-side only: the API key never goes to the browser)."""
import json, os, logging, mimetypes, time
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("EZCOWORKER_BASE_URL", "https://ezcoworker.ezdatamunch.com/api")
API_KEY = os.environ.get("EZCOWORKER_API_KEY", "")
TIMEOUT = int(os.environ.get("EZCOWORKER_CHAT_TIMEOUT", "300"))

# agent id (matches AGENT_REGISTRY / SKILLS_BY_AGENT in the React app) -> EzCoworker skills to enable.
# These slugs must exist in your EzCoworker workspace; rename to match.
AGENT_SKILLS: Dict[str, List[str]] = {
    # EzCoworker has no finance-specific skills, only generic ones (see ezcoworker_skills_catalog.json).
    # Finance logic lives in our markdown skills (skills/*.md) and policies (policies/*.md), so every agent
    # needs only the generic EzCoworker capability `data-analyst`; a few add one helper skill.
    "gst_engine": ["data-analyst"], "tds_engine": ["data-analyst"], "tp_monitor": ["data-analyst"],
    "reconciliation": ["data-analyst"], "expense_triage": ["data-analyst"], "rev_recognition": ["data-analyst"],
    "fixed_asset": ["data-analyst"], "ap_engine": ["data-analyst"], "ar_engine": ["data-analyst"],
    "close_orchestrator": ["data-analyst", "task-coordination-strategies"],
    "je_factory": ["data-analyst"],
    "financial_analyst": ["data-analyst", "data-storytelling"],
    "cash_forecaster": ["data-analyst"], "wc_optimizer": ["data-analyst"],
    "dispatch": ["data-analyst", "task-coordination-strategies"],
    "gl_harmonizer": ["data-analyst"], "entity_consolidator": ["data-analyst"], "segment_mapper": ["data-analyst"],
    "review": ["data-analyst", "fact-checker"],
    "sql": ["coding-agent", "schema-migration", "data-analyst"],
}

CATALOG_FILE = os.environ.get("EZCOWORKER_SKILLS_CATALOG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ezcoworker_skills_catalog.json"))


def known_skills() -> Optional[set]:
    """Skills that exist in the EzCoworker workspace (names and dirs), or None if no catalog file."""
    try:
        with open(CATALOG_FILE, encoding="utf-8") as f:
            return {v for k in json.load(f)["skills"] for v in (k["name"], k["dir"])}
    except (OSError, ValueError, KeyError):
        return None


def split_known(skills: List[str]):
    """-> (kept, dropped). Unknown slugs make /chat/stream return 400, so they are removed and reported."""
    known = known_skills()
    if known is None:
        return list(skills), []
    return [x for x in skills if x in known], [x for x in skills if x not in known]


# ---- conversations ----------------------------------------------------------------------------------------
# EzCoworker has no "create conversation" call. Conversation ids are INTEGERS. A chat/stream call WITHOUT
# conversationId starts a new conversation (that is how other agents on your workspace create theirs); we then read
# the new id from the stream, or from GET /conversations. Optional EZCOWORKER_CONVERSATION_ID pins every run to ONE
# existing conversation instead.
FIXED_CONVERSATION_ID = os.environ.get("EZCOWORKER_CONVERSATION_ID", "").strip()


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


def upload_file(filename: str, data: bytes) -> Dict[str, Any]:
    """POST /upload (multipart, field 'files'). EzCoworker exposes it server-side as input/<filename>."""
    if not API_KEY:
        raise RuntimeError("EZCOWORKER_API_KEY not configured")
    filename = os.path.basename(filename)
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    t0 = time.perf_counter()
    logger.info("[EzCoworker] UPLOAD START file=%s bytes=%d", filename, len(data))
    r = requests.post(
        f"{BASE_URL.rstrip('/')}/upload",
        headers={"Authorization": f"Bearer {API_KEY}"},   # no JSON Content-Type: requests sets the multipart boundary
        files={"files": (filename, data, mime)},
        timeout=60)
    if not r.ok:
        logger.error("ezcoworker /upload returned %s: %s", r.status_code, r.text[:2000])
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    logger.info("[EzCoworker] UPLOAD OK file=%s -> input/%s status=%s in %.0f ms", filename, filename, r.status_code, (time.perf_counter() - t0) * 1000)
    try:
        body = r.json()
    except ValueError:
        body = {}
    return {"filename": filename, "remote_path": f"input/{filename}", "response": body}


def valid_conversation_id(c: Any) -> bool:
    return c is not None and str(c).strip().isdigit()      # rejects "string", "null", uuids, ""


def list_conversations(limit: int = 10) -> List[Dict[str, Any]]:
    r = requests.get(f"{BASE_URL.rstrip('/')}/conversations", headers=_headers(), timeout=20)
    r.raise_for_status()
    return (r.json().get("conversations") or [])[:limit]


def _find_new_conversation(message: str) -> Optional[str]:
    """Fallback when the stream does not reveal the id: newest conversation whose (truncated) title is our message's start."""
    try:
        for c in list_conversations(15):
            title = str(c.get("title") or "").removesuffix("...").strip()
            if title and message.startswith(title):
                return str(c["id"])
    except (requests.RequestException, ValueError, KeyError):
        pass
    return None


def _post_chat(message: str, conv: Optional[str], enabled_skills: Optional[List[str]]):
    payload: Dict[str, Any] = {"message": message}
    if conv:
        payload["conversationId"] = int(conv)              # omitted entirely => EzCoworker starts a new conversation
    if enabled_skills:
        payload["enabledSkills"] = enabled_skills
    return requests.post(
        f"{BASE_URL.rstrip('/')}/chat/stream",
        headers={**_headers(), "Accept": "text/event-stream"}, json=payload, stream=True, timeout=TIMEOUT)


def chat_stream(message: str, conversation_id: Optional[str] = None,
                enabled_skills: Optional[List[str]] = None) -> Dict[str, Any]:
    """POST /chat/stream and accumulate the streamed reply. Returns {reply, conversationId, raw_head}.
    A valid (integer) conversation id continues that conversation; anything else starts a new one."""
    if not API_KEY:
        raise RuntimeError("EZCOWORKER_API_KEY not configured")
    t0 = time.perf_counter()
    supplied = valid_conversation_id(conversation_id)
    conv = str(conversation_id).strip() if supplied else (FIXED_CONVERSATION_ID or None)
    logger.info("[EzCoworker] ABOUT TO CALL /chat/stream conversation=%s skills=%s prompt_chars=%d", conv, enabled_skills, len(message))
    resp = _post_chat(message, conv, enabled_skills)
    if not resp.ok and conv and "conversation" in resp.text.lower():       # unknown/deleted id: start a new conversation
        conv = FIXED_CONVERSATION_ID if (FIXED_CONVERSATION_ID and conv != FIXED_CONVERSATION_ID) else None
        resp = _post_chat(message, conv, enabled_skills)
    if not resp.ok:
        logger.error("[EzCoworker] /chat/stream returned %s: %s", resp.status_code, resp.text[:600])
    if not resp.ok:                                                         # surface EzCoworker's own explanation
        raise RuntimeError(f"HTTP {resp.status_code} from EzCoworker: {resp.text[:600]}")
    resp.encoding = "utf-8"          # event-streams carry no charset; requests would guess latin-1 and garble ₹ and dashes
    out = parse_stream(resp.iter_lines(decode_unicode=True), conv)
    if not out["conversationId"]:
        out["conversationId"] = _find_new_conversation(message)
    logger.info("[EzCoworker] CHAT COMPLETE conversation=%s reply_chars=%d in %.1f s",
                out["conversationId"], len(out["reply"]), time.perf_counter() - t0)
    return out


def parse_stream(lines, conversation_id: Optional[str] = None) -> Dict[str, Any]:
    """Defensive SSE/NDJSON parser: 'data:' prefix optional, JSON delta under common keys, else raw text."""
    parts: List[str] = []
    conv = conversation_id
    head: List[str] = []
    for raw in lines:
        if raw is None:
            continue
        line = raw.strip()
        if len(head) < 6 and line:
            head.append(line[:200])
        if not line or line.startswith(":") or line.startswith("event:"):
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if line in ("[DONE]", "DONE"):
            break
        try:
            obj = json.loads(line)
        except ValueError:
            parts.append(line)
            continue
        if not isinstance(obj, dict):
            parts.append(str(obj))
            continue
        nested = obj.get("conversation") if isinstance(obj.get("conversation"), dict) else {}
        found = obj.get("conversationId") or obj.get("conversation_id") or obj.get("conversationID") or nested.get("id")
        if not found and str(obj.get("type", "")).lower() in ("conversation", "conversation_created", "init", "start", "meta"):
            found = obj.get("id")
        conv = str(found) if found else conv
        for k in ("delta", "text", "content", "token", "chunk", "message"):
            v = obj.get(k)
            if isinstance(v, str):
                parts.append(v)
                break
            if isinstance(v, dict) and isinstance(v.get("text"), str):
                parts.append(v["text"])
                break
    return {"reply": "".join(parts), "conversationId": conv, "raw_head": head}


def run_agent(agent_id: str, skill: str, user_text: str, policy_context: str,
              file_context: str = "", conversation_id: Optional[str] = None,
              skill_instructions: str = "", enabled_skills: Optional[List[str]] = None) -> Dict[str, Any]:
    """`skill` is the skill display name; `skill_instructions` is the body of its markdown definition."""
    skills, dropped = split_known(enabled_skills or AGENT_SKILLS.get(agent_id, ["data-analyst"]))
    logger.info("[EzCoworker] RUN_AGENT START agent_id=%r skill=%r skills=%s files_in_context=%d", agent_id, skill, skills, file_context.count("input/"))
    if dropped:
        logger.warning("[EzCoworker] unknown skills dropped: %s", dropped)
    file_note = ""
    if "input/" in file_context:
        file_note = ("The data files listed below are in your workspace. Open and read them with your tools "
                     "before answering. If a listed file cannot be opened, say so instead of guessing.\n")
    if "input/" not in file_context:
        file_note = ("No data file is attached to this request. If the task needs data from a file, do not guess and do not invent figures: "
                     "reply with exactly {\"answer\": \"<one sentence asking the user to attach a file or pick one from Scanned files>\", "
                     "\"needs_file\": true}. If the question can be answered from the policies alone, answer it.\n")
    prompt = (
        f"You are the {agent_id} agent on the CFO Back Office platform. Active skill: {skill}.\n"
        "This is a non-interactive API call: do NOT use the AskUserQuestion tool or any tool that waits for the user. "
        "If something is missing, say so in the JSON `answer` field.\n"
        f"=== SKILL INSTRUCTIONS ===\n{skill_instructions or '(none)'}\n=== END SKILL ===\n"
        "Follow ONLY the policies below; cite them by id (e.g. POL-003, never chunk ids like POL-003#0) in `policy_cited`.\n"
        "Every item in `alerts` MUST be an object {\"severity\": \"critical|warning|info\", \"message\": \"...\", "
        "\"policy\": \"POL-xxx\"} where `policy` is the id of the policy rule that was breached.\n\n"
        f"=== POLICIES ===\n{policy_context}\n=== END POLICIES ===\n"
        f"{file_note}{file_context}\n"
        "Respond with a single JSON object (no markdown fences) following the skill's Output section.\n\n"
        f"User request: {user_text}"
    )
    out = chat_stream(prompt, conversation_id, skills)
    out["skills"], out["skills_dropped"] = skills, dropped
    out["result"] = _to_json(out["reply"])
    logger.info("[EzCoworker] RUN_AGENT COMPLETE agent_id=%r conversation_id=%r", agent_id, out.get("conversationId"))
    return out


def _to_json(text: str) -> Dict[str, Any]:
    t = text.strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(t)
    except ValueError:
        a, b = t.find("{"), t.rfind("}")
        if a != -1 and b > a:
            try:
                return json.loads(t[a:b + 1])
            except ValueError:
                pass
    return {"answer": text, "analysis": "Free-text reply"}


def diagnose(agent_id: str = "je_factory") -> Dict[str, Any]:
    """Isolate why /chat/stream rejects a call. raw_head shows the first stream lines (where the new id appears)."""
    def probe(label, msg, skills=None):
        try:
            r = chat_stream(msg, None, skills)
            return {"probe": label, "ok": True, "conversationId": r["conversationId"],
                    "reply_preview": r["reply"][:120], "raw_head": r.get("raw_head")}
        except Exception as e:
            return {"probe": label, "ok": False, "error": str(e)[:600]}
    skills = AGENT_SKILLS.get(agent_id, ["data-analyst"])
    out = [probe("message only (no conversationId)", "Reply with the single word: pong"),
           probe("message + all agent skills", "Reply with the single word: pong", skills)]
    out += [probe(f"skill: {k}", "Reply with the single word: pong", [k]) for k in skills]
    convs = []
    if API_KEY:
        try:
            convs = [{"id": c.get("id"), "title": str(c.get("title"))[:60]} for c in list_conversations(5)]
        except Exception as e:
            convs = [{"error": str(e)[:200]}]
    return {"agent": agent_id, "base_url": BASE_URL, "key_configured": bool(API_KEY),
            "fixed_conversation_id": FIXED_CONVERSATION_ID or None, "latest_conversations": convs, "probes": out}

"""Log Registry: append-only audit trail + events (JSONL). One event per pipeline step."""
import json, logging, threading, time
from pathlib import Path
from typing import Any, Dict, List, Optional

_lock = threading.Lock()
logger = logging.getLogger("cfo.events")


class LogRegistry:
    def __init__(self, path: str = "events.jsonl"):
        self.path = Path(path)

    def event(self, type_: str, run_id: Optional[str] = None, agent_id: Optional[str] = None, **detail: Any):
        rec = {"ts": time.time(), "type": type_, "run_id": run_id, "agent_id": agent_id, "detail": detail}
        with _lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        lvl = logging.ERROR if ".failed" in type_ else logging.WARNING if type_.startswith("alert.") else logging.INFO
        logger.log(lvl, "%s run=%s agent=%s %s", type_, run_id, agent_id, json.dumps(detail, default=str)[:600])
        return rec

    def query(self, run_id: Optional[str] = None, type_prefix: Optional[str] = None,
              agent_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if run_id and r["run_id"] != run_id: continue
            if agent_id and r["agent_id"] != agent_id: continue
            if type_prefix and not r["type"].startswith(type_prefix): continue
            out.append(r)
        return out[-limit:]

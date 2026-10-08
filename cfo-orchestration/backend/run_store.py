"""Run history (JSONL on disk) so every orchestrated call is auditable (POL-002)."""
import json, threading, time, uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_lock = threading.Lock()


class RunStore:
    def __init__(self, path: str = "runs.jsonl"):
        self.path, self.mem = Path(path), {}
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                try:
                    r = json.loads(line); self.mem[r["run_id"]] = r
                except ValueError:
                    pass

    def create(self, agent_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
        r = {"run_id": uuid.uuid4().hex[:12], "agent_id": agent_id, "status": "queued",
             "created_at": time.time(), "request": request, "result": None, "error": None}
        self.update(r)
        return r

    def update(self, r: Dict[str, Any]):
        with _lock:
            self.mem[r["run_id"]] = r
            with self.path.open("a") as f:
                f.write(json.dumps(r, default=str) + "\n")

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self.mem.get(run_id)

    def count(self) -> int:
        with _lock:
            return len(self.mem)

    def counts_by_agent(self) -> Dict[str, int]:
        with _lock:
            counts: Dict[str, int] = {}
            for run in self.mem.values():
                agent_id = run["agent_id"]
                counts[agent_id] = counts.get(agent_id, 0) + 1
            return counts

    def list(self, agent_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        rs = [r for r in self.mem.values() if not agent_id or r["agent_id"] == agent_id]
        return sorted(rs, key=lambda r: -r["created_at"])[:limit]

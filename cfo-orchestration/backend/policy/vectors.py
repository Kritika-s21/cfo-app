"""Vector backends for policy chunks. LanceDB is the default; in-memory is a fallback/dev option."""
import math
from typing import Any, Dict, List, Optional, Tuple


class MemoryVectors:
    name = "memory"

    def __init__(self):
        self.rows: List[Dict[str, Any]] = []

    def rebuild(self, rows: List[Dict[str, Any]]):
        self.rows = rows

    def search(self, qv: List[float], k: int, allowed: Optional[set]) -> List[Tuple[float, Dict[str, Any]]]:
        sc = [(sum(a * b for a, b in zip(qv, r["vector"])), r) for r in self.rows if allowed is None or r["policy_id"] in allowed]
        return sorted(sc, key=lambda x: -x[0])[:k]


class LanceVectors:
    name = "lancedb"

    def __init__(self, uri: str = "lancedb", table: str = "policy_chunks"):
        import lancedb
        self.db, self.tname, self.table = lancedb.connect(uri), table, None

    def rebuild(self, rows: List[Dict[str, Any]]):
        if not rows:
            return
        self.table = self.db.create_table(self.tname, data=rows, mode="overwrite")

    def search(self, qv: List[float], k: int, allowed: Optional[set]) -> List[Tuple[float, Dict[str, Any]]]:
        if self.table is None:
            return []
        q = self.table.search(qv).metric("cosine")
        if allowed is not None:
            if not allowed:
                return []
            q = q.where("policy_id IN (" + ",".join("'" + a.replace("'", "''") + "'" for a in allowed) + ")", prefilter=True)
        return [(1.0 - r["_distance"], r) for r in q.limit(k).to_list()]


def make_backend(kind: str, uri: str):
    if kind == "lancedb":
        try:
            return LanceVectors(uri)
        except ImportError:
            print("lancedb not installed; falling back to in-memory vectors")
    return MemoryVectors()

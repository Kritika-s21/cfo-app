"""Policy store: markdown (source of truth) -> chunks -> vectors + graph. Pluggable embedder/vector DB/graph DB."""
import hashlib, json, math, re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import yaml

Embedder = Callable[[List[str]], List[List[float]]]


def hash_embedder(texts: List[str], dim: int = 256) -> List[List[float]]:
    """Dev/test embedder (bag-of-words hashing). Replace with a real model (OpenAI, bge, e5...) in prod."""
    out = []
    for t in texts:
        v = [0.0] * dim
        for w in re.findall(r"[a-z0-9₹%]+", t.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dim] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


@dataclass
class Chunk:
    id: str
    policy_id: str
    section: str
    text: str
    vec: List[float] = field(default_factory=list)


@dataclass
class Policy:
    id: str
    meta: Dict[str, Any]
    body: str
    path: str


def parse_markdown(path: Path) -> Policy:
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    meta, body = (yaml.safe_load(m.group(1)) or {}, m.group(2)) if m else ({}, raw)
    meta.setdefault("id", path.stem.upper())
    return Policy(id=meta["id"], meta=meta, body=body.strip(), path=str(path))


def chunk_policy(p: Policy) -> List[Chunk]:
    """One chunk per '## Section' (rules stay together with their heading)."""
    parts = re.split(r"(?m)^##\s+", p.body)
    chunks = []
    for i, part in enumerate(parts):
        if not part.strip():
            continue
        head, _, rest = part.partition("\n") if i or p.body.startswith("##") else ("Overview", "", part)
        text = f"{p.meta.get('name', p.id)} — {head.strip()}\n{rest.strip()}"
        chunks.append(Chunk(id=f"{p.id}#{len(chunks)}", policy_id=p.id, section=head.strip(), text=text))
    return chunks


class PolicyGraph:
    """Tiny typed-edge graph. Swap for Neo4j/Postgres edges: same add_edge/neighbors contract."""
    def __init__(self):
        self.edges: List[Tuple[str, str, str]] = []  # (src, rel, dst)

    def add_edge(self, s: str, rel: str, d: str):
        if (s, rel, d) not in self.edges:
            self.edges.append((s, rel, d))

    def neighbors(self, node: str, rels: Optional[set] = None) -> List[Tuple[str, str]]:
        out = []
        for s, r, d in self.edges:
            if rels and r not in rels:
                continue
            if s == node:
                out.append((r, d))
            elif d == node:
                out.append((f"~{r}", s))
        return out

    def to_json(self):
        nodes = sorted({n for s, _, d in self.edges for n in (s, d)})
        return {"nodes": [{"id": n, "type": n.split(":")[0] if ":" in n else "policy"} for n in nodes],
                "edges": [{"source": s, "rel": r, "target": d} for s, r, d in self.edges]}


class PolicyStore:
    def __init__(self, policy_dir: str, embedder: Embedder = hash_embedder, vectors=None, graph_file: Optional[str] = None):
        from .vectors import MemoryVectors
        self.dir, self.embed = Path(policy_dir), embedder
        self.vectors = vectors or MemoryVectors()
        self.graph_file = graph_file
        self.policies: Dict[str, Policy] = {}
        self.chunks: List[Chunk] = []
        self.graph = PolicyGraph()

    # ---- indexing -------------------------------------------------------
    def reindex(self):
        self.policies, self.chunks, self.graph = {}, [], PolicyGraph()
        for f in sorted(self.dir.glob("*.md")):
            p = parse_markdown(f)
            self.policies[p.id] = p
        for p in self.policies.values():
            cs = chunk_policy(p)
            self.chunks += cs
            for c in cs:
                self.graph.add_edge(p.id, "HAS_SECTION", c.id)
            for a in p.meta.get("agents", []) or []:
                self.graph.add_edge(p.id, "APPLIES_TO", f"agent:{a}")
            for ref in set(re.findall(r"\[\[(POL-\d+)\]\]", p.body)) | set(p.meta.get("related", []) or []):
                if ref != p.id:
                    self.graph.add_edge(p.id, "REFERENCES", ref)
            if p.meta.get("supersedes"):
                self.graph.add_edge(p.id, "SUPERSEDES", p.meta["supersedes"])
        vecs = self.embed([c.text for c in self.chunks])
        for c, v in zip(self.chunks, vecs):
            c.vec = v
        self.vectors.rebuild([{"id": c.id, "policy_id": c.policy_id, "section": c.section, "text": c.text, "vector": c.vec}
                              for c in self.chunks])
        if self.graph_file:
            Path(self.graph_file).write_text(json.dumps(self.graph.to_json()), encoding="utf-8")
        return {"policies": len(self.policies), "chunks": len(self.chunks), "edges": len(self.graph.edges)}

    # ---- retrieval: vector search + graph expansion ---------------------
    def search(self, query: str, agent_id: Optional[str] = None, k: int = 4, hops: bool = True, extra_policies: Optional[List[str]] = None) -> Dict[str, Any]:
        qv = self.embed([query])[0]
        allowed = None
        if agent_id:
            allowed = {s for s, r, d in self.graph.edges if r == "APPLIES_TO" and d == f"agent:{agent_id}"}
        # allowed policy set may be widened by the caller (e.g. skill-required policies)
        if allowed is not None and extra_policies:
            allowed = allowed | set(extra_policies)
        found = self.vectors.search(qv, k, allowed)
        scored = [(sc, Chunk(id=r["id"], policy_id=r["policy_id"], section=r["section"], text=r["text"])) for sc, r in found]
        hit_ids = [c.id for _, c in scored]
        pols = {c.policy_id for _, c in scored}
        expanded: List[str] = []
        if hops:  # pull in policies referenced by the hits (e.g. approval policy cited by a cash policy)
            for pid in list(pols):
                for rel, other in self.graph.neighbors(pid, {"REFERENCES"}):
                    if rel == "REFERENCES" and other in self.policies and other not in pols:
                        expanded.append(other)
        # mandatory policies (flagged `always_load: true`, e.g. POL-008 "load before routing any task")
        always = [p.id for p in self.policies.values() if p.meta.get("always_load") and p.id not in pols]
        return {"hits": [{"chunk": c.id, "score": round(s, 3), "text": c.text} for s, c in scored],
                "graph_expanded": expanded, "always_loaded": always, "hit_ids": hit_ids}

    def build_context(self, query: str, agent_id: str, k: int = 4, required: Optional[List[str]] = None) -> Dict[str, Any]:
        r = self.search(query, agent_id, k, extra_policies=required)
        blocks, cited = [], []
        for h in r["hits"]:
            blocks.append(f"[{h['chunk']}]\n{h['text']}")
            cited.append(h["chunk"].split("#")[0])
        have = {c.split("#")[0] for c in cited}
        forced = [p for p in (required or []) if p in self.policies and p not in have
                  and p not in r["graph_expanded"] and p not in r["always_loaded"]]
        for pid in r["graph_expanded"] + r["always_loaded"] + forced:
            p = self.policies[pid]
            blocks.append(f"[{pid} (linked/mandatory)]\n{p.meta.get('name', pid)}\n{p.body}")
            cited.append(pid)
        return {"context": "\n\n".join(blocks), "policies_used": sorted(set(cited)), "retrieval": r}

    # ---- CRUD (markdown stays the source of truth; every save archives the previous version) ----
    def _archive(self, pid: str):
        f = self.dir / f"{pid}.md"
        if f.exists():
            ver = str(parse_markdown(f).meta.get("version", "unversioned"))
            h = self.dir / ".history" / pid
            h.mkdir(parents=True, exist_ok=True)
            n = len(list(h.glob("*.md")))
            (h / f"{n:03d}_{ver}.md").write_text(f.read_text(encoding="utf-8"), encoding="utf-8")

    def save(self, pid: str, meta: Dict[str, Any], body: str):
        self._archive(pid)
        meta = {**meta, "id": pid}
        (self.dir / f"{pid}.md").write_text(f"---\n{yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)}---\n{body}\n", encoding="utf-8")
        return self.reindex()

    def delete(self, pid: str):
        self._archive(pid)
        (self.dir / f"{pid}.md").unlink(missing_ok=True)
        return self.reindex()

    def versions(self, pid: str) -> List[Dict[str, Any]]:
        h = self.dir / ".history" / pid
        out = []
        for f in sorted(h.glob("*.md")) if h.exists() else []:
            p = parse_markdown(f)
            out.append({"revision": f.stem, "version": p.meta.get("version"), "body": p.body, "meta": p.meta})
        return out

    def impact(self, pid: str) -> Dict[str, Any]:
        agents = sorted(d[6:] for s, r, d in self.graph.edges if s == pid and r == "APPLIES_TO")
        refd_by = sorted(s for s, r, d in self.graph.edges if d == pid and r == "REFERENCES")
        refs = sorted(d for s, r, d in self.graph.edges if s == pid and r == "REFERENCES")
        return {"policy": pid, "agents": agents, "references": refs, "referenced_by": refd_by}

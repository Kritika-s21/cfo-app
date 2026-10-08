"""Skill registry: skills are markdown files (frontmatter + instructions). Router picks a skill per request."""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import yaml


@dataclass
class Skill:
    id: str
    name: str
    description: str
    agents: List[str]
    ezcoworker_skills: List[str]
    triggers: List[str]
    policies: List[str]
    knowledge: List[str]
    outputs: List[str]
    priority: int
    body: str
    meta: Dict[str, Any] = field(default_factory=dict)

    def applies_to(self, agent_id: str) -> bool:
        return "*" in self.agents or agent_id in self.agents


class SkillRegistry:
    def __init__(self, directory: str = "skills"):
        self.dir = Path(directory)
        self.skills: Dict[str, Skill] = {}
        self.reload()

    def reload(self) -> int:
        self.skills = {}
        for f in sorted(self.dir.glob("*.md")):
            m = re.match(r"^---\n(.*?)\n---\n(.*)$", f.read_text(encoding="utf-8"), re.S)
            if not m:
                continue
            meta = yaml.safe_load(m.group(1)) or {}
            sid = meta.get("id", f.stem)
            self.skills[sid] = Skill(
                id=sid, name=meta.get("name", sid), description=meta.get("description", ""),
                agents=meta.get("agents") or ["*"], ezcoworker_skills=meta.get("ezcoworker_skills") or [],
                triggers=[t.lower() for t in meta.get("triggers") or []], policies=meta.get("policies") or [],
                knowledge=meta.get("knowledge") or ["policy_docs"], outputs=meta.get("outputs") or [],
                priority=int(meta.get("priority", 5)), body=m.group(2).strip(), meta=meta)
        return len(self.skills)

    def for_agent(self, agent_id: str) -> List[Skill]:
        return sorted((s for s in self.skills.values() if s.applies_to(agent_id)), key=lambda s: s.priority)

    def resolve(self, agent_id: str, hint: str, text: str) -> Tuple[Optional[Skill], str]:
        """Return (skill, how). how = hint | trigger | default | none."""
        h = (hint or "").strip().lower()
        if h:
            for s in self.skills.values():
                if h in (s.id, s.name.lower()):
                    return s, "hint"
        cands = self.for_agent(agent_id)
        q = text.lower()
        scored = [(sum(1 for t in s.triggers if t in q), -s.priority, s) for s in cands]
        scored = [x for x in scored if x[0] > 0]
        if scored:
            return max(scored, key=lambda x: (x[0], x[1]))[2], "trigger"
        return (cands[0], "default") if cands else (None, "none")

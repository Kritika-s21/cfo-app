"""Scheduler: cron triggers + a worker pool acting as the task queue. Jobs call the same orchestrate() as users."""
import glob, hashlib, json, logging, os, time, uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.executors.pool import ThreadPoolExecutor

import orchestrator as orch
import ezcoworker_client as ez

logger = logging.getLogger("cfo.scheduler")


def _fingerprint(paths) -> str:
    """Content hash of the watched files (name + bytes), so 'changed' means the data really changed."""
    h = hashlib.sha256()
    for p in paths:
        h.update(os.path.basename(p).encode())
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


class SchedulerService:
    def __init__(self, path: str = "schedules.json", default_path: str = "schedules.default.json"):
        self.path = Path(path)
        self.sched = BackgroundScheduler(executors={"default": ThreadPoolExecutor(int(os.environ.get("SCHED_WORKERS", "2")))},
                                         job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600})
        if self.path.exists():
            self.items: Dict[str, Dict[str, Any]] = {s["id"]: s for s in json.loads(self.path.read_text())}
        else:
            d = Path(default_path)
            self.items = {s["id"]: s for s in (json.loads(d.read_text()) if d.exists() else [])}
            self._save()

    def start(self):
        for s in self.items.values():
            self._register(s)
        self.sched.start()

    def shutdown(self):
        if self.sched.running:
            self.sched.shutdown(wait=False)

    def _save(self):
        self.path.write_text(json.dumps(list(self.items.values()), indent=2))

    def _trigger(self, s):
        f = s["cron"].split()
        if len(f) != 5:
            raise ValueError("cron must have 5 fields")
        if f[2].upper() == "L":
            f[2] = "last"
        return CronTrigger.from_crontab(" ".join(f), timezone=s.get("timezone", "Asia/Kolkata"))

    def _register(self, s):
        if self.sched.get_job(s["id"]):
            self.sched.remove_job(s["id"])
        if s.get("enabled", True):
            self.sched.add_job(self._fire, self._trigger(s), id=s["id"], args=[s["id"], "cron"], replace_existing=True)

    def _prepare_files(self, s, source: str):
        """Resolve s['watch_path'], skip unchanged data on cron runs, upload to EzCoworker.
        -> (file_context, fingerprint, skip_reason). skip_reason is set when the run should not happen."""
        wp = (s.get("watch_path") or "").strip()
        if not wp:
            return "", None, None
        paths = [p for p in sorted(glob.glob(wp, recursive=True)) if os.path.isfile(p)]
        if not paths:
            return "", None, f"no files match watch_path {wp!r}"
        fp = _fingerprint(paths)
        if source == "cron" and s.get("only_if_changed", True) and fp == s.get("last_fingerprint"):
            return "", fp, "watched files unchanged since last successful run"
        remote = []
        for p in paths:
            with open(p, "rb") as f:
                remote.append(ez.upload_file(os.path.basename(p), f.read())["remote_path"])
        ctx = "Uploaded data files (read these with your tools):\n" + "\n".join(f"- {r}" for r in remote)
        return ctx, fp, None

    def _fire(self, sid: str, source: str = "cron"):
        s = self.items.get(sid)
        if not s:
            return
        rec = orch.runs.create(s["agent_id"], {"text": s["text"], "schedule_id": sid, "source": source})
        rec["status"] = "running"; orch.runs.update(rec)
        orch.logs.event("schedule.fired", rec["run_id"], s["agent_id"], schedule_id=sid, source=source)
        logger.info("schedule %s fired (%s) agent=%s run=%s", sid, source, s["agent_id"], rec["run_id"])
        fp = None
        try:
            file_context, fp, skip = self._prepare_files(s, source)
            if skip:
                logger.info("schedule %s skipped: %s", sid, skip)
                orch.logs.event("schedule.skipped", rec["run_id"], s["agent_id"], schedule_id=sid, reason=skip)
                rec.update(status="skipped", result={"skipped": skip}); s["last_status"] = "skipped"
                s["last_run"] = time.time(); s["last_run_id"] = rec["run_id"]
                orch.runs.update(rec); self._save()
                return rec["run_id"]
            out = orch.orchestrate(s["agent_id"], s["text"], s.get("skill", ""), file_context=file_context,
                                   source=f"schedule:{sid}", run_id=rec["run_id"])
            rec.update(status="succeeded", result=out); s["last_status"] = "succeeded"
            if fp:
                s["last_fingerprint"] = fp          # only remembered after a successful run, so failures retry next time
        except Exception as e:
            logger.exception("schedule %s failed", sid)
            rec.update(status="failed", error=str(e)); s["last_status"] = "failed"
            orch.logs.event("run.failed", rec["run_id"], s["agent_id"], error=str(e))
        s["last_run"] = time.time(); s["last_run_id"] = rec["run_id"]
        orch.runs.update(rec); self._save()
        return rec["run_id"]

    # ---- public API ----
    def view(self, s) -> Dict[str, Any]:
        job = self.sched.get_job(s["id"]) if self.sched.running else None
        nxt = getattr(job, "next_run_time", None)
        return {**s, "next_run": nxt.isoformat() if nxt else None}

    def list(self) -> List[Dict[str, Any]]:
        return [self.view(s) for s in self.items.values()]

    def upsert(self, sid: Optional[str], data: Dict[str, Any]) -> Dict[str, Any]:
        sid = sid or "sch_" + uuid.uuid4().hex[:8]
        s = {**self.items.get(sid, {}), **data, "id": sid}
        self._trigger(s)  # validate before saving
        self.items[sid] = s
        self._save()
        if self.sched.running:
            self._register(s)
        return self.view(s)

    def delete(self, sid: str):
        self.items.pop(sid, None)
        if self.sched.running and self.sched.get_job(sid):
            self.sched.remove_job(sid)
        self._save()

    def run_now(self, sid: str, background: bool = True):
        if background and self.sched.running:  # goes through the worker pool (task queue)
            self.sched.add_job(self._fire, args=[sid, "manual"], id=f"manual_{sid}_{uuid.uuid4().hex[:6]}")
            return {"queued": True}
        return {"run_id": self._fire(sid, "manual")}

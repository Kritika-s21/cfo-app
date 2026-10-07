"""
cfo_bridge.py
-------------
Connects the File Pickup Scheduler to the CFO Back Office platform.

When a scan finds a NEW or MODIFIED file, run_pipeline() (api_server.py) extracts/classifies/ingests it as before and
then calls handle_file() here, which:
  1. downloads the file bytes with the same downloaders content_extractor uses (local, azure, s3, gcs, gdrive),
  2. uploads them to the CFO backend  POST /api/v1/files  ->  EzCoworker workspace as input/<file name>
     (a modified file overwrites the previous version, so agents always read the latest data),
  3. runs every agent/skill whose rule matches the file name  POST /api/v1/agents/{id}/run  with that file attached.

Config (env):
  CFO_BRIDGE_ENABLED=1            turn the bridge on (default off: ingestion behaves exactly as before)
  CFO_API_BASE=http://localhost:8766
  CFO_API_KEY=...                 one of the CFO backend's CFO_API_KEYS (if auth is enabled)
  CFO_RULES_FILE=cfo_rules.json   rules; each needs agent_id plus at least one criterion:
                                    "match":       filename glob, e.g. "ap_invoices*.csv"
                                    "columns_all": every one of these column headers must be present
                                    "columns_any": at least one of these column headers must be present
                                  Column names ignore case, spaces and punctuation ("PO Number" = "po_number"). Several criteria in
                                  one rule must all hold, so a file can have ANY name when the rule uses columns only.
  CFO_UPLOAD_ALWAYS=1             upload files even when no rule matches (so users can run skills on them from chat)
"""
from __future__ import annotations

import base64
import fnmatch
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List

import requests

from content_extractor import DOWNLOADERS, _download_local

logger = logging.getLogger("scheduler.cfo_bridge")

ENABLED = os.environ.get("CFO_BRIDGE_ENABLED", "0") == "1"
CFO_API_BASE = os.environ.get("CFO_API_BASE", "http://localhost:8766").rstrip("/")
CFO_API_KEY = os.environ.get("CFO_API_KEY", "")
_rf = Path(os.environ.get("CFO_RULES_FILE", "cfo_rules.json"))
# Resolve relative to this file, not the process working directory, so the rules are found wherever the scheduler is started
RULES_FILE = _rf if _rf.is_absolute() else Path(__file__).resolve().parent / _rf
UPLOAD_ALWAYS = os.environ.get("CFO_UPLOAD_ALWAYS", "1") == "1"
UPLOAD_TIMEOUT = int(os.environ.get("CFO_UPLOAD_TIMEOUT", "120"))
RUN_TIMEOUT = int(os.environ.get("CFO_RUN_TIMEOUT", "330"))      # CFO backend allows up to 300 s per agent call


def _headers() -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if CFO_API_KEY:
        h["X-API-Key"] = CFO_API_KEY
    return h


def _norm(col: Any) -> str:
    """'PO Number', 'po_number' and 'PO-NUMBER' all become 'ponumber'."""
    return re.sub(r"[^a-z0-9]", "", str(col).lower())


def _as_list(v: Any) -> List[str]:
    return [v] if isinstance(v, str) else list(v or [])


def _has_criteria(rule: Dict[str, Any]) -> bool:
    return bool(rule.get("match") or _as_list(rule.get("columns_all")) or _as_list(rule.get("columns_any")))


def _needs_columns(rule: Dict[str, Any]) -> bool:
    return bool(_as_list(rule.get("columns_all")) or _as_list(rule.get("columns_any")))


def _name_ok(rule: Dict[str, Any], name: str) -> bool:
    return not rule.get("match") or fnmatch.fnmatch(name.lower(), str(rule["match"]).lower())


def _columns_ok(rule: Dict[str, Any], columns: List[str]) -> bool:
    have = {_norm(c) for c in columns}
    want_all = {_norm(c) for c in _as_list(rule.get("columns_all"))}
    want_any = {_norm(c) for c in _as_list(rule.get("columns_any"))}
    return want_all <= have and (not want_any or bool(want_any & have))


def rule_matches(rule: Dict[str, Any], name: str, columns: List[str]) -> bool:
    """True when every criterion the rule declares (filename glob, columns_all, columns_any) holds for this file."""
    return _name_ok(rule, name) and (not _needs_columns(rule) or _columns_ok(rule, columns))


def load_rules() -> List[Dict[str, Any]]:
    """Re-read on every file so rules can be edited without restarting the scheduler."""
    if not RULES_FILE.exists():
        logger.warning("[cfo_bridge] rules file NOT FOUND: %s (copy cfo_rules.example.json to cfo_rules.json)", RULES_FILE)
        return []
    try:
        rules = json.loads(RULES_FILE.read_text(encoding="utf-8"))
        return [r for r in rules if r.get("agent_id") and r.get("enabled", True) and _has_criteria(r)]
    except (OSError, ValueError) as e:
        logger.error("Could not read %s: %s", RULES_FILE, e)
        return []


def handle_file(file_info: Dict[str, Any]) -> Dict[str, Any]:
    """Upload one picked-up file and run the matching CFO agents. Raises on upload failure; agent-run failures are
    reported per rule so one failing agent does not hide the others."""
    name = file_info.get("file_name") or Path(file_info.get("path", "")).name
    if not name:
        raise ValueError("file_info has no file name")
    status = "modified" if file_info.get("is_modified") else "new"
    all_rules = load_rules()
    # Rules that look only at the filename can be decided now; rules that look at columns need the file's header row
    by_name = [r for r in all_rules if not _needs_columns(r) and _name_ok(r, name)]
    content_candidates = [r for r in all_rules if _needs_columns(r) and _name_ok(r, name)]
    if not by_name and not content_candidates and not UPLOAD_ALWAYS:
        logger.info("[cfo_bridge] %s: no rule matches, not uploading", name)
        return {"uploaded": None, "runs": [], "note": "no matching rule", "rules_loaded": len(all_rules)}

    t0 = time.perf_counter()
    source_type = file_info.get("source_type", "local")
    data = DOWNLOADERS.get(source_type, _download_local)(file_info)
    logger.info("[cfo_bridge] %s (%s, %s): %d bytes downloaded", name, source_type, status, len(data))

    r = requests.post(f"{CFO_API_BASE}/api/v1/files", headers=_headers(), timeout=UPLOAD_TIMEOUT,
                      json={"filename": name, "content_b64": base64.b64encode(data).decode("ascii"),
                            "source": "scheduler", "source_ref": str(file_info.get("path") or file_info.get("source_id") or ""),
                            "change": status})
    if not r.ok:
        raise RuntimeError(f"CFO upload failed: HTTP {r.status_code}: {r.text[:300]}")
    up = r.json()
    remote = up.get("remote_path") or f"input/{name}"
    columns = (up.get("file") or {}).get("columns") or []     # header row, read by the backend (csv / tsv / xlsx)
    logger.info("[cfo_bridge] uploaded %s -> %s in %.1fs (columns: %s)", name, remote, time.perf_counter() - t0, columns[:12])

    by_content = [r for r in content_candidates if rule_matches(r, name, columns)]
    rules, seen = [], set()
    for rule in by_name + by_content:                         # a file can satisfy several rules; never run the same job twice
        key = (rule["agent_id"], rule.get("skill", ""), rule.get("text", ""))
        if key not in seen:
            seen.add(key); rules.append(rule)
    if rules:
        logger.info("[cfo_bridge] %s: matched %d rule(s): %s (by name: %d, by columns: %d)", name, len(rules),
                    [r["agent_id"] for r in rules], len(by_name), len(by_content))
    else:
        logger.warning("[cfo_bridge] %s: NO RULE MATCHED (%d rules loaded from %s). Columns seen: %s. Uploaded only, no agent will run.",
                       name, len(all_rules), RULES_FILE, columns[:20])

    runs: List[Dict[str, Any]] = []
    for rule in rules:
        agent_id = rule["agent_id"]
        text = (rule.get("text") or f"Process the {status} file and flag exceptions.") + f" (File: {remote}, {status} pickup)"
        body = {"text": text, "skill": rule.get("skill", ""),
                "file_context": f"Uploaded data files (read these with your tools):\n- {remote}"}
        try:
            t1 = time.perf_counter()
            rr = requests.post(f"{CFO_API_BASE}/api/v1/agents/{agent_id}/run", headers=_headers(), json=body, timeout=RUN_TIMEOUT)
            if not rr.ok:
                raise RuntimeError(f"HTTP {rr.status_code}: {rr.text[:300]}")
            out = rr.json()
            alerts = (out.get("structured_output") or {}).get("alerts") or []
            runs.append({"agent_id": agent_id, "status": "done", "alerts": len(alerts),
                         "skill": (out.get("skill") or {}).get("name"), "seconds": round(time.perf_counter() - t1, 1)})
            logger.info("[cfo_bridge] %s -> agent %s done (%d alerts)", name, agent_id, len(alerts))
        except Exception as e:
            logger.exception("[cfo_bridge] agent %s failed for %s", agent_id, name)
            runs.append({"agent_id": agent_id, "status": "error", "error": str(e)})
    note = "" if rules else f"uploaded only: no rule matched ({len(all_rules)} rules loaded)"
    return {"uploaded": remote, "change": status, "runs": runs, "rules_loaded": len(all_rules), "note": note}

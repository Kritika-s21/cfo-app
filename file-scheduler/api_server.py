"""
api_server.py
-------------
Unified FastAPI server — Scheduler REST API  +  Data Ingestion Agent
Runs on a single port (default 8765, override with API_PORT env var).

═══════════════════════════════════════════════════════════════════════
 SCHEDULER ENDPOINTS  (formerly api_server.py)
═══════════════════════════════════════════════════════════════════════
GET    /api/status                  — current scheduler state
POST   /api/scheduler/start         — start scheduler
POST   /api/scheduler/stop          — stop scheduler
POST   /api/scheduler/pause         — pause scheduler
POST   /api/scheduler/resume        — resume scheduler
POST   /api/scheduler/trigger       — run a scan NOW
PUT    /api/config                  — update ScheduleConfig
POST   /api/sources                 — add a source
DELETE /api/sources/{source_id}     — remove a source
GET    /api/profiles                — list saved scheduler profiles (durable)
PUT    /api/profiles/{profile_id}   — create/replace a scheduler profile
DELETE /api/profiles/{profile_id}   — delete a scheduler profile
GET    /api/history                 — last 50 scheduler scan results
GET    /api/timezones               — list available timezones
WS     /ws/events                   — real-time scheduler scan events (JSON)

═══════════════════════════════════════════════════════════════════════
 AGENT ENDPOINTS  (formerly agent_ingestion.py)
═══════════════════════════════════════════════════════════════════════
GET    /agent/status                — agent health + vector store stats
POST   /agent/ingest/file           — Extract → Classify → SQL/LanceDB (single file)
POST   /agent/ingest/batch          — same, concurrent batch
GET    /agent/ingested/structured   — recent SQL records
GET    /agent/ingested/vector/stats — LanceDB stats
POST   /agent/vector/search         — semantic search over LanceDB
GET    /agent/results               — recent pipeline results (last 200)
GET    /agent/logs                  — last N lines of agent log file
WS     /agent/ws/progress           — real-time pipeline events (JSON)

"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# ─────────────────────────────────────────────
#  Secret resolver  (no longer imported from scheduler_app)
# ─────────────────────────────────────────────
def resolve_secret(value: str) -> str:
    """
    Resolve a credential value to its plaintext form.

    Handles three storage forms used by scheduler_app when it saves sources:
      enc:v1:<b64>  — Fernet-encrypted with SCHED_MASTER_KEY env var
      $VAR_NAME     — read from environment variable
      anything else — returned as-is (plain literal / already decrypted)
    """
    if not isinstance(value, str):
        return value

    # ── Fernet-encrypted (enc:v1:...) ─────────────────────────
    if value.startswith("enc:v1:"):
        master_key = os.environ.get("SCHED_MASTER_KEY", "")
        if not master_key:
            raise RuntimeError(
                "Credential is Fernet-encrypted but SCHED_MASTER_KEY env var is not set."
            )
        try:
            from cryptography.fernet import Fernet
            f = Fernet(master_key.encode() if isinstance(master_key, str) else master_key)
            encrypted_b64 = value[len("enc:v1:"):]
            import base64
            return f.decrypt(base64.b64decode(encrypted_b64)).decode()
        except Exception as exc:
            raise RuntimeError(f"Failed to decrypt credential: {exc}") from exc

    # ── Environment variable reference ($VAR_NAME) ────────────
    if value.startswith("$"):
        var_name = value[1:]
        resolved = os.environ.get(var_name, "")
        if not resolved:
            logger.warning("Env var '%s' referenced in credentials is not set.", var_name)
        return resolved

    # ── Plain literal — return as-is ─────────────────────────
    return value

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ─────────────────────────────────────────────
#  Ports / paths
# ─────────────────────────────────────────────

API_PORT           = int(os.environ.get("API_PORT",            8765))
AGENT_BATCH_CONC   = int(os.environ.get("AGENT_BATCH_CONCURRENCY", 4))

LOG_DIR  = Path(os.environ.get("LOG_DIR", "./agent_logs"))
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "agent_ingestion.log"

# New logs always live beside this application, independent of the process
# working directory.  The previous sibling-project location remains readable
# and deletable below for backwards compatibility.
_APP_DIR = Path(__file__).resolve().parent
SCHEDULER_LOGS_DIR = Path(os.environ.get("SCHEDULER_LOGS_DIR", str(_APP_DIR / "scheduler_logs"))).resolve()
SCHEDULER_LOGS_DIR.mkdir(parents=True, exist_ok=True)
_LEGACY_SCHEDULER_LOGS_DIR = (_APP_DIR.parent / "scheduler_new" / "scheduler_logs").resolve()

# Scheduler *profiles* (name/schedule/sources per saved config) used to live
# only in browser localStorage (see api.js ProfileStore) — losing that
# storage (cache clear, private window, different browser/device) silently
# deleted every scheduler with no way to recover them, even though scan
# history survived. They're now persisted here too, the same way scan logs
# are, so /api/profiles is the durable source of truth and localStorage is
# just a fast local cache.
SCHEDULER_PROFILES_DIR = Path(os.environ.get("SCHEDULER_PROFILES_DIR", str(_APP_DIR / "scheduler_profiles"))).resolve()
SCHEDULER_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
SCHEDULER_PROFILES_FILE = SCHEDULER_PROFILES_DIR / "profiles.json"

# ─────────────────────────────────────────────
#  Logging  (file + console, used by both halves)
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("api.server")

# ─────────────────────────────────────────────
#  Scheduler engine imports
# ─────────────────────────────────────────────

from scheduler_engine import (
    FileScheduler, ScheduleConfig, ScheduleFrequency,
    SchedulerStatus, ScanResult, Weekday, SchedulerStateStore,
)
from sources import build_source

# ─────────────────────────────────────────────
#  Agent pipeline imports
# ─────────────────────────────────────────────

from content_extractor import extract as _extract_content
from classifier        import classify
import cfo_bridge as _cfo_bridge   # optional hand-off to the CFO Back Office agents (CFO_BRIDGE_ENABLED=1)
from vector_store      import ingest_unstructured, get_stats as vdb_stats, search as vdb_search
import db_connections
from sql_ingestion     import ingest_structured, get_ingested_records, delete_ingested_records

# ─────────────────────────────────────────────
#  FastAPI app
# ─────────────────────────────────────────────

app = FastAPI(
    title="File Scheduler + Ingestion Agent API",
    version="2.0.0",
    description=(
        "Unified API: Scheduler REST control (*/api/*) "
        "and Data Classification & Ingestion pipeline (*/agent/*)."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
#  Shared state
# ─────────────────────────────────────────────

# ── Scheduler ──
_state_store = SchedulerStateStore()
_scheduler   = FileScheduler(ScheduleConfig(), _state_store)
_sched_ws_clients: List[WebSocket] = []   # /ws/events subscribers

# ID of the frontend "profile" (ProfileStore entry) currently bound to this
# single backend engine. Set via PUT /api/config { scheduler_id: ... }.
# Lets /api/history report a scheduler_id that actually matches the id the
# React UI uses to key its scheduler rows, instead of the engine's own
# fixed internal id (which the frontend never knows about).
_active_scheduler_profile_id: Optional[str] = None

# ── Durable scan-history archive ────────────────────────────────────────
# _scheduler.scan_history lives in memory only and is wiped on every
# process restart, even though each scan is also written to disk as
# scheduler_logs/scan_*.json. We mirror every completed scan into this
# list (and rebuild it from those JSON files on startup) so history —
# including "today's" scans if the process restarts before "tomorrow" —
# survives restarts and /api/history stays accurate.
_history_archive: List[Dict] = []
_HISTORY_ARCHIVE_MAX = 500

# Set right before a manual trigger fires; consumed by the next scan
# completion so its trigger type is reported correctly instead of the
# previous hardcoded "auto" for every scan, manual or scheduled.
_manual_trigger_pending = False
# scan_id[:8] -> "manual" | "auto", so get_history() can report the right
# trigger type for entries still coming from the live in-memory list too
# (not just the ones freshly written via _store_scan_log).
_scan_trigger_map: Dict[str, str] = {}


def _history_log_dirs() -> List[Path]:
    """Current and legacy locations, de-duplicated and restricted to known dirs."""
    return list(dict.fromkeys((SCHEDULER_LOGS_DIR, _LEGACY_SCHEDULER_LOGS_DIR)))


def _read_scan_log(path: Path) -> Optional[Dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) and data.get("scan_id") else None
    except Exception:
        logger.warning("Could not load persisted scan log %s", path)
        return None


def _matching_log_files(scan_id: Optional[str] = None) -> List[Path]:
    """Find current and legacy JSON logs by their payload scan_id, not filename."""
    files: List[Path] = []
    for directory in _history_log_dirs():
        if not directory.is_dir():
            continue
        for path in directory.glob("*.json"):
            data = _read_scan_log(path)
            if data and (scan_id is None or str(data["scan_id"]) == scan_id):
                files.append(path)
    return files


def _load_history_archive_from_disk() -> None:
    """Rebuild archive from both current and legacy persisted scan logs."""
    global _history_archive
    by_scan_id: Dict[str, Dict] = {}
    for f in _matching_log_files():
        data = _read_scan_log(f)
        if data:
            by_scan_id[str(data["scan_id"])] = data
    loaded = list(by_scan_id.values())
    loaded.sort(key=lambda r: r.get("started_at") or "")
    _history_archive = loaded[-_HISTORY_ARCHIVE_MAX:]
    logger.info(
        f"Loaded {len(_history_archive)} persisted scan log(s) from {_history_log_dirs()}"
    )


_load_history_archive_from_disk()

# ── Durable scheduler-profile store ─────────────────────────────────────
# id -> profile dict (name/frequency/timezone/sources/status/...), the same
# shape ProfileStore used to keep only in localStorage. This is a single
# JSON file (not one-file-per-profile) since profiles are small and few;
# writes use a lock + atomic replace to avoid corrupting it under
# concurrent requests.
import threading as _threading
_profiles_lock = _threading.Lock()
_profiles: Dict[str, Dict] = {}


def _load_profiles_from_disk() -> None:
    global _profiles
    if not SCHEDULER_PROFILES_FILE.exists():
        _profiles = {}
        return
    try:
        with open(SCHEDULER_PROFILES_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        _profiles = data if isinstance(data, dict) else {}
        logger.info(f"Loaded {len(_profiles)} scheduler profile(s) from {SCHEDULER_PROFILES_FILE}")
    except Exception:
        logger.exception(f"Could not load scheduler profiles from {SCHEDULER_PROFILES_FILE}")
        _profiles = {}


def _save_profiles_to_disk() -> None:
    tmp_path = SCHEDULER_PROFILES_FILE.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(_profiles, fh, indent=2, default=str)
    tmp_path.replace(SCHEDULER_PROFILES_FILE)


_load_profiles_from_disk()

# ── Agent ──
_recent_results: List[Dict] = []          # last 200 pipeline results
_agent_ws_clients: List[WebSocket] = []   # /agent/ws/progress subscribers

# ─────────────────────────────────────────────
#  WebSocket broadcast helpers
# ─────────────────────────────────────────────
def _decrypt_file_info_credentials(file_info: dict) -> dict:
    """
    scheduler_app.py encrypts each source's credentials on save
    (encrypt_secret / encrypt_sa_file → "enc:v1:..." prefix). Those
    encrypted values travel through the scan JSON into file_info.
    resolve_secret() handles all three storage forms:
      enc:v1:...  → Fernet-decrypt with SCHED_MASTER_KEY
      $VAR_NAME   → os.environ lookup  (legacy)
      anything else → returned as-is   (plain literals like account name)

    Returns a shallow copy of file_info with only the credentials
    sub-dict decrypted, so sibling keys are unaffected and the caller's
    original dict is not mutated.
    """
    creds = file_info.get("credentials")
    if not (creds and isinstance(creds, dict)):
        return file_info
    decrypted_creds = {}
    for k, v in creds.items():
        if isinstance(v, str):
            try:
                decrypted_creds[k] = resolve_secret(v)
            except Exception as dec_err:
                logger.warning(
                    "Could not resolve credential field '%s': %s — "
                    "passing through as-is; extraction may fail.", k, dec_err
                )
                decrypted_creds[k] = v
        else:
            decrypted_creds[k] = v
    return {**file_info, "credentials": decrypted_creds}
 

async def _broadcast_sched(event: Dict) -> None:
    """Broadcast a scheduler event to all /ws/events clients."""
    dead = []
    for ws in _sched_ws_clients:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _sched_ws_clients.remove(ws)


async def _broadcast_agent(event: Dict) -> None:
    """Broadcast a pipeline event to all /agent/ws/progress clients."""
    dead = []
    for ws in _agent_ws_clients:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _agent_ws_clients.remove(ws)


def _emit_agent(event: Dict) -> None:
    """Sync fire-and-forget wrapper for agent broadcasts."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_broadcast_agent(event))
    except Exception:
        pass


# ─────────────────────────────────────────────
#  Scheduler scan-complete callback
# ─────────────────────────────────────────────

# Create once near the top of api_server.py
_pipeline_executor = ThreadPoolExecutor(max_workers=4)


def _enrich_file_with_extraction(file_dict: Dict, pipelines: List[Dict]) -> Dict:
    """Merge the extraction-stage output of a matching pipeline result
    (from _recent_results) into a new_files/modified_files entry, so the
    history payload actually carries extracted_content / content_preview /
    extraction_method / extraction_error instead of leaving them blank."""
    match = next(
        (p for p in pipelines if p.get("file_name") == file_dict.get("file_name")),
        None,
    )
    if not match:
        return file_dict

    extraction = (match.get("stages") or {}).get("extraction") or {}
    if extraction.get("status") == "done":
        file_dict["extraction_method"] = extraction.get("extraction_method")
        file_dict["content_preview"] = extraction.get("content_preview", "")
        file_dict["extracted_content"] = extraction.get("extracted_content", "")
    elif extraction.get("status") == "error":
        file_dict["extraction_error"] = extraction.get("error")

    return file_dict


def _store_scan_log(result: ScanResult) -> None:
    """Store scan result as a JSON log file in SCHEDULER_LOGS_DIR."""
    global _manual_trigger_pending
    try:
        # Create a short scan_id for the filename
        short_id = result.scan_id[:8]
        log_filename = f"scan_{short_id}_{result.started_at.strftime('%Y%m%d_%H%M%S')}.json"
        log_path = SCHEDULER_LOGS_DIR / log_filename

        # Consume the manual-trigger flag: whichever scan completes next
        # after POST /api/scheduler/trigger was called is the manual one.
        trigger_type = "manual" if _manual_trigger_pending else "auto"
        _manual_trigger_pending = False
        _scan_trigger_map[short_id] = trigger_type
        if len(_scan_trigger_map) > _HISTORY_ARCHIVE_MAX:
            for old_key in list(_scan_trigger_map.keys())[: len(_scan_trigger_map) - _HISTORY_ARCHIVE_MAX]:
                _scan_trigger_map.pop(old_key, None)
        
        # Get pipeline results for this scan
        pipelines = [p for p in _recent_results if p.get("scan_id") == result.scan_id]
        
        # Build new_files list safely
        new_files_list = []
        for f in result.new_files:
            try:
                new_files_list.append({
                    "file_name": f.file_name or (Path(f.path).name if f.path else "unknown"),
                    "file_type": Path(f.file_name or f.path).suffix if (f.file_name or f.path) else "",
                    "file_size_bytes": f.size_bytes or 0,
                    "path": f.path or "",
                    "source_id": f.source_id or "local",
                    "source_type": f.source_type or "local",
                    "last_modified": f.last_modified.isoformat() if f.last_modified else None,
                    "status": "new",
                    "discovered_at": f.discovered_at.isoformat() if f.discovered_at else None,
                })
            except Exception as e:
                logger.warning(f"Error processing new_file for log: {e}")
                continue
        new_files_list = [_enrich_file_with_extraction(f, pipelines) for f in new_files_list]

        # Build modified_files list safely
        modified_files_list = []
        for f in result.modified_files:
            try:
                modified_files_list.append({
                    "file_name": f.file_name or (Path(f.path).name if f.path else "unknown"),
                    "file_type": Path(f.file_name or f.path).suffix if (f.file_name or f.path) else "",
                    "file_size_bytes": f.size_bytes or 0,
                    "path": f.path or "",
                    "source_id": f.source_id or "local",
                    "source_type": f.source_type or "local",
                    "last_modified": f.last_modified.isoformat() if f.last_modified else None,
                    "status": "modified",
                    "discovered_at": f.discovered_at.isoformat() if f.discovered_at else None,
                })
            except Exception as e:
                logger.warning(f"Error processing modified_file for log: {e}")
                continue
        modified_files_list = [_enrich_file_with_extraction(f, pipelines) for f in modified_files_list]

        source_diagnostics = list(result.source_diagnostics)
        source_errors = [d.get("error") for d in source_diagnostics if d.get("error")]
        
        # Build the detailed scan log
        scan_log = {
            "scan_id": result.scan_id[:8],
            "scheduler_id": _active_scheduler_profile_id or _scheduler.scheduler_id[:8],
            "scheduler_name": getattr(_scheduler, 'scheduler_name', 'Scheduler'),
            "trigger": trigger_type,
            "started_at": result.started_at.isoformat(),
            "finished_at": result.finished_at.isoformat() if result.finished_at else None,
            "duration_seconds": result.duration_seconds,
            "sources_scanned": len(source_diagnostics),
            "source_diagnostics": source_diagnostics,
            "extraction_mode": "background",
            "extraction_pending": len(pipelines) > 0,
            "summary": {
                "total_new": len(result.new_files),
                "total_modified": len(result.modified_files),
                "total_files": result.total_files,
            },
            "new_files": new_files_list,
            "modified_files": modified_files_list,
            "errors": ([result.error] if result.error else []) + source_errors,
            "metadata": {
                "frequency": _scheduler.config.frequency.value if hasattr(_scheduler.config.frequency, 'value') else str(_scheduler.config.frequency),
                "timezone": _scheduler.config.timezone_name,
                "next_run": _scheduler.next_run_at.isoformat() if _scheduler.next_run_at else None,
            },
        }
        
        # Write the log file
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(scan_log, f, indent=2, default=str)
        
        logger.info(f"Scan log stored at {log_path}")

        _history_archive.append(scan_log)
        if len(_history_archive) > _HISTORY_ARCHIVE_MAX:
            del _history_archive[: len(_history_archive) - _HISTORY_ARCHIVE_MAX]
    except Exception as e:
        logger.exception(f"Failed to store scan log: {e}")


def _on_scan_done(result: ScanResult) -> None:
    """
    Called by the scheduler after every scan.

    1. Broadcast scan completion to the UI.
    2. Automatically run the ingestion pipeline for all new/modified files.
    """

    event = {
        "event": "scan_complete",
        "scan_id": result.scan_id,
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat() if result.finished_at else None,
        "new_files": len(result.new_files),
        "modified_files": len(result.modified_files),
        "error": result.error,
        "files": [
            f.to_dict()
            for f in (result.new_files + result.modified_files)
        ][:100],
    }

    #
    # Broadcast scheduler event
    #
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_broadcast_sched(event))
    except Exception:
        logger.exception("Failed to broadcast scheduler event")

    #
    # Store scan log
    #
    try:
        _store_scan_log(result)
    except Exception:
        logger.exception("Failed to store scan log")

    #
    # Run ingestion pipeline
    #
    files = result.new_files + result.modified_files

    if not files:
        logger.info("No new or modified files found.")
        return

    logger.info("Submitting %d pipeline(s)...", len(files))

    # Auto-ingest after a *scheduled* scan currently has no per-file caller to
    # pass sql_profile_id/vector_profile_id through (unlike /agent/ingest/file
    # and /agent/ingest/batch, which take them explicitly). Without this, a
    # scheduler configured to target a specific SQL database / vector store
    # (see NewScheduler.jsx "5 · Data destination") would silently fall back
    # to the SQL_CONNECTION_STRING / VECTOR_DB_PATH env-var defaults on every
    # scheduled run. _profiles is the same server-side store ProfileStore.js
    # syncs scheduler configs to, so look the active one up from there.
    active_profile = _profiles.get(_active_scheduler_profile_id) if _active_scheduler_profile_id else None
    auto_sql_profile_id    = (active_profile or {}).get("sql_profile_id")
    auto_vector_profile_id = (active_profile or {}).get("vector_profile_id")

    for file_record in files:
        try:
            file_info = _decrypt_file_info_credentials(
                file_record.to_dict()
            )
            
            # Ensure file_name is set — prefer the real name the source
            # discovered (e.g. Drive's actual "Report.xlsx"), falling back
            # to the basename of `path` only for sources (like local) that
            # don't populate a dedicated file_name.
            if not file_info.get("file_name") and file_info.get("path"):
                file_info["file_name"] = Path(file_info["path"]).name

            # Ensure file_type is set from the real file name, not from
            # `path` — for gdrive/azure/s3, path is an opaque identifier
            # (e.g. "gdrive://<folder_id>/<file_id>") with no extension,
            # so deriving file_type from it always produced an empty
            # string for those sources.
            if not file_info.get("file_type") and file_info.get("file_name"):
                file_info["file_type"] = Path(file_info["file_name"]).suffix

            # Attach the scan_id so downstream pipeline runs can be
            # associated with the originating scan (used by /api/history).
            try:
                file_info["_scan_id"] = result.scan_id
                if auto_sql_profile_id:
                    file_info["_sql_profile_id"] = auto_sql_profile_id
                if auto_vector_profile_id:
                    file_info["_vector_profile_id"] = auto_vector_profile_id
            except Exception:
                # defensive: file_info may be a plain mapping
                pass
            logger.info(
                "Submitting pipeline for %s",
                file_info.get("file_name") or file_info.get("path")
            )

            _pipeline_executor.submit(run_pipeline, file_info)

        except Exception:
            logger.exception(
                "Unable to submit pipeline for %s",
                getattr(file_record, "path", "<unknown>")
            )


_scheduler.on_scan_complete(_on_scan_done)


# ─────────────────────────────────────────────
#  Agent pipeline core
# ─────────────────────────────────────────────

def _store_result(result: Dict) -> None:
    _recent_results.insert(0, result)
    if len(_recent_results) > 200:
        _recent_results.pop()


def _patch_history_with_pipeline_result(pipeline_result: Dict) -> None:
    """A pipeline usually finishes *after* the scan log for its scan has
    already been written (extraction is submitted to a background thread
    pool once _store_scan_log has already run). Without this, the
    extraction_method / content_preview / extracted_content fields would
    never make it into _history_archive or the on-disk log once the scan
    ages out of the scheduler's in-memory scan_history. Patch both here so
    the History & Logs UI reflects the finished extraction."""
    scan_id = pipeline_result.get("scan_id")
    file_name = pipeline_result.get("file_name")
    if not scan_id or not file_name:
        return

    entry = next((r for r in _history_archive if r.get("scan_id") == scan_id[:8]), None)
    if not entry:
        return

    for f in (entry.get("new_files") or []) + (entry.get("modified_files") or []):
        if f.get("file_name") == file_name:
            _enrich_file_with_extraction(f, [pipeline_result])

    # Best-effort: also rewrite the on-disk log file for this scan, so the
    # patch survives a process restart instead of only living in memory.
    try:
        for logs_dir in _history_log_dirs():
            for log_path in logs_dir.glob(f"scan_{scan_id[:8]}_*.json"):
                with open(log_path, "w", encoding="utf-8") as fh:
                    json.dump(entry, fh, indent=2, default=str)
    except Exception:
        logger.exception("Failed to patch on-disk scan log with extraction result")


def run_pipeline(file_info: Dict) -> Dict[str, Any]:
    """
    Extract -> Classify -> Ingest
    """

    pipeline_id = str(uuid.uuid4())[:8]
    started_at = datetime.now(timezone.utc)

    # Prefer explicit `file_name`, but fall back to the basename of `path`.
    file_name = file_info.get("file_name") or Path(file_info.get("path", "")).name or "unknown"
    scan_id = file_info.get("_scan_id")
    sql_profile_id    = file_info.get("_sql_profile_id")
    vector_profile_id = file_info.get("_vector_profile_id")

    result = {
        "pipeline_id": pipeline_id,
        "file_name": file_name,
        "scan_id": scan_id,
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "stages": {
            "extraction": {"status": "pending"},
            "classification": {"status": "pending"},
            "ingestion": {"status": "pending"},
        },
        "error": None,
    }

    logger.info("[%s] Pipeline START %s", pipeline_id, file_name)

    _emit_agent({
        "event": "pipeline_start",
        "pipeline_id": pipeline_id,
        "file_name": file_name,
    })

    # Ensure all metadata is properly set before extraction
    if not file_info.get("file_name"):
        file_info["file_name"] = file_name
    if not file_info.get("file_type") and file_info.get("file_name"):
        file_info["file_type"] = Path(file_info["file_name"]).suffix
    if not file_info.get("source_type"):
        file_info["source_type"] = "local"

    # --------------------------------------------------
    # Stage 1 - Extraction
    # --------------------------------------------------

    try:

        _emit_agent({
            "event": "stage",
            "pipeline_id": pipeline_id,
            "stage": "extraction",
            "status": "running",
        })

        ext_result = _extract_content(file_info)

        if ext_result.get("error"):
            raise RuntimeError(ext_result["error"])

        metadata = ext_result.get("metadata", {})
        content = ext_result.get("content", "")

        # Ensure metadata includes basic file identifiers so downstream
        # components (classifier, vector store) can rely on them even
        # if the extractor returned an empty metadata dict.
        if not isinstance(metadata, dict):
            metadata = {}
        if not metadata.get("file_name"):
            metadata["file_name"] = file_info.get("file_name") or Path(file_info.get("path", "")).name or ""
        if not metadata.get("file_type"):
            # file_type may be stored as a suffix (e.g. 'xlsx') — prefer
            # explicit metadata, otherwise derive from path suffix.
            ft = file_info.get("file_type")
            if not ft:
                p = file_info.get("path", "")
                ft = Path(p).suffix.lstrip(".") if p else ""
            metadata["file_type"] = ft or ""

        result["stages"]["extraction"] = {
            "status": "done",
            "extraction_method": ext_result.get("extraction_method"),
            "content_length": len(content),
            "content_preview": ext_result.get("content_preview", ""),
            "extracted_content": content,
            "metadata": metadata,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:

        logger.exception("Extraction failed")

        result["stages"]["extraction"] = {
            "status": "error",
            "error": str(e),
        }

        result["error"] = str(e)
        result["finished_at"] = datetime.now(timezone.utc).isoformat()

        _store_result(result)
        _patch_history_with_pipeline_result(result)

        return result

    # --------------------------------------------------
    # Stage 2 - Classification
    # --------------------------------------------------

    try:

        _emit_agent({
            "event": "stage",
            "pipeline_id": pipeline_id,
            "stage": "classification",
            "status": "running",
        })

        cls_result = classify(metadata, content)

        result["stages"]["classification"] = {
            "status": "done",
            "classification": cls_result["classification"],
            "confidence": cls_result["confidence"],
            "data_type": cls_result["data_type"],
            "summary": cls_result["summary"],
            "schema_hint": cls_result.get("schema_hint", []),
            "chunk_strategy": cls_result.get("chunk_strategy"),
            "error": cls_result.get("error"),
        }

    except Exception as e:

        logger.exception("Classification failed")

        result["stages"]["classification"] = {
            "status": "error",
            "error": str(e),
        }

        result["error"] = str(e)
        result["finished_at"] = datetime.now(timezone.utc).isoformat()

        _store_result(result)
        _patch_history_with_pipeline_result(result)

        return result

    # --------------------------------------------------
    # Stage 3 - Ingestion
    # --------------------------------------------------

    try:

        _emit_agent({
            "event": "stage",
            "pipeline_id": pipeline_id,
            "stage": "ingestion",
            "status": "running",
        })

        if cls_result["classification"] == "structured":

            ing_result = ingest_structured(
                metadata,
                content,
                cls_result,
                sql_profile_id=sql_profile_id,
            )

            result["stages"]["ingestion"] = {
                "status": "done" if ing_result["success"] else "error",
                "target": "sql",
                "table": ing_result.get("table"),
                "mode": ing_result.get("mode"),
                "error": ing_result.get("error"),
            }

        else:
            try:
                ing_result = ingest_unstructured(
                    metadata,
                    content,
                    cls_result,
                    vector_profile_id=vector_profile_id,
                    sql_profile_id=sql_profile_id,
                )
            except Exception as e:
                logger.exception("ingest_unstructured raised an exception")
                ing_result = {"success": False, "error": str(e), "chunks_stored": 0}

            if not isinstance(ing_result, dict):
                ing_result = {"success": False, "error": "ingest_unstructured did not return a result", "chunks_stored": 0}

            result["stages"]["ingestion"] = {
                "status": "done" if ing_result.get("success") else "error",
                "target": "lancedb",
                "chunks_stored": ing_result.get("chunks_stored", 0),
                "collection": ing_result.get("collection"),
                "error": ing_result.get("error"),
            }

    except Exception as e:

        logger.exception("Ingestion failed")

        result["stages"]["ingestion"] = {
            "status": "error",
            "error": str(e),
        }

        result["error"] = str(e)

    # --------------------------------------------------
    # Stage 4 - hand the file to the CFO agents (optional, never breaks ingestion)
    # --------------------------------------------------

    if _cfo_bridge.ENABLED and not result.get("error"):
        try:
            _emit_agent({"event": "stage", "pipeline_id": pipeline_id, "stage": "cfo_agents", "status": "running"})
            result["stages"]["cfo_agents"] = {**_cfo_bridge.handle_file(file_info), "status": "done"}
        except Exception as e:
            logger.exception("CFO bridge failed for %s", file_name)
            result["stages"]["cfo_agents"] = {"status": "error", "error": str(e)}

    result["finished_at"] = datetime.now(timezone.utc).isoformat()

    _emit_agent({
        "event": "pipeline_done",
        "pipeline_id": pipeline_id,
        "result": result,
    })

    _store_result(result)
    _patch_history_with_pipeline_result(result)

    return result

# ═════════════════════════════════════════════
#  PYDANTIC MODELS
# ═════════════════════════════════════════════

# ── Scheduler ─────────────────────────────────────────────────
class ScheduleConfigIn(BaseModel):
    frequency:            str  = "daily"
    daily_hour:           int  = 9
    daily_minute:         int  = 0
    weekly_day:           int  = 0
    weekly_hour:          int  = 9
    weekly_minute:        int  = 0
    monthly_day_of_month: int  = 1
    monthly_hour:         int  = 9
    monthly_minute:       int  = 0
    hourly_minute:        int  = 0
    minutely_interval:    int  = 5
    cron_expression:      str  = "0 9 * * 1"
    timezone_name:        str  = "UTC"
    enabled:              bool = True
    # Frontend ProfileStore id for the scheduler being configured. Stored
    # so scan results can be reported back under the same id the UI uses
    # (see _active_scheduler_profile_id). Optional/backwards-compatible.
    scheduler_id:         Optional[str] = None


class SourceIn(BaseModel):
    source_id:   str
    source_type: str            # local | gcp | gdrive | azure | s3
    config:      Dict[str, Any]


class StatusOut(BaseModel):
    scheduler_id: str
    status:       str
    last_scan_at: Optional[str]
    next_run_at:  Optional[str]
    sources:      List[str]
    config:       Dict[str, Any]


# ── Agent ───────────────────────────────────────────────────────

class FileInfoIn(BaseModel):
    file_name:       str
    file_type:       Optional[str] = ""
    file_size_bytes: Optional[int] = 0
    path:            Optional[str] = ""
    source_id:       Optional[str] = ""
    source_type:     Optional[str] = "local"
    last_modified:   Optional[str] = ""
    status:          Optional[str] = "new"
    # pre-extracted content (set by Streamlit scanner to skip re-download)
    extracted_content:  Optional[str] = None
    content_preview:    Optional[str] = None
    extraction_method:  Optional[str] = None
    extraction_error:   Optional[str] = None
    # cloud-specific (optional)
    container:   Optional[str] = None
    blob_name:   Optional[str] = None
    bucket:      Optional[str] = None
    s3_key:      Optional[str] = None
    folder_id:   Optional[str] = None   # gdrive
    file_id:     Optional[str] = None   # gdrive
    credentials: Optional[Dict[str, Any]] = None   # per-source auth (all cloud types)


class BatchIn(BaseModel):
    files:    List[FileInfoIn]
    scan_log: Optional[str] = None   # path to scan log JSON (informational)
    sql_profile_id:    Optional[str] = None   # which registered SQL database to ingest into
    vector_profile_id: Optional[str] = None   # which registered vector store to ingest into


class SearchIn(BaseModel):
    query: str
    top_k: int = 5
    vector_profile_id: Optional[str] = None   # which registered vector store to search


# ── Data Connections (SQL / vector store profiles) ───────────────

class SqlProfileIn(BaseModel):
    name: str
    connection_string: str
    db_type: str = "mssql"


class VectorProfileIn(BaseModel):
    name: str
    db_path: str
    collection_name: str = "file_ingestion"


class TestSqlIn(BaseModel):
    connection_string: str


# ═════════════════════════════════════════════
#  SCHEDULER ROUTES   /api/*
# ═════════════════════════════════════════════
@app.get("/api/status", tags=["Scheduler"])
async def get_scheduler_status():
    try:
        raw_config = _scheduler.config.to_dict()

        # ── Unwrap any enum values that slipped through to_dict() ──────────
        def _unwrap(v):
            return v.value if hasattr(v, "value") else v

        safe_config = {k: _unwrap(v) for k, v in raw_config.items()}

        return {
            "scheduler_id": _scheduler.scheduler_id,
            "active_scheduler_profile_id": _active_scheduler_profile_id,
            "status":       _scheduler.status.value,
            "last_scan_at": _scheduler.last_scan_at.isoformat() if _scheduler.last_scan_at else None,
            "next_run_at":  _scheduler.next_run_at.isoformat()  if _scheduler.next_run_at  else None,
            "sources":      [s.source_id for s in _scheduler._sources],
            "config":       safe_config,   # <── was raw_config
        }
    except Exception as e:
        import traceback
        logger.exception("Status endpoint failed")
        return {"error": str(e), "traceback": traceback.format_exc()}

@app.post("/api/scheduler/start", tags=["Scheduler"])
async def start_scheduler():
    # Guard against duplicate Start clicks / re-fired requests spawning a
    # second concurrent scan loop (symptom: two scans firing seconds
    # apart with a negative "next scan in" on the first one).
    try:
        if _scheduler.status is not None and _scheduler.status.value == "running":
            logger.info("Start requested but scheduler is already running — ignoring.")
            return {"status": "already_running"}
    except Exception:
        pass
    _scheduler.start()
    return {"status": "started"}


@app.post("/api/scheduler/stop", tags=["Scheduler"])
async def stop_scheduler():
    _scheduler.stop()
    return {"status": "stopped"}


@app.post("/api/scheduler/pause", tags=["Scheduler"])
async def pause_scheduler():
    _scheduler.pause()
    return {"status": "paused"}


@app.post("/api/scheduler/resume", tags=["Scheduler"])
async def resume_scheduler():
    _scheduler.resume()
    return {"status": "resumed"}


@app.post("/api/scheduler/trigger")
async def trigger_now():
    global _manual_trigger_pending
    _manual_trigger_pending = True

    loop = asyncio.get_running_loop()

    await loop.run_in_executor(
        None,
        _scheduler.trigger_now,
    )

    return {
        "status": "completed"
    }


@app.put("/api/config", tags=["Scheduler"])
async def update_config(body: ScheduleConfigIn):
    global _active_scheduler_profile_id
    if body.scheduler_id:
        _active_scheduler_profile_id = body.scheduler_id

    # body.frequency is always a plain str (Pydantic model), so
    # ScheduleFrequency(body.frequency) is safe — but guard it anyway.
    try:
        freq = ScheduleFrequency(body.frequency)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid frequency '{body.frequency}'. "
                   f"Must be one of: {[e.value for e in ScheduleFrequency]}"
        )
    try:
        weekday = Weekday(body.weekly_day)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid weekly_day '{body.weekly_day}'. Must be 0–6."
        )

    config = ScheduleConfig(
        frequency            = freq,
        daily_hour           = body.daily_hour,
        daily_minute         = body.daily_minute,
        weekly_day           = weekday,
        weekly_hour          = body.weekly_hour,
        weekly_minute        = body.weekly_minute,
        monthly_day_of_month = body.monthly_day_of_month,
        monthly_hour         = body.monthly_hour,
        monthly_minute       = body.monthly_minute,
        hourly_minute        = body.hourly_minute,
        minutely_interval    = body.minutely_interval,
        cron_expression      = body.cron_expression,
        timezone_name        = body.timezone_name,
        enabled              = body.enabled,
    )
    _scheduler.update_config(config)
    return {"status": "updated", "config": {k: (v.value if hasattr(v, "value") else v)
                                             for k, v in config.to_dict().items()}}


@app.post("/api/sources", tags=["Scheduler"])
async def add_source(body: SourceIn):
    try:
        source = build_source(body.source_type, body.source_id, body.config)
        _scheduler.add_source(source)
        return {"status": "added", "source_id": body.source_id}
    except (ValueError, ImportError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/sources/{source_id}", tags=["Scheduler"])
async def remove_source(source_id: str):
    _scheduler.remove_source(source_id)
    return {"status": "removed", "source_id": source_id}


# ── Scheduler profiles (durable) ───────────────────────────────────────
# Mirrors what api.js's ProfileStore used to keep only in localStorage.
# The frontend still keeps a localStorage cache for instant reads, but
# this is now the source of truth: it survives cleared browser storage,
# private windows, and different browsers/devices.

@app.get("/api/profiles", tags=["Scheduler"])
async def list_profiles():
    """All saved scheduler profiles, keyed by id."""
    with _profiles_lock:
        return dict(_profiles)


@app.put("/api/profiles/{profile_id}", tags=["Scheduler"])
async def upsert_profile(profile_id: str, body: Dict[str, Any]):
    """Create or replace a scheduler profile. Body is stored as-is (plus id/updated_at)."""
    profile = dict(body)
    profile["id"] = profile_id
    profile["updated_at"] = datetime.now(timezone.utc).isoformat()
    with _profiles_lock:
        _profiles[profile_id] = profile
        try:
            _save_profiles_to_disk()
        except Exception:
            logger.exception(f"Failed to persist scheduler profile {profile_id} to disk")
            raise HTTPException(status_code=500, detail="Failed to persist profile to disk")
    return profile


@app.delete("/api/profiles/{profile_id}", tags=["Scheduler"])
async def delete_profile(profile_id: str):
    with _profiles_lock:
        removed = _profiles.pop(profile_id, None) is not None
        try:
            _save_profiles_to_disk()
        except Exception:
            logger.exception(f"Failed to persist removal of scheduler profile {profile_id}")
            raise HTTPException(status_code=500, detail="Failed to persist profile deletion to disk")
    return {"status": "removed" if removed else "not_found", "id": profile_id}


@app.get("/api/history", tags=["Scheduler"])
async def get_history():
    """Last 50 scan results recorded by the scheduler engine with full details."""
    try:
        out = []
        for r in reversed(_scheduler.scan_history):
            try:
                # correlate recent pipelines submitted during this scan (if any)
                pipelines = [p for p in _recent_results if p.get("scan_id") == r.scan_id]
                
                # Build new_files list safely
                new_files_list = []
                for f in r.new_files:
                    try:
                        new_files_list.append({
                            "file_name": f.file_name or (Path(f.path).name if f.path else "unknown"),
                            "file_type": Path(f.file_name or f.path).suffix if (f.file_name or f.path) else "",
                            "file_size_bytes": f.size_bytes or 0,
                            "path": f.path or "",
                            "source_id": f.source_id or "local",
                            "source_type": f.source_type or "local",
                            "last_modified": f.last_modified.isoformat() if f.last_modified else None,
                            "status": "new",
                            "discovered_at": f.discovered_at.isoformat() if f.discovered_at else None,
                        })
                    except Exception as e:
                        logger.warning(f"Error processing new_file: {e}")
                        continue
                new_files_list = [_enrich_file_with_extraction(f, pipelines) for f in new_files_list]

                # Build modified_files list safely
                modified_files_list = []
                for f in r.modified_files:
                    try:
                        modified_files_list.append({
                            "file_name": f.file_name or (Path(f.path).name if f.path else "unknown"),
                            "file_type": Path(f.file_name or f.path).suffix if (f.file_name or f.path) else "",
                            "file_size_bytes": f.size_bytes or 0,
                            "path": f.path or "",
                            "source_id": f.source_id or "local",
                            "source_type": f.source_type or "local",
                            "last_modified": f.last_modified.isoformat() if f.last_modified else None,
                            "status": "modified",
                            "discovered_at": f.discovered_at.isoformat() if f.discovered_at else None,
                        })
                    except Exception as e:
                        logger.warning(f"Error processing modified_file: {e}")
                        continue
                modified_files_list = [_enrich_file_with_extraction(f, pipelines) for f in modified_files_list]

                # Get sources count safely
                try:
                    all_files = r.new_files + r.modified_files
                    sources_scanned = len(set(f.source_type for f in all_files)) if all_files else 0
                except:
                    sources_scanned = 1
                
                scan_item = {
                    "scan_id": r.scan_id[:8],
                    "scheduler_id": _active_scheduler_profile_id or _scheduler.scheduler_id[:8],
                    "scheduler_name": getattr(_scheduler, 'scheduler_name', 'Scheduler'),
                    "trigger": _scan_trigger_map.get(r.scan_id[:8], "auto"),
                    "started_at": r.started_at.isoformat(),
                    "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                    "duration_seconds": r.duration_seconds,
                    "sources_scanned": sources_scanned,
                    "source_diagnostics": list(getattr(r, "source_diagnostics", [])),
                    "extraction_mode": "background",
                    "extraction_pending": len(pipelines) > 0,
                    "summary": {
                        "total_new": len(r.new_files),
                        "total_modified": len(r.modified_files),
                        "total_files": r.total_files,
                    },
                    "new_files": new_files_list,
                    "modified_files": modified_files_list,
                    "errors": ([r.error] if r.error else []) + [
                        d.get("error") for d in getattr(r, "source_diagnostics", []) if d.get("error")
                    ],
                    "metadata": {
                        "frequency": _scheduler.config.frequency.value if hasattr(_scheduler.config.frequency, 'value') else str(_scheduler.config.frequency),
                        "timezone": _scheduler.config.timezone_name,
                        "next_run": _scheduler.next_run_at.isoformat() if _scheduler.next_run_at else None,
                    },
                }
                out.append(scan_item)
            except Exception as e:
                logger.exception(f"Error processing scan result: {e}")
                continue

        # Merge in anything from the durable archive that isn't already
        # covered by the live in-memory scan_history above (this is what
        # brings back "today's" — or any earlier day's — scans after a
        # process restart, since scan_history itself resets to empty).
        seen_ids = {item["scan_id"] for item in out}
        for r in reversed(_history_archive):
            sid = r.get("scan_id")
            if sid and sid not in seen_ids:
                out.append(r)
                seen_ids.add(sid)

        return out[:50]
    except Exception as e:
        logger.exception("History endpoint error")
        return []


@app.delete("/api/history/{scan_id}", tags=["Scheduler"])
async def delete_history_entry(scan_id: str):
    """Permanently delete a single scan record (live + archived + on-disk log)."""
    global _history_archive
    removed_live = False
    removed_archive = False

    # Remove from the live in-memory engine history.
    # Mutate the list IN PLACE (not `_scheduler.scan_history = [...]`) —
    # if scan_history is exposed as a property without a setter,
    # reassignment silently raises inside the except below and the old
    # entries would keep reappearing on every /api/history poll.
    try:
        removed_live = _scheduler.remove_scan_history(scan_id)
    except Exception:
        logger.exception("Could not filter live scan_history")

    # Remove from the durable archive.
    before = len(_history_archive)
    _history_archive = [r for r in _history_archive if r.get("scan_id") != scan_id]
    removed_archive = len(_history_archive) != before

    # Remove the on-disk log file(s) for this scan. Tracked SEPARATELY
    # from removed_live/removed_archive — a glob that matches nothing
    # (wrong directory, filename drift, etc.) must NOT be masked by the
    # in-memory removal succeeding, or the file is silently orphaned on
    # disk forever while the UI reports success.
    pattern = scan_id  # retained only for the diagnostic message below
    matched_files = _matching_log_files(scan_id)
    files_deleted = []
    files_failed = []
    for f in matched_files:
        try:
            f.unlink()
            files_deleted.append(str(f))
        except Exception:
            logger.exception(f"Could not delete log file {f}")
            files_failed.append(str(f))

    if not matched_files:
        logger.warning(
            f"delete_history_entry: no on-disk file matched pattern "
            f"'{pattern}' in {SCHEDULER_LOGS_DIR.resolve()} — nothing to "
            f"unlink. If a file for this scan exists elsewhere, "
            f"SCHEDULER_LOGS_DIR may not match where it was written."
        )

    if not (removed_live or removed_archive or files_deleted):
        raise HTTPException(status_code=404, detail=f"scan_id '{scan_id}' not found")

    return {
        "status": "partial" if files_failed else "deleted",
        "scan_id": scan_id,
        "removed_from_live_memory": removed_live,
        "removed_from_archive": removed_archive,
        "files_deleted": files_deleted,
        "files_failed_to_delete": files_failed,
        "log_dirs_searched": [str(p) for p in _history_log_dirs()],
    }


@app.delete("/api/history", tags=["Scheduler"])
async def clear_history():
    """Permanently delete all scan records (live + archived + on-disk logs)."""
    global _history_archive
    try:
        live_removed = _scheduler.clear_scan_history()
    except Exception:
        logger.exception("Could not clear live scan_history")

    live_remaining = 0
    try:
        live_remaining = len(_scheduler.scan_history)
    except Exception:
        pass
    if live_remaining:
        logger.warning(
            f"clear_history: {live_remaining} entr(y/ies) still present in "
            f"_scheduler.scan_history after clear — it may not support "
            f"in-place mutation; check scheduler_engine.py's scan_history property."
        )
    _history_archive = []

    matched_files = _matching_log_files()
    files_deleted = []
    files_failed = []
    for f in matched_files:
        try:
            f.unlink()
            files_deleted.append(str(f))
        except Exception:
            logger.exception(f"Could not delete log file {f}")
            files_failed.append(str(f))

    if not matched_files:
        logger.warning(
            f"clear_history: no scan_*.json files found in "
            f"{SCHEDULER_LOGS_DIR.resolve()} to delete."
        )

    return {
        "status": "partial" if files_failed or live_remaining else "cleared",
        "live_entries_removed": live_removed,
        "live_entries_remaining": live_remaining,
        "files_deleted_count": len(files_deleted),
        "files_failed_to_delete": files_failed,
        "log_dirs_searched": [str(p) for p in _history_log_dirs()],
    }


@app.get("/api/timezones", tags=["Scheduler"])
async def list_timezones():
    try:
        import pytz
        return {"timezones": list(pytz.all_timezones)}
    except ImportError:
        return {"timezones": ["UTC"]}


@app.websocket("/ws/events")
async def ws_scheduler_events(websocket: WebSocket):
    """Real-time scheduler scan events (JSON)."""
    await websocket.accept()
    _sched_ws_clients.append(websocket)
    logger.info("Scheduler WS connected. Total: %d", len(_sched_ws_clients))
    try:
        while True:
            await websocket.receive_text()   # keep-alive
    except WebSocketDisconnect:
        if websocket in _sched_ws_clients:
            _sched_ws_clients.remove(websocket)
        logger.info("Scheduler WS disconnected. Total: %d", len(_sched_ws_clients))


# ═════════════════════════════════════════════
#  AGENT ROUTES   /agent/*
# ═════════════════════════════════════════════

@app.get("/agent/status", tags=["Agent"])
async def agent_status():
    """Agent health check + vector store stats."""
    vs = vdb_stats()
    return {
        "status":           "ok",
        "api_port":         API_PORT,
        "log_file":         str(LOG_FILE),
        "vector_store":     vs,
        "recent_pipelines": len(_recent_results),
    }


@app.post("/agent/ingest/file", tags=["Agent"])
async def ingest_file(body: FileInfoIn, sql_profile_id: Optional[str] = None, vector_profile_id: Optional[str] = None):
    """Run the full Extract → Classify → Ingest pipeline for a single file.
    Pass sql_profile_id / vector_profile_id (as query params) to target a
    specific database/vector store registered on the Data Connections screen."""
    file_info = body.model_dump(exclude_none=True)
    if sql_profile_id:
        file_info["_sql_profile_id"] = sql_profile_id
    if vector_profile_id:
        file_info["_vector_profile_id"] = vector_profile_id
    loop      = asyncio.get_event_loop()
    result    = await loop.run_in_executor(None, run_pipeline, file_info)
    return result


@app.post("/agent/ingest/batch", tags=["Agent"])
async def ingest_batch(body: BatchIn):
    """
    Process a batch of files concurrently (bounded by AGENT_BATCH_CONCURRENCY).
    Files with pre-extracted content (set by the Streamlit scanner) skip the
    download step entirely, so batches are fast even for remote sources.

    body.sql_profile_id / body.vector_profile_id select which registered
    database/vector store (see Data Connections in the UI) every file in
    this batch is ingested into. Falls back to the default env-configured
    database/store when omitted, exactly as before.
    """
    total     = len(body.files)
    results: List[Optional[Dict]] = [None] * total
    succeeded = 0
    failed    = 0
    processed = 0

    semaphore = asyncio.Semaphore(AGENT_BATCH_CONC)
    loop      = asyncio.get_event_loop()

    async def _run_one(idx: int, file_item: FileInfoIn):
        nonlocal succeeded, failed, processed
        file_info = file_item.model_dump(exclude_none=True)
        if body.sql_profile_id:
            file_info["_sql_profile_id"] = body.sql_profile_id
        if body.vector_profile_id:
            file_info["_vector_profile_id"] = body.vector_profile_id
        async with semaphore:
            res = await loop.run_in_executor(None, run_pipeline, file_info)
        results[idx] = res
        processed   += 1
        if res.get("error"):
            failed    += 1
        else:
            succeeded += 1
        _emit_agent({
            "event":     "batch_progress",
            "processed": processed,
            "total":     total,
            "succeeded": succeeded,
            "failed":    failed,
        })

    await asyncio.gather(*(_run_one(i, f) for i, f in enumerate(body.files)))

    return {
        "total":     total,
        "succeeded": succeeded,
        "failed":    failed,
        "results":   results,
    }


@app.get("/agent/ingested/structured", tags=["Agent"])
async def get_structured(limit: int = 50, sql_profile_id: Optional[str] = None):
    """Recent structured-file records from SQL (dbo.FileIngestion).
    Pass sql_profile_id to read from a specific registered database."""
    loop = asyncio.get_event_loop()
    # Run in an executor: this does a blocking DB call and must never
    # block the shared asyncio event loop, which would stall every other
    # in-flight request on this server (that's what the old ezcoworker
    # chat-based implementation did, causing unrelated /agent/* calls to
    # read-time-out at the client).
    records = await loop.run_in_executor(None, lambda: get_ingested_records(limit, sql_profile_id=sql_profile_id))
    return {"records": records, "count": len(records)}


@app.get("/agent/ingested/vector/stats", tags=["Agent"])
async def get_vector_stats(vector_profile_id: Optional[str] = None, sql_profile_id: Optional[str] = None):
    """LanceDB collection stats. Pass vector_profile_id / sql_profile_id to
    report stats for a specific registered store instead of the default."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: vdb_stats(vector_profile_id=vector_profile_id, sql_connection_string=None, sql_profile_id=sql_profile_id)
    )


@app.delete("/agent/results", tags=["Agent"])
async def clear_recent_results():
    """Clear the in-memory 'Scan-driven ingestion status' / pipeline
    results log shown in the Ingestion Agent UI. This does NOT touch the
    actual ingested data in SQL or LanceDB — it only clears the log of
    pipeline runs held in memory since the last api_server.py restart."""
    global _recent_results
    count = len(_recent_results)
    _recent_results = []
    return {"success": True, "cleared_count": count}


@app.delete("/agent/ingested/structured", tags=["Agent"])
async def delete_structured(
    ids: Optional[str] = None,
    all: bool = False,
    sql_profile_id: Optional[str] = None,
):
    """Delete rows from dbo.FileIngestion. Pass ids as a comma-separated
    list of row ids (e.g. ?ids=1,2,3), or all=true to wipe the table for
    the given (or default) SQL connection. Irreversible."""
    id_list = [int(x) for x in ids.split(",") if x.strip()] if ids else None
    if not all and not id_list:
        raise HTTPException(status_code=400, detail="Pass ids=1,2,3 or all=true.")
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: delete_ingested_records(ids=id_list, delete_all=all, sql_profile_id=sql_profile_id),
    )
    if not result["success"]:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@app.delete("/agent/ingested/vector", tags=["Agent"])
async def delete_vector_data(
    all: bool = False,
    vector_profile_id: Optional[str] = None,
    sql_profile_id: Optional[str] = None,
):
    """Delete all chunks from the LanceDB collection (drops and recreates
    the table, so the schema is preserved but every row is gone).
    Irreversible. Only all=true is supported for now — per-row vector
    deletion isn't wired up in the UI."""
    if not all:
        raise HTTPException(status_code=400, detail="Pass all=true to confirm — this deletes every vector chunk.")

    def _do_delete():
        import lancedb
        store = db_connections.resolve_vector_store(vector_profile_id)
        db = lancedb.connect(store["db_path"])
        table_name = store["collection_name"]
        if table_name in db.table_names():
            db.drop_table(table_name)
            return {"success": True, "error": None}
        return {"success": True, "error": None, "note": "table did not exist"}

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _do_delete)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return result


@app.post("/agent/vector/search", tags=["Agent"])
async def vector_search(body: SearchIn):
    """Semantic search over the LanceDB vector store. Pass
    body.vector_profile_id to search a specific registered store."""
    loop    = asyncio.get_event_loop()
    results = await loop.run_in_executor(
        None, lambda: vdb_search(body.query, body.top_k, vector_profile_id=body.vector_profile_id)
    )
    return {"query": body.query, "results": results}


@app.get("/agent/results", tags=["Agent"])
async def get_recent_results(limit: int = 50):
    """Recent pipeline run results (in-memory, last 200)."""
    return {"results": _recent_results[:limit], "total": len(_recent_results)}


@app.get("/agent/logs", tags=["Agent"])
async def get_logs(lines: int = 200):
    """Last N lines of the unified agent log file."""
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        return {
            "log_file":    str(LOG_FILE),
            "lines":       all_lines[-lines:],
            "total_lines": len(all_lines),
        }
    except Exception as e:
        return {"error": str(e), "lines": []}


@app.websocket("/agent/ws/progress")
async def ws_agent_progress(websocket: WebSocket):
    """Real-time pipeline events (JSON)."""
    await websocket.accept()
    _agent_ws_clients.append(websocket)
    logger.info("Agent WS connected — total: %d", len(_agent_ws_clients))
    try:
        while True:
            await websocket.receive_text()   # keep-alive
    except WebSocketDisconnect:
        if websocket in _agent_ws_clients:
            _agent_ws_clients.remove(websocket)
        logger.info("Agent WS disconnected — total: %d", len(_agent_ws_clients))


# ═════════════════════════════════════════════
# Data Connections — SQL database profiles
# ═════════════════════════════════════════════

def _extract_server_and_user(conn_str: str) -> Dict[str, Optional[str]]:
    """Pulls the host, database, and username out of a connection string,
    for a short "server / database / user" summary instead of the full
    masked string. Handles three shapes:
      1. semicolon form:            Server=...;Database=...;UID=...;PWD=...
      2. plain SQLAlchemy URL:      scheme://user:pass@host/db
      3. odbc_connect URL:          mssql+pyodbc:///?odbc_connect=DRIVER=...;SERVER=...;DATABASE=...;UID=...
         (the netloc here is empty — host/db/user live inside the query
         param's own semicolon-form string, not the URL itself)
    """
    def _from_semicolon(s: str) -> Dict[str, Optional[str]]:
        server = re.search(r"(?:Server)=([^;]*)", s, re.IGNORECASE)
        database = re.search(r"(?:Database|Initial\s*Catalog)=([^;]*)", s, re.IGNORECASE)
        user = re.search(r"(?:UID|User\s*Id)=([^;]*)", s, re.IGNORECASE)
        return {
            "server": server.group(1).strip().split(",")[0] if server else None,  # drop ",port"
            "database": database.group(1).strip() if database else None,
            "username": user.group(1).strip() if user else None,
        }

    if "://" in conn_str:
        from urllib.parse import urlparse, unquote, parse_qs
        try:
            parsed = urlparse(conn_str)
            if parsed.hostname or parsed.username:
                return {
                    "server": parsed.hostname,
                    "database": parsed.path.lstrip("/") or None,
                    "username": unquote(parsed.username) if parsed.username else None,
                }
            # Empty netloc — look for the real connection details inside
            # an odbc_connect query param (e.g. mssql+pyodbc:///?odbc_connect=...)
            qs = parse_qs(parsed.query)
            odbc_connect = (qs.get("odbc_connect") or [None])[0]
            if odbc_connect:
                return _from_semicolon(unquote(odbc_connect))
            return {"server": None, "database": None, "username": None}
        except Exception:
            return {"server": None, "database": None, "username": None}
    return _from_semicolon(conn_str)


@app.get("/api/connections/defaults", tags=["Connections"])
async def get_connection_defaults():
    """
    Resolves what "(default)" actually points to for SQL/vector, so the
    UI can show e.g. "Default — ezsql.ezdatamunch.com / Sales_1 / EzInsightsAiUat"
    instead of a generic "(default — SQL_CONNECTION_STRING env var)"
    placeholder. Only server + database + username are surfaced — the
    password is never returned, masked or otherwise.
    """
    loop = asyncio.get_event_loop()

    def _run():
        raw_sql = os.environ.get("SQL_CONNECTION_STRING", "")
        parsed = _extract_server_and_user(raw_sql) if raw_sql else {"server": None, "database": None, "username": None}
        vec = db_connections.resolve_vector_store(None)
        return {
            "sql_configured": bool(raw_sql),
            "sql_server": parsed["server"],
            "sql_database": parsed["database"],
            "sql_username": parsed["username"],
            "vector_db_path": vec.get("db_path"),
            "vector_collection_name": vec.get("collection_name"),
        }

    return await loop.run_in_executor(None, _run)


@app.get("/api/connections/sql", tags=["Connections"])
async def list_sql_connections():
    """List registered SQL profiles (name/id/db_type/masked conn string — never the raw secret)."""
    loop = asyncio.get_event_loop()
    return {"profiles": await loop.run_in_executor(None, db_connections.list_sql_profiles)}


@app.post("/api/connections/sql", tags=["Connections"])
async def add_sql_connection(body: SqlProfileIn):
    """Register (or overwrite, if you reuse an id) a SQL database profile.
    The connection string is encrypted at rest with SCHED_MASTER_KEY."""
    if not body.name.strip() or not body.connection_string.strip():
        raise HTTPException(status_code=400, detail="name and connection_string are both required.")
    loop = asyncio.get_event_loop()
    try:
        pid = await loop.run_in_executor(
            None,
            lambda: db_connections.save_sql_profile(body.name.strip(), body.connection_string.strip(), db_type=body.db_type),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"id": pid, "name": body.name.strip()}


@app.post("/api/connections/sql/{profile_id}/test", tags=["Connections"])
async def test_sql_connection_endpoint(profile_id: str):
    """Run a lightweight SELECT 1 against a registered SQL profile."""
    loop = asyncio.get_event_loop()

    def _run():
        full = db_connections.get_sql_profile(profile_id)
        if not full:
            return {"ok": False, "error": "profile not found"}
        return db_connections.test_sql_connection(full.get("connection_string", ""))

    return await loop.run_in_executor(None, _run)


@app.delete("/api/connections/sql/{profile_id}", tags=["Connections"])
async def delete_sql_connection(profile_id: str):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: db_connections.delete_sql_profile(profile_id))
    return {"success": True}


# ═════════════════════════════════════════════
# Data Connections — vector store (LanceDB) profiles
# ═════════════════════════════════════════════

@app.get("/api/connections/vector", tags=["Connections"])
async def list_vector_connections():
    loop = asyncio.get_event_loop()
    return {"profiles": await loop.run_in_executor(None, db_connections.list_vector_profiles)}


@app.post("/api/connections/vector", tags=["Connections"])
async def add_vector_connection(body: VectorProfileIn):
    """Register a vector-store profile pointing at any writable folder —
    local path, mounted network drive, etc."""
    if not body.name.strip() or not body.db_path.strip():
        raise HTTPException(status_code=400, detail="name and db_path are both required.")
    loop = asyncio.get_event_loop()
    try:
        pid = await loop.run_in_executor(
            None,
            lambda: db_connections.save_vector_profile(
                body.name.strip(), body.db_path.strip(), collection_name=body.collection_name.strip() or "file_ingestion"
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not create/access that folder: {e}")
    return {"id": pid, "name": body.name.strip()}


@app.post("/api/connections/vector/{profile_id}/test", tags=["Connections"])
async def test_vector_connection_endpoint(profile_id: str):
    loop = asyncio.get_event_loop()

    def _run():
        p = db_connections.get_vector_profile(profile_id)
        if not p:
            return {"ok": False, "error": "profile not found"}
        return db_connections.test_vector_path(p["db_path"])

    return await loop.run_in_executor(None, _run)


@app.delete("/api/connections/vector/{profile_id}", tags=["Connections"])
async def delete_vector_connection(profile_id: str):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: db_connections.delete_vector_profile(profile_id))
    return {"success": True}


# ═════════════════════════════════════════════
#  ENTRYPOINT
# ═════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api_server:app",
        host="localhost",
        port=API_PORT,
        reload=False,
        log_config=None,   # use our custom logging setup above
    )
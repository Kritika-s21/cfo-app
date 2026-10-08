"""
scheduler_engine.py
--------------------
Core scheduling engine for the File Pickup Scheduler Agent.
Standalone, generic — attach to any agent via the AgentBridge interface.

Author  : Senior AI Agent Engineer
Version : 1.0.0
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("scheduler.engine")


# ─────────────────────────────────────────────
#  Enums & Constants
# ─────────────────────────────────────────────

class ScheduleFrequency(str, Enum):
    MINUTELY = "minutely"   # dev / testing
    HOURLY   = "hourly"
    DAILY    = "daily"
    WEEKLY   = "weekly"
    MONTHLY  = "monthly"
    CRON     = "cron"       # power users


class Weekday(int, Enum):
    MONDAY    = 0
    TUESDAY   = 1
    WEDNESDAY = 2
    THURSDAY  = 3
    FRIDAY    = 4
    SATURDAY  = 5
    SUNDAY    = 6


class SchedulerStatus(str, Enum):
    IDLE     = "idle"
    RUNNING  = "running"
    PAUSED   = "paused"
    STOPPED  = "stopped"
    ERROR    = "error"


# ─────────────────────────────────────────────
#  Data Models
# ─────────────────────────────────────────────

@dataclass
class ScheduleConfig:
    """
    Complete schedule specification — serialisable to/from JSON so the UI
    can persist and reload it, and so agents can store it in their state.
    """
    frequency: ScheduleFrequency = ScheduleFrequency.DAILY

    # --- DAILY ---
    daily_hour: int   = 9          # 0-23
    daily_minute: int = 0          # 0-59

    # --- WEEKLY ---
    weekly_day: Weekday = Weekday.MONDAY
    weekly_hour: int    = 9
    weekly_minute: int  = 0

    # --- MONTHLY ---
    monthly_day_of_month: int = 1  # 1-28  (capped at 28 for safety)
    monthly_hour: int         = 9
    monthly_minute: int       = 0

    # --- HOURLY ---
    hourly_minute: int = 0         # at which minute of the hour

    # --- MINUTELY (dev / test) ---
    minutely_interval: int = 5     # every N minutes

    # --- CRON (power users) ---
    cron_expression: str = "0 9 * * 1"   # default: Mon 09:00

    # --- Misc ---
    timezone_name: str = "UTC"
    enabled: bool      = True

    def to_dict(self) -> Dict:
        d = asdict(self)

        # Handle frequency safely — never trust isinstance(Enum) alone; some
        # code paths (raw dict loads, partial UI updates, duplicate module
        # imports) can leave self.frequency as a plain string, the Enum
        # class itself, or another non-member object. Try .value, then fall
        # back to the raw value, then to the default.
        try:
            d["frequency"] = self.frequency.value
        except AttributeError:
            if isinstance(self.frequency, str) and self.frequency:
                d["frequency"] = self.frequency
            else:
                logger.warning(
                    "ScheduleConfig.frequency was not a valid ScheduleFrequency "
                    "(got %r) — falling back to %r", self.frequency, ScheduleFrequency.DAILY.value
                )
                d["frequency"] = ScheduleFrequency.DAILY.value

        # Handle weekly_day safely (same defensive pattern)
        try:
            d["weekly_day"] = self.weekly_day.value
        except AttributeError:
            if isinstance(self.weekly_day, int):
                d["weekly_day"] = self.weekly_day
            else:
                logger.warning(
                    "ScheduleConfig.weekly_day was not a valid Weekday "
                    "(got %r) — falling back to %r", self.weekly_day, Weekday.MONDAY.value
                )
                d["weekly_day"] = Weekday.MONDAY.value

        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "ScheduleConfig":
        d = dict(d)
        d["frequency"]  = ScheduleFrequency(d["frequency"])
        d["weekly_day"] = Weekday(d["weekly_day"])
        return cls(**d)


@dataclass
class FileRecord:
    """Tracks a single file discovered by the scanner."""
    path: str
    source_type: str          # local | gcs | azure | s3 | gdrive
    size_bytes: int
    last_modified: datetime
    etag: Optional[str]       = None
    is_new: bool              = True
    is_modified: bool         = False
    discovered_at: datetime   = field(default_factory=lambda: datetime.now(timezone.utc))

    # ── Per-source identity + auth, needed downstream ──────────────────
    # Previously missing entirely: FileRecord had no field to carry any
    # of this, so even when a FileSource (sources.py) held valid
    # credentials for a gdrive/azure/s3 source, there was nowhere for
    # list_files() to attach them to the records it returned — they were
    # structurally dropped at scan time. By the time api_server.py built
    # file_info from file_record.to_dict() for the ingestion pipeline,
    # "credentials" was always absent, which is what produced
    # "no service-account credentials resolved" in content_extractor.py
    # for every cloud source, not just Google Drive.
    #
    # sources.py's list_files() implementations must now populate these
    # when constructing each FileRecord, e.g.:
    #   FileRecord(path=..., source_type="gdrive", size_bytes=..., ...,
    #              source_id=self.source_id, credentials=self.credentials,
    #              file_id=file["id"], folder_id=self.folder_id)
    source_id:   Optional[str]           = None
    credentials: Optional[Dict[str, Any]] = None
    # The real, human-readable file name. Needed because `path` for cloud
    # sources is often an opaque identifier (e.g. gdrive's path is
    # "gdrive://<folder_id>/<file_id>", where <file_id> is not the file's
    # actual name) — deriving file_name as Path(path).name would silently
    # show the Drive file ID instead of e.g. "Q3 Report.xlsx". Sources
    # that discover a real name (gdrive, azure, s3) should set this.
    file_name:   Optional[str] = None
    # cloud-specific identifiers (only the ones relevant to source_type
    # need to be set; the rest stay None)
    folder_id:   Optional[str] = None   # gdrive
    file_id:     Optional[str] = None   # gdrive
    container:   Optional[str] = None   # azure
    blob_name:   Optional[str] = None   # azure
    bucket:      Optional[str] = None   # gcs / s3
    s3_key:      Optional[str] = None   # s3

    def to_dict(self) -> Dict:
        return {
            **asdict(self),
            "last_modified": self.last_modified.isoformat(),
            "discovered_at": self.discovered_at.isoformat(),
        }


@dataclass
class ScanResult:
    """Summary produced after each scan cycle."""
    scan_id: str              = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime      = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    new_files: List[FileRecord]      = field(default_factory=list)
    modified_files: List[FileRecord] = field(default_factory=list)
    # One entry per configured source.  This makes an empty scan
    # distinguishable from a source that was not reachable.
    source_diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str]             = None

    @property
    def total_files(self) -> int:
        return len(self.new_files) + len(self.modified_files)

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


# ─────────────────────────────────────────────
#  State Store  (in-memory; swap for Redis/DB)
# ─────────────────────────────────────────────

class SchedulerStateStore:
    """
    Persists the 'last seen' metadata so the scheduler survives restarts.
    Default is in-memory + JSON file.  Swap __init__ for a DB-backed variant.
    """

    def __init__(self, state_path: Optional[Path] = None):
        self._state_path = state_path or Path("scheduler_state.json")
        self._lock = threading.Lock()
        # { source_id: { file_path: { "last_modified": ISO, "etag": str } } }
        self._data: Dict[str, Dict[str, Dict]] = {}
        self._load()

    # ---------- public API ----------

    def get_known_files(self, source_id: str) -> Dict[str, Dict]:
        with self._lock:
            return dict(self._data.get(source_id, {}))

    def update_files(self, source_id: str, records: List[FileRecord]) -> None:
        with self._lock:
            bucket = self._data.setdefault(source_id, {})
            for r in records:
                bucket[r.path] = {
                    "last_modified": r.last_modified.isoformat(),
                    "etag": r.etag,
                    "size_bytes": r.size_bytes,
                }
            self._save()

    def clear(self, source_id: str) -> None:
        with self._lock:
            self._data.pop(source_id, None)
            self._save()

    # ---------- internals ----------

    def _load(self) -> None:
        if self._state_path.exists():
            try:
                with open(self._state_path) as f:
                    self._data = json.load(f)
                logger.info("State loaded from %s", self._state_path)
            except Exception as e:
                logger.warning("Could not load state: %s", e)

    def _save(self) -> None:
        try:
            with open(self._state_path, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            logger.warning("Could not save state: %s", e)


# ─────────────────────────────────────────────
#  Abstract Source
# ─────────────────────────────────────────────

class FileSource(ABC):
    """Base class every storage connector must implement."""

    def __init__(self, source_id: str, config: Dict[str, Any]):
        self.source_id = source_id
        self.config    = config

    @abstractmethod
    async def list_files(self) -> List[FileRecord]:
        """Return a flat list of all files visible to this source."""
        ...

    @abstractmethod
    def validate_config(self) -> None:
        """Raise ValueError with a clear message if config is incomplete."""
        ...


# ─────────────────────────────────────────────
#  Trigger / Next-Run Calculator
# ─────────────────────────────────────────────

class NextRunCalculator:
    """
    Given a ScheduleConfig and a reference 'now', compute when the
    scheduler should next fire.  Pure functions — easy to unit test.
    """

    @staticmethod
    def compute(config: ScheduleConfig, now: Optional[datetime] = None) -> datetime:
        import pytz
        from dateutil.relativedelta import relativedelta

        now = now or datetime.now(timezone.utc)
        try:
            tz = pytz.timezone(config.timezone_name)
        except Exception:
            tz = pytz.utc

        local_now = now.astimezone(tz)

        freq = config.frequency

        if freq == ScheduleFrequency.MINUTELY:
            return NextRunCalculator._minutely(local_now, config.minutely_interval)

        if freq == ScheduleFrequency.HOURLY:
            return NextRunCalculator._hourly(local_now, config.hourly_minute)

        if freq == ScheduleFrequency.DAILY:
            return NextRunCalculator._daily(local_now, config.daily_hour, config.daily_minute)

        if freq == ScheduleFrequency.WEEKLY:
            return NextRunCalculator._weekly(
                local_now, config.weekly_day.value,
                config.weekly_hour, config.weekly_minute
            )

        if freq == ScheduleFrequency.MONTHLY:
            return NextRunCalculator._monthly(
                local_now,
                config.monthly_day_of_month,
                config.monthly_hour,
                config.monthly_minute,
            )

        if freq == ScheduleFrequency.CRON:
            return NextRunCalculator._cron(local_now, config.cron_expression, tz)

        raise ValueError(f"Unknown frequency: {freq}")

    # ---- helpers ----

    @staticmethod
    def _minutely(now: datetime, interval: int) -> datetime:
        import math
        total = now.minute * 60 + now.second
        next_total = math.ceil((total + 1) / (interval * 60)) * (interval * 60)
        delta = next_total - total
        from datetime import timedelta
        return (now + timedelta(seconds=delta)).replace(second=0, microsecond=0)

    @staticmethod
    def _hourly(now: datetime, at_minute: int) -> datetime:
        from datetime import timedelta
        candidate = now.replace(minute=at_minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(hours=1)
        return candidate

    @staticmethod
    def _daily(now: datetime, hour: int, minute: int) -> datetime:
        from datetime import timedelta
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    @staticmethod
    def _weekly(now: datetime, weekday: int, hour: int, minute: int) -> datetime:
        from datetime import timedelta
        days_ahead = weekday - now.weekday()
        if days_ahead < 0 or (days_ahead == 0 and
                               now.replace(hour=hour, minute=minute, second=0, microsecond=0) <= now):
            days_ahead += 7
        candidate = (now + timedelta(days=days_ahead)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        return candidate

    @staticmethod
    def _monthly(now: datetime, day: int, hour: int, minute: int) -> datetime:
        from dateutil.relativedelta import relativedelta
        import calendar
        day = min(day, 28)
        candidate = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += relativedelta(months=1)
        return candidate

    @staticmethod
    def _cron(now: datetime, expression: str, tz) -> datetime:
        from croniter import croniter
        cron = croniter(expression, now)
        return cron.get_next(datetime)


# ─────────────────────────────────────────────
#  Core Scheduler
# ─────────────────────────────────────────────

class FileScheduler:
    """
    The main scheduler.  Attach sources and an agent callback, then start().

    Usage
    -----
    scheduler = FileScheduler(config, state_store)
    scheduler.add_source(LocalFolderSource(...))
    scheduler.on_scan_complete(my_agent.handle_files)
    scheduler.start()
    """

    def __init__(
        self,
        config: ScheduleConfig,
        state_store: Optional[SchedulerStateStore] = None,
        scheduler_id: Optional[str] = None,
    ):
        self.scheduler_id  = scheduler_id or str(uuid.uuid4())
        self.config        = config
        self.state_store   = state_store or SchedulerStateStore()
        self.status        = SchedulerStatus.IDLE

        self._sources: List[FileSource]   = []
        self._callbacks: List[Callable]   = []
        self._scan_history: List[ScanResult] = []

        self._loop:   Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread]          = None
        self._stop_event = threading.Event()
        # Set by update_config() so a mid-sleep loop wakes up immediately and
        # re-reads the new schedule instead of waiting out the stale target.
        self._config_changed = threading.Event()

        self.last_scan_at:  Optional[datetime] = None
        self.next_run_at:   Optional[datetime] = None

    # ── source management ──────────────────────────────────────────────────

    def add_source(self, source: FileSource) -> "FileScheduler":
        source.validate_config()
        # Source sync is intentionally idempotent: the React client replays
        # a profile before Start/Run (including after an API restart).
        self._sources = [s for s in self._sources if s.source_id != source.source_id]
        self._sources.append(source)
        logger.info("Source added: %s (%s)", source.source_id, type(source).__name__)
        return self

    def remove_source(self, source_id: str) -> None:
        self._sources = [s for s in self._sources if s.source_id != source_id]

    # ── callback management ────────────────────────────────────────────────

    def on_scan_complete(self, callback: Callable[[ScanResult], None]) -> "FileScheduler":
        """Register a callback (sync or async) invoked after every scan."""
        self._callbacks.append(callback)
        return self

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if self.status == SchedulerStatus.RUNNING:
            logger.warning("Scheduler already running")
            return
        self._stop_event.clear()
        self._config_changed.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name=f"scheduler-{self.scheduler_id}"
        )
        self._thread.start()
        self.status = SchedulerStatus.RUNNING
        logger.info("Scheduler %s started", self.scheduler_id)

    def stop(self) -> None:
        self._stop_event.set()
        self.status = SchedulerStatus.STOPPED
        logger.info("Scheduler %s stopped", self.scheduler_id)

    def pause(self) -> None:
        self.status = SchedulerStatus.PAUSED

    def resume(self) -> None:
        if self.status == SchedulerStatus.PAUSED:
            self.status = SchedulerStatus.RUNNING

    def trigger_now(self) -> None:
        """Force an immediate scan (outside normal schedule)."""
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._scan_all(), self._loop)

    def update_config(self, config: ScheduleConfig) -> None:
        self.config    = config
        self.next_run_at = NextRunCalculator.compute(config)
        # Wake the loop immediately if it's mid-sleep on a stale schedule —
        # without this, a config change (e.g. daily -> minutely) has no
        # effect until whatever sleep the loop already committed to finishes,
        # which can be hours away.
        self._config_changed.set()
        logger.info("Schedule updated. Next run: %s", self.next_run_at)

    @property
    def scan_history(self) -> List[ScanResult]:
        return list(self._scan_history[-50:])   # last 50

    def remove_scan_history(self, scan_id: str) -> bool:
        """Remove a scan by its full or UI-shortened ID from real storage."""
        before = len(self._scan_history)
        self._scan_history[:] = [
            item for item in self._scan_history
            if item.scan_id != scan_id and item.scan_id[:8] != scan_id
        ]
        return len(self._scan_history) != before

    def clear_scan_history(self) -> int:
        """Clear the actual backing list and return the number removed."""
        removed = len(self._scan_history)
        self._scan_history.clear()
        return removed

    # ── internals ─────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._async_run_loop())

    async def _async_run_loop(self) -> None:
        while not self._stop_event.is_set():
            if self.status == SchedulerStatus.PAUSED:
                await asyncio.sleep(5)
                continue

            # Consume any pending config-change signal before computing —
            # so this iteration always reflects the latest saved schedule.
            self._config_changed.clear()

            self.next_run_at = NextRunCalculator.compute(self.config)
            now = datetime.now(timezone.utc)

            # Ensure both datetimes are in UTC for accurate comparison
            next_run_utc = self.next_run_at.astimezone(timezone.utc) if self.next_run_at.tzinfo else self.next_run_at.replace(tzinfo=timezone.utc)
            wait_seconds = max(0.0, (next_run_utc - now).total_seconds())

            logger.info(
                "Scheduler mode: %s | Next scan: %s (UTC: %s) | Wait: %.0fs (%.1fh)",
                self.config.frequency.value,
                self.next_run_at.isoformat(),
                next_run_utc.isoformat(),
                wait_seconds,
                wait_seconds / 3600
            )

            # Sleep in small chunks so we can react to stop/pause/config-change
            slept = 0.0
            while slept < wait_seconds and not self._stop_event.is_set():
                chunk = min(5.0, wait_seconds - slept)
                await asyncio.sleep(chunk)
                slept += chunk
                if self.status == SchedulerStatus.PAUSED:
                    break
                if self._config_changed.is_set():
                    logger.info("Config changed mid-sleep — recomputing next run now.")
                    break

            if self._stop_event.is_set():
                break
            if self.status == SchedulerStatus.PAUSED:
                continue
            if self._config_changed.is_set():
                # Loop back immediately to recompute from the new config,
                # rather than falling through to a scan on the old target.
                continue

            await self._scan_all()

    async def _scan_all(self) -> None:
        result = ScanResult()
        try:
            logger.info("Scan %s started — %d source(s)", result.scan_id, len(self._sources))
            tasks = [self._scan_source(src, result) for src in self._sources]
            await asyncio.gather(*tasks)
            self.last_scan_at = datetime.now(timezone.utc)
        except Exception as e:
            result.error = str(e)
            self.status  = SchedulerStatus.ERROR
            logger.exception("Scan failed: %s", e)
        finally:
            result.finished_at = datetime.now(timezone.utc)
            self._scan_history.append(result)
            await self._dispatch_callbacks(result)

            # Calculate time to next scan for logging
            next_scan_in = "unknown"
            if self.next_run_at:
                now = datetime.now(timezone.utc)
                next_utc = self.next_run_at.astimezone(timezone.utc) if self.next_run_at.tzinfo else self.next_run_at
                secs = (next_utc - now).total_seconds()
                if secs > 3600:
                    next_scan_in = f"{secs/3600:.1f}h"
                elif secs > 60:
                    next_scan_in = f"{secs/60:.0f}m"
                else:
                    next_scan_in = f"{secs:.0f}s"

            logger.info(
                "Scan %s done — %d new, %d modified (%.1fs) | Mode: %s | Next scan in: %s",
                result.scan_id, len(result.new_files),
                len(result.modified_files), result.duration_seconds or 0,
                self.config.frequency.value, next_scan_in
            )

    async def _scan_source(self, source: FileSource, result: ScanResult) -> None:
        diagnostic = {"source_id": source.source_id, "source_type": source.__class__.__name__,
                      "files_visible": 0, "new_files": 0, "modified_files": 0, "error": None}
        try:
            known = self.state_store.get_known_files(source.source_id)
            current_files = await source.list_files()
            diagnostic["files_visible"] = len(current_files)

            new_or_modified: List[FileRecord] = []
            for record in current_files:
                prev = known.get(record.path)
                if prev is None:
                    record.is_new = True
                    record.is_modified = False
                    result.new_files.append(record)
                    new_or_modified.append(record)
                    diagnostic["new_files"] += 1
                else:
                    prev_ts = datetime.fromisoformat(prev["last_modified"])
                    if record.last_modified > prev_ts or record.etag != prev.get("etag"):
                        record.is_new = False
                        record.is_modified = True
                        result.modified_files.append(record)
                        new_or_modified.append(record)
                        diagnostic["modified_files"] += 1

            if new_or_modified:
                self.state_store.update_files(source.source_id, new_or_modified)
        except Exception as exc:
            diagnostic["error"] = str(exc)
            logger.exception("Source %s scan failed", source.source_id)
        finally:
            result.source_diagnostics.append(diagnostic)

    async def _dispatch_callbacks(self, result: ScanResult) -> None:
        for cb in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(result)
                else:
                    cb(result)
            except Exception as e:
                logger.error("Callback error: %s", e)
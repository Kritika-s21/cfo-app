"""Central logging: everything goes to the terminal AND to rotating files under LOG_DIR.

  logs/app.log    all records (INFO and above by default)
  logs/error.log  WARNING and above only
Env: LOG_DIR (default ./logs), LOG_LEVEL (default INFO; DEBUG shows request bodies' sizes, SSE lines, etc.)
"""
import logging, logging.handlers, os, sys
from pathlib import Path

LOG_DIR = Path(os.environ.get("LOG_DIR", "logs"))
FMT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def setup_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_cfo_configured", False):
        return
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(FMT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    app_file = logging.handlers.RotatingFileHandler(LOG_DIR / "app.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8")
    app_file.setFormatter(fmt)
    err_file = logging.handlers.RotatingFileHandler(LOG_DIR / "error.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8")
    err_file.setFormatter(fmt)
    err_file.setLevel(logging.WARNING)

    root.handlers = [console, app_file, err_file]
    root.setLevel(level)
    root._cfo_configured = True

    # route uvicorn / apscheduler through the same handlers; the request middleware in main.py replaces uvicorn.access
    for name in ("uvicorn", "uvicorn.error", "fastapi", "apscheduler"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
        lg.setLevel(level)
    acc = logging.getLogger("uvicorn.access")
    acc.handlers = []
    acc.propagate = False
    logging.getLogger("urllib3").setLevel(logging.WARNING if level != "DEBUG" else logging.DEBUG)
    logging.getLogger("cfo.startup").info("Logging to console + %s (level=%s)", LOG_DIR.resolve(), level)

"""
sql_ingestion.py
----------------
Handles ingestion of STRUCTURED data into SQL Server via the ezcoworker
agent, using the upload-first flow:

    Generate scan_xxxxx.json
             │
             ▼
    Store in ./scheduler_logs/
             │
             ▼
    Upload JSON to ezcoworker (POST /api/upload)
             │
             ▼
    Call POST /api/chat/stream
             │
             ▼
    AI reads uploaded JSON
             │
             ▼
    AI connects using its own Database Tool
             │
             ▼
    For every record in new_files/modified_files:
        if id exists → UPDATE
        else         → INSERT
             │
             ▼
    Return SUCCESS
             │
             ▼
    Move JSON to ./scheduler_logs/processed  (or failed/ on error)

Why this shape, specifically:
  - The raw scan_xxxxx.json is uploaded as a file (not described inline
    in the chat message), so there's no message-size limit to work
    around and the agent reads new_files/modified_files itself.
  - The agent is explicitly told to use only its Database Tool — no
    Python, no pyodbc, no pymssql, no simulated execution — which is
    the framing that reliably works against ezcoworker (asking it to
    blindly run an opaque literal SQL statement gets refused or
    produces hallucinated tool errors).
  - The agent replies in its own words but must end with a single
    machine-parseable STATUS line, which is all this module depends on.

Table written: dbo.FileIngestion (created by the agent if missing).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

import db_connections

load_dotenv()

logger = logging.getLogger("agent.sql_ingestion")

# ─── Environment ───────────────────────────────────────────────
# Standard pyodbc-style connection string, e.g.:
#   "Server=myserver.database.windows.net;Database=mydb;UID=user;PWD=pass"
# or using the "User Id=" / "Password=" spelling ezcoworker itself expects.
#
# This module-level value is now only the *fallback* used when a caller
# doesn't pass an explicit sql_connection_string / sql_profile_id — every
# public function below can be pointed at a different, user-supplied
# database on a per-call basis via db_connections.py profiles.
SQL_CONNECTION_STRING = os.environ.get("SQL_CONNECTION_STRING")


def _resolve_conn_str(
    sql_connection_string: Optional[str] = None,
    sql_profile_id: Optional[str] = None,
) -> str:
    """
    Resolution order for which SQL database to use on a given call:
      1. an explicit sql_connection_string passed by the caller
      2. a sql_profile_id passed by the caller (looked up via db_connections)
      3. the legacy SQL_CONNECTION_STRING env var
    """
    if sql_connection_string:
        return sql_connection_string
    return db_connections.resolve_sql_connection_string(sql_profile_id) or SQL_CONNECTION_STRING or ""

EZCOWORKER_BASE_URL = os.environ.get(
    "EZCOWORKER_BASE_URL", "https://ezcoworker.ezdatamunch.com/api"
)
EZCOWORKER_API_KEY = os.environ.get("EZCOWORKER_API_KEY", "")
EZCOWORKER_CHAT_TIMEOUT = int(os.environ.get("EZCOWORKER_CHAT_TIMEOUT", "300"))

# Skills enabled for the ezcoworker agent when it performs the SQL task.
_EZCOWORKER_SQL_SKILLS = [
    "coding-agent",
    "schema-migration",
    "data-analyst",
]

_STATUS_SUCCESS_RE = re.compile(r"STATUS:\s*SUCCESS", re.IGNORECASE)
_STATUS_FAILURE_RE = re.compile(r"STATUS:\s*FAILED\b\s*[:\-]?\s*(.*)", re.IGNORECASE)


def _parse_sql_server_credentials(conn_str: str) -> Dict[str, str]:
    """
    Pull SERVER / DATABASE / UID / PWD out of SQL_CONNECTION_STRING.
    Accepts both the pyodbc-style short keys (UID, PWD) and the longer
    "User Id" / "Password" spelling — different tools default to
    different spellings and this previously caused silently-empty
    credentials being sent to the agent when only the short keys were
    matched.
    """
    patterns = {
        "SERVER":   r"Server",
        "DATABASE": r"Database",
        "UID":      r"UID|User\s*Id",
        "PWD":      r"PWD|Password",
    }
    fields = {}
    for key, alt in patterns.items():
        match = re.search(rf"(?:{alt})=([^;]*)", conn_str, re.IGNORECASE)
        fields[key] = match.group(1).strip() if match else ""
    return fields


def _parse_status(reply: str) -> "tuple[bool, Optional[str]]":
    """
    Look for a STATUS: SUCCESS / STATUS: FAILED line anywhere in the
    reply (the agent may explain its steps before it). An unparseable
    reply is treated as a failure so we never silently mark something
    ingested.
    """
    if _STATUS_SUCCESS_RE.search(reply):
        return True, None
    m = _STATUS_FAILURE_RE.search(reply)
    if m:
        return False, m.group(1).strip() or "agent reported failure"
    return False, f"Could not find a STATUS line in agent reply: {reply[:300]}"


# ═══════════════════════════════════════════════════════════════
#  ezcoworker transport: /api/upload  and  /api/chat/stream
# ═══════════════════════════════════════════════════════════════

def _ezcoworker_upload_file(file_path: str) -> Dict[str, Any]:
    """
    Upload a file to ezcoworker exactly like the UI does:
        POST /api/upload
        multipart/form-data, field name "files"
    On success the backend makes it available server-side as
    input/<filename>, which is what we reference in the chat message.
    """
    if not EZCOWORKER_API_KEY:
        raise RuntimeError("EZCOWORKER_API_KEY not configured")

    url = f"{EZCOWORKER_BASE_URL.rstrip('/')}/upload"
    headers = {"Authorization": f"Bearer {EZCOWORKER_API_KEY}"}
    filename = os.path.basename(file_path)

    with open(file_path, "rb") as fh:
        files = {"files": (filename, fh, "application/json")}
        resp = requests.post(
            url, headers=headers, files=files, timeout=EZCOWORKER_CHAT_TIMEOUT
        )

    if not resp.ok:
        logger.error(
            "ezcoworker /upload returned %s: %s", resp.status_code, resp.text[:2000]
        )
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        # Some upload endpoints reply with plain text/empty body on 200.
        return {"filename": filename}


def _extract_uploaded_filename(upload_result: Dict[str, Any], fallback: str) -> str:
    """Pull the server-side filename out of a /api/upload response,
    tolerating a few different response shapes."""
    if not isinstance(upload_result, dict):
        return fallback

    for key in ("filename", "fileName", "name"):
        val = upload_result.get(key)
        if isinstance(val, str) and val:
            return val

    files_list = upload_result.get("files")
    if isinstance(files_list, list) and files_list:
        first = files_list[0]
        if isinstance(first, dict):
            for key in ("filename", "fileName", "name"):
                val = first.get(key)
                if isinstance(val, str) and val:
                    return val
        elif isinstance(first, str) and first:
            return first

    return fallback


def _ezcoworker_chat_stream(
    message: str, conversation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Call POST /api/chat/stream and accumulate the streamed reply into a
    single string (mirroring what the UI shows once streaming finishes).

    The exact event framing isn't something we control, so this parses
    defensively: each line is treated as SSE ("data: ..." prefix
    stripped if present), then as JSON with a text delta under one of a
    few common key names, falling back to appending the raw line as
    plain text if it isn't JSON.

    Returns {"reply": str, "conversationId": str | None}.
    """
    if not EZCOWORKER_API_KEY:
        raise RuntimeError("EZCOWORKER_API_KEY not configured")

    url = f"{EZCOWORKER_BASE_URL.rstrip('/')}/chat/stream"
    headers = {
        "Authorization": f"Bearer {EZCOWORKER_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    payload = {
        "message": message,
        "conversationId": conversation_id,
        "enabledSkills": _EZCOWORKER_SQL_SKILLS,
    }

    reply_parts: List[str] = []
    resp_conversation_id = conversation_id

    with requests.post(
        url, json=payload, headers=headers, timeout=EZCOWORKER_CHAT_TIMEOUT, stream=True
    ) as resp:
        if not resp.ok:
            logger.error(
                "ezcoworker /chat/stream returned %s: %s",
                resp.status_code,
                resp.text[:2000],
            )
        resp.raise_for_status()

        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line.strip()
            if line.startswith("data:"):
                line = line[len("data:"):].strip()
            if not line or line == "[DONE]":
                continue

            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                reply_parts.append(line)
                continue

            if isinstance(obj, dict):
                cid = obj.get("conversationId") or obj.get("conversation_id")
                if cid:
                    resp_conversation_id = cid

                appended = False
                for key in ("delta", "content", "text", "chunk", "token"):
                    val = obj.get(key)
                    if isinstance(val, str):
                        reply_parts.append(val)
                        appended = True
                        break
                if not appended:
                    val = obj.get("reply")
                    if isinstance(val, str):
                        reply_parts.append(val)

    return {"reply": "".join(reply_parts).strip(), "conversationId": resp_conversation_id}


# ═══════════════════════════════════════════════════════════════
#  Prompt: Database Tool, no Python/pyodbc/pymssql, no simulation
# ═══════════════════════════════════════════════════════════════

_TABLE_SCHEMA_SQL_DDL = """
- id (NVARCHAR(64), PRIMARY KEY)
- file_name (NVARCHAR(512))
- file_type (NVARCHAR(32))
- file_size_bytes (BIGINT)
- source_type (NVARCHAR(64))
- source_id (NVARCHAR(256))
- file_path (NVARCHAR(2048))
- last_modified (NVARCHAR(64))
- classification (NVARCHAR(64))
- data_type (NVARCHAR(64))
- confidence (FLOAT)
- summary (NVARCHAR(MAX))
- schema_hint (NVARCHAR(MAX))
- content_preview (NVARCHAR(MAX))
- extracted_content (NVARCHAR(MAX))
- metadata_json (NVARCHAR(MAX))
- ingested_at (NVARCHAR(64))
- ingestion_status (NVARCHAR(64))
""".strip()

_SCAN_JSON_FIELD_MAPPING = """
- id → id
- file_name → file_name
- file_type → file_type
- file_size_bytes → file_size_bytes
- source_type → source_type
- source_id → source_id
- path → file_path
- last_modified → last_modified
- classification → classification
- data_type → data_type
- confidence → confidence
- summary → summary
- schema_hint → schema_hint
- content_preview → content_preview
- extracted_content → extracted_content
- metadata → metadata_json
- extracted_at → ingested_at
- status → ingestion_status
""".strip()


_CREATE_TABLE_SQL = """
IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='FileIngestion' AND xtype='U')
CREATE TABLE dbo.FileIngestion (
    id                NVARCHAR(64)   NOT NULL PRIMARY KEY,
    file_name         NVARCHAR(512),
    file_type         NVARCHAR(32),
    file_size_bytes   BIGINT,
    source_type       NVARCHAR(64),
    source_id         NVARCHAR(256),
    file_path         NVARCHAR(2048),
    last_modified     NVARCHAR(64),
    classification    NVARCHAR(64),
    data_type         NVARCHAR(64),
    confidence        FLOAT,
    summary           NVARCHAR(MAX),
    schema_hint       NVARCHAR(MAX),
    content_preview   NVARCHAR(MAX),
    extracted_content NVARCHAR(MAX),
    metadata_json     NVARCHAR(MAX),
    ingested_at       NVARCHAR(64),
    ingestion_status  NVARCHAR(64)
)
""".strip()

_MERGE_ROW_SQL = """
MERGE dbo.FileIngestion AS target
USING (SELECT :id AS id) AS src
ON target.id = src.id
WHEN MATCHED THEN UPDATE SET
    file_name = :file_name, file_type = :file_type, file_size_bytes = :file_size_bytes,
    source_type = :source_type, source_id = :source_id, file_path = :file_path,
    last_modified = :last_modified, classification = :classification, data_type = :data_type,
    confidence = :confidence, summary = :summary, schema_hint = :schema_hint,
    content_preview = :content_preview, extracted_content = :extracted_content,
    metadata_json = :metadata_json, ingested_at = :ingested_at, ingestion_status = :ingestion_status
WHEN NOT MATCHED THEN INSERT (
    id, file_name, file_type, file_size_bytes, source_type, source_id, file_path,
    last_modified, classification, data_type, confidence, summary, schema_hint,
    content_preview, extracted_content, metadata_json, ingested_at, ingestion_status
) VALUES (
    :id, :file_name, :file_type, :file_size_bytes, :source_type, :source_id, :file_path,
    :last_modified, :classification, :data_type, :confidence, :summary, :schema_hint,
    :content_preview, :extracted_content, :metadata_json, :ingested_at, :ingestion_status
);
""".strip()


def _upsert_row_direct(row: Dict[str, Any], conn_str: str) -> Dict[str, Any]:
    """
    Write a single dbo.FileIngestion row straight to SQL Server via
    SQLAlchemy, using whichever connection was actually resolved for this
    call (a specific "Data Connections" profile, or the SQL_CONNECTION_STRING
    env-var default).

    This exists because the previous path — asking the ezcoworker chat
    agent's "Database Tool" to connect using credentials typed into a
    prompt — cannot be relied on to switch databases per call. That tool
    is driven by natural language, not a structured API parameter, so
    there's no guarantee it doesn't keep using whatever connection it's
    already configured with on ezcoworker's own side; in practice every
    write kept landing in the env-configured database no matter which
    profile was selected here. Writing directly removes that indirection
    — the connection used is exactly the one this function was given.
    """
    from sqlalchemy import create_engine, text

    try:
        engine = create_engine(_to_sqlalchemy_url(conn_str))
        with engine.begin() as conn:
            conn.execute(text(_CREATE_TABLE_SQL))
            conn.execute(text(_MERGE_ROW_SQL), {
                "id": row["id"],
                "file_name": row.get("file_name", ""),
                "file_type": row.get("file_type", ""),
                "file_size_bytes": row.get("file_size_bytes", 0),
                "source_type": row.get("source_type", ""),
                "source_id": row.get("source_id", ""),
                "file_path": row.get("path", ""),
                "last_modified": row.get("last_modified", ""),
                "classification": row.get("classification", ""),
                "data_type": row.get("data_type", ""),
                "confidence": row.get("confidence", 0.0),
                "summary": row.get("summary", ""),
                "schema_hint": json.dumps(row.get("schema_hint", []), default=str),
                "content_preview": row.get("content_preview", ""),
                "extracted_content": row.get("extracted_content", ""),
                "metadata_json": json.dumps(row.get("metadata", {}), default=str),
                "ingested_at": row.get("extracted_at", ""),
                "ingestion_status": row.get("status", "ingested"),
            })
        return {"success": True, "mode": "direct_sql", "table": "dbo.FileIngestion", "error": None}
    except Exception as e:
        logger.error("Direct SQL upsert failed: %s", e)
        return {"success": False, "mode": "direct_sql", "table": "dbo.FileIngestion", "error": str(e)}


def _build_upload_flow_prompt(uploaded_filename: str, conn_str: str) -> str:
    """Build the chat message sent after upload — asks the agent to use
    only its Database Tool (no Python/pyodbc/pymssql, no simulation),
    read the uploaded scan JSON itself, and upsert every record from
    new_files + modified_files."""
    creds = _parse_sql_server_credentials(conn_str)
    conn_line = (
        f"Server={creds.get('SERVER', '')}\n"
        f"Database={creds.get('DATABASE', '')}\n"
        f"User Id={creds.get('UID', '')}\n"
        f"Password={creds.get('PWD', '')}"
    )

    return f"""[I've uploaded these files to input/: {uploaded_filename}]

You are a database administrator with access to the Database Tool.

Use only the Database Tool to connect to Microsoft SQL Server.

Do NOT write Python.
Do NOT use pyodbc.
Do NOT use pymssql.
Do NOT simulate the execution.

Connect using:

{conn_line}

Check whether the table dbo.FileIngestion exists.

If it does not exist, create it using the following schema:

{_TABLE_SCHEMA_SQL_DDL}

If the table already exists, do not modify its schema.

Read the uploaded JSON file input/{uploaded_filename}.

Process every record from both the `new_files` and `modified_files` arrays.

Map the JSON fields to the table columns as follows:

{_SCAN_JSON_FIELD_MAPPING}

Use `id` as the unique key.

If a record with the same `id` already exists, update it.

Otherwise, insert it.

After processing, return:
- whether the table was created or already existed,
- number of records inserted,
- number of records updated,
- number of records skipped,
- and any errors encountered.

Once you're done, end your reply with a line in exactly this format so I can
parse it programmatically — you can explain what you did before it:
STATUS: SUCCESS
or, if anything failed:
STATUS: FAILED - <short reason>
"""


# ═══════════════════════════════════════════════════════════════
#  PUBLIC API — single record file → SQL Server
# ═══════════════════════════════════════════════════════════════

def ingest_scan_json_file(
    json_path: str,
    sql_connection_string: Optional[str] = None,
    sql_profile_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Full agent-driven ingestion for one scan_xxxxx.json file:

        1. POST /api/upload      — upload the raw JSON file
        2. POST /api/chat/stream — ask the agent to read it via the
           Database Tool and upsert every record in new_files +
           modified_files
        3. Parse the trailing STATUS: line from the streamed reply

    Caller is responsible for moving the file to processed/failed
    afterwards — see process_pending_scan_files() below.

    Args:
        sql_connection_string: explicit connection string to use for
            THIS call. Takes priority over sql_profile_id and over the
            legacy SQL_CONNECTION_STRING env var.
        sql_profile_id: id of a profile registered via db_connections.py
            (i.e. a database the user connected through the "Data
            Connections" screen). Ignored if sql_connection_string is set.

    Returns:
      {
        "success": bool,
        "mode":    "ezcoworker_upload" | "skipped",
        "table":   "dbo.FileIngestion",
        "error":   None | str,
        "reply":   str,          # full agent reply, when available
        "source_json": json_path,
      }
    """
    conn_str = _resolve_conn_str(sql_connection_string, sql_profile_id)

    if not EZCOWORKER_API_KEY or not conn_str:
        missing = []
        if not EZCOWORKER_API_KEY:
            missing.append("EZCOWORKER_API_KEY")
        if not conn_str:
            missing.append("SQL connection (no sql_connection_string, sql_profile_id, or SQL_CONNECTION_STRING env var)")
        logger.warning(
            "Missing %s — skipping ingestion for %s", ", ".join(missing), json_path
        )
        return {
            "success": False,
            "mode": "skipped",
            "table": "dbo.FileIngestion",
            "error": f"Missing configuration: {', '.join(missing)}",
            "source_json": json_path,
        }

    if not os.path.isfile(json_path):
        return {
            "success": False,
            "mode": "ezcoworker_upload",
            "table": "dbo.FileIngestion",
            "error": f"file not found: {json_path}",
            "source_json": json_path,
        }

    filename = os.path.basename(json_path)

    try:
        upload_result = _ezcoworker_upload_file(json_path)
    except Exception as e:
        logger.error("ezcoworker upload failed for %s: %s", json_path, e)
        return {
            "success": False,
            "mode": "ezcoworker_upload",
            "table": "dbo.FileIngestion",
            "error": f"upload failed: {e}",
            "source_json": json_path,
        }

    uploaded_name = _extract_uploaded_filename(upload_result, filename)
    message = _build_upload_flow_prompt(uploaded_name, conn_str)

    try:
        stream_result = _ezcoworker_chat_stream(message)
    except Exception as e:
        logger.error("ezcoworker /chat/stream failed for %s: %s", json_path, e)
        return {
            "success": False,
            "mode": "ezcoworker_upload",
            "table": "dbo.FileIngestion",
            "error": f"chat/stream failed: {e}",
            "source_json": json_path,
        }

    reply = stream_result.get("reply", "")
    ok, err = _parse_status(reply)

    if ok:
        logger.info("ezcoworker ingestion succeeded: %s", filename)
    else:
        logger.error(
            "ezcoworker ingestion failed for %s: %s", filename, err or reply[:300]
        )

    return {
        "success": ok,
        "mode": "ezcoworker_upload",
        "table": "dbo.FileIngestion",
        "error": err,
        "reply": reply,
        "source_json": json_path,
    }


def ingest_structured(
    metadata: Dict[str, Any],
    content: str,
    classification_result: Dict[str, Any],
    sql_connection_string: Optional[str] = None,
    sql_profile_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Upserts a single record into dbo.FileIngestion.

    Pass sql_profile_id (the id of a profile the user set up on the
    "Data Connections" screen) or sql_connection_string to direct this
    record at a specific user-chosen database instead of the default
    SQL_CONNECTION_STRING.

    Writes directly to SQL Server via SQLAlchemy (see _upsert_row_direct)
    rather than routing through the ezcoworker chat agent's "Database
    Tool" — that path took the target connection as free-text inside a
    chat prompt, which isn't a reliable way to redirect a live write to
    an arbitrary user-selected database. Writing directly guarantees the
    resolved connection is the one actually used.

    Returns:
      {
        "success": bool,
        "mode":    "direct_sql" | "skipped",
        "table":   "dbo.FileIngestion",
        "error":   None | str,
      }
    """
    import hashlib
    from datetime import datetime, timezone

    file_path = metadata.get("path", metadata.get("file_name", "unknown"))
    row_id = hashlib.md5(file_path.encode()).hexdigest()[:32]
    now = datetime.now(timezone.utc).isoformat()

    row = {
        "id":              row_id,
        "file_name":       metadata.get("file_name", ""),
        "file_type":       metadata.get("file_type", ""),
        "file_size_bytes": metadata.get("file_size_bytes", 0),
        "source_type":     metadata.get("source_type", ""),
        "source_id":       metadata.get("source_id", ""),
        "path":            file_path,
        "last_modified":   metadata.get("last_modified", ""),
        "classification":  classification_result.get("classification", "structured"),
        "data_type":       classification_result.get("data_type", ""),
        "confidence":      float(classification_result.get("confidence", 0.0)),
        "summary":         classification_result.get("summary", ""),
        "schema_hint":     classification_result.get("schema_hint", []),
        "content_preview": content[:500] if content else "",
        "extracted_content": content if content else "",
        "metadata":        metadata,
        "extracted_at":    now,
        "status":          "ingested",
    }

    conn_str = _resolve_conn_str(sql_connection_string, sql_profile_id)

    if not conn_str:
        logger.warning(
            "No SQL connection available — skipping SQL ingestion for %s "
            "(no sql_connection_string, sql_profile_id, or SQL_CONNECTION_STRING env var)",
            file_path,
        )
        return {
            "success": False,
            "mode": "skipped",
            "table": "dbo.FileIngestion",
            "error": "Missing configuration: SQL connection (no sql_connection_string, sql_profile_id, or SQL_CONNECTION_STRING env var)",
        }

    result = _upsert_row_direct(row, conn_str)
    result["reply"] = None
    return result


def _to_sqlalchemy_url(conn_str: str) -> str:
    """
    conn_str from _resolve_conn_str() is the ODBC "Key=Value;Key=Value"
    style (Server=...;Database=...;UID=...;PWD=...) that this module's
    own ingest flow parses with regex for the ezcoworker agent. But
    get_ingested_records() / delete_ingested_records() below talk to SQL
    Server directly via SQLAlchemy, which needs a real mssql+pyodbc://
    URL, not that Key=Value string. Convert here so both paths can share
    one saved profile. If conn_str is already a URL, pass it through.
    """
    if "://" in conn_str:
        return conn_str

    fields = {}
    for key, pattern in {
        "SERVER":   r"Server",
        "DATABASE": r"Database",
        "UID":      r"UID|User\s*Id",
        "PWD":      r"PWD|Password",
    }.items():
        m = re.search(rf"(?:{pattern})=([^;]*)", conn_str, re.IGNORECASE)
        fields[key] = m.group(1).strip() if m else ""

    if not fields["SERVER"] or not fields["DATABASE"]:
        return conn_str

    from urllib.parse import quote_plus
    driver = quote_plus(os.environ.get("SQL_ODBC_DRIVER", "ODBC Driver 17 for SQL Server"))
    user = quote_plus(fields["UID"])
    pwd = quote_plus(fields["PWD"])
    return f"mssql+pyodbc://{user}:{pwd}@{fields['SERVER']}/{fields['DATABASE']}?driver={driver}"


def delete_ingested_records(
    ids: Optional[List[int]] = None,
    delete_all: bool = False,
    sql_connection_string: Optional[str] = None,
    sql_profile_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Delete rows from dbo.FileIngestion.
    - delete_all=True wipes the whole table.
    - otherwise, ids must be a non-empty list of row ids to delete.
    Returns {"success": bool, "deleted_count": int, "error": str|None}.
    """
    conn_str = _resolve_conn_str(sql_connection_string, sql_profile_id)
    if not conn_str:
        return {"success": False, "deleted_count": 0, "error": "No SQL connection available."}
    if not delete_all and not ids:
        return {"success": False, "deleted_count": 0, "error": "Pass ids=[...] or delete_all=True."}

    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(_to_sqlalchemy_url(conn_str))
        with engine.begin() as conn:
            if delete_all:
                result = conn.execute(text("DELETE FROM dbo.FileIngestion"))
            else:
                result = conn.execute(
                    text("DELETE FROM dbo.FileIngestion WHERE id IN :ids").bindparams(
                        __import__("sqlalchemy").bindparam("ids", expanding=True)
                    ),
                    {"ids": ids},
                )
        return {"success": True, "deleted_count": result.rowcount, "error": None}
    except Exception as e:
        logger.error("delete_ingested_records failed: %s", e)
        return {"success": False, "deleted_count": 0, "error": str(e)}


def get_ingested_records(
    limit: int = 100,
    include_content: bool = False,
    sql_connection_string: Optional[str] = None,
    sql_profile_id: Optional[str] = None,
) -> List[dict]:
    """
    Fetch recent records from dbo.FileIngestion for UI display, via the
    ezcoworker agent's Database Tool (POST /api/chat/stream — no upload
    needed here since there's no file to read, just a query to run).

    By default this excludes extracted_content — it's a full-document
    text field that can be very large across many rows. Pass
    include_content=True only when you actually need the full text.

    Pass sql_profile_id / sql_connection_string to read from a specific
    user-registered database instead of the default.
    """
    conn_str = _resolve_conn_str(sql_connection_string, sql_profile_id)

    # Prefer querying the SQL Server directly rather than routing through
    # the chat-based agent. This is faster, reliable and avoids parsing
    # natural-language replies as JSON.
    if not conn_str:
        logger.debug("get_ingested_records: no SQL connection available — returning empty list.")
        return []

    try:
        from sqlalchemy import create_engine, text

        cols = (
            "id, file_name, file_type, source_type, classification, data_type, "
            "confidence, summary, ingested_at, ingestion_status, content_preview"
        )
        if include_content:
            cols += ", extracted_content"

        query = f"SELECT TOP {int(limit)} {cols} FROM dbo.FileIngestion ORDER BY ingested_at DESC"

        engine = create_engine(_to_sqlalchemy_url(conn_str))
        with engine.connect() as conn:
            res = conn.execute(text(query))
            rows = [dict(row._mapping) for row in res.fetchall()]

        return rows

    except Exception as e:
        logger.error("get_ingested_records SQL query failed: %s", e)
        return []


def _extract_json_array(reply: str) -> Optional[list]:
    """
    Parse a JSON array out of an ezcoworker reply, tolerating prose
    before/after it or a ```json code fence around it.
    """
    fence_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", reply, re.DOTALL)
    if fence_match:
        try:
            parsed = json.loads(fence_match.group(1))
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

    cleaned = re.sub(r"^```(?:json)?|```$", "", reply.strip(), flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        candidate = cleaned[start:end + 1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

    return None


# ═══════════════════════════════════════════════════════════════
#  AUTOMATIC INGESTION FROM SCAN-GENERATED JSON FILES
# ═══════════════════════════════════════════════════════════════
#
# Expected JSON shape (adjust in the prompt above if your scanner's
# output differs):
#
# {
#   "new_files": [ {...}, {...} ],
#   "modified_files": [ {...}, {...} ]
# }
#
# where each record roughly matches:
#   id, file_name, file_type, file_size_bytes, source_type, source_id,
#   path, last_modified, classification, data_type, confidence,
#   summary, schema_hint, content_preview, extracted_content,
#   metadata, extracted_at, status

SCAN_OUTPUT_DIR = os.environ.get("SCAN_OUTPUT_DIR", "./scheduler_logs")
SCAN_PROCESSED_DIR = os.environ.get("SCAN_PROCESSED_DIR", "./scheduler_logs/processed")
SCAN_FAILED_DIR = os.environ.get("SCAN_FAILED_DIR", "./scheduler_logs/failed")


def _move_file(path: str, dest_dir: str) -> None:
    os.makedirs(dest_dir, exist_ok=True)
    try:
        os.replace(path, os.path.join(dest_dir, os.path.basename(path)))
    except Exception as e:
        logger.error("Could not move %s to %s: %s", path, dest_dir, e)


def process_pending_scan_files(scan_dir: str = SCAN_OUTPUT_DIR) -> List[Dict[str, Any]]:
    """
    One-shot sweep: ingest every *.json file sitting directly in
    scan_dir via the upload → /api/chat/stream → Database Tool flow,
    then move it to processed/ (success) or failed/ (error) so it
    isn't picked up again. Call this at the end of a scan job, or on a
    timer/cron.
    """
    results = []
    if not os.path.isdir(scan_dir):
        return results

    for name in sorted(os.listdir(scan_dir)):
        if not name.lower().endswith(".json"):
            continue
        full_path = os.path.join(scan_dir, name)
        if not os.path.isfile(full_path):
            continue

        result = ingest_scan_json_file(full_path)
        results.append(result)

        if result.get("success"):
            _move_file(full_path, SCAN_PROCESSED_DIR)
        else:
            logger.error("Ingestion failed for %s: %s", full_path, result.get("error"))
            _move_file(full_path, SCAN_FAILED_DIR)

    return results


def watch_scan_output_dir(scan_dir: str = SCAN_OUTPUT_DIR, poll_seconds: int = 5) -> None:
    """
    Blocking loop: polls scan_dir every poll_seconds and ingests any new
    JSON file as soon as it appears. Run this in a background thread or
    as its own long-lived process.

        import threading
        threading.Thread(
            target=watch_scan_output_dir, daemon=True
        ).start()
    """
    import time

    logger.info("Watching %s for new scan JSON files...", scan_dir)
    while True:
        try:
            process_pending_scan_files(scan_dir)
        except Exception as e:
            logger.error("Error while polling %s: %s", scan_dir, e)
        time.sleep(poll_seconds)
        
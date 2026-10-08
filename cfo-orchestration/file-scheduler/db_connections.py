"""
db_connections.py
------------------
Lets users register *their own* SQL databases and vector-store locations
instead of the app being wired to one fixed SQL_CONNECTION_STRING / one
fixed ./lance_db folder.

Two kinds of named, persisted "profiles":

  SQL profile      → { id, name, connection_string (encrypted) }
  Vector profile    → { id, name, db_path, collection_name, sql_connection_string (encrypted, optional) }

Profiles are stored as one JSON file per profile under:
    ./data_connections/sql/<id>.json
    ./data_connections/vector/<id>.json

Secrets (the connection string) are encrypted at rest with the SAME
Fernet master-key mechanism scheduler_app_1.py already uses for cloud
source credentials (SCHED_MASTER_KEY / SCHED_KEY_PROVIDER=...). That
logic is duplicated here in a small, framework-free form so this module
has no dependency on Streamlit and can be imported directly by
sql_ingestion.py, vector_store.py, presentation_agent.py, and api_server.py.

Every public getter/lister returns the *resolved* (decrypted) values —
callers never see "enc:v1:..." ciphertext.

Backwards compatibility: if no profile_id is passed, or the requested
profile doesn't exist, callers fall back to the legacy env vars
(SQL_CONNECTION_STRING / VECTOR_DB_PATH / VECTOR_COLLECTION_NAME) exactly
like before. Nothing breaks for existing deployments that don't use
profiles at all.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet, InvalidToken

# ─── Storage locations ──────────────────────────────────────────
BASE_DIR   = Path(os.environ.get("DATA_CONNECTIONS_DIR", "./data_connections"))
SQL_DIR    = BASE_DIR / "sql"
VECTOR_DIR = BASE_DIR / "vector"
SQL_DIR.mkdir(parents=True, exist_ok=True)
VECTOR_DIR.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()

# ─── Encryption (same scheme as scheduler_app_1.py) ────────────
_ENC_PREFIX = "enc:v1:"
_master_key_cache: Optional[bytes] = None


def _coerce_to_fernet_key(raw: str) -> bytes:
    try:
        Fernet(raw.encode())
        return raw.encode()
    except Exception:
        digest = hashlib.sha256(raw.encode()).digest()
        return base64.urlsafe_b64encode(digest)


def _fetch_master_key_from_aws_secrets() -> str:
    import boto3
    secret_id = os.environ.get("SCHED_KEY_AWS_SECRET_ID", "scheduler/master-key")
    region    = os.environ.get("SCHED_KEY_AWS_REGION", "")
    client    = boto3.client("secretsmanager", **({"region_name": region} if region else {}))
    return client.get_secret_value(SecretId=secret_id).get("SecretString", "")


def _fetch_master_key_from_azure_kv() -> str:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
    vault_url   = os.environ.get("SCHED_KEY_AZURE_VAULT_URL", "")
    secret_name = os.environ.get("SCHED_KEY_AZURE_SECRET_NAME", "scheduler-master-key")
    if not vault_url:
        raise EnvironmentError("SCHED_KEY_AZURE_VAULT_URL must be set when SCHED_KEY_PROVIDER=azure_kv.")
    client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
    return client.get_secret(secret_name).value


def _fetch_master_key_from_gcp_secrets() -> str:
    from google.cloud import secretmanager
    secret_path = os.environ.get("SCHED_KEY_GCP_SECRET_NAME", "")
    if not secret_path:
        raise EnvironmentError("SCHED_KEY_GCP_SECRET_NAME must be set when SCHED_KEY_PROVIDER=gcp_secrets.")
    client = secretmanager.SecretManagerServiceClient()
    return client.access_secret_version(name=secret_path).payload.data.decode("utf-8")


def _fetch_master_key_from_vault() -> str:
    import hvac
    vault_addr  = os.environ.get("VAULT_ADDR", "")
    vault_token = os.environ.get("VAULT_TOKEN", "")
    secret_path = os.environ.get("SCHED_KEY_VAULT_PATH", "secret/data/scheduler/master-key")
    if not vault_addr:
        raise EnvironmentError("VAULT_ADDR must be set when SCHED_KEY_PROVIDER=vault.")
    client = hvac.Client(url=vault_addr, token=vault_token or None)
    resp = client.secrets.kv.v2.read_secret_version(path=secret_path.split("/data/", 1)[-1])
    return resp["data"]["data"]["value"]


def _fetch_master_key_from_file() -> str:
    import platform
    path = os.environ.get("SCHED_KEY_FILE_PATH", "")
    if not path:
        raise EnvironmentError("SCHED_KEY_FILE_PATH must be set when SCHED_KEY_PROVIDER=file.")
    p = Path(path)
    if not p.exists():
        raise EnvironmentError(f"Key file not found at {path!r}.")
    if platform.system() != "Windows":
        mode = p.stat().st_mode & 0o777
        if mode & 0o077:
            raise EnvironmentError(f"Key file {path!r} has overly permissive mode {oct(mode)}. Run: chmod 600 {path}")
    return p.read_text().strip()


_KEY_PROVIDERS = {
    "aws_secrets": _fetch_master_key_from_aws_secrets,
    "azure_kv":    _fetch_master_key_from_azure_kv,
    "gcp_secrets": _fetch_master_key_from_gcp_secrets,
    "vault":       _fetch_master_key_from_vault,
    "file":        _fetch_master_key_from_file,
}


def _get_master_key() -> bytes:
    global _master_key_cache
    if _master_key_cache is not None:
        return _master_key_cache
    provider = os.environ.get("SCHED_KEY_PROVIDER", "env").lower()
    if provider == "env":
        raw = os.environ.get("SCHED_MASTER_KEY", "")
        if not raw:
            raise EnvironmentError(
                "SCHED_MASTER_KEY is not set. Generate one with: "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\" "
                "and add it to your .env file."
            )
    elif provider in _KEY_PROVIDERS:
        raw = _KEY_PROVIDERS[provider]()
        if not raw:
            raise EnvironmentError(f"Master key fetched from provider '{provider}' was empty.")
    else:
        raise EnvironmentError(f"Unknown SCHED_KEY_PROVIDER={provider!r}.")
    _master_key_cache = _coerce_to_fernet_key(raw)
    return _master_key_cache


def _encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _ENC_PREFIX + Fernet(_get_master_key()).encrypt(plaintext.encode()).decode()


def _decrypt(stored: str) -> str:
    if not stored or not stored.startswith(_ENC_PREFIX):
        return stored or ""
    try:
        return Fernet(_get_master_key()).decrypt(stored[len(_ENC_PREFIX):].encode()).decode()
    except InvalidToken:
        raise ValueError(
            "Could not decrypt a stored connection secret — SCHED_MASTER_KEY may "
            "have changed since this profile was saved, or the value is corrupted."
        )


_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _slugify(name: str) -> str:
    s = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return s or uuid.uuid4().hex[:8]


# ══════════════════════════════════════════════════════════════
#  SQL connection profiles
# ══════════════════════════════════════════════════════════════
#
# A profile stores a raw SQLAlchemy/pyodbc-style connection string
# exactly as the user typed it, e.g.:
#   mssql+pyodbc://user:pass@server/db?driver=ODBC+Driver+17+for+SQL+Server
#   Server=myserver.database.windows.net;Database=mydb;UID=user;PWD=pass
#
# It is stored encrypted; only decrypted in memory when resolved.

def save_sql_profile(
    name: str,
    connection_string: str,
    db_type: str = "mssql",
    profile_id: Optional[str] = None,
) -> str:
    """Create or update a SQL profile. Returns the profile id."""
    pid = profile_id or f"{_slugify(name)}-{uuid.uuid4().hex[:6]}"
    payload = {
        "id": pid,
        "name": name,
        "db_type": db_type,
        "connection_string": _encrypt(connection_string),
    }
    with _lock:
        with open(SQL_DIR / f"{pid}.json", "w") as f:
            json.dump(payload, f, indent=2)
    return pid


def list_sql_profiles() -> List[Dict[str, Any]]:
    """Returns profiles WITHOUT decrypting secrets — safe for UI display
    (name/id/db_type only, plus a masked preview of the connection string)."""
    out = []
    with _lock:
        for fp in sorted(SQL_DIR.glob("*.json")):
            try:
                with open(fp) as f:
                    p = json.load(f)
                out.append({
                    "id": p.get("id", fp.stem),
                    "name": p.get("name", fp.stem),
                    "db_type": p.get("db_type", "mssql"),
                    "masked": _mask_conn_str(_safe_decrypt(p.get("connection_string", ""))),
                })
            except Exception:
                continue
    return out


def _safe_decrypt(value: str) -> str:
    try:
        return _decrypt(value)
    except Exception:
        return ""


def _mask_conn_str(conn: str) -> str:
    if not conn:
        return ""
    # Hide password= / pwd= values, keep everything else so users can
    # recognize which server/db a profile points to.
    masked = re.sub(r"(?i)(pwd|password)=([^;]*)", r"\1=****", conn)
    if "://" in masked:
        masked = re.sub(r"://([^:]+):([^@]+)@", r"://\1:****@", masked)
    return masked


def get_sql_profile(profile_id: str) -> Optional[Dict[str, Any]]:
    """Returns the profile with the connection string DECRYPTED. Internal
    use / server-side only — never send this dict back to the browser."""
    fp = SQL_DIR / f"{profile_id}.json"
    if not fp.exists():
        return None
    with _lock:
        with open(fp) as f:
            p = json.load(f)
    p["connection_string"] = _decrypt(p.get("connection_string", ""))
    return p


def delete_sql_profile(profile_id: str) -> None:
    (SQL_DIR / f"{profile_id}.json").unlink(missing_ok=True)


def resolve_sql_connection_string(profile_id: Optional[str] = None) -> str:
    """
    Returns the connection string to actually use:
      - if profile_id given and found → that profile's decrypted string
      - else → falls back to SQL_CONNECTION_STRING env var (legacy default)
    """
    if profile_id:
        profile = get_sql_profile(profile_id)
        if profile and profile.get("connection_string"):
            return profile["connection_string"]
    return os.environ.get("SQL_CONNECTION_STRING", "")


# ══════════════════════════════════════════════════════════════
#  Vector store profiles (LanceDB location)
# ══════════════════════════════════════════════════════════════

def save_vector_profile(
    name: str,
    db_path: str,
    collection_name: str = "file_ingestion",
    profile_id: Optional[str] = None,
) -> str:
    """Create or update a vector-store profile pointing at a user-chosen
    LanceDB directory (local path, mounted network drive, etc.)."""
    pid = profile_id or f"{_slugify(name)}-{uuid.uuid4().hex[:6]}"
    # Make sure the target directory exists / is creatable up front, so
    # a bad path is caught here rather than deep inside an ingestion run.
    Path(db_path).mkdir(parents=True, exist_ok=True)
    payload = {
        "id": pid,
        "name": name,
        "db_path": db_path,
        "collection_name": collection_name,
    }
    with _lock:
        with open(VECTOR_DIR / f"{pid}.json", "w") as f:
            json.dump(payload, f, indent=2)
    return pid


def list_vector_profiles() -> List[Dict[str, Any]]:
    out = []
    with _lock:
        for fp in sorted(VECTOR_DIR.glob("*.json")):
            try:
                with open(fp) as f:
                    p = json.load(f)
                out.append(p)
            except Exception:
                continue
    return out


def get_vector_profile(profile_id: str) -> Optional[Dict[str, Any]]:
    fp = VECTOR_DIR / f"{profile_id}.json"
    if not fp.exists():
        return None
    with _lock:
        with open(fp) as f:
            return json.load(f)


def delete_vector_profile(profile_id: str) -> None:
    (VECTOR_DIR / f"{profile_id}.json").unlink(missing_ok=True)


def resolve_vector_store(profile_id: Optional[str] = None) -> Dict[str, str]:
    """
    Returns {"db_path": ..., "collection_name": ...} to actually use:
      - if profile_id given and found → that profile's path/collection
      - else → legacy env-var defaults (VECTOR_DB_PATH / VECTOR_COLLECTION_NAME)
    """
    if profile_id:
        profile = get_vector_profile(profile_id)
        if profile:
            return {
                "db_path": profile.get("db_path") or os.environ.get("VECTOR_DB_PATH", "./lance_db"),
                "collection_name": profile.get("collection_name") or os.environ.get("VECTOR_COLLECTION_NAME", "file_ingestion"),
            }
    return {
        "db_path": os.environ.get("VECTOR_DB_PATH", "./lance_db"),
        "collection_name": os.environ.get("VECTOR_COLLECTION_NAME", "file_ingestion"),
    }


# ══════════════════════════════════════════════════════════════
#  Connectivity test helpers (used by the "Test connection" UI button)
# ══════════════════════════════════════════════════════════════

def _to_sqlalchemy_url(conn_str: str) -> str:
    """
    Converts the semicolon "Server=...;Database=...;UID=...;PWD=..." form
    (what the SQL Server "Add a database" form in Data Connections builds,
    and what sql_ingestion.py's ezcoworker-prompt path historically used)
    into a real mssql+pyodbc:// URL that SQLAlchemy's create_engine() can
    actually parse. If conn_str is already a URL (postgres/mysql profiles,
    or someone pasted a URL via "advanced" mode), it's passed through
    unchanged. Kept in sync with sql_ingestion.py's identical helper —
    duplicated rather than imported to keep this module dependency-free
    (see module docstring).
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


def test_sql_connection(connection_string: str) -> Dict[str, Any]:
    """Tries a lightweight SELECT 1 against the given connection string."""
    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(_to_sqlalchemy_url(connection_string), pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True, "error": None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def test_vector_path(db_path: str) -> Dict[str, Any]:
    """Confirms the path is writable and LanceDB can open/create it."""
    try:
        import lancedb
        Path(db_path).mkdir(parents=True, exist_ok=True)
        db = lancedb.connect(db_path)
        tables = db.table_names()
        return {"ok": True, "error": None, "existing_tables": tables}
    except Exception as e:
        return {"ok": False, "error": str(e)}
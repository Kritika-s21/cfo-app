"""
content_extractor.py
--------------------
Extracts metadata + raw text content from files on Azure Blob Storage,
local disk, GCS, or S3.

Supported formats: PDF, XLSX/XLS, CSV, DOCX/DOC, TXT, JSON, JSONL,
                   MD, LOG, and generic binary (base64).

Returns a dict with keys:
  metadata  — file-level info (name, size, type, source, timestamps, …)
  content   — extracted text (str)
  content_preview — first 500 chars of content
  extraction_method — how content was extracted
  error     — None on success, error string on failure
"""

from __future__ import annotations

import io
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("agent.extractor")


# ─── Helpers ───────────────────────────────────────────────────

def _size_label(n: int) -> str:
    if n < 1024:        return f"{n} B"
    if n < 1024**2:     return f"{n/1024:.1f} KB"
    if n < 1024**3:     return f"{n/1024**2:.1f} MB"
    return f"{n/1024**3:.2f} GB"


def _ext(name: str) -> str:
    return Path(name).suffix.lower()


# ─── Per-format extractors ─────────────────────────────────────

def _extract_pdf(data: bytes) -> Tuple[str, str]:
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        text = "\n".join(pages).strip()
        return text, "pdfplumber"
    except ImportError:
        pass
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(data))
        text = "\n".join(p.extract_text() or "" for p in reader.pages).strip()
        return text, "pypdf"
    except ImportError:
        pass
    return "[PDF extraction requires pdfplumber or pypdf]", "none"


def _extract_xlsx(data: bytes) -> Tuple[str, str]:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        lines = []
        # The "=== Sheet: X ===" marker only earns its place when there's
        # more than one sheet to disambiguate — for the common single-sheet
        # workbook it was just noise sitting in front of every row of
        # extracted_content (and therefore content_preview, and anything
        # downstream that echoes extracted_content verbatim).
        multi_sheet = len(wb.sheetnames) > 1
        for sheet in wb.sheetnames:
            ws = wb[sheet]
            if multi_sheet:
                lines.append(f"=== Sheet: {sheet} ===")
            for row in ws.iter_rows(values_only=True):
                row_str = "\t".join(str(c) if c is not None else "" for c in row)
                if row_str.strip():
                    lines.append(row_str)
        return "\n".join(lines), "openpyxl"
    except ImportError:
        pass
    return "[XLSX extraction requires openpyxl]", "none"


def _extract_csv(data: bytes) -> Tuple[str, str]:
    try:
        import pandas as pd
        df = pd.read_csv(io.BytesIO(data), nrows=1000)
        return df.to_csv(index=False), "pandas"
    except Exception:
        try:
            return data.decode("utf-8", errors="replace"), "raw"
        except Exception as e:
            return f"[CSV read error: {e}]", "none"


def _extract_docx(data: bytes) -> Tuple[str, str]:
    try:
        import docx
        doc = docx.Document(io.BytesIO(data))
        text = "\n".join(p.text for p in doc.paragraphs)
        return text.strip(), "python-docx"
    except ImportError:
        pass
    return "[DOCX extraction requires python-docx]", "none"


def _extract_text(data: bytes) -> Tuple[str, str]:
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return data.decode(enc), "text"
        except Exception:
            continue
    return data.decode("utf-8", errors="replace"), "text"


def _extract_json(data: bytes) -> Tuple[str, str]:
    try:
        obj = json.loads(data)
        return json.dumps(obj, indent=2, default=str), "json"
    except Exception:
        return data.decode("utf-8", errors="replace"), "text"


EXT_MAP = {
    ".pdf":  _extract_pdf,
    ".xlsx": _extract_xlsx,
    ".xls":  _extract_xlsx,
    ".csv":  _extract_csv,
    ".tsv":  _extract_csv,
    ".docx": _extract_docx,
    ".doc":  _extract_docx,
    ".txt":  _extract_text,
    ".md":   _extract_text,
    ".log":  _extract_text,
    ".json": _extract_json,
    ".jsonl": _extract_json,
    ".xml":  _extract_text,
    ".html": _extract_text,
    ".htm":  _extract_text,
}


# ─── Blob downloaders ──────────────────────────────────────────

def _resolve_env_ref(value: str) -> str:
    """A value of '$SOME_VAR' means 'look up SOME_VAR in the environment'.
    Anything else is treated as the literal secret value."""
    if value and value.startswith("$"):
        return os.environ.get(value[1:], "")
    return value


def _resolve_secret(value: str) -> str:
    """Universal resolver, mirrors sources.py / scheduler_app.py's scheme so
    credentials stored via the Streamlit Sources UI (Fernet-encrypted) work
    here too, in addition to the extractor's existing $VAR / literal forms:

      enc:v1:<b64>  — Fernet-encrypted with SCHED_MASTER_KEY env var
      $VAR_NAME     — read from environment variable
      anything else — returned as-is (plain literal / already decrypted)
    """
    if not value:
        return ""
    if value.startswith("enc:v1:"):
        master_key = os.environ.get("SCHED_MASTER_KEY", "")
        if not master_key:
            raise RuntimeError(
                "Credential is Fernet-encrypted (enc:v1:...) but SCHED_MASTER_KEY "
                "env var is not set in this process — the extractor needs the same "
                "SCHED_MASTER_KEY as the scheduler app to decrypt stored credentials."
            )
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            raise ImportError("cryptography not installed. Run: pip install cryptography")
        f = Fernet(master_key.encode() if isinstance(master_key, str) else master_key)
        return f.decrypt(value[len("enc:v1:"):].encode()).decode()
    if value.startswith("$"):
        return os.environ.get(value[1:], "")
    return value


def _resolve_azure_client(creds_cfg: Dict):
    """Mirrors scheduler_app.py's _resolve_azure_client so the extractor
    supports the same per-source auth methods the scheduler UI configures:
    conn_str, acc_key, sas_url, env_var, default_az."""
    from azure.storage.blob import BlobServiceClient

    method = creds_cfg.get("auth_method", "env_var")

    if method == "conn_str":
        cs = _resolve_env_ref(creds_cfg.get("connection_string_ref", ""))
        if not cs:
            raise ValueError(
                "Azure connection string is empty. Set credentials.connection_string_ref "
                "on the source, or set AZURE_STORAGE_CONNECTION_STRING in the environment."
            )
        return BlobServiceClient.from_connection_string(cs)

    elif method == "acc_key":
        account_name = creds_cfg.get("account_name", "")
        account_key  = _resolve_env_ref(creds_cfg.get("account_key_ref", ""))
        if not account_name or not account_key:
            raise ValueError("Azure account name or key missing.")
        return BlobServiceClient(
            account_url=f"https://{account_name}.blob.core.windows.net",
            credential=account_key,
        )

    elif method == "sas_url":
        sas_url = _resolve_env_ref(creds_cfg.get("sas_url_ref", ""))
        if not sas_url:
            raise ValueError("Azure SAS URL is empty.")
        return BlobServiceClient(account_url=sas_url)

    elif method == "default_az":
        from azure.identity import DefaultAzureCredential
        account_name = creds_cfg.get("account_name", "")
        if not account_name:
            raise ValueError("Azure: account_name required for DefaultAzureCredential.")
        return BlobServiceClient(
            account_url=f"https://{account_name}.blob.core.windows.net",
            credential=DefaultAzureCredential(),
        )

    else:  # "env_var" (default) — also the backward-compatible fallback
        conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
        if not conn_str:
            raise ValueError(
                "Azure connection string is empty. Set credentials.connection_string_ref "
                "on the source, or set AZURE_STORAGE_CONNECTION_STRING in the environment."
            )
        return BlobServiceClient.from_connection_string(conn_str)


def _download_azure(file_info: Dict) -> bytes:
    creds_cfg = file_info.get("credentials", {}) or {}
    client    = _resolve_azure_client(creds_cfg)
    container = file_info.get("container", "")
    blob_name = file_info.get("blob_name", "")
    cc = client.get_container_client(container)
    data = cc.download_blob(blob_name).readall()
    return data


def _resolve_gcs_client(creds_cfg: Dict):
    from google.cloud import storage as gcs

    method = creds_cfg.get("auth_method", "adc")
    if method == "sa_file":
        from google.oauth2 import service_account
        sa_path = creds_cfg.get("credentials_path", "")
        if not sa_path or not os.path.exists(sa_path):
            raise FileNotFoundError(f"GCS SA file not found: {sa_path!r}")
        return gcs.Client(credentials=service_account.Credentials.from_service_account_file(sa_path))
    elif method == "env_var":
        env_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
        if env_path and os.path.exists(env_path):
            return gcs.Client()
        raise EnvironmentError("GOOGLE_APPLICATION_CREDENTIALS not set or path missing.")
    return gcs.Client()


def _download_gcs(file_info: Dict) -> bytes:
    creds_cfg = file_info.get("credentials", {}) or {}
    client = _resolve_gcs_client(creds_cfg)
    bucket = client.bucket(file_info.get("bucket", ""))
    blob   = bucket.blob(file_info.get("blob_name", ""))
    return blob.download_as_bytes()


def _resolve_s3_client(creds_cfg: Dict):
    import boto3

    method = creds_cfg.get("auth_method", "env_var")
    region = creds_cfg.get("region", "")
    kwargs: Dict[str, Any] = {}
    if region:
        kwargs["region_name"] = region

    if method == "access_key":
        access_key  = _resolve_env_ref(creds_cfg.get("aws_access_key_id_ref", ""))
        secret_key  = _resolve_env_ref(creds_cfg.get("aws_secret_access_key_ref", ""))
        session_tok = _resolve_env_ref(creds_cfg.get("aws_session_token_ref", ""))
        if not access_key or not secret_key:
            raise ValueError("AWS access key / secret key empty.")
        kwargs.update(aws_access_key_id=access_key, aws_secret_access_key=secret_key)
        if session_tok:
            kwargs["aws_session_token"] = session_tok
        return boto3.client("s3", **kwargs)

    elif method == "iam_role":
        role_arn = creds_cfg.get("role_arn", "")
        if not role_arn:
            raise ValueError("IAM Role ARN required.")
        sts = boto3.client("sts", **kwargs)
        cr  = sts.assume_role(RoleArn=role_arn, RoleSessionName="ContentExtractor")["Credentials"]
        kwargs.update(
            aws_access_key_id=cr["AccessKeyId"],
            aws_secret_access_key=cr["SecretAccessKey"],
            aws_session_token=cr["SessionToken"],
        )
        return boto3.client("s3", **kwargs)

    return boto3.client("s3", **kwargs)  # "env_var" / default — relies on default credential chain


def _download_s3(file_info: Dict) -> bytes:
    creds_cfg = file_info.get("credentials", {}) or {}
    s3  = _resolve_s3_client(creds_cfg)
    obj = s3.get_object(Bucket=file_info.get("bucket", ""), Key=file_info.get("s3_key", ""))
    return obj["Body"].read()


def _download_local(file_info: Dict) -> bytes:
    path = file_info.get("path", "")
    with open(path, "rb") as f:
        return f.read()


def _resolve_gdrive_service(creds_cfg: Dict):
    """Mirrors scheduler_app.py's _resolve_gdrive_service / sources.GoogleDriveSource
    so the extractor supports the same auth methods the scheduler UI configures
    for gdrive sources: service_account (SA JSON, via credentials_path or an
    already-decrypted sa_json string) and oauth (Application Default Credentials)."""
    from googleapiclient.discovery import build as build_drive

    _GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
    method = creds_cfg.get("auth_method", "service_account")

    if method == "service_account":
        from google.oauth2 import service_account

        sa_path = creds_cfg.get("credentials_path", "")
        sa_json = _resolve_secret(creds_cfg.get("sa_json", "") or creds_cfg.get("sa_json_encrypted", ""))
        if sa_path and os.path.exists(sa_path):
            creds = service_account.Credentials.from_service_account_file(sa_path, scopes=_GDRIVE_SCOPES)
        elif sa_json:
            info = json.loads(sa_json)
            creds = service_account.Credentials.from_service_account_info(info, scopes=_GDRIVE_SCOPES)
        else:
            raise ValueError(
                "Google Drive: no service-account credentials resolved. Expected "
                "credentials.credentials_path (SA file on disk), or credentials.sa_json / "
                "credentials.sa_json_encrypted (plain JSON string or enc:v1:... Fernet blob, "
                "requires SCHED_MASTER_KEY) on this file_info."
            )
        return build_drive("drive", "v3", credentials=creds, cache_discovery=False)

    # "oauth" -> Application Default Credentials (server's own Google identity)
    import google.auth
    adc_creds, _ = google.auth.default(scopes=_GDRIVE_SCOPES)
    return build_drive("drive", "v3", credentials=adc_creds, cache_discovery=False)


def _download_gdrive(file_info: Dict) -> bytes:
    creds_cfg = file_info.get("credentials", {}) or {}
    service   = _resolve_gdrive_service(creds_cfg)
    file_id   = file_info.get("file_id", "")

    if not file_id:
        # Fall back to parsing it out of the gdrive://<folder_id>/<file_id> path,
        # since older scan records may only carry "path".
        path = file_info.get("path", "")
        if path.startswith("gdrive://"):
            file_id = path.rsplit("/", 1)[-1]
    if not file_id:
        raise ValueError(f"Google Drive: no file_id available to download for {file_info.get('file_name')!r}")

    import googleapiclient.http

    meta = service.files().get(fileId=file_id, fields="mimeType, name", supportsAllDrives=True).execute()
    mime = meta.get("mimeType", "")

    buf = io.BytesIO()
    if mime.startswith("application/vnd.google-apps."):
        # Native Google Docs/Sheets/Slides have no raw bytes — export instead.
        export_mime = {
            "application/vnd.google-apps.document":
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.google-apps.spreadsheet":
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.google-apps.presentation":
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }.get(mime, "text/plain")
        request = service.files().export_media(fileId=file_id, mimeType=export_mime)
    else:
        request = service.files().get_media(fileId=file_id, supportsAllDrives=True)

    downloader = googleapiclient.http.MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


DOWNLOADERS = {
    "azure":  _download_azure,
    "gcs":    _download_gcs,
    "gdrive": _download_gdrive,
    "s3":     _download_s3,
    "local":  _download_local,
}


# ─── Main entrypoint ───────────────────────────────────────────

def extract(file_info: Dict) -> Dict[str, Any]:
    """
    Given a file_info dict (from the scanner), download the file and
    extract metadata + content.

    Returns a dict:
      {
        "metadata": {...},
        "content": "...",
        "content_preview": "...",
        "extraction_method": "...",
        "error": None | "..."
      }
    """
    file_name   = file_info.get("file_name", "unknown")
    source_type = file_info.get("source_type", "local")
    ext         = _ext(file_name)

    meta: Dict[str, Any] = {
        "file_name":       file_name,
        "file_type":       ext,
        "file_size_bytes": file_info.get("file_size_bytes", 0),
        "file_size_human": _size_label(file_info.get("file_size_bytes", 0)),
        "source_type":     source_type,
        "source_id":       file_info.get("source_id", ""),
        "path":            file_info.get("path", ""),
        "last_modified":   file_info.get("last_modified", ""),
        "status":          file_info.get("status", "new"),
        "extracted_at":    datetime.now(timezone.utc).isoformat(),
        # extra cloud info if present
        "container":       file_info.get("container"),
        "blob_name":       file_info.get("blob_name"),
        "bucket":          file_info.get("bucket"),
        "s3_key":          file_info.get("s3_key"),
        "folder_id":       file_info.get("folder_id"),
        "file_id":         file_info.get("file_id"),
    }
    # remove None keys for cleanliness
    meta = {k: v for k, v in meta.items() if v is not None}

    # 1 — Download
    try:
        downloader = DOWNLOADERS.get(source_type, _download_local)
        raw_bytes  = downloader(file_info)
    except Exception as e:
        logger.error("Download failed for %s: %s", file_name, e)
        return {
            "metadata": meta,
            "content": "",
            "content_preview": "",
            "extraction_method": "none",
            "error": f"Download error: {e}",
        }

    meta["file_size_bytes"] = len(raw_bytes)          # update with actual size
    meta["file_size_human"] = _size_label(len(raw_bytes))

    # 2 — Extract text
    # Google Drive files always go through the openpyxl (xlsx) extractor,
    # regardless of their detected extension — Drive's export/native
    # formats don't reliably map onto EXT_MAP's extension lookup (native
    # Google Docs/Sheets/Slides have no filesystem-style extension at
    # all), so this is a fixed choice for source_type == "gdrive" rather
    # than the normal ext-based dispatch used for other sources.
    if source_type == "gdrive":
        try:
            content, method = _extract_xlsx(raw_bytes)
        except Exception as e:
            logger.warning("openpyxl extraction error for %s: %s", file_name, e)
            content, method = f"[Extraction error: {e}]", "none"
    else:
        extractor = EXT_MAP.get(ext)
        if extractor:
            try:
                content, method = extractor(raw_bytes)
            except Exception as e:
                logger.warning("Extraction error for %s: %s", file_name, e)
                content, method = f"[Extraction error: {e}]", "none"
        else:
            # Unknown format — store as UTF-8 best-effort
            content  = raw_bytes.decode("utf-8", errors="replace")
            method   = "raw"

    return {
        "metadata": meta,
        "content":  content,
        "content_preview": content[:500],
        "extraction_method": method,
        "error": None,
    }
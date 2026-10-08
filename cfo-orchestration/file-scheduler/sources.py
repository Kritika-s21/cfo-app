"""
sources.py
----------
Storage-source connectors.  Each implements FileSource.

Connectors
----------
  LocalFolderSource   — local filesystem (recursive or flat)
  GCPSource            — Google Cloud Storage bucket/prefix
  GoogleDriveSource   — Google Drive folder (recursive, incl. Shared Drives)
  AzureBlobSource     — Azure Blob Storage container/prefix
  S3Source            — AWS S3 bucket/prefix

All do lazy imports so the scheduler can be installed without every
cloud SDK present — only import the SDK you actually use.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from scheduler_engine import FileSource, FileRecord


# ─────────────────────────────────────────────
#  Local Folder
# ─────────────────────────────────────────────

class LocalFolderSource(FileSource):
    """
    Scans a local directory (optionally recursive).

    Config keys
    -----------
    path        : str   — absolute or relative folder path (required)
    recursive   : bool  — descend into sub-folders (default True)
    pattern     : str   — glob pattern filter, e.g. "*.csv" (default "*")
    """

    def __init__(self, source_id: str, config: Dict[str, Any]):
        super().__init__(source_id, config)
        self._path      = Path(config["path"])
        self._recursive = config.get("recursive", True)
        self._pattern   = config.get("pattern", "*")

    def validate_config(self) -> None:
        if not self._path.exists():
            # Auto-create directory so upload-based local sources work on first use.
            # For genuine server paths that should already exist this preserves prior
            # behaviour (creation succeeds or fails with a clear error).
            try:
                self._path.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                raise ValueError(
                    f"LocalFolderSource: path does not exist and could not be created: "
                    f"{self._path} — {exc}")
        if not self._path.is_dir():
            raise ValueError(f"LocalFolderSource: path is not a directory: {self._path}")

    async def list_files(self) -> List[FileRecord]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._list_sync)

    def _list_sync(self) -> List[FileRecord]:
        records: List[FileRecord] = []
        glob = self._path.rglob if self._recursive else self._path.glob
        for p in glob(self._pattern):
            if p.is_file():
                stat = p.stat()
                records.append(FileRecord(
                    path          = str(p.resolve()),
                    source_type   = "local",
                    size_bytes    = stat.st_size,
                    last_modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    etag          = None,
                ))
        return records


# ─────────────────────────────────────────────
#  Google Cloud Storage (GCP)
# ─────────────────────────────────────────────

def _resolve_gcp_secret(value: Optional[str]) -> str:
    """
    Resolve a credential value to plaintext. Mirrors the scheme used
    elsewhere in the app (api_server.resolve_secret / scheduler_app's
    Fernet-based secret store) so a GCP source configured through the
    Streamlit UI works correctly when driven from here too:

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
                "GCPSource: credential is Fernet-encrypted (enc:v1:...) but "
                "SCHED_MASTER_KEY env var is not set.")
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            raise ImportError("cryptography not installed. Run: pip install cryptography")
        f = Fernet(master_key.encode() if isinstance(master_key, str) else master_key)
        return f.decrypt(value[len("enc:v1:"):].encode()).decode()
    if value.startswith("$"):
        return os.environ.get(value[1:], "")
    return value


# class GCPSource(FileSource):
#     """
#     Lists objects in a GCP (Google Cloud Storage) bucket/prefix.

#     Two credential shapes are supported, so a source configured through
#     the Streamlit scheduler UI *or* built directly via the API/config
#     file both work correctly:

#     1. Structured 'credentials' dict (matches what scheduler_app saves):
#          credentials.auth_method       : "service_account" | "oauth"
#          credentials.sa_json_encrypted : Fernet-encrypted (enc:v1:...) or
#                                           $VAR-referenced service-account
#                                           JSON string (used when
#                                           auth_method == "service_account")
#        "oauth" uses Application Default Credentials — the server's own
#        GCP identity (e.g. GOOGLE_APPLICATION_CREDENTIALS, GCE/GKE
#        workload identity) — no secret required.

#     2. Legacy flat keys, for direct/scripted use:
#          credentials_path : str — path to a service-account JSON file on
#                                    disk (falls back to ADC if omitted)

#     Other config keys
#     ------------------
#     bucket        : str  — GCP bucket name (required)
#     project_id    : str  — GCP project id override (optional; normally
#                             inferred from the service-account JSON / ADC)
#     prefix        : str  — object prefix/folder (default "")
#     pattern       : str  — fnmatch pattern filter (default "*")
#     """

#     def __init__(self, source_id: str, config: Dict[str, Any]):
#         super().__init__(source_id, config)

#     def validate_config(self) -> None:
#         if "bucket" not in self.config:
#             raise ValueError("GCPSource: 'bucket' is required in config")
#         creds = self.config.get("credentials") or {}
#         method = creds.get("auth_method", "service_account" if creds else None)
#         if method == "service_account" and not creds.get("sa_json_encrypted") \
#                 and not self.config.get("credentials_path"):
#             raise ValueError(
#                 "GCPSource: auth_method 'service_account' requires "
#                 "credentials.sa_json_encrypted (or legacy credentials_path).")

#     def _build_client(self):
#         try:
#             from google.cloud import storage as gcs
#             from google.oauth2 import service_account
#         except ImportError:
#             raise ImportError("google-cloud-storage not installed. Run: pip install google-cloud-storage")

#         creds_cfg = self.config.get("credentials") or {}
#         project_id = self.config.get("project_id") or creds_cfg.get("project_id")

#         # ── Structured credentials dict (Streamlit-managed sources) ──
#         if creds_cfg:
#             method = creds_cfg.get("auth_method", "service_account")
#             if method == "service_account":
#                 sa_json_str = _resolve_gcp_secret(creds_cfg.get("sa_json_encrypted", ""))
#                 if not sa_json_str:
#                     raise ValueError("GCPSource: no service-account JSON resolved for this source.")
#                 import json as _json
#                 info = _json.loads(sa_json_str)
#                 gcp_creds = service_account.Credentials.from_service_account_info(info)
#                 return gcs.Client(credentials=gcp_creds, project=project_id or info.get("project_id"))
#             # "oauth" -> Application Default Credentials (server's own GCP identity)
#             return gcs.Client(project=project_id)

#         # ── Legacy flat credentials_path (direct/scripted use) ──
#         cred_path = self.config.get("credentials_path")
#         if cred_path:
#             gcp_creds = service_account.Credentials.from_service_account_file(cred_path)
#             return gcs.Client(credentials=gcp_creds, project=project_id)

#         # ── No credentials supplied at all -> ADC ──
#         return gcs.Client(project=project_id)

#     async def list_files(self) -> List[FileRecord]:
#         loop = asyncio.get_event_loop()
#         return await loop.run_in_executor(None, self._list_sync)

#     def _list_sync(self) -> List[FileRecord]:
#         client = self._build_client()

#         import fnmatch
#         pattern = self.config.get("pattern", "*")
#         prefix  = self.config.get("prefix", "")
#         bucket  = client.bucket(self.config["bucket"])

#         records: List[FileRecord] = []
#         for blob in client.list_blobs(bucket, prefix=prefix):
#             if blob.name.endswith("/"):
#                 continue   # skip folder placeholder objects
#             name = blob.name[len(prefix):].lstrip("/")
#             if not fnmatch.fnmatch(name, pattern):
#                 continue
#             updated = blob.updated or blob.time_created
#             if updated and updated.tzinfo is None:
#                 updated = updated.replace(tzinfo=timezone.utc)
#             records.append(FileRecord(
#                 path          = f"gcp://{self.config['bucket']}/{blob.name}",
#                 source_type   = "gcp",
#                 size_bytes    = blob.size or 0,
#                 last_modified = updated or datetime.now(timezone.utc),
#                 etag          = blob.etag,
#             ))
#         return records


# ─────────────────────────────────────────────
#  Google Drive
# ─────────────────────────────────────────────

_DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


class GCPSource(FileSource):
    """
    Scans a Google Drive folder (optionally recursive), including files
    living in Shared Drives the service account has been granted access
    to (e.g. the "Data Pipeline" shared folder).

    Credentials use the same two shapes as GCPSource, so a service-account
    JSON already configured for GCS can be reused here as-is:

    1. Structured 'credentials' dict (matches what scheduler_app saves):
         credentials.auth_method       : "service_account" | "oauth"
         credentials.sa_json_encrypted : Fernet-encrypted (enc:v1:...) or
                                          $VAR-referenced service-account
                                          JSON string
       "oauth" uses Application Default Credentials — the server's own
       Google identity — no secret required (rarely useful for Drive
       unless the server itself is domain-joined / has delegated access).

    2. Legacy flat keys, for direct/scripted use:
         credentials_path : str — path to a service-account JSON file on
                                   disk (falls back to ADC if omitted)

    IMPORTANT: a bare service account cannot "see" a folder just because
    it exists in someone's My Drive — the folder (or the Shared Drive it
    lives in) must be explicitly shared with the service account's
    client_email (e.g. priyanka-j-ezdatamunch-com@...iam.gserviceaccount.com)
    as at least Viewer.

    Other config keys
    ------------------
    folder_id            : str  — Drive folder ID to scan (required).
                                   Found in the folder's URL:
                                   https://drive.google.com/drive/folders/<folder_id>
    recursive             : bool — descend into sub-folders (default True)
    include_shared_drives : bool — search across Shared Drives too
                                    (default True)
    pattern               : str  — fnmatch pattern filter on file name
                                    (default "*")
    export_google_formats  : bool — for native Google Docs/Sheets/Slides,
                                    record them using their Drive export
                                    mimeType sizing info where available
                                    (default True; they otherwise have no
                                    real byte size)
    """

    def __init__(self, source_id: str, config: Dict[str, Any]):
        super().__init__(source_id, config)

    def validate_config(self) -> None:
        if "folder_id" not in self.config:
            raise ValueError("GoogleDriveSource: 'folder_id' is required in config")
        creds = self.config.get("credentials") or {}
        method = creds.get("auth_method", "service_account" if creds else None)
        if method == "service_account" and not creds.get("sa_json_encrypted") \
                and not self.config.get("credentials_path"):
            raise ValueError(
                "GoogleDriveSource: auth_method 'service_account' requires "
                "credentials.sa_json_encrypted (or legacy credentials_path).")

    def _build_service(self):
        try:
            from googleapiclient.discovery import build as build_drive
            from google.oauth2 import service_account
            import google.auth
        except ImportError:
            raise ImportError(
                "google-api-python-client / google-auth not installed. Run: "
                "pip install google-api-python-client google-auth")

        creds_cfg = self.config.get("credentials") or {}

        # ── Structured credentials dict (Streamlit-managed sources) ──
        if creds_cfg:
            method = creds_cfg.get("auth_method", "service_account")
            if method == "service_account":
                sa_json_str = _resolve_gcp_secret(creds_cfg.get("sa_json_encrypted", ""))
                if not sa_json_str:
                    raise ValueError("GoogleDriveSource: no service-account JSON resolved for this source.")
                import json as _json
                info = _json.loads(sa_json_str)
                gcp_creds = service_account.Credentials.from_service_account_info(
                    info, scopes=_DRIVE_SCOPES)
                return build_drive("drive", "v3", credentials=gcp_creds, cache_discovery=False)
            # "oauth" -> Application Default Credentials (server's own identity)
            adc_creds, _ = google.auth.default(scopes=_DRIVE_SCOPES)
            return build_drive("drive", "v3", credentials=adc_creds, cache_discovery=False)

        # ── Legacy flat credentials_path (direct/scripted use) ──
        cred_path = self.config.get("credentials_path")
        if cred_path:
            gcp_creds = service_account.Credentials.from_service_account_file(
                cred_path, scopes=_DRIVE_SCOPES)
            return build_drive("drive", "v3", credentials=gcp_creds, cache_discovery=False)

        # ── No credentials supplied at all -> ADC ──
        adc_creds, _ = google.auth.default(scopes=_DRIVE_SCOPES)
        return build_drive("drive", "v3", credentials=adc_creds, cache_discovery=False)

    async def list_files(self) -> List[FileRecord]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._list_sync)

    def _list_sync(self) -> List[FileRecord]:
        import fnmatch

        service   = self._build_service()
        root_id   = self.config["folder_id"]
        recursive = self.config.get("recursive", True)
        include_shared = self.config.get("include_shared_drives", True)
        pattern   = self.config.get("pattern", "*")

        records: List[FileRecord] = []
        folders_to_scan = [root_id]
        seen_folders = set()

        while folders_to_scan:
            folder_id = folders_to_scan.pop()
            if folder_id in seen_folders:
                continue
            seen_folders.add(folder_id)

            page_token = None
            while True:
                resp = service.files().list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields=("nextPageToken, files(id, name, mimeType, size, "
                            "modifiedTime, md5Checksum, parents, webViewLink)"),
                    pageSize=1000,
                    pageToken=page_token,
                    supportsAllDrives=include_shared,
                    includeItemsFromAllDrives=include_shared,
                    corpora="allDrives" if include_shared else "user",
                ).execute()

                for item in resp.get("files", []):
                    if item.get("mimeType") == _DRIVE_FOLDER_MIME:
                        if recursive:
                            folders_to_scan.append(item["id"])
                        continue

                    name = item.get("name", "")
                    if pattern and pattern != "*" and not fnmatch.fnmatch(name, pattern):
                        continue

                    modified = item.get("modifiedTime")
                    if modified:
                        last_modified = datetime.fromisoformat(modified.replace("Z", "+00:00"))
                    else:
                        last_modified = datetime.now(timezone.utc)

                    # Native Google Docs/Sheets/Slides report no 'size'.
                    size_bytes = int(item["size"]) if item.get("size") else 0

                    records.append(FileRecord(
                        path          = f"gdrive://{folder_id}/{item['id']}",
                        source_type   = "gdrive",
                        size_bytes    = size_bytes,
                        last_modified = last_modified,
                        etag          = item.get("md5Checksum") or modified,
                        source_id     = self.source_id,
                        credentials   = self.config.get("credentials"),
                        folder_id     = folder_id,
                        file_id       = item["id"],
                        file_name     = name,
                    ))

                page_token = resp.get("nextPageToken")
                if not page_token:
                    break

        return records


# ─────────────────────────────────────────────
#  Azure Blob Storage
# ─────────────────────────────────────────────

class AzureBlobSource(FileSource):
    """
    Lists blobs in an Azure Blob Storage container/prefix.

    Config keys
    -----------
    connection_string   : str — Azure storage connection string (required
                                unless account_name + account_key given)
    account_name        : str — storage account name
    account_key         : str — storage account key
    sas_token           : str — SAS token (alternative auth)
    container           : str — container name (required)
    prefix              : str — blob prefix (default "")
    pattern             : str — fnmatch pattern filter (default "*")
    """

    def __init__(self, source_id: str, config: Dict[str, Any]):
        super().__init__(source_id, config)

    def validate_config(self) -> None:
        if "container" not in self.config:
            raise ValueError("AzureBlobSource: 'container' is required")
        has_connstr = "connection_string" in self.config
        has_keys    = "account_name" in self.config and "account_key" in self.config
        has_sas     = "account_name" in self.config and "sas_token" in self.config
        if not (has_connstr or has_keys or has_sas):
            raise ValueError(
                "AzureBlobSource: provide 'connection_string', "
                "or ('account_name' + 'account_key'), "
                "or ('account_name' + 'sas_token')"
            )

    async def list_files(self) -> List[FileRecord]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._list_sync)

    def _list_sync(self) -> List[FileRecord]:
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError:
            raise ImportError("azure-storage-blob not installed. Run: pip install azure-storage-blob")

        import fnmatch

        if "connection_string" in self.config:
            client = BlobServiceClient.from_connection_string(self.config["connection_string"])
        elif "sas_token" in self.config:
            account_url = f"https://{self.config['account_name']}.blob.core.windows.net"
            client = BlobServiceClient(account_url=account_url, credential=self.config["sas_token"])
        else:
            from azure.storage.blob import StorageSharedKeyCredential
            cred   = StorageSharedKeyCredential(self.config["account_name"], self.config["account_key"])
            client = BlobServiceClient(
                account_url=f"https://{self.config['account_name']}.blob.core.windows.net",
                credential=cred,
            )

        container = client.get_container_client(self.config["container"])
        prefix    = self.config.get("prefix", "")
        pattern   = self.config.get("pattern", "*")

        records: List[FileRecord] = []
        for blob in container.list_blobs(name_starts_with=prefix):
            name = blob.name[len(prefix):].lstrip("/")
            if not fnmatch.fnmatch(name, pattern):
                continue
            last_mod = blob.last_modified
            if last_mod and last_mod.tzinfo is None:
                last_mod = last_mod.replace(tzinfo=timezone.utc)
            records.append(FileRecord(
                path          = f"az://{self.config['container']}/{blob.name}",
                source_type   = "azure",
                size_bytes    = blob.size or 0,
                last_modified = last_mod or datetime.now(timezone.utc),
                etag          = blob.etag,
                source_id     = self.source_id,
                credentials   = self.config.get("credentials"),
                container     = self.config["container"],
                blob_name     = blob.name,
                file_name     = blob.name.rsplit("/", 1)[-1],
            ))
        return records


# ─────────────────────────────────────────────
#  AWS S3
# ─────────────────────────────────────────────

class S3Source(FileSource):
    """
    Lists objects in an S3 bucket/prefix.

    Config keys
    -----------
    bucket              : str  — S3 bucket name (required)
    prefix              : str  — key prefix (default "")
    aws_access_key_id   : str  — (optional; falls back to env/IAM)
    aws_secret_access_key: str — (optional)
    aws_session_token   : str  — (optional, for temporary creds)
    region_name         : str  — AWS region (default "us-east-1")
    endpoint_url        : str  — custom endpoint, e.g. MinIO (optional)
    pattern             : str  — fnmatch pattern filter (default "*")
    """

    def __init__(self, source_id: str, config: Dict[str, Any]):
        super().__init__(source_id, config)

    def validate_config(self) -> None:
        if "bucket" not in self.config:
            raise ValueError("S3Source: 'bucket' is required in config")

    async def list_files(self) -> List[FileRecord]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._list_sync)

    def _list_sync(self) -> List[FileRecord]:
        try:
            import boto3
        except ImportError:
            raise ImportError("boto3 not installed. Run: pip install boto3")

        import fnmatch

        session_kwargs: Dict[str, Any] = {}
        for k in ("aws_access_key_id", "aws_secret_access_key", "aws_session_token", "region_name"):
            if k in self.config:
                session_kwargs[k] = self.config[k]

        session    = boto3.Session(**session_kwargs)
        client_kw: Dict[str, Any] = {}
        if "endpoint_url" in self.config:
            client_kw["endpoint_url"] = self.config["endpoint_url"]

        s3      = session.client("s3", **client_kw)
        bucket  = self.config["bucket"]
        prefix  = self.config.get("prefix", "")
        pattern = self.config.get("pattern", "*")

        paginator = s3.get_paginator("list_objects_v2")
        records: List[FileRecord] = []

        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key  = obj["Key"]
                name = key[len(prefix):].lstrip("/")
                if not name or not fnmatch.fnmatch(name, pattern):
                    continue
                last_mod = obj["LastModified"]
                if last_mod.tzinfo is None:
                    last_mod = last_mod.replace(tzinfo=timezone.utc)
                records.append(FileRecord(
                    path          = f"s3://{bucket}/{key}",
                    source_type   = "s3",
                    size_bytes    = obj.get("Size", 0),
                    last_modified = last_mod,
                    etag          = obj.get("ETag", "").strip('"'),
                    source_id     = self.source_id,
                    credentials   = self.config.get("credentials"),
                    bucket        = bucket,
                    s3_key        = key,
                    file_name     = name.rsplit("/", 1)[-1],
                ))
        return records


# ─────────────────────────────────────────────
#  Source Factory
# ─────────────────────────────────────────────

SOURCE_REGISTRY: Dict[str, type] = {
    "local":  LocalFolderSource,
    "gcp":    GCPSource,
    "gdrive": GCPSource,   # frontend's source_type for this same Drive scanner
    "azure":  AzureBlobSource,
    "s3":     S3Source,
}


def build_source(source_type: str, source_id: str, config: Dict[str, Any]) -> FileSource:
    """Convenience factory used by the REST API and config loader."""
    cls = SOURCE_REGISTRY.get(source_type)
    if cls is None:
        raise ValueError(f"Unknown source type '{source_type}'. "
                         f"Valid types: {list(SOURCE_REGISTRY)}")
    return cls(source_id, config)
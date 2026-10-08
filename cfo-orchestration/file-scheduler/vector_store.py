"""
vector_store.py
---------------
Chunks + vectorises unstructured content and stores it in LanceDB.

Embedding model: configurable via EMBEDDING_PROVIDER env var.
  - "ollama"  → EzOllama /api/embeddings  (default, uses local server)

Table schema (LanceDB):
  id              — unique chunk id (file_path + chunk_index)
  file_name       — original file name
  source_type     — azure / local / gcs / s3
  source_id       — scheduler source id
  file_path       — full path / blob URL
  chunk_index     — 0-based chunk sequence number
  total_chunks    — total chunks for this file
  chunk_text      — raw text of the chunk
  vector          — embedding (dimension depends on model)
  ingested_at     — ISO timestamp
  data_type       — from classifier (pdf_report, docx, …)
  classification  — always "unstructured" here
  metadata_json   — JSON string of full metadata dict
"""

from __future__ import annotations

import json
import logging
import os
import pyodbc
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import create_engine, text

import numpy as np

import db_connections

logger = logging.getLogger("agent.vector_store")

# These module-level values are now only the fallback used when a caller
# doesn't pass an explicit vector_db_path/collection_name/sql_profile_id —
# every public function below can be pointed at a user-chosen LanceDB
# folder and/or SQL Server mirror on a per-call basis. See
# _resolve_vector_target() / _resolve_conn_str() below.
SQL_CONNECTION_STRING = os.environ.get("SQL_CONNECTION_STRING", "")
VECTOR_DB_PATH         = os.environ.get("VECTOR_DB_PATH",         "./lance_db")
VECTOR_COLLECTION_NAME = os.environ.get("VECTOR_COLLECTION_NAME", "file_ingestion")


def _resolve_vector_target(
    vector_db_path: Optional[str] = None,
    collection_name: Optional[str] = None,
    vector_profile_id: Optional[str] = None,
) -> Dict[str, str]:
    """Resolution order: explicit args > vector_profile_id (via
    db_connections.py) > legacy VECTOR_DB_PATH/VECTOR_COLLECTION_NAME env vars."""
    if vector_db_path or collection_name:
        resolved = db_connections.resolve_vector_store(vector_profile_id)
        return {
            "db_path": vector_db_path or resolved["db_path"],
            "collection_name": collection_name or resolved["collection_name"],
        }
    return db_connections.resolve_vector_store(vector_profile_id)


def _resolve_conn_str(
    sql_connection_string: Optional[str] = None,
    sql_profile_id: Optional[str] = None,
) -> str:
    if sql_connection_string:
        return sql_connection_string
    return db_connections.resolve_sql_connection_string(sql_profile_id) or SQL_CONNECTION_STRING or ""
CHUNK_SIZE             = int(os.environ.get("CHUNK_SIZE",  1000))
CHUNK_OVERLAP          = int(os.environ.get("CHUNK_OVERLAP", 200))

EMBEDDING_PROVIDER     = os.environ.get("EMBEDDING_PROVIDER", "ollama")

# Base URL e.g. https://ezollamab.ezdatamunch.com  (no /api/tags at the end)
EZOLLAMA_URL           = os.environ.get("EZOLLAMA_URL", "https://ezollamab.ezdatamunch.com")
OLLAMA_EMBEDDING_MODEL = os.environ.get("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text:latest")


# ─── Chunking ──────────────────────────────────────────────────

def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Split text into overlapping word-boundary chunks.

    Guards against CHUNK_OVERLAP >= CHUNK_SIZE (misconfigured env vars), which
    would make `i` advance by zero or go negative — stalling or looping
    far longer than intended and producing huge numbers of near-duplicate
    chunks (a likely contributor to multi-minute pipeline runs on one file).
    """
    if overlap >= chunk_size:
        logger.warning(
            "CHUNK_OVERLAP (%d) >= CHUNK_SIZE (%d) — clamping overlap to avoid a stalled chunk loop",
            overlap, chunk_size,
        )
        overlap = max(0, chunk_size - 1)

    words  = text.split()
    chunks = []
    i      = 0
    step   = max(1, chunk_size - overlap)
    MAX_CHUNKS = 500  # safety valve against pathologically large/degenerate input
    while i < len(words) and len(chunks) < MAX_CHUNKS:
        chunk = " ".join(words[i: i + chunk_size])
        chunks.append(chunk)
        i += step
    return chunks if chunks else [text]


# ─── Embedding ─────────────────────────────────────────────────

def _embed_ollama(
    texts: List[str],
    prefix: str = "search_document",
    max_retries: int = 3,
) -> List[Optional[List[float]]]:
    """Embed using Ollama /api/embeddings endpoint on the EzOllama server.

    Uses nomic-embed-text task prefixes for best retrieval quality:
      - prefix="search_document"  when storing/indexing chunks
      - prefix="search_query"     when embedding a search query

    Confirmed working: POST https://ezollamab.ezdatamunch.com/api/embeddings
      body: {"model": "nomic-embed-text:latest", "prompt": "<text>"}
      returns: {"embedding": [...]}

    Retries transient failures (connection errors, timeouts, 5xx) with
    exponential backoff — a single momentary hiccup on the Ollama server
    (model reload, brief OOM, etc.) previously failed the whole file
    immediately via raise_for_status(), discarding any chunks already
    embedded earlier in the same loop.

    Returns one entry per input text. An entry is None if that specific
    chunk failed after all retries — callers should filter these rather
    than assume every text produced a vector.
    """
    import time
    import requests as _requests

    url   = f"{EZOLLAMA_URL.rstrip('/')}/api/embeddings"  # ✅ /api/tags was wrong — this is the correct endpoint
    model = OLLAMA_EMBEDDING_MODEL
    vectors: List[Optional[List[float]]] = []

    for idx, text in enumerate(texts):
        vec = None
        last_err = None

        for attempt in range(max_retries):
            try:
                resp = _requests.post(
                    url,
                    json={"model": model, "prompt": f"{prefix}: {text}"},
                    timeout=300,
                )
                if resp.status_code >= 500:
                    # Transient server-side failure — worth retrying.
                    raise _requests.exceptions.HTTPError(
                        f"{resp.status_code} Server Error: {resp.reason} for url: {url}"
                    )
                resp.raise_for_status()  # 4xx — not retryable, fails immediately below

                data = resp.json()
                if "embedding" not in data:
                    raise ValueError(
                        f"Ollama /api/embeddings response missing 'embedding' key. "
                        f"Model '{model}' may not be loaded. Response: {data}"
                    )
                vec = data["embedding"]
                break

            except _requests.exceptions.HTTPError as e:
                last_err = e
                is_5xx = getattr(e.response, "status_code", 500) >= 500 if e.response is not None else True
                if not is_5xx:
                    break  # 4xx — retrying won't help (bad request, model missing, etc.)
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        "Embedding chunk %d/%d failed (attempt %d/%d): %s — retrying in %ds",
                        idx + 1, len(texts), attempt + 1, max_retries, e, wait,
                    )
                    time.sleep(wait)
            except (_requests.exceptions.ConnectionError, _requests.exceptions.Timeout) as e:
                last_err = e
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        "Embedding chunk %d/%d failed (attempt %d/%d): %s — retrying in %ds",
                        idx + 1, len(texts), attempt + 1, max_retries, e, wait,
                    )
                    time.sleep(wait)

        if vec is None:
            logger.error(
                "Embedding chunk %d/%d failed permanently after %d attempt(s): %s",
                idx + 1, len(texts), max_retries, last_err,
            )

        vectors.append(vec)

    return vectors


def embed_texts(texts: List[str], prefix: str = "search_document") -> List[Optional[List[float]]]:
    """Return list of float vectors, one per text.

    An entry is None if that specific text failed to embed after retries —
    callers must filter these out rather than assume every input produced
    a vector (see _embed_ollama).

    Args:
        texts:  list of strings to embed
        prefix: "search_document" for ingestion (default), "search_query" for search
    """
    if not texts:
        return []
    if EMBEDDING_PROVIDER == "ollama":
        return _embed_ollama(texts, prefix=prefix)
    raise ValueError(
        f"Unknown embedding provider: {EMBEDDING_PROVIDER!r}. "
        f"Set EMBEDDING_PROVIDER to 'ollama'."
    )


# ─── LanceDB helpers ───────────────────────────────────────────

def _get_table(vector_db_path: Optional[str] = None, collection_name: Optional[str] = None):
    import lancedb
    import pyarrow as pa
    import os

    db_path = vector_db_path or VECTOR_DB_PATH
    os.makedirs(db_path, exist_ok=True)

    db = lancedb.connect(db_path)

    logger.info("LanceDB path: %s", db_path)
    logger.info("Existing tables: %s", db.table_names())

    probe = embed_texts(["probe"])[0]
    if probe is None:
        raise RuntimeError(
            f"Could not determine embedding dimension — the embedding server "
            f"at {EZOLLAMA_URL} rejected a test probe. Check that it's reachable "
            f"and that model '{OLLAMA_EMBEDDING_MODEL}' is pulled/loaded."
        )
    dim = len(probe)

    schema = pa.schema([
        pa.field("id", pa.utf8()),
        pa.field("file_name", pa.utf8()),
        pa.field("source_type", pa.utf8()),
        pa.field("source_id", pa.utf8()),
        pa.field("file_path", pa.utf8()),
        pa.field("chunk_index", pa.int32()),
        pa.field("total_chunks", pa.int32()),
        pa.field("chunk_text", pa.utf8()),
        pa.field("vector", pa.list_(pa.float32(), dim)),
        pa.field("ingested_at", pa.utf8()),
        pa.field("data_type", pa.utf8()),
        pa.field("classification", pa.utf8()),
        pa.field("metadata_json", pa.utf8()),
    ])

    table_name = collection_name or VECTOR_COLLECTION_NAME
    try:
        return db.open_table(table_name)
    except Exception:
        logger.info("Creating LanceDB table: %s", table_name)

        return db.create_table(
            table_name,
            data=[],
            schema=schema,
            mode="create"
        )

def _insert_vectors_sql(rows, sql_connection_string: Optional[str] = None, sql_profile_id: Optional[str] = None):
    """
    Store vector chunks in SQL Server (a searchable mirror alongside LanceDB).
    Pass sql_connection_string / sql_profile_id to target a user-chosen
    database instead of the default SQL_CONNECTION_STRING.
    """
    conn_str = _resolve_conn_str(sql_connection_string, sql_profile_id)
    if not conn_str:
        logger.warning("No SQL connection configured — skipping SQL vector mirror")
        return

    engine = create_engine(conn_str)

    sql = text("""
    INSERT INTO dbo.FileVectors (
        id,
        file_name,
        source_type,
        source_id,
        file_path,
        chunk_index,
        total_chunks,
        chunk_text,
        vector_json,
        ingested_at,
        data_type,
        classification,
        metadata_json
    )
    VALUES (
        :id,
        :file_name,
        :source_type,
        :source_id,
        :file_path,
        :chunk_index,
        :total_chunks,
        :chunk_text,
        :vector_json,
        :ingested_at,
        :data_type,
        :classification,
        :metadata_json
    )
    """)

    with engine.begin() as conn:
        for row in rows:
            conn.execute(
                sql,
                {
                    "id": row["id"],
                    "file_name": row["file_name"],
                    "source_type": row["source_type"],
                    "source_id": row["source_id"],
                    "file_path": row["file_path"],
                    "chunk_index": row["chunk_index"],
                    "total_chunks": row["total_chunks"],
                    "chunk_text": row["chunk_text"],
                    "vector_json": json.dumps(row["vector"]),
                    "ingested_at": row["ingested_at"],
                    "data_type": row["data_type"],
                    "classification": row["classification"],
                    "metadata_json": row["metadata_json"],
                }
            )

    logger.info("Inserted %d vector rows into SQL Server", len(rows))

def get_sql_vector_count(sql_connection_string: Optional[str] = None, sql_profile_id: Optional[str] = None):
    try:
        import pyodbc

        conn_str = _resolve_conn_str(sql_connection_string, sql_profile_id)
        if not conn_str:
            return 0

        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM dbo.FileVectors")
        count = cursor.fetchone()[0]

        conn.close()

        return count

    except Exception:
        return 0

# ─── Public API ────────────────────────────────────────────────

def ingest_unstructured(
    metadata: Dict[str, Any],
    content: str,
    classification_result: Dict[str, Any],
    vector_db_path: Optional[str] = None,
    vector_collection_name: Optional[str] = None,
    vector_profile_id: Optional[str] = None,
    sql_connection_string: Optional[str] = None,
    sql_profile_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Chunk + embed + store unstructured file content in LanceDB.

    Pass vector_profile_id (an id registered on the "Data Connections"
    screen) or vector_db_path/vector_collection_name directly to store
    this file's vectors in a user-chosen location instead of the
    default ./lance_db folder. Same idea for sql_profile_id /
    sql_connection_string, which control where the SQL mirror row goes.

    Returns:
      {
        "success": bool,
        "chunks_stored": int,
        "collection": str,
        "vector_db_path": str,
        "error": None | str
      }
    """
    target = _resolve_vector_target(vector_db_path, vector_collection_name, vector_profile_id)
    db_path, collection = target["db_path"], target["collection_name"]

    file_path   = metadata.get("path", metadata.get("file_name", "unknown"))
    file_name   = metadata.get("file_name", "unknown")
    source_type = metadata.get("source_type", "local")
    source_id   = metadata.get("source_id", "")
    data_type   = classification_result.get("data_type", "unknown")
    now         = datetime.now(timezone.utc).isoformat()
    meta_json   = json.dumps(metadata, default=str)

    try:
        chunks = _chunk_text(content)
        logger.info("Chunked '%s' → %d chunks", file_name, len(chunks))

        # Embed with "search_document" prefix for storage
        vectors = embed_texts(chunks, prefix="search_document")

        table = _get_table(db_path, collection)

        rows = []
        for idx, (chunk, vec) in enumerate(zip(chunks, vectors)):
            if vec is None:
                logger.error("Skipping chunk %d due to failed embedding", idx)
                continue
            try:
                vec_floats = [float(v) for v in vec]
            except Exception:
                logger.exception("Invalid vector returned for chunk %d — skipping", idx)
                continue
            rows.append({
                "id":             f"{file_path}::{idx}",
                "file_name":      file_name,
                "source_type":    source_type,
                "source_id":      source_id,
                "file_path":      file_path,
                "chunk_index":    idx,
                "total_chunks":   len(chunks),
                "chunk_text":     chunk,
                "vector":         vec_floats,
                "ingested_at":    now,
                "data_type":      data_type,
                "classification": "unstructured",
                "metadata_json":  meta_json,
            })

        if not rows:
            msg = "All chunk embeddings failed — no vectors to store"
            logger.error(msg)
            return {
                "success": False,
                "chunks_stored": 0,
                "collection": collection,
                "vector_db_path": db_path,
                "error": msg,
            }

        import pyarrow as pa
        table.add(rows)

        try:
            _insert_vectors_sql(rows, sql_connection_string=sql_connection_string, sql_profile_id=sql_profile_id)
            logger.info(
                "Stored %d chunks for '%s' in LanceDB and SQL",
                len(rows),
                file_name,
            )
        except Exception as sql_error:
            logger.exception(
                "LanceDB insert succeeded but SQL insert failed: %s",
                sql_error,
            )

        return {
            "success": True,
            "chunks_stored": len(rows),
            "collection": collection,
            "vector_db_path": db_path,
            "error": None,
        }
    except Exception as e:
        logger.exception("Failed to ingest unstructured content: %s", e)
        return {
            "success": False,
            "chunks_stored": 0,
            "collection": collection,
            "vector_db_path": db_path,
            "error": str(e),
        }


def search(
    query: str,
    top_k: int = 5,
    filter_source: Optional[str] = None,
    vector_db_path: Optional[str] = None,
    vector_collection_name: Optional[str] = None,
    vector_profile_id: Optional[str] = None,
) -> List[Dict]:
    """Semantic search over the vector store. Returns top_k results.

    Pass vector_profile_id (or vector_db_path/vector_collection_name
    directly) to search a specific user-registered vector store instead
    of the default ./lance_db."""
    target = _resolve_vector_target(vector_db_path, vector_collection_name, vector_profile_id)
    try:
        # Use "search_query" prefix at query time for nomic-embed-text
        vec   = embed_texts([query], prefix="search_query")[0]
        table = _get_table(target["db_path"], target["collection_name"])
        q     = table.search(vec).limit(top_k)
        rows  = q.to_list()
        return rows
    except Exception as e:
        logger.error("Vector search failed: %s", e)
        return []


def get_stats(
    vector_db_path: Optional[str] = None,
    vector_collection_name: Optional[str] = None,
    vector_profile_id: Optional[str] = None,
    sql_connection_string: Optional[str] = None,
    sql_profile_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Pass vector_profile_id / sql_profile_id to report stats for a
    specific user-registered store instead of the default one."""
    target = _resolve_vector_target(vector_db_path, vector_collection_name, vector_profile_id)
    db_path, collection = target["db_path"], target["collection_name"]

    try:
        import lancedb

        db = lancedb.connect(db_path)

        if collection not in db.table_names():
            return {
                "total_chunks": 0,
                "sql_vector_rows": get_sql_vector_count(sql_connection_string, sql_profile_id),
                "sql_table": "dbo.FileVectors",
                "collection": collection,
                "vector_db_path": db_path,
                "status": "empty",
            }

        table = db.open_table(collection)

        return {
            "total_chunks": table.count_rows(),
            "sql_vector_rows": get_sql_vector_count(sql_connection_string, sql_profile_id),
            "sql_table": "dbo.FileVectors",
            "collection": collection,
            "vector_db_path": db_path,
            "status": "ok",
        }

    except Exception as e:
        return {
            "total_chunks": 0,
            "sql_vector_rows": 0,
            "sql_table": "dbo.FileVectors",
            "collection": collection,
            "vector_db_path": db_path,
            "status": "error",
            "error": str(e),
        }
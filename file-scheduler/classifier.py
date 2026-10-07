"""
classifier.py
-------------
Classifies files as Structured or Unstructured based on file extension.
No LLM call needed — extension-based classification is fast and reliable.

Returns a ClassificationResult dict:
  {
    "classification":  "structured" | "unstructured",
    "confidence":      0.0–1.0,
    "data_type":       "csv" | "xlsx" | "pdf_report" | …,
    "summary":         "Short human description",
    "schema_hint":     [],
    "chunk_strategy":  "row_based" | "semantic" | "page",
    "llm_raw":         "",
    "error":           None | "..."
  }
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("agent.classifier")

# ─── Extension maps ────────────────────────────────────────────

STRUCTURED_EXTENSIONS = {
    "csv":   ("csv",        "row_based",  "Comma-separated values file"),
    "tsv":   ("tsv",        "row_based",  "Tab-separated values file"),
    "xlsx":  ("xlsx",       "row_based",  "Excel spreadsheet"),
    "xls":   ("xls",        "row_based",  "Excel spreadsheet (legacy)"),
    "json":  ("json_array", "row_based",  "JSON data file"),
    "jsonl": ("jsonl",      "row_based",  "JSON Lines data file"),
    "xml":   ("xml",        "row_based",  "XML data file"),
    "parquet":("parquet",   "row_based",  "Parquet data file"),
    "db":    ("sqlite",     "row_based",  "SQLite database file"),
    "sql":   ("sql",        "row_based",  "SQL dump file"),
}

UNSTRUCTURED_EXTENSIONS = {
    "pdf":  ("pdf_report", "page",     "PDF document"),
    "docx": ("docx",       "semantic", "Word document"),
    "doc":  ("doc",        "semantic", "Word document (legacy)"),
    "txt":  ("txt",        "semantic", "Plain text file"),
    "md":   ("markdown",   "semantic", "Markdown document"),
    "html": ("html",       "semantic", "HTML document"),
    "htm":  ("html",       "semantic", "HTML document"),
    "pptx": ("pptx",       "page",     "PowerPoint presentation"),
    "ppt":  ("ppt",        "page",     "PowerPoint presentation (legacy)"),
    "eml":  ("email",      "semantic", "Email file"),
    "msg":  ("email",      "semantic", "Outlook email file"),
    "rtf":  ("rtf",        "semantic", "Rich text document"),
    "odt":  ("odt",        "semantic", "OpenDocument text file"),
}


def classify(metadata: Dict, content: str = "") -> Dict[str, Any]:
    """
    Classify a file using its extension from metadata.

    Args:
        metadata: must contain "file_type" or "file_name" key
        content:  ignored (kept for API compatibility)

    Returns:
        classification result dict
    """
    # Extract extension
    file_type = metadata.get("file_type", "")
    if not file_type:
        file_name = metadata.get("file_name", "")
        file_type = file_name.rsplit(".", 1)[-1] if "." in file_name else ""

    ext = file_type.lower().lstrip(".")

    # Check structured
    if ext in STRUCTURED_EXTENSIONS:
        data_type, chunk_strategy, summary = STRUCTURED_EXTENSIONS[ext]
        return {
            "classification": "structured",
            "confidence":     1.0,
            "data_type":      data_type,
            "summary":        summary,
            "schema_hint":    [],
            "chunk_strategy": chunk_strategy,
            "llm_raw":        "",
            "error":          None,
        }

    # Check unstructured
    if ext in UNSTRUCTURED_EXTENSIONS:
        data_type, chunk_strategy, summary = UNSTRUCTURED_EXTENSIONS[ext]
        return {
            "classification": "unstructured",
            "confidence":     1.0,
            "data_type":      data_type,
            "summary":        summary,
            "schema_hint":    [],
            "chunk_strategy": chunk_strategy,
            "llm_raw":        "",
            "error":          None,
        }

    # Unknown extension — default to unstructured
    logger.warning("Unknown file extension '%s', defaulting to unstructured", ext)
    return {
        "classification": "unstructured",
        "confidence":     0.5,
        "data_type":      ext or "unknown",
        "summary":        f"Unknown file type '.{ext}', treated as unstructured",
        "schema_hint":    [],
        "chunk_strategy": "semantic",
        "llm_raw":        "",
        "error":          None,
    }
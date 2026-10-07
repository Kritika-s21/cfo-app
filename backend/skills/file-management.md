---
id: file-management
name: File Management
description: Document I/O — parse uploaded files, detect periods, mask PII, prepare exports.
agents: ["*"]
ezcoworker_skills: [data-analyst]
priority: 4
triggers: [upload, file, excel, xlsx, csv, pdf, parse, extract, import, export, download, convert, attachment]
policies: [POL-006]
knowledge: [policy_docs]
outputs: [files, alerts]
---
## Purpose
Read and prepare documents for the other skills and package results for download.

## Steps
1. Identify file type, sheets/pages, header row and the date range covered.
2. Report row counts and obvious data-quality issues (blanks, duplicates, mixed currencies).
3. Mask PII (names, PAN, bank accounts, salary) in any output unless the user is authorised (POL-006).
4. For exports, list the file name and format in `files`.

## Output
JSON: `answer`, `analysis`, `file_summary` ({name, type, rows, period}), `files`, `policy_cited`, `alerts`.

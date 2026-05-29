# Redshift Query Editor V2 Toolkit

A Python CLI for managing notebooks and saved queries in Amazon Redshift Query Editor V2 — export, import, and crawl your workspace without a browser.

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## Overview

Query Editor V2 stores notebooks and saved queries in a private REST API (`sqlworkbench`). This toolkit authenticates to that API using the same AWS SigV4 signing the console uses, then exposes clean Python abstractions for the operations you actually need:

- **Crawl** the full folder hierarchy
- **Export** notebooks as `.ipynb` files and queries as `.sql` files
- **Import** notebooks from a local directory, recreating the original folder structure
- **Delete** individual queries or bulk-delete an entire folder

All without Selenium, Playwright, or browser automation of any kind.

---

## Requirements

- Python 3.11+
- AWS credentials with access to the `sqlworkbench` service (see [Credentials](#credentials))

```bash
pip install -r requirements.txt
```

---

## Credentials

Query Editor V2 uses temporary session credentials tied to the browser identity. Standard long-lived IAM credentials may not have a root folder initialised in Query Editor V2.

**Recommended:** extract credentials from an active browser session.

1. Open the AWS Console and navigate to Redshift Query Editor V2.
2. Open DevTools → Network → filter for `tb/creds`.
3. Copy the response values into `.env` at the project root:

```env
AWS_ACCESS_KEY_ID=ASIA...
AWS_SECRET_ACCESS_KEY=...
AWS_SESSION_TOKEN=...
AWS_DEFAULT_REGION=ap-south-1
```

The `.env` file is loaded automatically at startup. It is gitignored and never committed.

> Temporary credentials expire in approximately one hour. Re-export from the browser when requests return 403.

Copy `.env.example` to get started:

```bash
cp .env.example .env
```

---

## Project Structure

```
.
├── main.py                    # CLI entry point
├── src/
│   ├── aws_cli.py             # SigV4-authenticated HTTP client
│   ├── crawler.py             # Folder and resource traversal
│   ├── exporter.py            # Notebook export (.ipynb)
│   ├── query_exporter.py      # Query export (.sql)
│   ├── importer.py            # Notebook import with folder recreation
│   ├── models.py              # Folder, Notebook, Query dataclasses
│   └── renderer.py            # Terminal tree + JSON export
├── tests/                     # pytest test suite (90 tests, no network required)
│   ├── test_models.py
│   ├── test_crawler.py
│   ├── test_exporter.py
│   ├── test_importer.py
│   └── test_renderer.py
├── storage/                   # Default output directory (gitignored)
│   └── exports/
├── requirements.txt
├── pyproject.toml
└── .env.example
```

---

## Usage

All commands accept `--region` (default: `ap-south-1` or `$AWS_DEFAULT_REGION`) and `--debug` for verbose output.

### Crawl and display the hierarchy

```bash
# Notebooks
python -m main

# Saved queries
python -m main --type query

# Both
python -m main --type all
```

```
📁 Query Editor V2 — Notebooks
├── 📁 Analytics
│   ├── 📁 Q1
│   │   ├── 📄 Revenue Validation
│   │   └── 📄 Cost Analysis
│   └── 📁 Q2
│       └── 📄 Budget Report
└── 📁 Archive
    └── 📄 Legacy Notebook

────────────────────────────────────────
Statistics
────────────────────────────────────────
  Total folders   : 3
  Total notebooks : 4
  Maximum depth   : 2
────────────────────────────────────────
```

A JSON snapshot is written to `storage/tree.json` automatically.

---

### Export notebooks

```bash
# Export to storage/exports/ (default)
python -m main --export

# Export to a custom path
python -m main --export /path/to/output
```

Output mirrors the folder hierarchy:

```
storage/exports/
└── Analytics/
    ├── Q1/
    │   ├── Revenue Validation.ipynb
    │   └── Cost Analysis.ipynb
    └── Q2/
        └── Budget Report.ipynb
```

---

### Export saved queries

```bash
python -m main --type query --export
```

```
storage/exports/
└── Shared/
    ├── Daily Summary.sql
    └── Revenue Check.sql
```

---

### Import notebooks

Import a specific subfolder, recreating its full path in Query Editor V2:

```bash
python -m main \
  --import "storage/exports/Analytics/Q1" \
  --import-base "storage/exports"
```

This produces the following structure in Query Editor V2:

```
📁 Analytics        ← reused if it already exists
└── 📁 Q1           ← created inside Analytics
    ├── 📄 Revenue Validation
    └── 📄 Cost Analysis
```

Import the entire export directory:

```bash
python -m main \
  --import "storage/exports" \
  --import-base "storage/exports"
```

Re-running an import is safe — existing folders are reused and no duplicates are created.

---

### Delete saved queries

```bash
# Delete a single query
python -m main --delete-query <uuid-or-full-arn>

# Delete all queries (prompts for confirmation)
python -m main --delete-all-queries

# Scope deletion to a specific folder
python -m main --delete-all-queries --folder <folder-id>
```

---

## Full Option Reference

```
python -m main [options]

Connection:
  --region REGION          AWS region (default: ap-south-1 or $AWS_DEFAULT_REGION)
  --debug                  Enable verbose debug logging

Crawl / Export:
  --type {notebook,query,all}   Resource type (default: notebook)
  --output FILE                 JSON tree output path (default: storage/tree.json)
  --export [DIR]                Export to directory (default: storage/exports)
  --root-folder FOLDER_ID       Override root folder ID

Import:
  --import DIR                  Import notebooks from local directory
  --import-base DIR             Base directory for relative path reconstruction
                                (default: storage/exports)
  --import-root-folder ID       Target root folder (auto-discovered when omitted)

Delete:
  --delete-query ARN_OR_ID      Delete a single saved query
  --delete-all-queries          Delete all saved queries (confirmation required)
  --folder FOLDER_ID            Scope --delete-all-queries to a specific folder
```

---

## API Endpoints

The toolkit calls the following endpoints on `https://api.sqlworkbench.<region>.amazonaws.com`:

| Operation | Method | Endpoint |
|---|---|---|
| Current user & root folder | `GET` | `/user` |
| List child folders | `GET` | `/v2/file?actionName=folders-only` |
| List notebooks / queries | `POST` | `/tagged-resource` |
| Notebook metadata | `GET` | `/notebook/<arn>` |
| Export notebook | `GET` | `/notebook/<arn>/export` |
| Import notebook | `POST` | `/notebook/import/v1` |
| Create folder | `PUT` | `/folder` |
| Query metadata + SQL | `GET` | `/query-saved/<arn>` |
| Delete query | `DELETE` | `/query-saved/<arn>` |

---

## Running Tests

The test suite runs entirely offline — no AWS credentials required.

```bash
pip install pytest pytest-cov
pytest
```

```
========================= 90 passed in 0.54s =========================
```

Run with coverage:

```bash
pytest --cov=src --cov-report=term-missing
```

---

## Notes

- Folder names with special characters (apostrophes, spaces, etc.) are handled correctly in both export and import paths.
- All output is written under `storage/` by default, which is gitignored. The directory is created automatically.
- The `--import-base` flag controls how much of the local path is recreated in Query Editor V2. Set it to the export root to get an exact mirror of the original hierarchy.

---

## License

[MIT](LICENSE)

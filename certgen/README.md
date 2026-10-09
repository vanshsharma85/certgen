# Bulk Certificate Generator

A backend API that accepts a list of recipients in **one request**, generates a PDF certificate for each
from a single predefined template, tracks progress, and lets the client retrieve the results.

**Stack:** Python 3.10+, FastAPI, SQLAlchemy 2 + SQLite (any SQLAlchemy-supported relational DB works via `DATABASE_URL`), ReportLab (PDF), pytest.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:create_app --factory --reload
```

Interactive API docs: <http://127.0.0.1:8000/docs>. The DB (`certgen.db`) and PDFs (`./storage/<job_id>/<certificate_id>.pdf`) are created automatically.

| Env var | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./certgen.db` | SQLAlchemy URL (e.g. `postgresql+psycopg://...`) |
| `STORAGE_DIR` | `./storage` | Where PDFs are written |
| `PROCESSING_MODE` | `background` | `background` (thread pool) or `inline` (synchronous) |
| `WORKER_THREADS` | `2` | Background worker threads |
| `MAX_RECIPIENTS_PER_JOB` | `5000` | Upper bound per request |
| `CERT_FONT_PATH` | – | Optional Unicode `.ttf` for non-Latin names |

## Tests

```bash
pytest
```

Covers: job creation, request- and row-level validation, PDF generation (incl. recipient text in the file), live progress,
a single-certificate failure not affecting others, listing/downloading certificates, ZIP download, and resuming after a restart.

## API

| Method & path | Purpose |
|---|---|
| `POST /jobs` | Submit a bulk request → `202` + job (poll it) |
| `GET /jobs/{id}` | Status, counts, progress %, and the first 100 failures with reasons |
| `GET /jobs` | List jobs (`limit`, `offset`, `status`) |
| `GET /jobs/{id}/certificates` | Per-recipient results (`status`, `limit`, `offset` filters) |
| `GET /certificates/{id}/download` | One certificate PDF |
| `GET /jobs/{id}/download` | ZIP of all successfully generated certificates |

### Submit a request

```bash
curl -X POST http://127.0.0.1:8000/jobs -H 'Content-Type: application/json' -d '{
  "course_name": "Python Bootcamp 2026",
  "issuer_name": "Acme Academy",
  "issue_date": "2026-10-01",
  "recipients": [
    {"name": "Asha Rao",  "email": "asha@example.com"},
    {"name": "Ben Carter","email": "ben@example.com"},
    {"name": "",          "email": "broken"}
  ]
}'
```
`issue_date` is optional (defaults to today, UTC). Response (`202 Accepted`):
```json
{"id": "9f2c…", "status": "pending", "total": 3, "succeeded": 0, "failed": 1, "pending": 2,
 "progress_percent": 33.3, "certificates_url": "/jobs/9f2c…/certificates", "download_all_url": null, "…": "…"}
```

### Track progress

```bash
curl http://127.0.0.1:8000/jobs/<id>
```
Job `status`: `pending` → `processing` → `completed` (all OK) | `completed_with_errors` | `failed` (nothing succeeded).
Each failure lists `position` (index in your submitted array), the recipient, and an `error` explaining why.

### Retrieve certificates

```bash
curl http://127.0.0.1:8000/jobs/<id>/certificates                 # list, each with a download_url
curl -OJ http://127.0.0.1:8000/certificates/<certificate_id>/download   # one PDF
curl -OJ http://127.0.0.1:8000/jobs/<id>/download                 # ZIP of everything that succeeded
```
Downloads work while a job is still running (you get what is finished so far).

## Design decisions

**Background processing (not synchronous).** Rendering thousands of PDFs would hold an HTTP request open for
minutes, hit client/proxy timeouts, and tie up a server worker. `POST /jobs` therefore validates, persists, and
returns `202` right away; a `ThreadPoolExecutor` generates in the background and the client polls `GET /jobs/{id}`.
I chose an in-process pool over Celery/Redis to keep the project runnable with zero infrastructure. The
processing function (`service.process_job`) is independent of FastAPI, so moving it to a real queue is a small change.
`PROCESSING_MODE=inline` runs it synchronously, which the tests use for determinism.

**State lives in the database.** One `Job` row, one `Certificate` row per recipient (status `pending/succeeded/failed`,
error text, file path). Progress counts are computed from the certificate rows (`GROUP BY status`), so they can never
drift out of sync with reality. Each certificate is committed individually, so progress is visible live.

**Validation is per row, not all-or-nothing.** Request-shape problems (no recipients, missing course, over the
limit) → `422`. Row problems (missing/blank name, bad email, name too long, control characters, characters the font
can't render, duplicate email within the job) don't reject the request: the row is stored as `failed` with a reason
and the valid rows proceed. Every row therefore gets an explicit outcome. A job where *no* row is valid is `failed` immediately.

**Failure isolation.** Each certificate is rendered inside its own `try/except`; an exception marks only that
certificate `failed` (with `ExceptionType: message`) and the loop continues. PDFs are written to a temp file and
renamed, so a crash never leaves a corrupt certificate behind.

**Restart safety.** On startup, jobs left in `processing` are reset to `pending` and re-enqueued; because only `pending`
certificates are processed, finished ones aren't regenerated. A job is "claimed" with an atomic
`UPDATE … WHERE status='pending'` so it can't be processed twice by two threads.

**Template.** One fixed A4-landscape template drawn with ReportLab (no template files to manage). Long names/courses
auto-shrink to fit. Each certificate carries a unique ID.

**Storage.** PDFs are on the local filesystem keyed by job/certificate ID; the DB stores relative paths. This is
the piece to swap for S3/GCS in production.

### Known limitations / what I'd do next
- Single-process assumption for recovery. With several app instances you'd use a proper queue (Celery/RQ/SQS) and
  a lease/heartbeat on `processing` jobs instead of resetting them on startup.
- No authentication or per-client job ownership.
- SQLite is for convenience; for heavy concurrent writes use PostgreSQL (just change `DATABASE_URL`; there are no SQLite-specific queries).
- Built-in PDF fonts are Latin-1 only; set `CERT_FONT_PATH` for other scripts.
- ZIP download is built in a spooled temp file, fine for thousands of certificates; for much larger jobs stream it or pre-build it in the worker.
- No migrations (tables are created on startup); I'd add Alembic for a real deployment.

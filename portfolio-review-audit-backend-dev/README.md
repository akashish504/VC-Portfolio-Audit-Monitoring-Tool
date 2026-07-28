# VC Audit Discrepancy Tool — Backend API

## Overview

FastAPI backend for the PeakXV Portfolio Review Audit platform. Ingests audited financial PDFs submitted by portfolio companies, extracts structured figures via LLM, reconciles them against expected values, flags discrepancies, and supports the full analyst workflow: email management, org chart tracking, review cycle rollover, and exportable audit reports.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Local Setup](#local-setup)
- [Docker Setup](#docker-setup)
- [Environment Variables](#environment-variables)
- [Project Structure](#project-structure)
- [API Endpoints](#api-endpoints)
- [Database Migrations](#database-migrations)
- [Running Tests](#running-tests)
- [Built Using](#built-using)

---

## Prerequisites

- [Python 3.10](https://www.python.org/downloads/release/python-3100/)
- [Pipenv](https://pypi.org/project/pipenv/)
- [PostgreSQL](https://www.postgresql.org/)
- [Docker](https://www.docker.com/) (optional)

---

## Local Setup

```bash
# 1. Install dependencies
pipenv install
pipenv shell

# 2. Configure environment
cp .env.example .env
# Edit .env — minimum required: POSTGRES_* vars + CORS_ORIGIN + CSRF_SECRET_KEY

# 3. Create the database
createdb peakxv_pr_tool

# 4. Run migrations
alembic upgrade head

# 5. Start the server
uvicorn src.main:app --reload
```

API: `http://localhost:8000`  
Swagger docs: `http://localhost:8000/api/v1/docs`

For local development without Okta or CSRF, set `DISABLE_AUTH=true` and `DISABLE_CSRF=true` in `.env`.

---

## Docker Setup

```bash
# Development
docker-compose up -d

# Production
docker-compose -f docker-compose.prod.yaml pull
docker-compose -f docker-compose.prod.yaml up -d
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in values. Key variables:

| Variable | Description |
|---|---|
| `POSTGRES_HOST` | PostgreSQL host (default: `localhost`) |
| `POSTGRES_PORT` | PostgreSQL port (default: `5432`) |
| `POSTGRES_DB` | Database name (default: `peakxv_pr_tool`) |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` | DB credentials |
| `POSTGRES_SSLMODE` | Set `disable` for local dev without SSL |
| `CORS_ORIGIN` | Allowed frontend origin (e.g. `http://localhost:3000`) |
| `CSRF_SECRET_KEY` | Long random string for CSRF protection |
| `DISABLE_AUTH` | `true` to bypass JWT auth in local dev |
| `DISABLE_CSRF` | `true` to bypass CSRF validation in local dev |
| `OPENAI_API_KEY` | OpenAI key — used when `LOCAL_DEV=true` for document extraction |
| `LLM_PROVIDER` | `openai` or `bedrock` |
| `S3_BUCKET` | Primary bucket for org charts, audit uploads, and Bedrock batch staging |
| `AWS_S3_EMAIL_BUCKET` | Bucket for draft email attachments |
| `BEDROCK_MODEL_ID` | Bedrock model for document extraction (deployment) |
| `BEDROCK_BATCH_ROLE_ARN` | IAM role ARN trusted by Bedrock for batch inference |
| `XE_ACCOUNT_ID` / `XE_API_KEY` | Live FX rates (omit to use bundled mock rates) |

See `.env.example` for the full list including Bedrock cross-account, FX, and auth options.

---

## Project Structure

```
portfolio-review-audit-backend/
├── src/
│   ├── configs/            # App settings, logging, CSRF config
│   ├── db/                 # SQLAlchemy models and session
│   ├── exceptions/         # Custom exception classes and handlers
│   ├── llm/                # LLM provider abstraction (OpenAI / Bedrock)
│   ├── schema/             # Pydantic request/response models
│   ├── scheduler/          # APScheduler setup and registered jobs
│   ├── scripts/            # One-off admin scripts
│   ├── services/           # Business logic
│   │   ├── audit_service.py
│   │   ├── audit_document_extraction.py
│   │   ├── financial_reconciliation.py
│   │   ├── org_chart_reconciliation.py
│   │   ├── review_cycle_rollover.py
│   │   ├── email_threads.py
│   │   ├── draft_email.py
│   │   ├── fx_service.py
│   │   └── ...
│   ├── utils/              # Shared utilities (auth, AWS, logging)
│   ├── versions/
│   │   └── v1/
│   │       └── routes/     # All API route handlers
│   ├── main.py             # App entry point, middleware wiring
│   └── middlewares.py      # Access logging, process time middleware
├── alembic/
│   └── versions/           # 43 incremental migration scripts
├── tests/                  # Pytest test suite
├── scripts/                # Deployment and data scripts
├── fixtures/               # Qualitative gold fixtures for LLM testing
├── nginx/                  # Nginx config for containerized deployment
├── deployment/k8s/         # Kubernetes manifests
├── .env.example
├── Dockerfile
├── docker-compose.yaml
├── docker-compose.prod.yaml
├── Pipfile
├── alembic.ini
├── pytest.ini
└── entrypoint.sh
```

---

## API Endpoints

All routes are prefixed with `/api/v1/`.

### User & Auth

| Method | Path | Description |
|---|---|---|
| `GET` | `/user/get-user` | CSRF bootstrap — returns current user from JWT |
| `GET` | `/users/profile` | Get full user profile |

### Portfolio

| Method | Path | Description |
|---|---|---|
| `GET` | `/portfolio/companies` | List portfolio companies |
| `GET` | `/portfolio/company/{id}` | Get company detail |
| `PATCH` | `/portfolio/company/{id}` | Update company fields |
| `GET` | `/portfolio/review-cycles` | List review cycles |

### Audit

| Method | Path | Description |
|---|---|---|
| `GET` | `/audit/files` | List uploaded audit files |
| `POST` | `/audit/files/upload` | Upload an audit PDF |
| `POST` | `/audit/files/{id}/extract` | Trigger LLM extraction on a file |
| `GET` | `/audit/discrepancies` | List flagged discrepancies |
| `PATCH` | `/audit/discrepancies/{id}` | Update discrepancy reviewer remarks |

### Comparison

| Method | Path | Description |
|---|---|---|
| `GET` | `/comparison/workspace` | Get comparison workspace data |
| `POST` | `/comparison/reconcile` | Run reconciliation for a company/cycle |

### Dashboard

| Method | Path | Description |
|---|---|---|
| `GET` | `/dashboard/scoping` | Scoping dashboard data |
| `GET` | `/dashboard/variance` | Variance dashboard data |
| `GET` | `/dashboard-data/export` | Export dashboard data as Excel/CSV |

### Email

| Method | Path | Description |
|---|---|---|
| `GET` | `/email-threads/list` | List email threads (tagged and untagged) |
| `GET` | `/email-threads/{id}` | Get messages in a thread |
| `POST` | `/email-threads/tag` | Tag a thread to a company and quarter |
| `GET` | `/email-templates` | List email templates |
| `POST` | `/email-templates/create` | Create an email template |
| `POST` | `/draft-email/send` | Send new, reply, bulk, or reminder email |
| `GET` | `/email-attachments/{id}` | Download an email attachment |

### Org Chart

| Method | Path | Description |
|---|---|---|
| `POST` | `/org-chart-batches/upload` | Batch upload org chart data |
| `GET` | `/org-chart-reconciliation` | Get reconciliation results |

### Settings & Config

| Method | Path | Description |
|---|---|---|
| `GET` | `/settings` | Get application settings |
| `PUT` | `/settings` | Update settings |
| `GET` | `/settings/parameter-thresholds` | Get variance parameter thresholds |
| `POST` | `/data-sync-config` | Configure scheduled data sync |

### Export

| Method | Path | Description |
|---|---|---|
| `POST` | `/export/audit-report` | Generate and download audit report (Excel) |

### FX

| Method | Path | Description |
|---|---|---|
| `GET` | `/fx/rates` | Get current FX rates |

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check (auth-exempt) |

---

## Database Migrations

Migrations are managed with Alembic. The schema covers 43 incremental versions from initial setup through org chart reconciliation and data sync configuration.

```bash
# Apply all pending migrations
alembic upgrade head

# Create a new migration
alembic revision --autogenerate -m "description"

# Rollback one step
alembic downgrade -1
```

---

## Running Tests

```bash
./run_tests.sh
```

Or directly:

```bash
pipenv run pytest
```

Test files cover audit report export, email ingestion, email classification, org chart reconciliation, financial sync, review cycle rollover, and scheduled data sync.

---

## Built Using

- [FastAPI](https://fastapi.tiangolo.com/) — Web framework
- [SQLAlchemy](https://www.sqlalchemy.org/) — ORM
- [Alembic](https://alembic.sqlalchemy.org/) — Database migrations
- [PostgreSQL](https://www.postgresql.org/) — Primary database
- [APScheduler](https://apscheduler.readthedocs.io/) — Background job scheduling
- [AWS Bedrock](https://aws.amazon.com/bedrock/) — LLM for document extraction (production)
- [OpenAI](https://platform.openai.com/) — LLM for document extraction (local dev)
- [AWS S3](https://aws.amazon.com/s3/) — File storage
- [Docker](https://www.docker.com/) — Containerization

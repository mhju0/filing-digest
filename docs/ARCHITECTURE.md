# filing-digest Architecture

*Korean original: [ARCHITECTURE.ko.md](ARCHITECTURE.ko.md)*

v0.1 architecture document for a service that ingests Korean (DART) and US (SEC EDGAR)
regulatory filings and serves **citation-grounded**, bilingual (KO/EN) summaries and Q&A.

---

## 1. System Overview

```
                 ┌─────────────────────────────┐
                 │        iOS App (SwiftUI)     │
                 │  iOS 17+, no third-party deps│
                 └──────────────┬──────────────┘
                                │ HTTP (JSON, snake_case)
                                │ baseURL: http://127.0.0.1:8001
                                ▼
┌───────────────────────────────────────────────────────────────┐
│                  Backend (FastAPI, Python 3.11)                │
│                                                               │
│  GET  /health                     GET /companies?q=           │
│  GET  /companies/{id}/digest      POST /search  POST /answer  │
│                                    POST /ingest (stub)         │
│                                                               │
│  ┌─────────────┐   ┌──────────────┐   ┌───────────────────┐  │
│  │ PostgreSQL   │   │ Ingest (stub) │   │ Search / Answer    │  │
│  │ (real reads) │   │ job queue     │   │ (KURE-v1 + Solar)  │  │
│  └─────────────┘   └──────────────┘   └───────────────────┘  │
└───────┬──────────────────────┬────────────────────────────────┘
        │ SQLAlchemy 2.x       │  httpx (async)
        │ (psycopg3)           ▼
        ▼               ┌─────────────────┐  ┌─────────────────┐
┌────────────────┐      │  DART Open API   │  │  SEC EDGAR API   │
│ PostgreSQL 16  │      │ (opendart.fss.   │  │ (data.sec.gov)   │
│ + pgvector     │      │  or.kr/api)      │  │  UA must include │
│ (host 5433 ->  │      └─────────────────┘  │  contact info    │
│  container     │                           └─────────────────┘
│  5432)         │
└────────────────┘
```

- **Current state**: both DART and SEC are wired up with real API calls
  (`backend/app/clients/dart.py`, `backend/app/clients/sec.py`, `sec_document.py`,
  `backend/app/ingest/sec_ingest.py`). `/companies`, `/companies/{id}/digest`,
  `/search`, and `/answer` read from the real database (`companies`, `filings`,
  `filing_chunks`, `financials`). `filings.sec_accession_no` serves as the natural
  key for idempotent upserts on the SEC side.
- **Phase 2**: automated parsing/chunking, vector index tuning, multi-filing /
  multi-year expansion.

## 2. Monorepo Layout

```
filing-digest/
├── backend/                 # FastAPI backend (Python 3.11)
│   ├── app/                 #   routers, schemas (pydantic), settings, LLM guards
│   ├── db/init.sql          #   DB schema v0.1 (single init script instead of a migration tool)
│   ├── tests/               #   pytest
│   ├── Dockerfile
│   └── requirements.txt
├── ios/                     # SwiftUI client (FilingDigest.xcodeproj)
├── docs/                    # architecture & decision docs (this document)
├── docker-compose.yml       # local dev stack (db + backend)
└── README.md
```

## 3. Core Principles

> **"Numbers come only from structured APIs; the LLM handles narrative only; every claim carries a citation."**

1. **Every numeric value (`MetricCard.value`) comes exclusively from DART/SEC
   structured data.** The LLM never generates, estimates, or adjusts numbers.
   Values in the `financials` table are all linked via `citation_id` to a real
   `Citation` (the original filing), which enforces this pipeline contract.
2. **The LLM is responsible for narrative only.** The digest's
   `summary_ko`/`summary_en` and the `/answer` endpoint's `answer` are the LLM's
   territory — and even there, "making up" numbers is forbidden (enforced by the
   `number_guard` / bare-digit floor). Any figure that appears in narrative text
   quotes the structured data verbatim.
3. **Every narrative claim is linked to evidence via `citations[]`.**
   Sentences without supporting evidence are never included in a response.
   (`Citation.url` links back to the original filing.)

## 4. Decision Log

| # | Decision | Tag | Rationale |
|---|------|------|------|
| D1 | **Adopt a single `backend/db/init.sql` script instead of Alembic** | [Verified] | v0.1 has only four tables (companies, filings, filing_chunks, financials) and no production data, so migration-history management adds no value. Mounting it read-only into `docker-entrypoint-initdb.d` guarantees a reproducible schema from `compose up` alone. Revisit Alembic in Phase 2 once the schema starts evolving. |
| D2 | **pgvector embedding dimension 1024** | [Verified] | Embedding model finalized as KURE-v1 (nlpai-lab/KURE-v1). The dense dimension was cross-checked against `hidden_size=1024` in the HuggingFace `config.json` (bge-m3-based XLM-RoBERTa) and `word_embedding_dimension=1024` in `1_Pooling/config.json`. Reflected in the `EMBEDDING_DIM` environment variable (default 1024) and `filing_chunks.embedding vector(1024)`. |
| D3 | **Actual DART/SEC response formats** | [Verified] | DART confirmed via live calls (`docs/dart-api-notes.md`). SEC likewise verified live through `SecClient` (submissions + companyfacts) — validated against Apple's 10-K (CIK 320193, accession `0000320193-25-000079`) via `backend/app/ingest/sec_ingest.py`. Since SEC's `fy` describes the filing's period rather than the period of each fact, `fiscal_year` is derived from each fact's `period_end`. |
| D4 | **psycopg3 chosen** (`postgresql+psycopg://` driver) | [Verified] | psycopg2 is in maintenance mode; psycopg3 (package name `psycopg`) is the currently recommended driver and officially supported by SQLAlchemy 2.x via the `postgresql+psycopg` dialect. Reflected in the `DATABASE_URL` default and the docker-compose connection string. |
| D5 | **iOS 17 target + zero third-party dependencies** | [Verified] | SwiftUI + URLSession + Codable (async/await) is sufficient to consume the v0.1 API. Zero dependencies minimizes build reproducibility risk and review surface. Default baseURL is `http://127.0.0.1:8001` (simulator local development). |
| D6 | **Initial data scope: one filing per company, two sources** | [Verified] | Samsung Electronics (dart, KOSPI, 005930) FY2023 annual report (사업보고서) + Apple (sec, CIK 320193) FY2025 10-K (accession `0000320193-25-000079`) — one filing loaded per company. This exercises both the DART and SEC live-integration paths. `/digest` and `/answer` read from the real database (`financials`, `filing_chunks`), and every `MetricCard.value` is linked via `citation_id` to a real `Citation` (the original filing), enforcing the core principle. Multi-filing / multi-year expansion is Phase 2. *Superseded 2026-07-12: corpus expanded to 8 companies (4 DART / 4 SEC) via the `python -m app.ingest` CLI; annual filings also persist their prior-period figures for YoY.* |
| D7 | **API CONTRACT v0.1 frozen** (full text in section 5 below) | [Verified] | Backend and iOS are developed in parallel, so the contract was frozen first. JSON fields are snake_case. |
| D8 | **No vector index (hnsw/ivfflat) created yet** | [Verified] | Without real data, index parameters cannot be tuned. Left only as a Phase 2 TODO comment in init.sql. *Resolved 2026-07-12: hnsw (`vector_cosine_ops`, default parameters) created once the corpus reached 8 companies / ~1.2k chunks.* |
| D9 | **The metadata column on `filing_chunks` is named `meta`** | [Verified] | `metadata` collides with a reserved attribute name in SQLAlchemy Declarative (`Base.metadata`). The column itself is standardized as `meta` (jsonb). |

## 5. API CONTRACT v0.1 (full text)

The contract every component (backend, iOS) must follow exactly. JSON fields are snake_case.

```
GET /health -> 200
  {"status": "ok", "version": "0.1.0"}

GET /companies?q=<string> -> 200 CompanySearchResponse
  CompanySearchResponse = {"items": [Company], "total": int}
  Company = {"id": str(uuid), "name": str, "name_en": str|null, "ticker": str|null,
             "market": "KOSPI"|"KOSDAQ"|"NYSE"|"NASDAQ"|null, "source": "dart"|"sec"}

GET /companies/{company_id}/digest?lang=ko|en (defaults to ko when lang is omitted) -> 200 CompanyDigest, unknown id -> 404
  CompanyDigest = {"company_id": str, "company_name": str, "period": str (e.g. "2026Q1"),
                   "metrics": [MetricCard], "summary_ko": str, "summary_en": str,
                   "citations": [Citation], "generated_at": str(ISO8601)}
  MetricCard = {"key": "revenue"|"operating_income"|"net_income"|"eps"|"operating_margin",
                "label_ko": str, "label_en": str, "value": float|null, "unit": str,
                "yoy_delta_pct": float|null, "source": "dart"|"sec", "citation_id": str|null}
  Citation = {"id": str, "source": "dart"|"sec", "title": str, "url": str,
              "excerpt": str|null, "filed_at": str(ISO date)|null}

POST /search -> 200 SearchResponse
  request  SearchRequest  = {"query": str(min length 1), "top_k": int(default 5, max 50), "company_id": str(uuid)|null}
  SearchResponse = {"items": [SearchHit], "total": int}
  SearchHit = {"chunk_id": str, "filing_id": str, "text": str, "score": float,
               "rcept_no": str|null, "section_title": str|null, "section_order": int|null,
               "part_index": int|null, "chunk_index": int}

POST /answer -> 200 AnswerResponse
  request  AnswerRequest  = {"query": str(min length 1), "company_id": str(uuid), "period": str|null}
  AnswerResponse = {"answer": Answer|null, "figures": [Figure], "citations": [Citation],
                     "company_id": str, "narrative_status": "ok"|"blocked"|"no_results"}

POST /ingest -> 202 (stub -- no worker yet)
  request  IngestRequest  = {"company_id": str, "source": "dart"|"sec", "filing_types": [str]|null}
  response IngestResponse = {"job_id": str(uuid), "status": "queued"}
```

- Ports: backend **8001**, postgres **5433(host) -> 5432(container)**. iOS default baseURL: `http://127.0.0.1:8001`
- ENV variables (pydantic-settings, `.env`): `DART_API_KEY` (secret, only a placeholder is committed),
  `DART_BASE_URL` (default `https://opendart.fss.or.kr/api`), `SEC_BASE_URL` (default `https://data.sec.gov`),
  `SEC_USER_AGENT` (SEC requires a UA with contact info - placeholder),
  `DATABASE_URL` (default `postgresql+psycopg://filing_digest:filing_digest_dev@localhost:5433/filing_digest`),
  `EMBEDDING_DIM` (default 1024)

## 6. DB Schema v0.1 Summary

`backend/db/init.sql` and the SQLAlchemy models must match exactly.
(`CREATE EXTENSION IF NOT EXISTS vector;`, using pg16's built-in `gen_random_uuid()`)

- **companies**: id(uuid PK), name, name_en, ticker, market, source(`dart|sec` CHECK),
  dart_corp_code UNIQUE, sec_cik UNIQUE, created_at
- **filings**: id(uuid PK), company_id FK→companies ON DELETE CASCADE, source, filing_type,
  title, period, filed_at(date), url, sec_accession_no(text, UNIQUE — natural key for
  idempotent upserts on the SEC side), created_at; `idx_filings_company` index
- **filing_chunks**: id(uuid PK), filing_id FK→filings ON DELETE CASCADE, chunk_index,
  content, embedding `vector(1024)` [Verified - D2], meta(jsonb — avoids the reserved
  name `metadata`, see D9), created_at; UNIQUE(filing_id, chunk_index)
- **financials**: id(uuid PK), company_id FK→companies ON DELETE CASCADE,
  filing_id FK→filings ON DELETE SET NULL, fiscal_year, fiscal_quarter, period, metric,
  value numeric(24,4), unit, currency, source, created_at; UNIQUE(company_id, period, metric, source)
- No vector index (hnsw/ivfflat) is created - Phase 2 TODO (D8)

## 7. Phase 2 TODO

- [x] **Live DART/SEC integration**: httpx-based clients, response formats measured and finalized (resolves D3), live-verified against Apple's 10-K
- [x] **Multi-filing / multi-year expansion**: corpus expanded to 8 companies via the `python -m app.ingest` CLI (2026-07-12); YoY comes from each annual filing's own prior-period figures, stored as `<year-1>-annual` rows
- [ ] **Automated parsing/chunking**: ingest is CLI-triggered; a scheduled/queue-based pipeline is still open
- [x] **Vector index**: hnsw (`vector_cosine_ops`) created at 8-company / ~1.2k-chunk scale (D8, 2026-07-12)
- [ ] (Incidental) revisit Alembic adoption (D1), real async processing for ingest jobs (queue/worker)

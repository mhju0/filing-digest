# Filing Digest Architecture

This document describes the final v0.2.0 portfolio architecture and API
contract v0.2.

## System overview

```text
┌──────────────────────┐       JSON/HTTP        ┌────────────────────────┐
│ SwiftUI iOS 17 client├──────────────────────>│ FastAPI / Python 3.11  │
│ URLSession + Codable │   127.0.0.1:8001       │ Pydantic + SQLAlchemy  │
└──────────────────────┘                         └───────┬────────┬───────┘
                                                        │        │
                              ┌─────────────────────────┘        └──────────────┐
                              v                                                v
                 ┌────────────────────────┐                        ┌────────────────────┐
                 │ PostgreSQL 16          │                        │ External services  │
                 │ pgvector / HNSW cosine │                        │ DART, SEC, Solar   │
                 └────────────────────────┘                        └────────────────────┘
```

The repository contains two deployable components:

- `backend/`: FastAPI application, ingestion CLI, retrieval/generation services,
  database schema, eval harness, and pytest suite.
- `ios/`: SwiftUI client and Swift Testing target with no third-party packages.

Docker Compose starts PostgreSQL by default. The backend container is optional
under the `container` profile; native uvicorn is the normal development path.

## Trust boundary and data flow

### Ingestion

1. `python -m app.ingest` resolves a ticker through DART or SEC.
2. The source client fetches an annual filing and structured financial facts.
3. DART DSD or SEC HTML parsing removes tables before prose extraction.
4. Prose is chunked with source metadata and stored in `filing_chunks`.
5. KURE-v1 creates normalized 1024-dimensional vectors.
6. Structured facts are stored separately in `financials` as
   `numeric(24,4)` values linked to their filing.

DART receipt numbers and SEC accession numbers are natural keys for idempotent
filing upserts. Re-ingestion replaces a filing's chunks inside one transaction.

### Search and answers

`POST /search` embeds a bounded query with the same model and retrieves at most
50 HNSW-ranked chunks by cosine distance. Optional company scoping joins through
`filings`.

`POST /answer` keeps generated prose and financial values on separate tracks:

- `fetch_financials` returns authoritative structured values. The LLM never sees
  or calculates them.
- Retrieved chunks are labelled positionally before being sent to Solar. The
  response must match a JSON schema. Labels are then mapped back to real chunk
  UUIDs.
- The citation guard rejects missing or fabricated citation IDs. The number
  guard rejects currency, percentage, and multiplier tokens in prose.

The response state is `ok`, `blocked`, or `no_results`. Figures can still be
returned when the narrative is withheld.

## Component boundaries

| Area | Primary modules |
|---|---|
| API and contracts | `backend/app/api/routes.py`, `backend/app/schemas.py` |
| DART integration | `backend/app/clients/dart.py`, `backend/app/ingest/persist.py` |
| SEC integration | `backend/app/clients/sec.py`, `sec_document.py`, `sec_ingest.py` |
| Retrieval | `backend/app/embeddings/`, `backend/app/search/` |
| Narrative guards | `backend/app/llm/`, `backend/app/digest_narrative.py` |
| Database | `backend/db/init.sql`, `backend/app/db/models.py` |
| iOS transport/models | `ios/FilingDigest/Networking/`, `ios/FilingDigest/Models/` |
| iOS presentation | `ios/FilingDigest/Views/`, `ios/FilingDigest/Theme.swift` |

## API contract v0.2

All JSON fields are snake_case.

| Method | Path | Response |
|---|---|---|
| `GET` | `/health` | Liveness and application version |
| `GET` | `/companies?q=` | Matching companies; empty query returns the corpus |
| `GET` | `/companies/{id}/digest?lang=ko\|en` | Metrics, bilingual summaries, citations |
| `POST` | `/search` | Up to 50 citation-bearing search hits |
| `POST` | `/answer` | Guarded narrative, structured figures, citations |

Request limits protect model and embedding work: company query 100 characters,
search query 500 characters, answer query 1,000 characters, and period 32
characters. UUID fields are validated by Pydantic.

Ingestion is CLI-only. There is no remote write endpoint.

## Database

`backend/db/init.sql` is the schema source of truth and must match
`backend/app/db/models.py`.

- `companies`: source identity and optional DART/SEC natural keys.
- `filings`: company-owned filing metadata; DART receipt and SEC accession
  numbers are unique.
- `filing_chunks`: filing-owned prose, citation metadata, optional
  `vector(1024)`, unique `(filing_id, chunk_index)`, HNSW cosine index.
- `financials`: company/filing-owned exact values, unique
  `(company_id, period, metric, source)`.

Foreign keys cascade company deletion to filings and chunks. Financial rows use
`ON DELETE SET NULL` for their filing reference; application shaping fails loud
if a figure lacks that citation anchor.

The archived project intentionally uses a reproducible initialization script
instead of migration history. A future production deployment with persistent
data would need a migration strategy before changing this schema.

## Key decisions

- **KURE-v1 / 1024 dimensions:** one cross-lingual embedding space for Korean
  and English; vectors are normalized and queried with cosine distance.
- **Structured-number boundary:** exact values never pass through generated
  prose. This is enforced in code, not only in prompts.
- **Table removal:** filing tables are excluded before embedding to avoid
  presenting prose retrieval as an authoritative numeric source.
- **Async I/O:** external clients and SQLAlchemy request/ingest paths use async
  APIs. Pure parsing and mapping logic remains independently testable.
- **No iOS dependencies:** SwiftUI, URLSession, Codable, and Swift Testing keep
  the client build surface small.
- **Local demo security scope:** the API has no authentication, authorization,
  rate limiting, or tenant isolation and is not intended for public exposure.

## Known constraints

- DART 사업보고서 and SEC 10-K only.
- DART xforms documents and attachments are not parsed.
- Retrieval uses one similarity threshold; it is not a full groundedness model.
- Solar wording is nondeterministic, while guards and structured figures are
  deterministic.
- Request-scoped Solar clients favor explicit ownership over connection reuse.

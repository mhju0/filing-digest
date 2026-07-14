# Filing Digest Architecture

This document describes the v0.3.0 portfolio architecture and API contract
v0.3.

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
2. The DART or SEC adapter fetches an annual filing and maps it into one
   source-independent `NormalizedFiling` snapshot.
3. DART DSD or SEC HTML parsing removes tables before prose extraction.
4. One persistence module atomically replaces the filing's Financial Facts and
   Filing Chunks. A failed write leaves the previous complete snapshot intact.
5. KURE-v1 indexes the committed Filing Chunks in a separate, retryable step.
6. A filing becomes searchable only after every chunk in that snapshot is
   indexed; partially indexed snapshots remain hidden from retrieval.

DART receipt numbers and SEC accession numbers, paired with their regulatory
source, are Filing Identities for idempotent replacement. Structured values are
stored as exact `numeric(24,4)` Financial Facts and never pass through generated
prose.

### Search and answers

`POST /search` embeds a bounded query with the same model and retrieves at most
50 HNSW-ranked chunks by cosine distance. Optional company scoping joins through
`filings`, and retrieval excludes filings whose current snapshot is not fully
indexed.

`POST /answer` keeps generated prose and financial values on separate tracks:

- `fetch_financials` returns authoritative structured values. The LLM never sees
  or calculates them.
- Retrieved chunks are labelled positionally before being sent to Solar. The
  response must match a JSON schema. Labels are then mapped back to real chunk
  identities.
- A Citation identifies one supporting Filing Chunk and carries a bounded
  excerpt plus its location anchor. Filing Sources are separate, deduplicated,
  openable Corporate Filings ordered by first appearance in the answer.
- The evidence module blocks a narrative if any Citation cannot resolve to an
  openable Filing Source. The number guard rejects currency, percentage, and
  multiplier tokens in prose.

The response state is `ok`, `blocked`, or `no_results`, with a block reason when
applicable. Figures can still be returned when the narrative is withheld.

## Module seams

| Area | Primary modules |
|---|---|
| HTTP transport and contracts | `backend/app/api/routes.py`, `backend/app/schemas.py` |
| Filing domain and persistence | `backend/app/filings/`, `backend/app/ingest/` |
| Financial vocabulary | `backend/app/financials/`, `contracts/financial-vocabulary.json` |
| Evidence resolution | `backend/app/evidence/` |
| DART integration | `backend/app/clients/dart.py`, `backend/app/ingest/persist.py` |
| SEC integration | `backend/app/clients/sec.py`, `sec_document.py`, `sec_ingest.py` |
| Indexing and retrieval | `backend/app/embeddings/`, `backend/app/search/` |
| Narrative guards | `backend/app/llm/`, `backend/app/digest_narrative.py` |
| Database | `backend/db/init.sql`, `backend/app/db/models.py` |
| iOS transport/models | `ios/FilingDigest/Networking/`, `ios/FilingDigest/Models/` |
| iOS screen state | `ios/FilingDigest/State/` |
| iOS presentation | `ios/FilingDigest/Views/`, `ios/FilingDigest/Theme.swift` |

## API contract v0.3

All JSON fields are snake_case.

| Method | Path | Response |
|---|---|---|
| `GET` | `/health` | Liveness and application version |
| `GET` | `/companies?q=` | Matching companies; empty query returns the corpus |
| `GET` | `/companies/{id}/digest?lang=ko\|en` | Metrics, bilingual summaries, Filing Sources |
| `POST` | `/search` | Up to 50 citation-bearing search hits |
| `POST` | `/answer` | Guarded narrative, structured figures, Citations, and Filing Sources |

Request limits protect model and embedding work: company query 100 characters,
search query 500 characters, answer query 1,000 characters, and period 32
characters. UUID fields are validated by Pydantic.

Ingestion is CLI-only. There is no remote write endpoint.

## Database

`backend/db/init.sql` is the schema source of truth and must match
`backend/app/db/models.py`.

- `companies`: source identity and optional DART/SEC natural keys.
- `filings`: company-owned filing metadata; DART receipt and SEC accession
  numbers are unique, and indexing readiness belongs to the current snapshot.
- `filing_chunks`: filing-owned prose, citation metadata, optional
  `vector(1024)`, unique `(filing_id, chunk_index)`, HNSW cosine index.
- `financials`: filing-owned exact values, unique by Filing Identity, reporting
  period, and Reported Metric. Period kind, available source dates, currency,
  unit, and scale remain explicit.

Foreign keys cascade filing deletion to its Financial Facts and Filing Chunks.
Every Financial Fact has a non-null filing reference.

`backend/db/init.sql` remains the empty-database source of truth. Versioned SQL
migrations under `backend/db/migrations/` upgrade existing local volumes before
new application code runs. Because previous rows discarded some exact source
period dates, migrations never invent them; re-ingestion enriches those fields
when the regulator provides honest dates.

## Key decisions

- **KURE-v1 / 1024 dimensions:** one cross-lingual embedding space for Korean
  and English; vectors are normalized and queried with cosine distance.
- **Structured-number boundary:** exact values never pass through generated
  prose. This is enforced in code, not only in prompts.
- **Authoritative snapshots:** a Normalized Filing replaces its facts and chunks
  atomically; indexing is derived, filing-scoped, and retryable.
- **Explicit evidence identity:** Citations identify Filing Chunks while Filing
  Sources identify openable Corporate Filings. Client metadata heuristics are
  not part of the evidence chain.
- **Canonical financial vocabulary:** the backend owns Reported Metrics,
  Derived Metrics, and Reporting Period kinds; a checked manifest prevents iOS
  vocabulary drift.
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

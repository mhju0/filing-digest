<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/design/logos/mark_dark.png">
  <img src="docs/design/logos/mark_light.png" width="130" alt="[F] — the brackets are the brand: every claim carries a citation">
</picture>

# Filing Digest

**Every claim carries a citation.**

Bilingual (KO/EN) corporate-filings digest & Q&A — DART + SEC EDGAR,<br>
FastAPI + pgvector RAG with deterministic anti-hallucination guards, SwiftUI client.

[![CI](https://github.com/mhju0/filing-digest/actions/workflows/ci.yml/badge.svg)](https://github.com/mhju0/filing-digest/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-1d5c45.svg)](LICENSE)
![Python 3.11](https://img.shields.io/badge/Python-3.11-3776ab.svg)
![iOS 17+](https://img.shields.io/badge/iOS-17%2B-black.svg)

</div>

> **Status**: `v0.2` — feature-complete portfolio project. Everything shown
> below was verified against the live pipeline; run it locally with your own
> DART / Upstage API keys (see [Local setup](#local-setup)). Not actively
> maintained.

Dual-source and live end-to-end: Korean disclosures via DART OpenAPI and US
filings via SEC EDGAR. The corpus holds **8 companies** — Samsung
Electronics, SK Hynix, NAVER, Hyundai Motor (DART annual reports) and Apple,
Microsoft, NVIDIA, Tesla (SEC 10-Ks) — each ingested with one CLI command:

```bash
cd backend
python -m app.ingest --source dart --ticker 000660   # SK Hynix, latest 사업보고서
python -m app.ingest --source sec --ticker NVDA      # NVIDIA, latest 10-K
```

> **Core principle:** every financial figure comes exclusively from a
> structured filing API (DART, SEC) — never from the LLM. The
> LLM writes narrative prose only, and every prose claim carries a citation
> anchor back to a retrieved chunk. This isn't just a prompting convention:
> it's enforced at runtime by deterministic guards that inspect every LLM
> response before it reaches the client (see [Guard pipeline](#guard-pipeline)
> below).

## Architecture

A thin SwiftUI iOS client talks to a FastAPI backend backed by
PostgreSQL + pgvector:

```
SwiftUI (iOS 17+, zero 3rd-party deps) → FastAPI (Python 3.11) → PostgreSQL 16 + pgvector
```

- **Embeddings**: [KURE-v1](https://huggingface.co/nlpai-lab/KURE-v1)
  (`nlpai-lab/KURE-v1`), 1024-dim, cosine distance (`vector_cosine_ops`)
  under an hnsw index. Cross-lingual KO/EN semantic search over
  `filing_chunks`.
- **LLM**: Upstage Solar Pro 3 (`solar-pro3`), called through a thin
  OpenAI-compatible `chat/completions` adapter
  (`backend/app/llm/solar.py`) — prose generation only, never numbers.
- **DART client** (`backend/app/clients/dart.py`, ~1160 lines): live,
  response formats verified against the real API
  (see `docs/dart-api-notes.md`).
- **SEC client** (`backend/app/clients/sec.py`, `sec_document.py`): live —
  submissions + companyfacts APIs, and Item 1/Item 7 10-K prose extraction
  (see [SEC ingest](#sec-ingest) below).

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full system diagram,
decision log, and DB schema.

## Guard pipeline

Numbers and citations are validated by pure, deterministic functions — no
network, no DB, no LLM call — so a hallucination or a fabricated citation is
caught mechanically, not by asking the model nicely twice.

| Guard | File | What it does |
|---|---|---|
| Number guard | `backend/app/llm/number_guard.py` | Scans narrated prose (NFKC-normalized) for suffix-anchored currency (`원`), percentage (`%`), and multiple (`배`) tokens — plus English equivalents: `$` amounts, currency words/codes (`dollars`, `USD`/`KRW`/`EUR`), and `x`/`times` multipliers. Never scans citation ids. |
| Citation guard | `backend/app/llm/citation_guard.py` | Flags any citation id not in the actually-retrieved chunk set (`"unknown"`, real hallucination) and any segment with no citation at all (`"empty"`, nothing groundable). |
| Digest bare-digit floor | `backend/app/digest_narrative.py` (`_ANY_DIGIT_RE`, lines 70, 190–195) | Stricter than the number guard: rejects *any* digit in a generated `/digest` summary — bare counts, years, `$` amounts — since a digest summary must be fully qualitative. |

These guards drive a 3-state `narrative_status` on `POST /answer`
(`backend/app/schemas.py:181-193`, enforced in `backend/app/api/routes.py:288-327`):

- **`ok`** — prose was generated and passed both guards.
- **`blocked`** — the number guard tripped; prose is withheld, `figures` (the
  authoritative numeric track) is still returned.
- **`no_results`** — retrieval was empty, the best chunk scored below the
  similarity floor, or the LLM cited nothing at all. There's nothing
  groundable to narrate, so no narrative was attempted.

`blocked`-by-default is the design goal: suppressing an ungrounded or
number-bearing sentence is treated as success, not failure, since `figures`
never depends on the LLM.

10-K prose extraction (`backend/app/clients/sec_document.py`) strips
`<table>` content entirely before chunking, so financial tables never reach
the guards or the retrieval corpus as narrated text — only clean prose is
indexed.

## SEC ingest

`backend/app/ingest/sec_ingest.py` orchestrates a 10-K end-to-end: SEC's
`submissions` API lists filings and `companyfacts` supplies structured
financials, then `sec_document.py` extracts Item 1/Item 7 prose for chunking
and embedding. SEC's `fy` field describes the *filing's* period, not
necessarily the period each individual fact covers, so fiscal year is
derived from each fact's own `period_end` instead of trusted from `fy`
directly. Filings upsert idempotently on `sec_accession_no` (`filings` table,
`backend/db/init.sql:33`), mirroring the DART path's conflict key.

## API surface

All routes live in `backend/app/api/routes.py`:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness check. |
| `GET` | `/companies?q=` | Case-insensitive substring search over name/name_en/ticker. |
| `GET` | `/companies/{company_id}/digest?lang=ko\|en` | DB-backed metric cards plus a guarded KO/EN business-overview summary. |
| `POST` | `/ingest` | **Stub** — accepts a job, returns `202`, logs it. No worker yet (Phase 2). |
| `POST` | `/search` | Semantic search over `filing_chunks` (KURE-v1, KO/EN cross-lingual). Zero hits is a valid `200`. |
| `POST` | `/answer` | Citation-grounded narrative + authoritative figures, 3-state `narrative_status`. |

`POST /answer` response (`AnswerResponse`, `backend/app/schemas.py:196-217`)
top-level keys: `answer`, `figures`, `citations`, `company_id`,
`narrative_status`.

## Demo script

Four `curl` calls against a locally running backend (`127.0.0.1:8001`): three
DART-sourced calls each landing in a different `narrative_status`, plus one
SEC-sourced call. Look up a company's `company_id` first:

```bash
curl -s "http://127.0.0.1:8001/companies?q=samsung"
# -> {"items": [{"id": "<company_id>", ...}], "total": 1}
```

**1. `ok`** — a grounded business question the corpus can actually answer:

```bash
curl -s -X POST http://127.0.0.1:8001/answer \
  -H "Content-Type: application/json" \
  -d '{"query": "삼성전자의 반도체 사업에 대해 설명해줘", "company_id": "<company_id>"}'
# expected: narrative_status = "ok"
```

**2. `blocked`** — a figure-shaped question that tempts the LLM to state a
number inline (the number guard strips it, `figures` still comes back):

```bash
curl -s -X POST http://127.0.0.1:8001/answer \
  -H "Content-Type: application/json" \
  -d '{"query": "삼성전자 매출액이 얼마야?", "company_id": "<company_id>"}'
# expected: narrative_status = "blocked" (depends on live LLM phrasing —
# guards are deterministic, model output is not)
```

**3. `no_results`** — an off-topic question retrieval can't ground (nothing
clears the similarity floor, so no narrative is attempted):

```bash
curl -s -X POST http://127.0.0.1:8001/answer \
  -H "Content-Type: application/json" \
  -d '{"query": "오늘 날씨 어때?", "company_id": "<company_id>"}'
# expected: narrative_status = "no_results"
```

**4. SEC source** — a business-overview question against Apple's FY2025
10-K (cross-lingual: English filing, Korean narrative):

```bash
curl -s -X POST http://127.0.0.1:8001/answer \
  -H "Content-Type: application/json" \
  -d '{"query": "Tell me about Apple services business", "company_id": "<apple_company_id>"}'
# expected: narrative_status = "ok"
```

## Screenshots

<div align="center">

**Full flow in one loop** — browse → filter → digest → cited answer → guard-blocked figures:

<img src="docs/screenshots/walkthrough.gif" width="240" alt="Full flow: browse, filter, digest, cited Q&A, guard-blocked figures">

| Browse | Digest | Answer | Figures |
|:---:|:---:|:---:|:---:|
| <img src="docs/screenshots/search.png" width="200"> | <img src="docs/screenshots/digest.png" width="200"> | <img src="docs/screenshots/answer_ok.png" width="200"> | <img src="docs/screenshots/answer_figures.png" width="200"> |
| The whole corpus, grouped by source, filtered as you type | Structured figures + guarded KO/EN summary | Citation-anchored narrative segments | Numbers exclusively from the structured filing track |
| **SEC digest** | **3-state answers** | **Dark mode** | **Cross-lingual** |
| <img src="docs/screenshots/digest_sec.png" width="200"> | <img src="docs/screenshots/answer_states.gif" width="200"> | <img src="docs/screenshots/digest_dark.png" width="200"> | <img src="docs/screenshots/search_sec.png" width="200"> |
| Korean summary from an English 10-K | `ok` → `blocked` → `no_results`, rendered live | Warm charcoal counterpart, automatic | Same client, both sources |

</div>

The UI follows the "Ledger" design system — paper/ink palette, one
ledger-green accent, hairline borders, square citation markers, system fonts
only (spec: [docs/design/DESIGN.md](docs/design/DESIGN.md)).

## Known Limitations

- **One filing type per source** — DART 사업보고서 (annual) and SEC 10-K only;
  quarterly/interim reports and DART xforms documents are not parsed yet.
  YoY deltas come from each annual filing's own prior-period figures.
- **Nonexistent-year queries can land in either `blocked` or `no_results`**,
  depending on whether the LLM's refusal states a bare number without a
  citation, or omits citations entirely — the guards are deterministic, but
  the LLM's exact phrasing isn't.
- **`SIMILARITY_THRESHOLD = 0.42`** (`backend/app/search/constants.py:14`)
  is a single cosine-similarity cutoff. A query that shares surface
  vocabulary with an unrelated chunk can still clear it — there's no
  separate semantic-groundedness check, only the similarity floor.
- **`POST /ingest` is a stub** (`backend/app/api/routes.py:318-332`) —
  accepts and logs a job id, but there's no worker; ingestion runs through
  the `python -m app.ingest` CLI instead.

## Stack & tests

- **Backend**: Python 3.11, FastAPI, `httpx` (async, no `requests`),
  SQLAlchemy 2.x + psycopg3, pgvector, `sentence-transformers` (KURE-v1
  loader). Version ranges pinned in `backend/requirements.txt`.
- **iOS**: SwiftUI, iOS 17.0 deployment target, Swift Testing (`import
  Testing`, no XCTest), zero third-party dependencies.
- **Tests**: 368 backend tests (`pytest --collect-only -q`) — all offline
  except `tests/test_smoke.py`, which needs the local Docker DB (CI runs the
  offline suite), plus
  `ios/FilingDigestTests/FilingDigestTests.swift` covering JSON decoding and
  request construction.

## Local setup

```bash
# 1) DB only, in Docker (host port 5433 -> container 5432)
docker compose up -d db

# 2) backend, natively on the host (repo-root venv, Python 3.11)
.venv/bin/pip install -r backend/requirements.txt
cd backend && ../.venv/bin/python -m uvicorn app.main:app --reload --port 8001

# 3) tests
cd backend && ../.venv/bin/python -m pytest
```

Running the backend in a container is opt-in via a compose profile
(`docker compose --profile container up backend`) — the default `docker
compose up` only starts the DB, so host `uvicorn --reload` stays the
canonical way to iterate on backend code.

```bash
cp backend/.env.example backend/.env   # fill in DART_API_KEY, etc.
```

| Variable | Default | Notes |
|----------|---------|-------|
| `DART_API_KEY` | (none) | Secret — never commit a real key. |
| `DART_BASE_URL` | `https://opendart.fss.or.kr/api` | |
| `SEC_BASE_URL` | `https://data.sec.gov` | |
| `SEC_USER_AGENT` | placeholder | SEC requires a User-Agent with contact info. |
| `DATABASE_URL` | `postgresql+psycopg://filing_digest:filing_digest_dev@localhost:5433/filing_digest` | psycopg3 driver; port 5433 is this project's fixed host port. |
| `EMBEDDING_DIM` | `1024` | KURE-v1 dense dimension. |

## iOS

```bash
xcodebuild -project ios/FilingDigest.xcodeproj -scheme FilingDigest -sdk iphonesimulator build
```

Targets iOS 17+, no third-party dependencies, points at
`http://127.0.0.1:8001` by default (run the backend locally first).

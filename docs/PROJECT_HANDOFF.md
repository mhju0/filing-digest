# Project Handoff

A one-file description of what Filing Digest actually is, as of **2026-09-05**,
written so a new agent or engineer can work on it without prior conversation
history.

Scope note: this document records *state and rationale*. It is not a style
guide and it does not tell you how to behave. Design rules live in
[docs/design/DESIGN.md](design/DESIGN.md), domain vocabulary in
[CONTEXT.md](../CONTEXT.md), architecture in
[docs/ARCHITECTURE.md](ARCHITECTURE.md), decision history in
[docs/DECISIONS.md](DECISIONS.md), and forward work in
[docs/ROADMAP.md](ROADMAP.md).

The owner approved the clean-slate Codex takeover on 2026-09-05. Root
[`AGENTS.md`](../AGENTS.md) contains the minimal project instructions;
`CLAUDE.md` and its release-test dependency are retired (DECISIONS D47).
Application behavior and architecture are unchanged. The Claude environment
inventory remains an archive, with no harness configuration migrated.

Claims about code behavior carry a `file:line` reference and a tag:
`[Verified]` = read in the repository during this audit, `[Inferred]` =
reasoned from what was read, `[Unknown]` = not established. Claims whose only
source is prior assistant conversation are marked **(conversation context)**.

---

## 1. Purpose

Filing Digest turns Korean **DART** and US **SEC EDGAR** annual filings into
three products: structured financial figures, short bilingual company digests,
and cross-lingual question answering where every generated sentence resolves to
an excerpt of an original filing.

The organizing constraint, stated in the README and implemented by the separate
financial and narrative paths:

> Numbers come only from structured filing APIs. The LLM writes narrative only.
> Every claim carries a citation. Numeric hallucination is never acceptable.

This is a **portfolio project**, not a service. It is local, single-user, and
has no authentication, authorization, rate limiting, or tenant isolation
(`README.md:265-272`). It shipped **v0.5.1 as its final release** on
2026-08-27 and is in maintenance mode.

---

## 2. Architecture

```text
SwiftUI (iOS 17+) ──HTTP/JSON──> FastAPI (Python 3.11) ──> PostgreSQL 16 + pgvector
                                        ├──> DART OpenAPI (opendart.fss.or.kr)
                                        ├──> SEC EDGAR (data.sec.gov)
                                        ├──> KURE-v1 embeddings (local, sentence-transformers)
                                        └──> Upstage Solar (narrative generation only)
```

Two deployable components: `backend/` (API + ingestion CLI + eval harness) and
`ios/` (SwiftUI client, no third-party packages).

The defining structural choice is that **generated prose and financial values
travel on separate tracks and never merge**. `backend/app/answers/service.py`
[Verified] retrieves chunks before fetching figures, then preserves figures on
handled narrative failures and no-result responses. A retrieval or database
failure can still abort the request; the two paths are not fault-independent.
Q&A citations identify supporting chunks, while digest summaries have
filing-level evidence without per-segment citations (`app/digest_narrative.py`).

---

## 3. Major components

| Area | Location | Owns |
|---|---|---|
| HTTP contract | `backend/app/api/routes.py`, `backend/app/schemas.py` | The five endpoints and their request limits |
| Answer orchestration | `backend/app/answers/service.py` | Two-track answering, narrative state machine |
| Digest orchestration | `backend/app/digests/` | Metric eligibility, period selection, summary |
| Evidence resolution | `backend/app/evidence/service.py` | Citation → Filing Chunk → openable Filing Source |
| Filing domain | `backend/app/filings/model.py`, `persistence.py` | `NormalizedFiling` value objects; the only JSONB boundary |
| Financial vocabulary | `backend/app/financials/`, `contracts/financial-vocabulary.json` | Canonical metric names and period semantics |
| Regulatory adapters | `backend/app/clients/dart.py`, `sec.py`, `sec_document.py` | Raw source parsing (largest files in the repo) |
| Ingest lifecycle | `backend/app/ingest/` | CLI, chunking, one unified pipeline for both sources |
| Retrieval | `backend/app/embeddings/`, `backend/app/search/` | KURE-v1 vectors, HNSW cosine search |
| Guards | `backend/app/llm/number_guard.py`, `citation_guard.py` | Deterministic rejection of numbers and fabricated citations |
| iOS transport/state/views | `ios/FilingDigest/{Networking,State,Views}` | Client; `Models/FigureDisplay.swift` owns KO/EN metric labels |

Backend is ~7,200 lines across 39 modules; the iOS client is ~4,400 lines
across 24 files [Verified, `wc -l`].

---

## 4. Important files and directories

- `AGENTS.md` — minimal tracked project commands, invariants, and evidence order.
  The release-version test checks product metadata and documentation without
  depending on an agent instruction file. Historical `CLAUDE.md` is in Git.
- `CONTEXT.md` — the domain glossary the code is named after. Read before
  renaming anything.
- `contracts/financial-vocabulary.json` — cross-tier metric vocabulary;
  `backend/tests/test_financial_vocabulary_contract.py` and
  `ios/FilingDigestTests/FinancialVocabularyContractTests.swift` both check
  against it, so it is the anti-drift anchor between Python and Swift.
- `backend/db/init.sql` — **the schema source of truth.** Must match
  `backend/app/db/models.py` exactly. There is no Alembic; see §17.
- `backend/db/migrations/0001_normalized_filing_snapshots.sql` — the only
  versioned migration, for upgrading pre-v0.3 local volumes.
- `backend/evals/golden_set.yaml` — 24 live evaluation cases (14 full, 10
  retrieval). Manual, not a CI gate.
- `docs/index.html`, `docs/demo.js`, `docs/styles.css`, `docs/screenshots/` —
  the GitHub Pages walkthrough. `backend/tests/test_portfolio_demo.py`
  [Verified] asserts it is fully local (no `http(s)://` image sources) and
  discloses that it makes no live API calls.
- `docs/design/explorations/` — a static four-direction UI design lab. Not
  shipped product; a comparison artifact.
- `.github/workflows/ci.yml` — backend (PostgreSQL service) + iOS jobs.

---

## 5. Data flow

### Ingestion (CLI only — there is no write endpoint)

1. `python -m app.ingest --source dart|sec --ticker X` resolves the company.
2. The source adapter fetches one annual filing and maps it to a single
   source-independent `NormalizedFiling` (`backend/app/filings/model.py:93-121`
   [Verified]), which validates completeness in `__post_init__` — duplicate
   facts or duplicate chunk indexes raise rather than persist.
3. Tables are stripped before prose extraction, on both sources, so table
   numbers can never re-enter through retrieval (`docs/dart-api-notes.md` §4).
4. `backend/app/filings/persistence.py` atomically replaces that filing's
   Financial Facts and Filing Chunks. It is the only module that serializes a
   typed `FilingChunkLocation` to JSONB.
5. Embedding is a **separate, retryable** step. `filings.indexed_at` stays NULL
   until every chunk in the snapshot is embedded
   (`backend/db/init.sql:39-41` [Verified]).

### Search

`POST /search` embeds the query with the same model, then ranks by pgvector
cosine distance `<=>`, joining through `filings` and requiring
`Filing.indexed_at IS NOT NULL` (`backend/app/search/service.py:172-188`
[Verified]). `top_k` is clamped to `MAX_TOP_K = 50`
(`backend/app/search/constants.py:7` [Verified]). Each hit carries
`filing_period` from the owning filing row (`service.py:178`), which is what
makes the eval harness independent of regenerated UUIDs.

### Answer

1. Retrieval and `fetch_financials` run independently.
2. If there are no chunks, or the best score is below
   `SIMILARITY_THRESHOLD = 0.42`, the response is `no_results` **with figures**
   (`backend/app/answers/service.py:62-67` [Verified]). The threshold is
   empirically calibrated: relevant queries scored ≥0.45, unrelated ≤0.38
   (`backend/app/search/constants.py:11-14`).
3. Chunks go to Solar under positional labels; the response is schema-validated
   and labels are mapped back to real chunk ids.
4. Guards run: citation guard (fabricated/missing citations), number guard
   (currency, percent, multiplier tokens in prose), then evidence integrity.
   Each failure maps to a distinct `blocked_reason` (`backend/app/answers/service.py:73-110`).
5. `resolve_evidence` walks segments in first-citation order and raises
   `EvidenceIntegrityError` if any claim cannot reach an openable Filing Source
   (`backend/app/evidence/service.py:105-153` [Verified]).

### Digest

`select_reporting_periods` chooses the current and prior-year period from
canonical fiscal year, scope, kind, and available source dates — not label
strings. Filing ties break by `filed_at`, `created_at`, then UUID descending
(`backend/app/digests/service.py:56-61` [Verified]). A metric whose Filing
Source is not openable is **omitted with a warning** rather than shown
unsourced (`service.py:90-97`).

---

## 6. External services

| Service | Used for | Credential | Failure behavior |
|---|---|---|---|
| DART OpenAPI | KR company codes, filing list, structured financials, filing documents | `DART_API_KEY` | `DartApiError`; key never logged (masked in `backend/app/logging_config.py`) |
| SEC EDGAR | US company facts and 10-K HTML | `SEC_USER_AGENT` (must carry real contact info) | HTTP errors surface to the CLI |
| Upstage Solar | Narrative and digest summary **only** | `SOLAR_API_KEY` | Answer degrades to `blocked` + figures; digest summary becomes `null` |
| Hugging Face Hub | First-time KURE-v1 model download (~2.2 GB) | none | `EMBEDDING_OFFLINE_FIRST=true` skips the network check when cached |

The LLM is a swappable seam: `backend/app/llm/base.py` defines the protocol and
`solar.py` is one adapter.

---

## 7. Database and state architecture

Four tables, schema **v0.3**, defined in `backend/db/init.sql`:

- `companies` — regulator-scoped identity; `dart_corp_code` and `sec_cik` are
  unique natural keys.
- `filings` — `rcept_no` (DART) and `sec_accession_no` (SEC) are both nullable
  and both UNIQUE. PostgreSQL treats NULLs as distinct, so each source dedupes
  on its own key without colliding with the other (`init.sql:24-33` [Verified]).
- `filing_chunks` — prose, `meta jsonb`, `vector(1024)`, `UNIQUE (filing_id,
  chunk_index)`, HNSW index with `vector_cosine_ops` (`init.sql:66-67`).
  The column is named `meta`, not `metadata`, because `metadata` is reserved on
  SQLAlchemy declarative models.
- `financials` — `numeric(24,4)` exact values, `UNIQUE (filing_id, period, metric)`,
  explicit `period_kind`, nullable `period_start`/`period_end`, `scale`,
  `currency`.

Deletion of a filing cascades to its facts and chunks. Every Financial Fact has
a non-null `filing_id` — this was enforced by migration 0001, which **refuses to
run** if unanchored rows exist rather than inventing a Filing Identity
(`0001_normalized_filing_snapshots.sql:25-32` [Verified]).

There is no application-level cache beyond the digest summary, which is
versioned by filing snapshot (commit `2334ba8`).

**Live local corpus** (verified 2026-09-04 against the running database):
8 companies, 13 filings, 1,191 chunks, 1,191 embedded, 86 financial rows.
DART: 삼성전자, SK하이닉스, NAVER, 현대자동차. SEC: Apple, Microsoft, NVIDIA,
Tesla. This database is local and is not distributed with the repository.

---

## 8. Environment and setup

- **Python 3.11 exactly** (`requires-python == "3.11.*"`). The venv is at the
  repository root `.venv`; run backend commands from `backend/`.
- **The committed default DB port is 5433** (Docker Compose host mapping):
  `backend/app/config.py:47` and `backend/.env.example`.
- **This machine does not use Docker for the database.** The local
  `backend/.env` points at `localhost:5432`, a Homebrew `postgresql@16`
  instance kept running by `brew services` [Verified: `lsof` shows postgres
  listening on 5432; `brew services list` shows `postgresql@16 started`].
  pgvector was built from source for that server because
  `brew install pgvector` only builds for @17/@18. This divergence is
  intentional and verified by read-only inspection; the Docker path is kept in the
  repository because it is what a fresh clone can reproduce.
- **Port 8001, always.** Host `8000` belongs to a neighboring project on this
  machine. Omitting `--port 8001` starts uvicorn on 8000 and collides.
- Secrets live only in `backend/.env` (gitignored). Variable names are in
  `backend/.env.example`; no real values are committed.
- Device builds read the Mac's LAN address from a gitignored
  `ios/Local.xcconfig`, routed through an `FD_SLASH` variable because `//`
  starts a comment in xcconfig (`README.md` "Run on a device").

---

## 9. Build, run, test, lint

```bash
# Backend (from backend/)
../.venv/bin/python -m uvicorn app.main:app --reload --port 8001
../.venv/bin/ruff check .
../.venv/bin/python -m pytest -q --ignore=tests/test_smoke.py   # offline
../.venv/bin/python -m pytest -q                                # + DB smoke tests

# Ingestion (from backend/, DB running)
../.venv/bin/python -m app.ingest --source dart --ticker 000660
../.venv/bin/python -m app.ingest --source sec  --ticker NVDA

# Compose file validation (works without a Docker daemon)
docker compose config -q

# iOS (from repository root) — resolve a simulator at runtime, never by name
simulator=$(xcrun simctl list devices available -j | jq -r '
  [.devices | to_entries[] | select(.key | contains("iOS"))
   | .value[] | select(.name | startswith("iPhone"))][0].udid')
xcodebuild test -project ios/FilingDigest.xcodeproj -scheme FilingDigest \
  -destination "platform=iOS Simulator,id=$simulator" CODE_SIGNING_ALLOWED=NO

# Live evaluation (manual: needs an ingested corpus and a Solar account)
../.venv/bin/python evals/run_eval.py
```

**Verified during the 2026-09-05 takeover audit:**

- `ruff check .` — All checks passed.
- `pytest -q` — **419 passed, 10 skipped** (413 offline + 6 PostgreSQL smoke).
- `xcodebuild test` — **TEST SUCCEEDED**, 37 of 37 iOS tests passed, including
  the `FilingDigestUITests` flow using stubbed HTTP responses.

The ten Python skips are six live DART tests and four persistence tests requiring
an isolated `TEST_DATABASE_URL`. The latter drop tables and are also skipped by
current CI. These results do not establish current regulator or Solar behavior.

Do not pin an iOS simulator by name. Runner images and this Mac carry different
simulator inventories, and a missing name exits 70. `ci.yml` resolves a UDID at
runtime as of `fdb728b`. `backend/tests/test_ci_workflow.py:48` [Verified]
requires the string `platform=iOS Simulator` to remain in the workflow, so do
not remove that part of the destination.

---

## 10. Deployment

**There is no deployment.** Deliberately.

The only published artifact is a static, read-only GitHub Pages walkthrough at
`https://mhju0.github.io/filing-digest/`, served from `docs/` and built entirely
from captured app sessions. It makes no backend connection, no model call, and
carries no credentials — asserted by `backend/tests/test_portfolio_demo.py`.

`docker-compose.yml` and `backend/Dockerfile` remain in the repository as a
reproducible path for someone cloning it. The backend requires the `container`
profile and persists its model cache in `hf_cache`; bare Compose does not start
it. This machine uses host uvicorn with Homebrew PostgreSQL. Running the optional
backend container simultaneously with host uvicorn would contend for port 8001.

---

## 11. What currently works

The takeover audit verified source and test coverage for both ingestion adapters,
the five API endpoints, three-state answers, evidence resolution, bilingual
digests, YoY calculations, and iOS navigation. It also ran the database smoke
suite and confirmed the existing 8-company corpus read-only. These checks did
not re-ingest filings or exercise live generation. Recent GitHub CI and Pages
runs were green; the published walkthrough HTML matched the repository.

The v0.5.1 release notes (2026-08-27) report the latest recorded full benchmark:
24/24 passed, Hit@1 0.900, Hit@3 1.000, MRR 0.950, following PR #7's earlier run.
The owner approved deferring a paid rerun for the takeover. `[Unknown]` whether
those live results still hold today.

---

## 12. What is partially implemented

- **DART coverage is narrow by design.** Only 사업보고서 (annual reports) in DSD
  format are parsed. `xforms` documents are *detected and skipped*
  (`backend/app/clients/dart.py:592-609` [Verified]); attachments are listed in
  `member_names` for diagnostics but never ingested (`dart.py:1169-1170`).
  This is a stated limitation, not a bug.
- **`list_filings` fetches one bounded page.** Callers needing deeper history
  must paginate themselves (`docs/dart-api-notes.md` §2). Pagination was on the
  Phase C "smaller gaps" list and was never built.
- **`?lang=` on the digest endpoint is a display hint only.** The route deletes
  it and returns both summaries (`backend/app/api/routes.py:90` [Verified]).
- **The similarity gate is one calibrated cutoff**, not a groundedness
  classifier. An out-of-corpus numeric question can land in either `blocked` or
  `no_results` depending on nondeterministic wording.
- **The live eval harness is manual**, never a CI gate, because full-tier cases
  spend money.

---

## 13. What is broken

No failing tests were found in the takeover audit. No open GitHub issues, open
PRs, stashes, or tracked `TODO`/`FIXME`/`HACK`/`XXX` markers were found. This is
not proof that live integrations are bug-free; see §§11–12 and §15.

The architecture and v0.5.1 release description now identify `filing-agent` as
planned and deferred. The v0.5.0 release's API v0.3 statement remains historically
correct; v0.5.1 changed the API to v0.4 after removing MetricCard labels.

---

## 14. Git baseline at takeover

- On 2026-09-05, branch `main` matched `origin/main` at **`4b8855d`**
  ("docs: drop dated measurements and repeated port rules from CLAUDE.md",
  2026-09-02). Tracked files were clean before migration edits; the four handoff
  documents were untracked. Use Git directly for subsequent working-tree state.
- 180 commits; tags `v0.2.0` … `v0.5.1`; GitHub Releases published for v0.3.0
  through v0.5.1 (v0.2.0 has a tag but no release).
- `main` was the only remote branch.
- 16 PRs total: #1–#8 feature/release work, #9–#15 Dependabot, and #16
  architecture/ownership documentation. **#14 is the
  only PR closed unmerged** (sentence-transformers 6.x — see DECISIONS).
- **Zero issues** returned by GitHub's open/closed issue listing — despite
  `docs/agents/issue-tracker.md` documenting a full GitHub-Issues workflow. That
  tooling was configured and never used.
- Public repository, no license file, all rights reserved as of 2026-07-30.

---

## 15. Technical debt

Small, and mostly deliberate. In rough order of how likely it is to bite:

1. **Verification gaps.** CI does not load KURE-v1, run paid evaluation, or set
   `TEST_DATABASE_URL` for the four destructive persistence tests. Schema tests
   check selected ORM invariants rather than complete SQL/ORM parity.
2. **`ios/FilingDigest/Networking/UITestTransport.swift`** ships a `#if DEBUG`
   `URLProtocol` with hardcoded JSON fixtures, keyed on a hardcoded UUID
   (`11111111-…`) and activated by a `-ui-testing` launch argument. It is
   production-tree code that exists only for the XCUITest. Correctly gated, but
   it is a test seam living in the app target.
3. **`backend/app/clients/dart.py` is 1,211 lines** — by far the largest module,
   holding encoding detection, format detection, three classes of DSD
   malformation repair, and ZIP member selection. It is heavily commented and
   thoroughly unit-tested, but it is where complexity concentrates.
4. **Local `backend/.env` drift.** It still defines `EMBEDDING_DIM`, a setting
   deleted from `Settings` in `478aaf6` (2026-08-11). It is silently ignored
   (`extra="ignore"` in `config.py:23`) [Verified], and it is also missing
   `EMBEDDING_OFFLINE_FIRST` / `EMBEDDING_WARMUP_ENABLED`, which fall back to
   their defaults. Harmless, but the file no longer mirrors `.env.example`.
   A stale `backend/.env.bak-20260725` also sits beside it, untracked.
5. **Request-scoped Solar clients.** Explicitly chosen for clear ownership over
   connection reuse (`docs/ARCHITECTURE.md`, "Known constraints"). A real cost
   at any load; irrelevant at this one.

---

## 16. Temporary hacks

Only two, both documented at their site:

- **`FD_SLASH` in `ios/Local.xcconfig`.** `//` opens a comment in xcconfig, so a
  literal URL truncates to `http:`. The slashes are routed through a variable.
  A workaround for a file-format quirk, not for a bug in this project.
- **DSD malformation repair.** `backend/app/clients/dart.py` narrowly repairs
  literal ampersands, prose angle quotations, and doubled attribute quotes
  before handing content to `defusedxml`, because DART's DSD output is not
  consistently well-formed XML. Each of the three repair classes was found at
  eight-company scale and has a regression test proven to fail unfixed
  (commit `313856a`).

---

## 17. Implementation conventions and verification limits

These conventions are supported by current source. Test coverage is specific;
it does not enforce every convention or guarantee live integration behavior.

- **No Alembic.** `backend/db/init.sql` is the single schema source and must
  match `backend/app/db/models.py`; `backend/tests/test_database_schema.py`
  checks selected ORM properties. Existing databases are upgraded by versioned SQL under
  `backend/db/migrations/` after a `pg_dump`.
- **XML parsing goes through `defusedxml`** (XXE / billion-laughs).
- **Runtime clients use `httpx` and application logging.** The evaluation CLI
  prints its report to stdout. `httpx2` is present only as
  Starlette's TestClient transport; runtime clients still use `httpx`
  (`backend/requirements.txt` [Verified]).
- **The `crtfc_key` masking filter in `backend/app/logging_config.py` must not
  be bypassed**; `backend/tests/test_logging_masking.py` covers it.
- **Financial Facts and answer figures retain `Decimal`.** Digest metric cards
  convert values to `float` (`backend/app/digests/service.py:101`); the blanket
  claim that EPS never passes through a float was incorrect.
- **The metric vocabulary is mirrored in Python and Swift**, with exhaustive
  tests against `contracts/financial-vocabulary.json`; it is not generated from
  a single runtime definition.
- **`DROP DATABASE filing_digest` destroys the local corpus**, which is not
  reproducible without re-ingesting eight companies through rate-limited
  external APIs. A `pg_dump` backup from 2026-07-14 sits in the gitignored
  `scratchpad/backups/`.

---

## 18. Deferred work and remaining unknowns

- `filing-agent` remains deferred by owner decision (DECISIONS D43). The API
  freeze and maintenance policy stand independently of its implementation.
- Current live benchmark quality is unknown. The approved plan is to rerun
  before a live demonstration or after relevant ingestion, retrieval, embedding,
  or generation changes; see ROADMAP.
- A future KURE-v1 loader security upgrade needs model and corpus validation
  beyond the existing CI gates. No such upgrade is currently queued.

---

## 19. Current development focus

**Maintenance only.** The README defines the entire allowed surface:
security patches, dependency vulnerability fixes, documentation corrections, and
dead-link repairs. New features, database schema changes, API contract changes,
and refactors are out of scope.

The clean-slate takeover is complete. There is no queued feature implementation;
ROADMAP holds the current maintenance state and deferred work.

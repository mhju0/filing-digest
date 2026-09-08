# Engineering audit — 2026-09-05

Scope: owner-authorized cleanup (D48), starting at `d752423`. API v0.4, schema
v0.3, model selection, financial precision, and deployment scope are preserved.

## Changes and evidence

| Change | Why | Verification |
|---|---|---|
| Run inference in worker threads; serialize access to the shared CPU model | Synchronous encoding stalled unrelated async API requests; concurrent cold loads could duplicate the model | Health-during-inference regression, concurrent-load test, actual KURE vector comparison and event-loop measurement |
| Send vector updates with executemany and combine readiness counts | One awaited update per chunk added avoidable database overhead | Actual PostgreSQL alignment, partial failure/resume, count mismatch, readiness, and concurrent replacement tests; timed benchmark below |
| Delete unused operating-margin implementation, SEC mapping wrapper, batching/alignment/query wrappers, and their private-helper tests | No production callers for the first two; ordinary iteration and strict zip express indexing directly | Call-site/history review, offline suite, unchanged OpenAPI; keep `operating_margin` in the wire vocabulary |
| Remove the redundant digest financial scan and temporary Answer objects | Every financial regex requires a digit; the existing stricter normalized digit scan already rejects it | KO/EN, Unicode digit, retry/fallback tests; live evaluation |
| Add `make check`, targeted `make test`, and `make test-db`; run persistence in CI | Four important PostgreSQL tests were previously skipped in CI; local testing required manual database setup | Actual SQL schema + seeded smoke tests + persistence suite in a unique database; injected failure propagates and still removes the database |
| Delete workflow-text and Dependabot-schedule assertions | They enforced incidental syntax and configuration rather than runtime behavior | Execute the actual shared commands and iOS scheme in CI |
| Install CPU PyTorch in CI and Docker | The prior Linux CI install included unused NVIDIA libraries despite `device="cpu"` | Prior CI package log; container build and runtime checks; official [PyTorch CPU installation](https://pytorch.org/get-started/locally/) |

## Measurements

Same machine, PostgreSQL 16 / pgvector 0.8.4, before and after this patch:

- **Indexing:** 96 chunks, normalized 1024-component fixture vectors, batch size
  32, eight runs with the first discarded. Median 140.02 ms → 52.18 ms;
  driver update executions 96 → 3. Encoding was stubbed, so this measures the
  indexing/database path, not total ingestion time or fewer PostgreSQL row updates.
- **Event loop:** a 5 ms heartbeat around eight real KURE encodes of the digest
  overview query, after model warm-up. Maximum gap 520.25 ms synchronously →
  6.92 ms in a worker. Output vectors were exactly equal. A controlled 250 ms
  encoder independently reproduced the issue: 260.80 ms → 7.17 ms maximum gap.
  This is a responsiveness improvement, not a claim of faster model computation.
- **Dependency waste:** [prior CI run](https://github.com/mhju0/filing-digest/actions/runs/33943511811)
  installed roughly 2 GB of NVIDIA wheels plus a 555 MB torch wheel. CPU
  installation removes that unused GPU dependency chain; network/cache differences
  make whole-job timing a poor controlled benchmark.

## Verification

- `make check`: 403 offline tests passed; six live DART and thirteen PostgreSQL
  tests skipped intentionally; Ruff and Compose validation passed.
- `TEST_DATABASE_ADMIN_URL=postgresql:///postgres make test-db`: six smoke and
  thirteen persistence tests passed using the checked-in SQL schema.
- Database-runner failure injection: nonzero test exit preserved, scratch database
  removed. The existing local corpus was never migrated or rewritten.
- Full OpenAPI document equals the baseline at `d752423`; `pip check` passes.
- Container builds successfully. With networking disabled: CPU torch 2.14.0,
  no NVIDIA packages or baked `.env`, model-library import, application startup,
  `/health`, and dependency consistency checks all passed.
- Live API evaluation: **24/24 passed**, including ten retrieval cases with
  Hit@1 0.90, Hit@3 1.00, MRR 0.95. Reports remain in ignored `backend/evals/reports/`.

## Deliberately retained / remaining work

- Citation/number guards, atomic replacement, filing publication locks, and
  digest snapshot validation protect real behavior. The previously reverted
  cache shortcut (`fe64efb`) must not be recreated accidentally.
- iOS request generations prevent stale results; language switching already
  avoids refetching. With eight companies, extra sorting caches or state-layer
  rewrites have no demonstrated value. Static Pages already defers JavaScript,
  lazy-loads lower images, and downloads animations only on demand.
- Regulator parsing retains deliberately defensive handling of irregular real
  payloads. No parser rewrite, model upgrade, schema change, new authentication,
  or production deployment was warranted.
- GitHub: #17 is green and mergeable, left for the owner's requested review.
  No open issues or stalled implementation branches exist. Closed #14 remains
  correctly deferred: a major model-loader upgrade needs its own quality checks.
- A future valuable verification improvement is a legacy-schema migration test
  with a representative old database fixture. Current tests exercise fresh-schema
  behavior; they do not establish upgrade compatibility for every old local corpus.
- Dependency ranges remain unlocked. A future loader/security upgrade should
  capture a resolved environment and repeat model/corpus evaluation; this audit
  does not broaden the dependency-update policy.

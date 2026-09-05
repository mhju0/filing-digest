# Changelog

All notable changes to Filing Digest, reconstructed from the tagged git
history. The project is feature-complete and in maintenance mode as of v0.5.1;
see the Status block in [README.md](README.md) for what maintenance covers.

## v0.5.1 — 2026-08-27

Release-closing pass. No feature work.

**Changed**
- Kept regulator identity on the Corporate Filing: chunking became
  source-neutral and Filing Chunk Locations carry only section and part
  coordinates.
- Moved bilingual metric labels to the iOS `FigureDisplay` module; the digest
  wire contract transports metric keys only.
- Deepened Reporting Period selection so digests choose the current and
  prior-year period from fiscal year, scope, kind, and source dates.

**Added**
- A read-only portfolio walkthrough published from `docs/` to GitHub Pages,
  built entirely from captured app sessions.
- Dependabot coverage for backend pip requirements and GitHub Actions.
- An XCUITest that automates the core iOS user flow, wired into CI.

**Fixed**
- Annual reporting periods now rank above Q4 of the same fiscal year.
- The live evaluation harness compares canonical filing periods instead of
  database UUIDs, so a regenerated corpus no longer invalidates the eval map.

**Documentation**
- Froze the v0.4 API contract for downstream tool callers.
- Synchronized the architecture document with the shipped contract and
  ownership boundaries.

## v0.5.0 — 2026-08-22

**Added**
- The Ledger editorial design system across the iOS client: presentation
  foundation, company index, company folio, and evidence sheets.
- PostgreSQL and iOS jobs in CI, so the schema and the app are both exercised
  on every push.
- Stricter live evaluation contracts on the golden set.

**Changed**
- Moved response orchestration out of the route layer and clarified domain
  dependency boundaries.
- Unified the DART and SEC ingestion lifecycle behind one regulatory pipeline.
- Kept evidence errors in the state layer and aligned iOS browse ownership.
- Removed obsolete architecture scaffolding and redundant per-request work.
- Replaced the MIT license with all-rights-reserved terms.

**Fixed**
- Digest summaries are versioned by filing snapshot.
- Filing readiness is checked before the digest cache is consulted.
- Answer validation error messages are preserved instead of being flattened.

**Performance**
- Cached digests reuse generated summaries after retrieval validates the chunk snapshot.

## v0.4.0 — 2026-07-25

**Added**
- Filing sources open inside the app instead of leaving it.
- Suggested questions that the narrative guard can actually answer.
- A device build path that reaches the host backend over the LAN.

**Fixed**
- Contrast and Dynamic Type baselines across iOS screens.
- Empty, error, and no-result states rewritten for people.
- Asking a second question in the same session.

**Documentation**
- Surfaced the domain glossary and the ADR set; untracked local agent tooling.
- Recaptured the screenshot gallery and social preview against the v0.4 client.

## v0.3.0 — 2026-07-14

**Added**
- A browse-first home screen: the corpus list with type-to-filter search.
- The citation-bracket brand identity — app icon, lockup, and tagline.

**Changed**
- Deepened the filing evidence architecture.
- Recorded the domain model and the first architecture decisions.
- Adopted httpx2 for the Starlette test client and updated CI to Node 24
  runtimes.

**Fixed**
- Aligned the narrative prompt with the number guard's actual scope.
- Hardened containerized backend reliability.

## v0.2.0 — 2026-07-12

Initial development, from an empty scaffold to an eight-company demo corpus.

**Added**
- Backend, iOS client, and Compose scaffolding under the `filing-digest` name.
- DART integration: corpCode lookup with a local snapshot cache, filing list,
  financials with dual net income and decimal EPS, and DSD prose extraction.
- SEC EDGAR integration: companyfacts parsing, 10-K Item 1 and Item 7 prose
  extraction, and a dual-source persistence layer keyed by accession number.
- KURE-v1 embeddings at 1024 dimensions, chunk backfill, and an HNSW index on
  `filing_chunks.embedding`.
- Cross-lingual semantic search behind `POST /search`.
- A swappable LLM provider interface with a Solar adapter, a citation answer
  schema, a deterministic citation guard, and a number guard covering Korean
  and English numerals.
- `POST /answer` combining retrieval, narrative, and figures on separate
  tracks, with a three-state response and a similarity threshold that blocks
  ungrounded queries.
- Guarded KO/EN business-overview summaries and YoY deltas on the digest path.
- A one-command ingestion CLI.
- The offline eval harness and its golden question set.
- Ruff, a GitHub Actions workflow, and the first README badges.

**Changed**
- Restyled the iOS search, digest, and answer screens onto Ledger design
  tokens, with an accessibility pass on the blocked notice and figures callout.
- Removed the fake `/chat` endpoint and its stub data pipeline.
- Moved the API host port to 8001 and the Compose PostgreSQL host port to 5433
  to avoid local collisions.

**Fixed**
- Masked the DART API key in httpx request and source logs.
- Made multi-filing metric, period, and narrative selection deterministic.
- Mapped all-empty citation violations to `no_results` instead of a 500.
- Repaired three DSD malformation classes found at eight-company scale.

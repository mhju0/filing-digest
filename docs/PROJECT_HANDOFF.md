# Project Handoff

Current state of Filing Digest for a new agent or engineer, plus a short
append-only log. Design rules live in [DESIGN.md](design/DESIGN.md), vocabulary
in [CONTEXT.md](../CONTEXT.md), architecture and the API contract in
[ARCHITECTURE.md](ARCHITECTURE.md), setup and tests in
[DEVELOPMENT.md](DEVELOPMENT.md), decision history in
[DECISIONS.md](DECISIONS.md), and forward work in [ROADMAP.md](ROADMAP.md).

## Summary through 2026-10-01

Condensed on 2026-10-02 from the 2026-09-05 takeover audit (sections 1–19) and
the dated entries that followed. Git history has the full text.

### What it is

- An iPhone app (SwiftUI, iOS 17+, no third-party packages) over a local
  FastAPI backend, PostgreSQL 16 + pgvector, KURE-v1 embeddings and an LLM
  (Gemini Flash-Lite since D56; Upstage Solar before). Korean DART 사업보고서 and US SEC 10-K annual filings only.
- Figures come from structured DART/SEC facts and never pass through the
  model. The LLM writes narrative only, under positional citation labels; the
  citation, number and evidence-integrity guards decide whether prose reaches
  the client. Figures survive blocked and no-result answers.
- Local, single-user portfolio project. No authentication, rate limiting or
  deployment, by decision. The only published artifact is the static
  walkthrough at https://mhju0.github.io/filing-digest/ (served from `docs/`,
  no API calls; `backend/tests/test_portfolio_demo.py` asserts this).
- v0.5.1 · API v0.4 · schema v0.3. The release-version test also checks the
  README `**Status:**` line and ARCHITECTURE.md.

### Timeline

- 2026-08-27: v0.5.1 released. Latest full golden-set run (24/24, Hit@1 0.900,
  Hit@3 1.000, MRR 0.950) was recorded on 2026-08-27; reports are in
  `backend/evals/reports/`. The harness is manual because it calls the paid or rate-limited LLM.
- 2026-09-05: Clean-slate Codex takeover (D47). `AGENTS.md` replaced
  `CLAUDE.md`; behavior and architecture were preserved.
- 2026-09-09: D48–D50 release remediation. `make test-db` runs the PostgreSQL
  suites in a disposable database locally and in CI. Compose binds to loopback
  and the API validates Host headers. Install from `backend/requirements.lock`.
  Ten more companies qualified: 18 companies, 23 filings, 153 financial rows,
  2,057 chunks ([coverage](COVERAGE.md), [release](RELEASE_READINESS.md)).
- 2026-09-29: The digest KO/EN toggle carries into answer and evidence screens (D52).
- 2026-10-01: Filing family alignment. Walkthrough "Works with Filing Agent"
  section and shared text steps (D53, #25); English screens (#27); Agent's
  number rules, a search-screen language toggle and the shared
  `contracts/family-glossary.json` (D54, #26).

### This machine

- The database is Homebrew `postgresql@16` on **port 5432** (`brew services`),
  with pgvector built from source. The committed default is Docker on 5433;
  the local `backend/.env` points at 5432. Docker is not running here.
- Run the API on **port 8001** only; 8000 belongs to a neighboring project.
- Secrets live only in the gitignored `backend/.env`. Do not read or commit it.
  The 2026-09-05 audit found it still defines the removed `EMBEDDING_DIM`
  (ignored) and lacks the two newer embedding flags (defaults apply); a stale
  untracked `backend/.env.bak-20260725` sat beside it. Not re-checked since.
- `DROP DATABASE filing_digest` destroys the corpus, which can only be rebuilt
  by re-ingesting through rate-limited APIs. A 2026-07-14 `pg_dump` is in the
  gitignored `scratchpad/backups/`, older than the 18-company expansion.
- Never pin an iOS simulator by name; resolve a UDID at runtime (CI does).

### Conventions worth knowing

- `backend/db/init.sql` is the schema source of truth; no Alembic. Upgrades
  are versioned SQL under `backend/db/migrations/` after a `pg_dump`.
- `contracts/financial-vocabulary.json` anchors metric names across Python and
  Swift; both sides have contract tests.
- `contracts/family-glossary.json` must stay byte-identical to Filing Agent's
  `slice/web/src/glossary.json`.
- XML goes through `defusedxml`. The DART `crtfc_key` masking filter in
  `backend/app/logging_config.py` must not be bypassed.
- Digest metric cards convert values to `float`
  (`backend/app/digests/service.py`); Financial Facts and answer figures keep
  `Decimal`.

### Known limits and technical debt

- DART `xforms` documents and attachments are detected and skipped.
  `list_filings` fetches one page. `?lang=` on the digest endpoint is a
  display hint only.
- The 0.42 similarity gate is one calibrated cutoff, not a groundedness
  classifier; an out-of-corpus numeric question can land in `blocked` or
  `no_results`.
- CI does not load KURE-v1 or run the paid evaluation. Schema tests check
  selected ORM invariants, not full SQL/ORM parity.
- `ios/FilingDigest/Networking/UITestTransport.swift` is a `#if DEBUG` fixture
  transport in the app target, activated by `-ui-testing`.
- `backend/app/clients/dart.py` (about 1,200 lines) concentrates encoding,
  format detection and three narrow DSD malformation repairs, each with a
  regression test.
- LLM clients are request-scoped by choice. `FD_SLASH` in
  `ios/Local.xcconfig` works around `//` starting an xcconfig comment.
- The GitHub Issues workflow in `docs/agents/issue-tracker.md` is configured
  but has never been used.

### Open issues carried forward

- Transport and validation error messages are still Korean-only.
- `CompanyDirectoryTests.ordering()` was reported failing on 2026-09-29 because
  `localizedStandardCompare` depends on the simulator locale. It passed in the
  2026-10-02 unit run on an iPhone 17 Pro simulator; watch it on other locales.
- The full golden set has not been re-run on the 18-company corpus.

## README refresh · 2026-10-02

- What changed: README rewritten around the walkthrough (326 → 81 lines) with a current screenshot (`docs/screenshots/digest-apple-en.png`). Setup, ingestion, tests, device builds and the API table moved word for word to `docs/DEVELOPMENT.md`. The README-only `strip_core.png` and `strip_more.png` were removed.
- Decisions and why: The owner approved the before/after report. The tagline "Every claim carries a citation" was dropped because it overstated the guards. The `**Status:** v0.5.1` line stays because `test_release_version.py` requires it. Facts were re-checked: 460 offline tests passed on Oct 2, and the latest full golden-set run (Aug 27) is 24/24.
- Open issues: Recapture the walkthrough's phone screens and GIFs, which still show the July and early-September design.
- Next step: Recapture the walkthrough screens.

## Ledger links and handoff condensed · 2026-10-02

- What changed:
  - Covered companies (Samsung Electronics, NAVER, Microsoft) link from the digest's Filing Sources section and from the answer evidence sheet to their section of Filing Agent's ledger (D55).
  - `ledger_years` was added to the shared glossary.
  - This file was condensed from 510 lines.
- Decisions and why: The link opens the company's ledger section in the reader's language, not a single row. Citations are passages, and the ledger covers earlier years (2022–2024) than Digest's latest filings, so the row states its years.
- Open issues:
  - The walkthrough recapture is blocked. Upstage Solar returned `403 Forbidden` for every call on 2026-10-02, so the backend served figures only and no real cited answer or digest summary could be captured. Frames that don't need Solar were captured to the session scratchpad only and are not committed.
  - The owner needs to check the Upstage key or credit in `backend/.env`.
- Next step: Once Solar responds, recapture all eight screens, both GIFs and both posters with the temporary UI-test method (real backend, no `-ui-testing`). Update the capture-date disclosure in `docs/index.html` (both languages).

## Gemini replaces Solar · 2026-10-03

- What changed:
  - The narrative model is `gemini-3.5-flash-lite` through Gemini's OpenAI-compatible endpoint (D56).
  - Settings are now `LLM_API_KEY`, `LLM_BASE_URL` and `LLM_MODEL`; the adapter is `app/llm/chat_completions.py`.
  - The eval harness fails `narrative_unavailable` responses and takes `--delay`.
  - Golden set: 24/24 on the 18-company corpus, all 14 full cases with a cited narrative.
- Decisions and why: The Upstage free credit ran out (balance $0.00 in the console), which caused the 403s. The owner chose Gemini's free tier. Full Flash models allow only 20 requests a day on the free tier, so Flash-Lite (15 a minute, 500 a day) is the default.
- Open issues:
  - Google can change free limits without notice; check AI Studio's rate-limit page if calls start returning 429.
  - The local `backend/.env` still has unused `SOLAR_BASE_URL` and `SOLAR_MODEL` lines; they are ignored.
  - The walkthrough recapture is unblocked but not yet done.
- Next step: Recapture the walkthrough screens and GIFs.

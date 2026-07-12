# Roadmap — portfolio-ready filing-digest

Goal: make this repo something a recruiter or hiring engineer can open,
understand in two minutes, and be impressed by — a designed iOS app on top of
an already-solid citation-grounded RAG backend.

State as of 2026-07-12 (full audit: backend, iOS, repo hygiene):

| Area | State |
|---|---|
| Backend logic | Complete: DART + SEC live ingest, KURE-v1 embeddings, deterministic guard pipeline, 3-state `/answer` |
| Tests | 357 offline tests pass (`pytest`, DB-less); 5 smoke tests need the Docker DB |
| README | English, strong, with screenshots and honest limitations |
| iOS presentation | Undesigned: stock SwiftUI, no asset catalog, no app icon, raw strings on screen |
| CI / lint | None — no `.github/`, no ruff config |
| Deep docs | Korean-only (`docs/ARCHITECTURE.md`), linked from the English README |

Design direction for Phase B is locked in
[docs/design/DESIGN.md](docs/design/DESIGN.md) ("Ledger" editorial system,
mockups in `docs/design/mockups/`).

## Phase A — Repo polish (fast wins, do first)

- [x] **CI**: GitHub Actions workflow running `ruff check` + the offline
      pytest suite (`--ignore=tests/test_smoke.py`; smoke needs the local DB).
      Badge added to README. *(2026-07-12)*
- [x] **Lint**: ruff config in `backend/pyproject.toml` (E,F,W,I,UP,B), all
      findings fixed — including a test that was missing its `assert`.
      *(2026-07-12)*
- [x] **English architecture doc**: `docs/ARCHITECTURE.md` is now English
      (canonical, linked from README); Korean original kept as
      `docs/ARCHITECTURE.ko.md`. *(2026-07-12)*
- [x] **README touch-ups**: CI/MIT/Python/iOS badges, test count corrected
      (339 → 368), stale "in-memory stub data" docstring fixed. *(2026-07-12)*

Done when: repo shows a green CI badge and every doc a recruiter can click
from README is English.

## Phase B — iOS redesign ("Ledger", docs/design/DESIGN.md)

- [x] **Design tokens**: `Assets.xcassets` (AccentColor = ledger green,
      Paper/Ink/InkMuted with dark variants), Didot "F." app icon,
      `Theme.swift` (serif display, hairline card, SectionHeader,
      CitationMarker, FlowLayout). Zero third-party deps held. *(2026-07-12)*
- [x] **Restyle screens**: SearchView, DigestView, AnswerView — hairline
      cards, square citation markers, small-caps section rules, pull-quote
      question, green figures callout. Dark mode verified (warm charcoal
      counterpart). *(2026-07-12)*
- [x] **Kill raw strings on screen**: `periodTitle` ("2023-annual" →
      "사업보고서 2023"), `formattedValue` (조/억, T/B/M abbreviation,
      display-only), `generated_at` removed, empty `YoY —` hidden — both new
      pure functions unit-tested. *(2026-07-12)*
- [x] **Missing states**: DigestView no-filings empty state; citation chips
      wrap via a FlowLayout (closes the README known limitation).
      *(2026-07-12)*
- [x] **Accessibility**: labels/traits on badges, markers, cards, rows,
      section headers, send/clear buttons. *(2026-07-12)*
      **String Catalog: deliberately deferred** — the in-app KO/EN toggle is
      this product's bilingual mechanism; device-locale chrome localization
      would add a second competing language system for little demo value.
      Revisit only if the app ships to non-Korean testers.
- [x] **Refresh README media**: all 6 screenshots re-shot on the redesigned
      UI, plus `answer_states.gif` cycling the genuine ok → blocked →
      no_results states; fixed-limitation bullet (non-wrapping citation
      chips) removed from README. *(2026-07-12)*

Done when: screenshots in README look like a designed product, not a default
SwiftUI app.

## Phase C — Backend productization (selective, portfolio depth)

Scope decision (2026-07-12): the demo corpus grows to **8 companies** so
search feels like a product, not a fixture — DART: Samsung Electronics ✓,
SK Hynix, NAVER, Hyundai Motor; SEC: Apple ✓, Microsoft, NVIDIA, Tesla.
Non-financial large caps only. **Financial/holding companies are a
non-goal** (IFRS financial-sector account mapping is a separate project).
Format quirks surfaced by the new companies double as parser-robustness
evidence.

- [ ] **C1 — Ingest CLI**: `python -m app.ingest --source dart|sec
      --ticker …` resolves the company, ingests its latest annual filing
      (DART 사업보고서 / SEC 10-K), and runs the embedding backfill. Closes
      the "logic complete, no trigger" gap.
- [ ] **C2 — Six new companies** via the CLI (SK Hynix, NAVER, Hyundai
      Motor; Microsoft, NVIDIA, Tesla), fixing per-company parser issues as
      they surface. Live-verify /digest and /answer for each.
- [ ] **C3 — YoY for DART**: verify whether prior-period (전기) amounts
      already flow through `fnlttSinglAcntAll` into `financials`; if yes,
      YoY is free — if not, ingest Samsung's 2024 annual report only
      (two-year depth for every company is over-investment). SEC YoY
      already works via companyfacts comparative rows.
- [ ] **C4 — Vector index**: with the corpus at 8 companies, create the
      hnsw index (`vector_cosine_ops`) in `db/init.sql` + live DB + a note
      in the README.
- [ ] **C5 — Docs refresh**: README demo script and Known Limitations
      ("two companies, one filing each") updated to the 8-company corpus.
- [ ] Smaller gaps, if time allows: `/search` period/source filters, DART
      `list_filings` pagination, CORS middleware for on-device testing.

Done when: a stranger can ingest a new company end-to-end with one command,
and searching any household-name KR/US ticker returns a real digest.

## Phase D — Ship

- [ ] Tag `v0.2.0`, write GitHub repo description + topics, set the social
      preview image (use a mockup or the digest screenshot).
- [ ] Optional: 60–90s screen-recording demo video linked from README.

## Non-goals (deliberately out of scope for the portfolio)

- Auth / rate limiting / multi-tenant concerns — single-user demo service.
- Alembic migrations — `db/init.sql` stays the single schema source (D1).
- Production deployment / k8s — local Docker + host uvicorn is the story.
- DART xforms parsing, attachment ingestion — documented Phase-2 TODOs in code.

## Working agreement

Work top-to-bottom within a phase; commit at each verified checkpoint
(conventional commits, English). Every figure-related change must keep the
core principle: numbers only from structured filing APIs, never from the LLM.

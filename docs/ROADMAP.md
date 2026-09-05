# Roadmap

Status as of **2026-09-05**. Filing Digest's latest release is **v0.5.1**
(`507692d`, 2026-08-27). The owner authorized a bounded engineering cleanup
(D48), making an exception to the original no-refactors maintenance policy.
Features, API v0.4, and database schema v0.3 remain frozen.

Definitions used here:
- **Maintenance** — security patches, dependency vulnerability fixes,
  documentation corrections, dead-link repairs. D48 additionally authorizes
  the current cleanup and verification pass.
- **Frozen contract** — the v0.4 API shape in
  [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) stays fixed under the maintenance
  policy, independently of the planned `filing-agent` consumer.

---

## NOW

**Engineering cleanup implemented and ready for review (D48).**
The changes remove unused implementations, offload inference, batch vector writes,
and add disposable PostgreSQL verification. See [the audit record](ENGINEERING_AUDIT.md).
The clean-slate Codex takeover (D47) remains complete.

## NEXT

**Nothing is scheduled.** `filing-agent` remains a planned, deferred project
(D43); the architecture and v0.5.1 release note describe it accordingly.

The cleanup audit passed all 24 live evaluation cases on 2026-09-05. Re-run
before a live demonstration or after further ingestion, retrieval, embedding,
or generation changes; deterministic CI does not establish live-model quality.

## LATER

Reserved for work that only becomes real if the project leaves maintenance mode.
Currently the sole entry:

- **Revisit HNSW build parameters** if the corpus grows by an order of
  magnitude. `m=16, ef_construction=64` are defaults chosen for ~1,200 chunks
  and the index comment in `backend/db/init.sql` says to leave them alone until
  the scale changes.

## BLOCKED

No active work is blocked. The following constraint applies to a future upgrade:

- **`sentence-transformers` 6.x** — cannot be validated by the automation that
  exists. CI never loads the embedding model
  (`EMBEDDING_WARMUP_ENABLED: "false"`) and runs against a seeded SQL corpus, so
  a green CI run would prove nothing about a loader upgrade. Real validation
  needs a re-embedded corpus and a live evaluation pass. PR #14 was closed
  unmerged for this reason; the pin stays at `>=3.0,<6.0`.

## CONSIDERED BUT NOT COMMITTED

Recorded so they are not rediscovered as if new. None of these is planned.

| Idea | Where it came from | Why it is not committed |
|---|---|---|
| `filing-agent`: a LangGraph multi-turn agent consuming the five endpoints as tools | `~/Workspace/Projects/filing-agent/FOUNDATION.md`, 2026-08-27 | Planned separate project; design document only. Owner confirmed deferral on 2026-09-05; implementation requires an explicit decision (D43) |
| `/search` filters by period and source | unchecked leftover in the deleted `ROADMAP.md` (`git show c8f881e^:ROADMAP.md`) | Pre-freeze idea; would change the API contract |
| DART `list_filings` pagination | same | Never needed at eight companies |
| CORS middleware | same | No browser client exists; the iOS app is native |
| Design directions **B Research Desk**, **C Signal Brief**, **D Evidence Thread** | `docs/design/explorations/`, 2026-08-11 | Direction A shipped as Ledger. C additionally needs a defensible *what changed* model this corpus cannot support (D28) |
| String Catalog / device-locale localization | Phase B checklist | The in-app KO/EN toggle **is** the bilingual mechanism; a second language system for chrome adds competing state (D19) |
| Expanding the corpus beyond eight companies | Phase C scope discussion | Adds ingestion cost without changing what the project demonstrates |

## EXPLICITLY REJECTED

Declined on the record. Re-proposing any of these should require a reason that
did not exist when it was rejected.

- **Authentication, rate limiting, multi-tenancy** — single-user local demo;
  adding them would be theater rather than engineering.
- **Production deployment / Kubernetes** — a hosted instance costs money, and an
  unauthenticated public API over a personal DART key is not something to ship.
  The [GitHub Pages walkthrough](https://mhju0.github.io/filing-digest/) is the
  answer instead, and it discloses that it is not live (D34).
- **Alembic, or any migration framework** — `backend/db/init.sql` is the single
  schema source; existing databases move by reviewed versioned SQL after a
  `pg_dump` (D4, D26).
- **DART `xforms` parsing and attachment ingestion** — detected and skipped
  deliberately; a different kind of parser project.
- **Financial and holding companies in the corpus** — IFRS financial-sector
  account mapping is its own project, not a robustness improvement here (D15).
- **An in-app conversational chat surface** — the fake `/chat` endpoint and Chat
  tab were deleted once `/answer` was real, and the app has had no tab bar
  since (D8).
- **Routine Dependabot version bumps** — `open-pull-requests-limit: 0` for both
  ecosystems. Routine bumps are outside the maintenance policy; the policy was
  not widened to match the bot (D41). **Security updates remain enabled.**
- **Re-adding a license** — removed 2026-07-31; the repository is public for
  portfolio review only (D12).

## COMPLETED RECENTLY

Newest first.

| Date | Work | Evidence |
|---|---|---|
| 2026-09-05 | Completed the approved clean-slate Codex takeover; retired the Claude release-test dependency and tracked minimal project instructions | D47, `AGENTS.md`, `backend/tests/test_release_version.py` |
| 2026-09-05 | Clarified that `filing-agent` is deferred and the API freeze stands independently | D40, D43, `docs/ARCHITECTURE.md`, v0.5.1 release note |
| 2026-09-02 | Trimmed dated measurements and duplicated port rules out of `CLAUDE.md` | `4b8855d` |
| 2026-09-01/02 | Landing page scales on one line between the two approved layouts; overflow sweep across 28 viewports | `5be287e` → `b4c536f` |
| 2026-08-30 | Stopped routine dependency PRs; kept security updates | `43b172b` |
| 2026-08-30 | CI resolves an iPhone simulator UDID at runtime instead of pinning a device name | `fdb728b` |
| 2026-08-30 | Closed the `sentence-transformers` 6.x bump as unvalidatable | PR #14 |
| 2026-08-27 | **v0.5.1 released**; contract frozen, project closed for maintenance | `507692d`, tag `v0.5.1` |
| 2026-08-26/27 | Metric presentation ownership moved to iOS; API contract renumbered v0.4 | `3321951`, `af765ed` |
| 2026-08-26 | Reporting Period selection made semantic (annual outranks Q4 of the same year) | `7b56b89`, `b2a05c1` |
| 2026-08-26 | Filing identity kept on the Corporate Filing; chunking made source-neutral | `e579e1d`, `48c16b5`, ADR 0002 |
| 2026-08-22 | Read-only GitHub Pages walkthrough, with no-live-API disclosure enforced by test | PR #8, `f3e8dda` |
| 2026-08-22 | CI gained a real PostgreSQL service and a real iOS simulator job | PR #5 |
| 2026-08-22 | Live evaluation decoupled from database UUIDs | PR #7 |
| 2026-08-11 | Ledger design system implemented across the client | PRs #1, #2 |
| 2026-08-11 | Orchestration moved out of the route layer; DART and SEC ingestion unified | PR #4 |

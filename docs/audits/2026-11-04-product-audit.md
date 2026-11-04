# Product audit, 2026-11-04

## Scope and acceptance

[Verified] Baseline: `526783072b056070471c6f94ef1ca8050e83cb33` on `main`.
The product is a local, single-user iPhone reader for DART/SEC annual filings.
Its public service is the [recorded walkthrough](https://mhju0.github.io/filing-digest/),
served from `main:/docs` by GitHub Pages. There is no hosted application API.
See [maintenance boundaries](../MAINTENANCE.md), [architecture](../ARCHITECTURE.md),
and [visual system](../design/DESIGN.md).

Acceptance was established before implementation:

1. Preserve API v0.4, schema v0.3, the local-only trust boundary, exact Financial
   Facts and `/answer.figures`, and citation-to-source resolution.
2. Reproduce concrete defects before fixing them; preserve successful workflows.
3. Preserve atomic snapshot replacement and publish only completely indexed filings.
4. Pass lint, offline tests, real disposable PostgreSQL tests, and iOS build/tests.
5. Inspect rendered controls, language/theme navigation, narrow layouts, keyboard
   access, large text, and reduced motion while preserving the Ledger design.
6. Independently review the frozen candidate, pass required GitHub checks, merge
   through a PR, and verify the published website after deployment.
7. Leave the owner's corpus, credentials, existing uncommitted work, API contract,
   and deferred feature/model scope intact.

[Verified] Work used an isolated task branch/worktree. Existing iOS configuration
changes in the original checkout were excluded. No production migration, corpus
write, new hosted service, new paid resource, or live paid-provider call was made.

## Findings and implemented corrections

Severity considers the existing local single-user boundary, rather than assuming
an authorized public multi-user API.

| ID | Severity | Finding and evidence | Correction and discriminating verification |
|---|---|---|---|
| A1 | High | [Verified] DART's five HTTP failure paths exposed `crtfc_key` through HTTPX exception URLs and standard tracebacks, outside the logger's masking filter. `backend/app/clients/dart.py`; `backend/tests/test_dart_http_errors.py`. | Centralized request failure boundary rebuilds safe request/response diagnostics, preserves HTTP status and standard transport exception type, suppresses the original displayed chain. Fifteen synthetic-key status/timeout/connect failures failed before and pass after; inspect message, standard traceback, request, response, headers, history and captured logs. No real key was used. |
| A2 | High | [Verified] A 10-K can contain both annual and quarterly facts ending on the same date. SEC selection ignored the start date, so input/tag order could choose Q4 and subsequently label it annual. `backend/app/clients/sec.py`; `backend/app/ingest/sec_ingest.py`. | On equal accession/metric/end, prefer the earlier valid known start before tag priority. Permutation tests cover exact EPS, cross-tag revenue, missing/invalid starts, equal-duration tag priority and comparative years. Four regressions failed before correction. No existing corpus rows were rewritten. |
| A3 | Medium | [Verified] HTTP 200 with malformed provider JSON raised `JSONDecodeError`, bypassing the existing figures-only fallback. `backend/app/llm/chat_completions.py`. | Translate JSON decoding failure to existing `LLMApiError` with static diagnostic text. Adapter and actual adapter-through-route tests prove `/answer` retains exact figures and digest retains metrics when prose is unavailable. |
| A4 | Medium | [Verified] Malformed IPv6 source URLs raised outside `EvidenceIntegrityError`; empty authorities and invalid ports could be accepted. `backend/app/evidence/service.py`. | Catch URL parsing failure and require a hostname, parseable port and no whitespace. Existing HTTP(S)/arbitrary-host compatibility retained. Regressions exercise invalid URLs through both evidence and route fallback paths. |
| A5 | Medium | [Verified] DART/SEC decimal parsing and direct `FinancialFact` construction accepted nonfinite values. `backend/app/clients/{dart,sec}.py`; `backend/app/financials/model.py`. | Parsers omit nonfinite values; the domain rejects bypasses before persistence. Fifteen new pre-fix failures cover NaN/sNaN/infinities, including SEC float input; finite exact values remain covered. No schema migration. |
| A6 | Medium | [Verified] Four lock pins matched published security advisories. | Minimal security updates: anyio 4.14.2, certifi 2026.10.19, filelock 4.2.1, setuptools 84.1.0. OSV: 69 lock entries, four affected packages before, zero findings after. Details below; zero findings is a database scan result, not proof of no vulnerabilities. |
| A7 | Medium | [Verified] Public copy said Digest read a whole filing and implied narrative support could be established by its guards. `docs/index.html`; `docs/walkthrough.js`. | Both languages now describe selected supported sections and explicitly state guards do not establish sentence-level semantic support. New bilingual copy regression failed before and passes after. |
| A8 | Low | [Verified] Keyboard skip link stacked below sticky header; language/theme targets were 36/42px; dark playback hover used a light foreground against a light background. `docs/styles.css`; `docs/typography.css`. | Raised skip-link stacking, 44px control heights, dark hover foreground. Native Chrome confirmed visible keyboard skip navigation and target/layout reflow; hover correction remains source-reviewed rather than physically hovered. |
| A9 | Medium | [Verified] Two complete Korean title fonts required 6,255,608 bytes in TTF format. | Lossless WOFF2 replacements total 1,337,648 bytes, a 78.6% reduction for those two assets. Verified all 14,695/14,697 glyphs, character maps, advance metrics and glyph coordinates match. Font notices unchanged; no glyph subsetting. Korean body/heading identity retained in rendered checks. |
| A10 | Low | [Verified] Korean introduction left a short isolated final line at inspected widths. | Scoped balanced wrapping removes it at inspected desktop/393px widths, preserving Korean word boundaries. This does not certify every possible viewport/font substitution. |

[Verified] Source URLs continue supporting the existing HTTP(S) contract. A new
regulator-domain/HTTPS allowlist was independently considered and rejected as
an unsupported contract/policy expansion. Syntax validation does not certify
that a destination is a regulator or that an external URL remains reachable.

## Security and operational review

- [Verified, source + tests] SQLAlchemy bound statements and literal ILIKE escaping
  handle company queries; UUIDs and bounded request models constrain search and
  answer inputs. No remote ingestion or data-deletion endpoint is exposed.
- [Verified, source + hostile HTTP checks] TrustedHost and browser-origin/fetch-site
  checks reject untrusted hosts, foreign origins, sibling-port origins and
  cross/same-site browser requests before provider/database dependencies. The
  static website makes no API requests. These checks are not authentication,
  authorization or tenant isolation.
- [Verified] No authentication, ownership model, rate limiting or multi-tenancy
  exists, as explicitly documented for local single-user operation. No such
  capability was added. Public API exposure would require a separate phase.
- [Verified, source] Company/search/answer/period lengths are 100/500/1000/32;
  search top-k caps at 50. DART/SEC use 30s HTTP timeouts (10s connect), LLM uses
  120s (10s connect); CPU embeddings run off the event loop under a shared model
  lock. [Unknown] Hostile sustained-load behavior, total HTTP-body limits and
  untrusted-upload/ZIP resource ceilings have not been certified. Ingestion is
  CLI-only and upstream-regulator sourced; no upload UI exists.
- [Verified, source] Public text injection uses fixed repository-owned translations,
  not query-string HTML. Language accepts only `ko`/`en`; localStorage failures
  fall back to usable browsing. No session cookies, remote forms or user-content
  rendering were found in the public pages. CSRF-sensitive account operations
  are absent. This was source review, not a browser exploitation certification.
- [Verified] Public HTTPS is enforced; live response has HSTS and a 600s cache
  policy. GitHub Pages serves public assets with CORS `*`; these are public
  documents, not credentialed API responses. The observed response does not
  provide an application CSP, frame policy or `nosniff` header. No hosting
  migration was introduced solely to change response headers.
- [Verified, source] Compose binds API/DB host ports to loopback; the backend
  container is opt-in, `.dockerignore` excludes environment files, and the
  checked-in database password is an intentionally published local-dev default.
  [Unknown] A fresh full container build was not performed. The container runs
  as root; it is not qualified for public production hosting.
- [Verified] A pattern-only scan of tracked text found no private-key blocks,
  GitHub tokens, Google API keys or AWS access identifiers. Environment files
  were not opened. This is not an exhaustive secret detector or credential audit.
- [Verified, source] LLM keys travel in Authorization headers; runtime routes log
  provider error types. DART log masking remains and exception diagnostics now
  have separate regression coverage. Traceback-local capture and introspection
  of private exception contexts are outside that protection claim.

## Data integrity and financial correctness

[Verified, source + real PostgreSQL tests] Complete facts/chunks replace within one
transaction after natural-key upserts. Indexing begins after commit. Failed
replacement preserves the earlier snapshot; failed embedding batches preserve
committed progress; readiness publication holds the filing row lock and recounts
pending chunks. Retrieval excludes unpublished/incomplete filings. Real tests
exercise rollback, interrupted/resumed indexing, concurrent re-ingestion during
encoding, vector alignment and the readiness lock against a competing writer.
Deletion cascades and fresh-schema/model parity remain tested. These paths were
preserved rather than rewritten.

[Verified, source + tests] Financial Facts and `/answer.figures` preserve Decimal.
YoY requires adjacent matching fiscal scope/kind, currency/unit/scale, compatible
known durations and a positive previous value. Digest metric JSON remains float
under the frozen contract; no lossless-digest-JSON claim is made. The SEC correction
resolves equal-end competing durations; it does not certify quarter-only facts
as annual, transition-period scope, or all business comparability.

[Verified, read-only database check] The current local corpus had 18 companies
(9 DART/9 SEC) and 153 financial rows (88 DART/65 SEC). No nonfinite rows or
known annual durations shorter than 300 days were found. This coarse query does
not certify all amounts, periods or narrative accuracy. The check used a read-only
transaction; no corpus repair or re-ingestion was performed.

[Verified] SEC's [API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
explains facts aggregation and date scopes. Apple's authentic
[2017 10-K, Note 12](https://www.sec.gov/Archives/edgar/data/320193/000032019317000070/a10-k20179302017.htm)
contains annual and quarterly EPS. The regression shape is reconstructed from
that disclosed distinction, not a downloaded companyfacts JSON fixture. Direct
companyfacts access returned 403 during investigation.

## UX, accessibility and motion

[Verified] Before visual edits, rendered desktop review and the existing Ledger
system established the direction: retain warm paper/ink, serif headings, ruled
structure and restrained green. The control map remains hero/navigation →
manual core/answer playback → screen gallery → separate figures/narrative tracks
→ Filing Agent → implementation/contact. Header language/theme controls and
contact-return navigation remain in place; no new feature flow was added.

[Verified, native Chrome interaction] Local candidate: both languages/themes,
repeated language changes during playback, contact/return links preserving
language, visible keyboard skip-link navigation, 320/393/768px header/hero layouts,
200% zoom in English dark mode, and manual play/stop with emulated reduced motion.
Both animations start paused. Font changes retain the rendered character.
[Unknown] This responsive review does not certify every lower-page section at
all widths, hover was not physically exercised, and full screen-reader use was
not performed.

[Verified, iOS automated rendered fixtures] iOS build plus 52 unit tests and six
UI tests passed on a dedicated iPhone simulator/iOS 26.0. UI checks include company
→ digest → cited answer/evidence, KO/EN propagation, XXL text navigation, contrast,
hit regions, descriptions, and summary/figure fallback presentation. Transport
is mocked in these UI tests. Source review found generation/cancellation guards
and stale-result regressions for request state. [Unknown] Physical-device and
full VoiceOver coverage, iOS 17 runtime behavior and the figures disclosure's
reduced-motion behavior remain unverified; no iOS production source was changed.

## Verification ledger

| Evidence | Result and limit |
|---|---|
| Baseline `make check` | [Verified] Ruff + 462 passed/21 skipped + Compose validation. |
| Candidate `make check`, patched environment | [Verified] Ruff + 519 passed/21 skipped + Compose validation. The 57 additional cases include parameterized regressions. |
| Candidate `make test-db` | [Verified] 6 smoke + 13 persistence/concurrency + 2 migration tests pass, on a unique disposable PostgreSQL/pgvector DB removed afterward. No production migration. |
| iOS build/test | [Verified] 52 unit + 6 UI tests pass; no iOS code/config changed by this audit. |
| Patched dependency environment | [Verified] All 65 platform-active pins match; pip check and full-lock resolution dry-run pass. This is an isolated overlay over unchanged qualified packages, not a fresh installation of the entire ML stack. |
| Dependency cache integration | [Verified] HuggingFace lock acquire/exclusion/timeout/reacquire/content preservation pass. Actual cached KURE loaded under patched dependencies and served local HTTP digest/answer requests; no re-embedding or semantic-quality claim. |
| Connected HTTP + actual PostgreSQL | [Verified] Separate seeded DB and loopback API: browse, literal injection-like query, digest with figures and unavailable prose, malformed ID 404, hostile origin 403/host 400, oversized answer 422. Actual cached KURE `/answer` returned no_results with exact `1000000.0000` figure. No provider generation call. |
| Connected native client → API | [Unknown] UI tests use mock transport. Manual simulator-window access was unavailable; separate HTTP evidence is not presented as native end-to-end proof. |
| Public resources | [Verified] Both HTML documents and local image/script/style/font references resolve; hosted baseline resources sampled three times each. Final deployment readback is recorded separately. |

[Verified] The independent initial invariant review approved narrow fixes and
challenged the proposed URL allowlist. Frozen-candidate review and release
results are recorded separately from this candidate report, so implementation,
tests, review and public deployment remain separate evidence states.

## Dependency advisories and qualification

[Verified] Four packages were updated for 14 unique GHSA advisories (PYSEC aliases
deduplicated). Several require Windows, shared writable lock paths or process
helpers not shown exploitable by this local app. Updating affected pins does not
claim all fourteen were remotely exploitable here.

- AnyIO: [TLS hostname encoding](https://github.com/agronholm/anyio/security/advisories/GHSA-82r6-8w77-94w6),
  [supplementary groups](https://github.com/agronholm/anyio/security/advisories/GHSA-3w57-8xmc-8v26),
  [process stderr hang](https://github.com/agronholm/anyio/security/advisories/GHSA-5p39-cfhj-2xmp).
- certifi: [compromised emSign intermediate/root trust](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2).
  Maintainer-recommended 2026.10.19 restores unaffected roots after the initial fix.
- filelock: [4.2.1 security release](https://github.com/tox-dev/filelock/releases/tag/4.2.1)
  fixes nine lock-path/timeout/concurrency advisories. No fixed 3.x release;
  [4.0 breaking change](https://github.com/tox-dev/filelock/releases/tag/4.0.0)
  concerns SoftReadWriteLock's disk protocol. Reviewed HuggingFace uses FileLock/
  SoftFileLock; torch/HuggingFace constraints allow the new version. Relevant lock
  primitives and cached model loading were exercised.
- setuptools: [legacy installation path escape](https://github.com/pypa/setuptools/security/advisories/GHSA-xmj3-9gf7-h52w).

[Verified] No model/transformers/sentence-transformers pin, prompt, retrieval
threshold, embedding implementation or financial vocabulary was changed.

## Performance evidence

[Verified] Same complete title fonts, measured by file bytes: Bold
3,074,720 → 609,112; ExtraBold 3,180,888 → 728,536. Total savings 4,917,960 bytes.
Including unchanged Pretendard, referenced font payload is 8,313,296 → 3,395,336
bytes (59.2% smaller). Browser requests depend on language/weight usage; this is
not a claim that every view loads all three files or that page latency fell 59.2%.

[Verified] Baseline hosted three-sample median total fetches from this machine:
index 35.62ms, contact 46.77ms, Bold TTF 350.97ms, ExtraBold TTF 178.26ms.
These are network/cache-dependent total fetch times, not browser LCP/INP or mobile
cold-start benchmarks. Final comparable samples belong in the deployment record.
No backend latency gain, database speedup or bundle-rendering gain is claimed.

## Remaining risks and release boundary

- [Unknown] Fresh live DART/LLM success, current representative semantic evaluation,
  arbitrary question correctness, live-provider failure recovery, and provider
  quotas were not requalified. Credentials were not read or used. Historical
  24-case results remain dated and cover only three companies' questions.
- [Verified, documented limitation] Citation/number guards do not prove semantic
  entailment, qualifier preservation or complete filing coverage. This audit
  corrected public claims without presenting transport fixes as semantic fixes.
- [Unknown] Full physical-device/VoiceOver acceptance, all responsive lower-page
  combinations, OS-version range, exhaustive load/security testing and a fresh
  Linux/Windows/container installation remain gaps.
- [Verified] Public deployment affects the recorded website only. Backend fixes
  are delivered as source; no hosted inference/API or App Store release is implied.
- [Verified] Rollback is a reviewed revert PR to baseline `5267830` for source and
  Pages; preserve financial corpus separately. No data rollback is needed because
  this release does not migrate or mutate it. Required backend/iOS checks remain
  enabled; Pages may cache assets for up to the observed 600s.

Release follow-through is reported separately after independent review and
[GitHub checks/deployment](https://github.com/mhju0/filing-digest/actions).

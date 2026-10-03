# Development

Local setup, data ingestion, tests and device builds for Filing Digest. The [README](../README.md) gives the overview; [ARCHITECTURE.md](ARCHITECTURE.md) explains the design.

## Local setup

Prerequisites: Python 3.11, Docker with Compose, and Xcode 16 or newer for the
iOS client.

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r backend/requirements.lock
.venv/bin/pip install ruff==0.15.21
cp backend/.env.example backend/.env
docker compose up -d db
cd backend
../.venv/bin/python -m uvicorn app.main:app --reload --port 8001
```

On Linux, preinstall the CPU build with
`.venv/bin/pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu`
before installing requirements, as CI and the Docker image do. This avoids
unused CUDA libraries; see [PyTorch CPU installation](https://pytorch.org/get-started/locally/).

Fill in `backend/.env` before using DART, SEC ingestion, or generated narrative.
The file is ignored by Git. The embedding model is downloaded from Hugging Face
on first use; set `EMBEDDING_WARMUP_ENABLED=false` when you only need lightweight
API or health checks.

| Variable | Required | Purpose |
|---|---:|---|
| `DART_API_KEY` | DART ingestion | OpenDART credential |
| `DART_BASE_URL` | No | Defaults to `https://opendart.fss.or.kr/api` |
| `LLM_API_KEY` | Narrative | Gemini API key (or another OpenAI-compatible provider's) |
| `LLM_BASE_URL` | No | Defaults to `https://generativelanguage.googleapis.com/v1beta/openai` |
| `LLM_MODEL` | No | Defaults to `gemini-3.5-flash-lite` |
| `SEC_BASE_URL` | No | Defaults to `https://data.sec.gov` |
| `SEC_USER_AGENT` | SEC ingestion | Must contain real contact information |
| `DATABASE_URL` | No | Local default targets PostgreSQL on port 5433 |
| `ALLOWED_HOSTS` | No | JSON array of accepted HTTP host names; defaults to localhost and loopback |
| `EMBEDDING_MODEL` | No | Defaults to `nlpai-lab/KURE-v1` |
| `EMBEDDING_OFFLINE_FIRST` | No | Prefer a cached model snapshot |
| `EMBEDDING_WARMUP_ENABLED` | No | Load the model during API startup |

The Compose backend is optional and isolated behind the `container` profile. It
reads the same gitignored `backend/.env` as native uvicorn and persists the
Hugging Face model cache in a named volume:

```bash
docker compose --profile container up -d --build backend
```

Host API and database ports bind to `127.0.0.1`. Physical-device testing over
LAN requires an explicit API bind and adding the Mac's address to
`ALLOWED_HOSTS`. Use a trusted private network and restore loopback afterward;
the API has no authentication. Do not expose PostgreSQL for device testing.

### Upgrade an existing local database

Fresh databases receive the v0.3 schema from `backend/db/init.sql`. Before
running the current application against an older persistent volume, back it up
and apply the versioned migration from the repository root:

```bash
docker compose exec -T db sh -c \
  'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > filing-digest-pre-v0.3.sql
docker compose exec -T db sh -c \
  'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  < backend/db/migrations/0001_normalized_filing_snapshots.sql
cd backend
../.venv/bin/python -m app.embeddings.backfill
```

Use PostgreSQL 16 `pg_dump` and `pg_restore` with this PostgreSQL 16 database.
Check both client versions first; do not use a newer default client blindly.
Restore the backup into a disposable database and verify rows before migration.
Keep dumps outside Git. The legacy v0.2 migration and restore path are covered
by `make test-db`; null filing identities fail and roll back rather than being invented.

The migration never invents historical Reporting Period dates. Re-ingest a
filing to enrich exact dates when its regulator provides them. The final
backfill command publishes `indexed_at` only after every chunk in each filing is
ready, so partially indexed filings stay out of search.

### Ingest data

From `backend/` with the database running:

```bash
../.venv/bin/python -m app.ingest --source dart --ticker 000660
../.venv/bin/python -m app.ingest --source sec --ticker NVDA
```

The reference portfolio corpus used for the screenshots contains four DART
companies (Samsung Electronics, SK Hynix, NAVER, Hyundai Motor) and four SEC
companies (Apple, Microsoft, NVIDIA, Tesla). That database is local and is not
distributed with the repository; a fresh checkout starts empty. As of September
9, 2026, the owner's qualified local corpus adds ten companies beyond that
screenshot set; see [coverage](COVERAGE.md).

### Validate

From the repository root:

```bash
make check                                      # Ruff, offline tests, Compose
make test TESTS='tests/test_search.py -k health'  # targeted test
make test-db                                    # fresh database, smoke + persistence
```

`make test-db` creates a unique temporary database on the server configured by
`DATABASE_URL`, applies the checked-in schema and seed, runs the PostgreSQL
suites, and removes that database even when a test fails. It never resets the
application database. The PostgreSQL role needs `CREATEDB` and permission to
install pgvector. When the application role lacks those privileges, supply a
separate test connection; for local Homebrew PostgreSQL:

```bash
TEST_DATABASE_ADMIN_URL=postgresql:///postgres make test-db
```

`PYTHON` and `RUFF` can point to an existing environment when working in another
checkout, for example `make check PYTHON=/path/to/.venv/bin/python
RUFF=/path/to/.venv/bin/ruff`. No additional worktree configuration is required.

Build the iOS client from the repository root:

```bash
xcodebuild -project ios/FilingDigest.xcodeproj -scheme FilingDigest \
  -destination 'generic/platform=iOS Simulator' build
```

GitHub Actions lints with Ruff, validates the Compose file, applies
`backend/db/init.sql` and the smoke-test seed to a fresh pgvector/PostgreSQL 16
service, runs offline, smoke, and persistence tests through the same Make targets,
then builds the app and runs unit tests plus the core XCUITest flow on a macOS iOS Simulator. The
live evaluation harness remains manual because it requires an ingested corpus
and a configured LLM key. Retrieval cases compare canonical filing periods
returned by the API, so regenerated database UUIDs do not require an eval-map
update; see [`backend/evals/README.md`](../backend/evals/README.md).

### Run on a device

The Simulator shares the host's network stack, so a fresh checkout needs no
configuration: `APIClient` falls back to `http://127.0.0.1:8001`. On a real
device that address is the phone itself, so the build has to be told where the
Mac is. Create `ios/Local.xcconfig`. It is gitignored because a signing
identity and a LAN address belong to one machine, not to the repository:

```text
DEVELOPMENT_TEAM = YOURTEAMID
FD_SLASH = /
FD_BACKEND_URL = http:$(FD_SLASH)$(FD_SLASH)your-mac.local:8001
```

`//` opens a comment in an xcconfig, so a literal URL silently truncates to
`http:`; routing the slashes through `FD_SLASH` avoids it. The value reaches the
app as `FDBackendURL` in `ios/FilingDigest-Info.plist`, and an unset or
malformed value falls back to loopback rather than failing the build.

```bash
xcodebuild -project ios/FilingDigest.xcodeproj -scheme FilingDigest \
  -xcconfig ios/Local.xcconfig -destination 'id=<device-udid>' \
  -allowProvisioningUpdates build
```

Serve on the LAN with `--host 0.0.0.0`, and note that this exposes an
unauthenticated API to everyone on the same Wi-Fi. macOS also blocks incoming
connections to the venv Python binary by default; allow it in System Settings →
Network → Firewall. App Transport Security permits the plain-HTTP dev server
through `NSAllowsLocalNetworking`, which covers `.local` and unqualified
hostnames only, not arbitrary internet loads.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Process liveness and version |
| `GET` | `/companies?q=` | Company browse/filter data |
| `GET` | `/companies/{company_id}/digest?lang=ko\|en` | Metrics, summaries, and Filing Sources |
| `POST` | `/search` | Bounded semantic search over filing chunks |
| `POST` | `/answer` | Guarded narrative, figures, Citations, and Filing Sources |

Ingestion is intentionally CLI-only. The application does not expose a remote
write endpoint.

Digest metric cards transport `key`, `value`, `unit`, `yoy_delta_pct`, `source`,
and `filing_source_id`. Presentation labels are deliberately absent from the
wire contract and are resolved by the iOS `FigureDisplay` module.


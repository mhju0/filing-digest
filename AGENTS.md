# Filing Digest

Evidence order: current source/tests → current Git state/history →
`docs/DECISIONS.md` → `docs/ROADMAP.md` → `docs/PROJECT_HANDOFF.md` → historical
Claude material only when additional context is needed.

Python is exactly 3.11; the virtualenv lives at the repository root. Backend
dependencies are maintained in `backend/requirements.txt`, not `pyproject.toml`.

```sh
# Install from the repository root in a fresh environment
python3.11 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt ruff==0.15.21

# Backend commands, from backend/
../.venv/bin/python -m uvicorn app.main:app --reload --port 8001
../.venv/bin/ruff check .
../.venv/bin/python -m pytest -q --ignore=tests/test_smoke.py
```

Keep `DART_API_KEY` and `TEST_DATABASE_URL` unset for the offline suite.
Omit `--ignore` to include the read-only database smoke tests. Persistence tests
activated by `TEST_DATABASE_URL` drop tables; use only an isolated `*_test` database.

From the repository root, validate Compose with
`docker compose --profile container config -q`. The backend container is opt-in.
This machine's existing `backend/.env` uses Homebrew PostgreSQL on 5432; the
fresh-clone Compose default is 5433. Preserve the local corpus and back it up
before applying SQL migrations. Host API port is 8001; container-internal is 8000.
The API has no authentication; keep it local.

For iOS build and tests, select an installed iPhone UDID with
`xcrun simctl list devices available` (CI contains an automatic resolver):

```sh
xcodebuild test -project ios/FilingDigest.xcodeproj -scheme FilingDigest \
  -destination 'platform=iOS Simulator,id=<available-UDID>' CODE_SIGNING_ALLOWED=NO
```

- Financial values come from structured DART/SEC data. Preserve `Decimal` in
  Financial Facts and `/answer.figures`; narrative guards must preserve figures.
  Q&A citations must resolve through chunks to openable Filing Sources.
- Replace complete Normalized Filings atomically, then index after commit.
  Retrieval exposes only fully indexed filings. Regulator identity belongs to
  the filing; chunk locations are source-neutral, serialized in
  `backend/app/filings/persistence.py`.
- KURE-v1 vectors are normalized, 1024-dimensional, and searched by cosine;
  vector indexes use `vector_cosine_ops`.
- `backend/db/init.sql` defines fresh schemas and must stay aligned with
  `backend/app/db/models.py`; existing databases use `backend/db/migrations/` SQL.
- Backend metric vocabulary and the Swift enums must match
  `contracts/financial-vocabulary.json`; iOS `FigureDisplay` owns KO/EN labels.
- DART XML uses `defusedxml`; preserve `crtfc_key` redaction in `logging_config.py`.
- Update `docs/DECISIONS.md` for significant technical/product decisions,
  `docs/ROADMAP.md` for material roadmap changes, and `docs/PROJECT_HANDOFF.md`
  only for material high-level state or architecture changes; omit routine details.

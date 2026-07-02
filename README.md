# filing-digest

Bilingual (KO/EN) filings & earnings digest.
Fetches Korean disclosures (DART) and US filings (SEC EDGAR), then produces
citation-grounded summaries and Q&A. Numbers come only from structured data;
the LLM narrates and never invents figures.

Stack: FastAPI + PostgreSQL(pgvector) backend · SwiftUI iOS client.

## Monorepo Layout

```
filing-digest/
├── backend/                 # FastAPI backend (Python 3.11)
│   ├── app/                 #   routers, pydantic schemas, settings, stub data
│   ├── db/init.sql          #   DB schema v0.1 (mounted into postgres on first boot)
│   ├── tests/               #   pytest
│   ├── Dockerfile
│   ├── requirements.txt
│   └── .env.example         #   copy to backend/.env and fill in placeholders
├── ios/                     # SwiftUI client (Haeksim.xcodeproj, iOS 17+, no 3rd-party deps)
├── docs/                    # architecture & decision log (docs/ARCHITECTURE.md)
├── docker-compose.yml       # local dev stack (postgres+pgvector, backend)
└── README.md
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the system overview,
API contract v0.1, DB schema, and the decision log.

## Backend Quick Start (local, without Docker)

Uses the repo-root virtualenv at `.venv` (Python 3.11):

```bash
# 1) install dependencies
.venv/bin/pip install -r backend/requirements.txt

# 2) run tests
cd backend && ../.venv/bin/python -m pytest

# 3) run the API server (http://127.0.0.1:8000)
cd backend && ../.venv/bin/python -m uvicorn app.main:app --reload
```

v0.1 serves deterministic stub data (Samsung Electronics / Apple Inc.), so the
API works without a database or external API keys. Smoke test:

```bash
curl http://127.0.0.1:8000/health
curl "http://127.0.0.1:8000/companies?q=samsung"
```

## Docker Compose

Brings up postgres 16 + pgvector (port 5432, schema from `backend/db/init.sql`)
and the backend (port 8000):

```bash
docker compose up --build
```

Secrets are injected from the host environment via compose variable
substitution — nothing is hardcoded in `docker-compose.yml`. The default
postgres credentials (`haeksim` / `haeksim-dev`) are local-development-only
defaults, not production passwords.

## iOS Build

Requires Xcode with the iOS 17 SDK:

```bash
xcodebuild -project ios/Haeksim.xcodeproj -scheme Haeksim -sdk iphonesimulator build
```

The app targets iOS 17+, uses no third-party dependencies, and points at
`http://127.0.0.1:8000` by default (run the backend locally first).

## .env Configuration

The backend reads settings via pydantic-settings from `backend/.env`:

```bash
cp backend/.env.example backend/.env
```

| Variable | Default | Notes |
|----------|---------|-------|
| `DART_API_KEY` | (none) | Secret. Use a placeholder in examples; never commit a real key. |
| `DART_BASE_URL` | `https://opendart.fss.or.kr/api` | DART Open API base URL. |
| `SEC_BASE_URL` | `https://data.sec.gov` | SEC EDGAR base URL. |
| `SEC_USER_AGENT` | placeholder | SEC requires a User-Agent that includes contact info — set your own. |
| `DATABASE_URL` | `postgresql+psycopg://haeksim:haeksim-dev@localhost:5432/haeksim` | psycopg3 driver; docker compose overrides host to `db`. |
| `EMBEDDING_DIM` | `1536` | Inferred default; embedding model is fixed in Phase 2. |

Do not commit real secrets. `backend/.env` is git-ignored; only
`backend/.env.example` (placeholders) is tracked.

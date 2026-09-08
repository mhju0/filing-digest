PYTHON ?= $(CURDIR)/.venv/bin/python
RUFF ?= $(CURDIR)/.venv/bin/ruff

.PHONY: check lint test test-db compose

check: lint test compose

lint:
	cd backend && "$(RUFF)" check .

# Target individual tests with: make test TESTS='tests/test_search.py -k responsiveness'
test:
	cd backend && env -u DART_API_KEY -u TEST_DATABASE_URL EMBEDDING_WARMUP_ENABLED=false "$(PYTHON)" -m pytest -q --ignore=tests/test_smoke.py $(TESTS)

test-db:
	cd backend && "$(PYTHON)" -m tests.run_postgres

compose:
	docker compose --profile container config -q

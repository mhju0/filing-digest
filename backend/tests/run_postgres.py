"""Run database tests on a fresh disposable database, never the local corpus.

Uses DATABASE_URL's server/role by default. Set TEST_DATABASE_ADMIN_URL when
the application role cannot create databases (for example, postgresql:///postgres
with a local Homebrew superuser). Each run owns a unique *_test database.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app.config import get_settings

BACKEND = Path(__file__).resolve().parents[1]


def main() -> None:
    admin_url = make_url(
        os.environ.get("TEST_DATABASE_ADMIN_URL") or get_settings().database_url
    ).set(drivername="postgresql")
    database = f"filing_digest_{uuid.uuid4().hex}_test"
    test_url = admin_url.set(database=database)
    with psycopg.connect(
        admin_url.render_as_string(hide_password=False), autocommit=True
    ) as admin:
        # Keep creation outside the try: never drop a database we did not create.
        try:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        except psycopg.errors.InsufficientPrivilege:
            raise SystemExit(
                "Database tests need a role with CREATEDB. Set TEST_DATABASE_ADMIN_URL "
                "to a test PostgreSQL server with pgvector installed."
            ) from None
        try:
            print(f"Database tests: {database} (removed on exit)", flush=True)
            with psycopg.connect(
                test_url.render_as_string(hide_password=False), autocommit=True
            ) as connection:
                for path in ("db/init.sql", "tests/fixtures/ci_smoke_seed.sql"):
                    connection.execute((BACKEND / path).read_text())

            environment = os.environ.copy()
            environment.pop("DART_API_KEY", None)
            environment.update(
                DATABASE_URL=test_url.set(drivername="postgresql+psycopg").render_as_string(
                    hide_password=False
                ),
                EMBEDDING_WARMUP_ENABLED="false",
            )
            environment["TEST_DATABASE_URL"] = environment["DATABASE_URL"]
            # Smoke tests consume the seed. Persistence tests then replace it
            # per test using the actual SQL schema and real transactions.
            for suite in ("test_smoke.py", "test_normalized_filing_persistence.py"):
                subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", f"tests/{suite}"],
                    cwd=BACKEND,
                    env=environment,
                    check=True,
                )
        finally:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from None

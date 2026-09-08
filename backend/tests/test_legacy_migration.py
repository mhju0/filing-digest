"""Qualify the stored pre-snapshot schema in a runner-owned database."""
import os
from pathlib import Path

import psycopg
import pytest
from sqlalchemy.engine import make_url

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="isolated TEST_DATABASE_URL required")
ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("anchored", [True, False])
def test_legacy_migration_preserves_values_or_rolls_back(anchored):
    url = make_url(URL).set(drivername="postgresql")
    if not (url.database or "").endswith("_test"):
        raise RuntimeError("migration test requires a disposable *_test database")
    with psycopg.connect(url.render_as_string(hide_password=False), autocommit=True) as db:
        db.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        db.execute((ROOT / "tests/fixtures/legacy-schema-v02.sql").read_text())
        company = db.execute("INSERT INTO companies (name, source) VALUES ('Migration fixture', 'dart') RETURNING id").fetchone()[0]
        filing = db.execute("INSERT INTO filings (company_id,source,rcept_no,filing_type,title) VALUES (%s,'dart','migration-fixture','business_report','Migration fixture') RETURNING id", (company,)).fetchone()[0]
        db.execute("INSERT INTO financials (company_id,filing_id,fiscal_year,period,metric,value,unit,currency,source) VALUES (%s,%s,2023,'2023-annual','revenue',123456789.1234,'KRW','KRW','dart')", (company, filing if anchored else None))
        migration = (ROOT / "db/migrations/0001_normalized_filing_snapshots.sql").read_text()
        if not anchored:
            with pytest.raises(psycopg.errors.RaiseException, match="Filing Identity"):
                db.execute(migration)
            db.execute("ROLLBACK")
            assert db.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='financials' AND column_name='scale'").fetchone()[0] == 0
        else:
            db.execute(migration)
            assert db.execute("SELECT period_kind,scale,period_start,period_end FROM financials").fetchone() == ('duration', 1, None, None)
            assert db.execute("SELECT indexed_at FROM filings").fetchone()[0] is None
        assert str(db.execute("SELECT value FROM financials").fetchone()[0]) == '123456789.1234'

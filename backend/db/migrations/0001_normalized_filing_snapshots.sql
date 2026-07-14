-- Upgrade the pre-migration schema to DB SCHEMA v0.3 atomic Normalized Filing snapshots.
--
-- Run once against an existing database after taking a backup. This migration
-- refuses to continue if legacy unanchored Financial Facts exist; their Filing
-- Identity cannot be reconstructed honestly. Exact period dates stay NULL and
-- must be populated by source re-ingestion rather than guessed.

BEGIN;

ALTER TABLE filings
    ADD COLUMN IF NOT EXISTS indexed_at timestamptz;

ALTER TABLE financials
    ADD COLUMN IF NOT EXISTS period_kind text,
    ADD COLUMN IF NOT EXISTS period_start date,
    ADD COLUMN IF NOT EXISTS period_end date,
    ADD COLUMN IF NOT EXISTS scale bigint;

-- Every existing mapped metric is a duration measure and existing values are
-- stored as absolute currency/per-share amounts, so these two backfills preserve
-- known semantics. Exact dates are deliberately not inferred from period labels.
UPDATE financials SET period_kind = 'duration' WHERE period_kind IS NULL;
UPDATE financials SET scale = 1 WHERE scale IS NULL;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM financials WHERE filing_id IS NULL) THEN
        RAISE EXCEPTION
            'financials contains rows without Filing Identity; re-ingest or repair them before migration';
    END IF;
END
$$;

ALTER TABLE financials
    ALTER COLUMN filing_id SET NOT NULL,
    ALTER COLUMN period_kind SET NOT NULL,
    ALTER COLUMN period_kind SET DEFAULT 'duration',
    ALTER COLUMN scale SET NOT NULL,
    ALTER COLUMN scale SET DEFAULT 1;

ALTER TABLE financials
    DROP CONSTRAINT IF EXISTS financials_filing_id_fkey,
    ADD CONSTRAINT financials_filing_id_fkey
        FOREIGN KEY (filing_id) REFERENCES filings(id) ON DELETE CASCADE;

ALTER TABLE financials
    DROP CONSTRAINT IF EXISTS financials_company_id_period_metric_source_key,
    DROP CONSTRAINT IF EXISTS financials_filing_id_period_metric_key,
    ADD CONSTRAINT financials_filing_id_period_metric_key
        UNIQUE (filing_id, period, metric);

ALTER TABLE financials
    DROP CONSTRAINT IF EXISTS financials_period_kind_check,
    DROP CONSTRAINT IF EXISTS financials_scale_positive_check,
    DROP CONSTRAINT IF EXISTS financials_period_range_check,
    ADD CONSTRAINT financials_period_kind_check
        CHECK (period_kind IN ('instant', 'duration')),
    ADD CONSTRAINT financials_scale_positive_check CHECK (scale > 0),
    ADD CONSTRAINT financials_period_range_check
        CHECK (period_start IS NULL OR period_end IS NULL OR period_start <= period_end);

COMMIT;

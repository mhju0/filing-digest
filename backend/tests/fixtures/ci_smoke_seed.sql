-- Minimal deterministic corpus for DB-backed endpoint smoke tests in CI.

INSERT INTO companies (
    id,
    name,
    name_en,
    ticker,
    market,
    source,
    sec_cik
) VALUES (
    '11111111-1111-4111-8111-111111111111',
    'CI 테스트 회사',
    'CI Test Company',
    'CITEST',
    'NASDAQ',
    'sec',
    '0000000001'
);

INSERT INTO filings (
    id,
    company_id,
    source,
    sec_accession_no,
    filing_type,
    title,
    period,
    filed_at,
    url,
    indexed_at
) VALUES (
    '22222222-2222-4222-8222-222222222222',
    '11111111-1111-4111-8111-111111111111',
    'sec',
    'ci-smoke-2025',
    '10-K',
    'CI Test Company Annual Report',
    '2025-annual',
    '2026-02-01',
    'https://www.sec.gov/Archives/edgar/data/1/ci-smoke-2025.txt',
    now()
);

INSERT INTO financials (
    company_id,
    filing_id,
    fiscal_year,
    period,
    period_kind,
    period_start,
    period_end,
    metric,
    value,
    unit,
    currency,
    source
) VALUES (
    '11111111-1111-4111-8111-111111111111',
    '22222222-2222-4222-8222-222222222222',
    2025,
    '2025-annual',
    'duration',
    '2025-01-01',
    '2025-12-31',
    'revenue',
    1000000,
    'USD',
    'USD',
    'sec'
);

-- DB SCHEMA v0.1 for filing-digest backend.
-- Intended for docker-entrypoint-initdb.d (Postgres 16 + pgvector image).
-- Must stay in exact sync with backend/app/db/models.py.
-- Note: pg16 has gen_random_uuid() built in; only the vector extension is needed.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS companies (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    name_en text,
    ticker text,
    market text,
    source text NOT NULL CHECK (source IN ('dart', 'sec')),
    dart_corp_code text UNIQUE,
    sec_cik text UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS filings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    source text NOT NULL,
    -- DART 접수번호. DART filing의 자연키이자 financials/document 조인키.
    -- SEC filing은 rcept_no가 없으므로 nullable. Postgres는 NULL을 서로 distinct로
    -- 취급하므로 UNIQUE 아래에서도 여러 SEC(NULL) row는 충돌하지 않고, DART rcept_no
    -- 끼리만 dedup된다. 이 UNIQUE가 ON CONFLICT (rcept_no) DO UPDATE의 inference target.
    rcept_no text UNIQUE,
    -- SEC accession number. SEC filing의 자연키; DART filing은 이 값이 없으므로
    -- nullable. Postgres는 NULL을 서로 distinct로 취급하므로 여러 DART(NULL) row는
    -- 이 UNIQUE에서 충돌하지 않고, SEC sec_accession_no끼리만 dedup된다. 이 UNIQUE가
    -- ON CONFLICT (sec_accession_no) DO UPDATE의 inference target.
    sec_accession_no text UNIQUE,
    filing_type text NOT NULL,
    title text NOT NULL,
    period text,
    filed_at date,
    url text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_filings_company ON filings(company_id);

-- Column is named "meta" (not "metadata"): 'metadata' is a reserved attribute
-- name on SQLAlchemy Declarative models.
CREATE TABLE IF NOT EXISTS filing_chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    filing_id uuid NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
    chunk_index int NOT NULL,
    content text NOT NULL,
    -- [Verified] vector(1024): KURE-v1 (nlpai-lab/KURE-v1) dense dimension.
    -- Source: HuggingFace config.json hidden_size=1024 (bge-m3 / XLM-RoBERTa base).
    embedding vector(1024),
    meta jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (filing_id, chunk_index)
);

-- hnsw over cosine distance (<=>), matching the /search ORDER BY. Default
-- build parameters (m=16, ef_construction=64) are appropriate at the current
-- corpus scale (~1k chunks); revisit only if the corpus grows by orders of
-- magnitude. Resolves the former Phase-2 TODO (decision D8).
CREATE INDEX IF NOT EXISTS idx_filing_chunks_embedding
    ON filing_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS financials (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    filing_id uuid REFERENCES filings(id) ON DELETE SET NULL,
    fiscal_year int NOT NULL,
    fiscal_quarter int,
    period text NOT NULL,
    metric text NOT NULL,
    value numeric(24, 4) NOT NULL,
    unit text NOT NULL,
    currency text,
    source text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, period, metric, source)
);

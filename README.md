# filing-digest

Bilingual (KO/EN) filings & earnings digest.
Fetches Korean disclosures (DART) and US filings (SEC EDGAR), then produces
citation-grounded summaries and Q&A. Numbers come only from structured data;
the LLM narrates and never invents figures.

Stack: FastAPI + PostgreSQL(pgvector) backend · SwiftUI iOS client.

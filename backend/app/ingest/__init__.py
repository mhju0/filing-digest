"""Regulatory filing adapters and their shared ingestion lifecycle."""

from app.ingest.pipeline import IngestResult, RegulatoryFilingAdapter, ingest_filing

__all__ = ["IngestResult", "RegulatoryFilingAdapter", "ingest_filing"]

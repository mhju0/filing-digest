"""Company digest application module."""

from app.digests.service import (
    CompanyNotFoundError,
    build_company_digest,
)

__all__ = [
    "CompanyNotFoundError",
    "build_company_digest",
]

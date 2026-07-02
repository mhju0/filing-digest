"""In-memory stub data for Phase 1 (no DB required).

Two fixed companies: Samsung Electronics (dart/KOSPI/005930) and
Apple Inc. (sec/NASDAQ/AAPL) with fixed UUIDs so clients can hardcode ids
during development.

Principle (applies to real implementation too): numbers come only from
structured APIs (DART/SEC structured data); the LLM narrates only; every
claim carries a citation. Even these stub MetricCard values link to a stub
Citation via citation_id.

All figures below are placeholder stub numbers, not real financials.
"""

import logging
from datetime import datetime, timezone

from app.schemas import (
    ChatResponse,
    Citation,
    Company,
    CompanyDigest,
    Language,
    MetricCard,
)

logger = logging.getLogger(__name__)

# Fixed stub UUIDs (deterministic; safe to hardcode in clients during Phase 1).
SAMSUNG_ID = "11111111-1111-4111-8111-111111111111"
APPLE_ID = "22222222-2222-4222-8222-222222222222"

STUB_COMPANIES: dict[str, Company] = {
    SAMSUNG_ID: Company(
        id=SAMSUNG_ID,
        name="삼성전자",
        name_en="Samsung Electronics Co., Ltd.",
        ticker="005930",
        market="KOSPI",
        source="dart",
    ),
    APPLE_ID: Company(
        id=APPLE_ID,
        name="Apple Inc.",
        name_en="Apple Inc.",
        ticker="AAPL",
        market="NASDAQ",
        source="sec",
    ),
}

_SAMSUNG_CITATIONS = [
    Citation(
        id="cit-samsung-2026q1-report",
        source="dart",
        title="삼성전자 분기보고서 (2026.03)",
        url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=STUB-20260515",
        excerpt="당사의 2026년 1분기 매출액은 79.1조원, 영업이익은 6.6조원입니다. (스텁 발췌)",
        filed_at="2026-05-15",
    ),
]

_APPLE_CITATIONS = [
    Citation(
        id="cit-apple-2026q1-10q",
        source="sec",
        title="Apple Inc. Form 10-Q (Q2 FY2026)",
        url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193&type=10-Q",
        excerpt="Total net sales were $95.4 billion for the quarter. (stub excerpt)",
        filed_at="2026-05-01",
    ),
]

_SAMSUNG_METRICS = [
    MetricCard(
        key="revenue",
        label_ko="매출액",
        label_en="Revenue",
        value=79_100.0,
        unit="KRW_bn",
        yoy_delta_pct=9.8,
        source="dart",
        citation_id="cit-samsung-2026q1-report",
    ),
    MetricCard(
        key="operating_income",
        label_ko="영업이익",
        label_en="Operating Income",
        value=6_600.0,
        unit="KRW_bn",
        yoy_delta_pct=-12.3,
        source="dart",
        citation_id="cit-samsung-2026q1-report",
    ),
    MetricCard(
        key="net_income",
        label_ko="당기순이익",
        label_en="Net Income",
        value=5_750.0,
        unit="KRW_bn",
        yoy_delta_pct=-8.1,
        source="dart",
        citation_id="cit-samsung-2026q1-report",
    ),
    MetricCard(
        key="eps",
        label_ko="주당순이익",
        label_en="EPS",
        value=846.0,
        unit="KRW",
        yoy_delta_pct=-8.1,
        source="dart",
        citation_id="cit-samsung-2026q1-report",
    ),
    MetricCard(
        key="operating_margin",
        label_ko="영업이익률",
        label_en="Operating Margin",
        value=8.3,
        unit="%",
        yoy_delta_pct=-2.1,
        source="dart",
        citation_id="cit-samsung-2026q1-report",
    ),
]

_APPLE_METRICS = [
    MetricCard(
        key="revenue",
        label_ko="매출액",
        label_en="Revenue",
        value=95_400.0,
        unit="USD_mn",
        yoy_delta_pct=5.1,
        source="sec",
        citation_id="cit-apple-2026q1-10q",
    ),
    MetricCard(
        key="operating_income",
        label_ko="영업이익",
        label_en="Operating Income",
        value=29_600.0,
        unit="USD_mn",
        yoy_delta_pct=4.2,
        source="sec",
        citation_id="cit-apple-2026q1-10q",
    ),
    MetricCard(
        key="net_income",
        label_ko="당기순이익",
        label_en="Net Income",
        value=24_780.0,
        unit="USD_mn",
        yoy_delta_pct=3.6,
        source="sec",
        citation_id="cit-apple-2026q1-10q",
    ),
    MetricCard(
        key="eps",
        label_ko="주당순이익",
        label_en="EPS (Diluted)",
        value=1.65,
        unit="USD",
        yoy_delta_pct=6.5,
        source="sec",
        citation_id="cit-apple-2026q1-10q",
    ),
    MetricCard(
        key="operating_margin",
        label_ko="영업이익률",
        label_en="Operating Margin",
        value=31.0,
        unit="%",
        yoy_delta_pct=-0.3,
        source="sec",
        citation_id="cit-apple-2026q1-10q",
    ),
]

_DIGEST_PARTS: dict[str, dict] = {
    SAMSUNG_ID: {
        "period": "2026Q1",
        "metrics": _SAMSUNG_METRICS,
        "citations": _SAMSUNG_CITATIONS,
        "summary_ko": (
            "삼성전자의 2026년 1분기 매출은 전년 동기 대비 증가했으나 "
            "영업이익은 감소했습니다. 자세한 수치는 각 지표 카드의 "
            "인용(분기보고서)을 참조하세요. (스텁 요약)"
        ),
        "summary_en": (
            "Samsung Electronics' Q1 2026 revenue grew year over year while "
            "operating income declined. See the cited quarterly report for "
            "each metric. (stub summary)"
        ),
    },
    APPLE_ID: {
        "period": "2026Q1",
        "metrics": _APPLE_METRICS,
        "citations": _APPLE_CITATIONS,
        "summary_ko": (
            "Apple의 해당 분기 매출과 순이익은 전년 동기 대비 증가했습니다. "
            "모든 수치는 Form 10-Q 인용을 참조하세요. (스텁 요약)"
        ),
        "summary_en": (
            "Apple's revenue and net income for the quarter increased year "
            "over year. All figures reference the cited Form 10-Q. "
            "(stub summary)"
        ),
    },
}


def search_companies(q: str) -> list[Company]:
    """Case-insensitive substring search over name / name_en / ticker."""
    needle = q.strip().lower()
    if not needle:
        return list(STUB_COMPANIES.values())
    results = []
    for company in STUB_COMPANIES.values():
        haystacks = [company.name, company.name_en or "", company.ticker or ""]
        if any(needle in h.lower() for h in haystacks):
            results.append(company)
    return results


def get_company(company_id: str) -> Company | None:
    return STUB_COMPANIES.get(company_id)


def build_digest(company_id: str) -> CompanyDigest | None:
    """Deterministic stub digest; returns None for unknown company ids.

    Both summary_ko and summary_en are always included per the contract;
    the `lang` query parameter is a client-side display hint in Phase 1.
    """
    company = STUB_COMPANIES.get(company_id)
    parts = _DIGEST_PARTS.get(company_id)
    if company is None or parts is None:
        return None
    return CompanyDigest(
        company_id=company.id,
        company_name=company.name,
        period=parts["period"],
        metrics=parts["metrics"],
        summary_ko=parts["summary_ko"],
        summary_en=parts["summary_en"],
        citations=parts["citations"],
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def build_chat_response(
    company_id: str | None, question: str, language: Language
) -> ChatResponse:
    """Deterministic canned chat response with at least one citation."""
    company = STUB_COMPANIES.get(company_id) if company_id else None
    if company is not None:
        citations = _DIGEST_PARTS[company.id]["citations"]
        if language == "en":
            answer = (
                f"[Stub] Regarding {company.name_en or company.name}: figures "
                "come only from structured DART/SEC data and every claim is "
                "cited. See the attached citation for the latest filing. "
                f"(question: {question})"
            )
        else:
            answer = (
                f"[스텁] {company.name} 관련 답변입니다. 수치는 구조화된 "
                "DART/SEC 데이터에서만 가져오며 모든 주장에는 인용이 "
                f"붙습니다. 첨부된 인용을 참조하세요. (질문: {question})"
            )
    else:
        # Generic answer still carries at least one stub citation.
        citations = _SAMSUNG_CITATIONS
        if language == "en":
            answer = (
                "[Stub] No specific company selected. Ask about a company "
                "(e.g. Samsung Electronics or Apple Inc.) for a cited answer. "
                f"(question: {question})"
            )
        else:
            answer = (
                "[스텁] 선택된 회사가 없습니다. 삼성전자 또는 Apple Inc.에 "
                "대해 질문하면 인용이 포함된 답변을 드립니다. "
                f"(질문: {question})"
            )
    return ChatResponse(answer=answer, language=language, citations=citations)

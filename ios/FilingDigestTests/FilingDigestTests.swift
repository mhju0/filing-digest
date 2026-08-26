//
//  FilingDigestTests.swift
//  FilingDigestTests
//
//  Swift Testing (import Testing) — no XCTest.
//  Covers: snake_case JSON decoding of CompanyDigest, and
//  APIClient URLRequest construction (path / query / method / body).
//

import Foundation
import Testing
@testable import FilingDigest

// MARK: - Sample payloads (snake_case, mirroring API CONTRACT v0.3)

private let companyDigestJSON = """
{
  "company_id": "11111111-1111-1111-1111-111111111111",
  "company_name": "삼성전자",
  "period": "2026Q1",
  "metrics": [
    {
      "key": "revenue",
      "value": 79.1,
      "unit": "조원",
      "yoy_delta_pct": 11.2,
      "source": "dart",
      "filing_source_id": "dart:2026-report"
    },
    {
      "key": "operating_margin",
      "value": null,
      "unit": "%",
      "yoy_delta_pct": null,
      "source": "dart",
      "filing_source_id": "dart:2026-report"
    }
  ],
  "summary_ko": "분기 요약입니다.",
  "summary_en": "Quarterly summary.",
  "filing_sources": [
    {
      "id": "dart:2026-report",
      "source": "dart",
      "source_filing_id": "2026-report",
      "title": "분기보고서 (2026.03)",
      "url": "https://dart.fss.or.kr/report/stub-1",
      "filed_at": "2026-05-15"
    }
  ],
  "generated_at": "2026-07-01T09:00:00Z"
}
"""

// MARK: - POST /answer sample payloads (3-state narrative_status)

private let answerOKJSON = """
{
  "answer": {
    "answer_segments": [
      {
        "text": "매출은 전년 동기 대비 증가했습니다.",
        "citations": ["chunk-aaaa", "chunk-bbbb"]
      },
      {
        "text": "환율 영향은 제한적이었습니다.",
        "citations": ["chunk-bbbb"]
      }
    ]
  },
  "figures": [
    {
      "metric": "revenue",
      "value": "258935494000000.0000",
      "unit": "KRW",
      "currency": "KRW",
      "period": "2025Q4",
      "period_kind": "duration",
      "fiscal_year": 2025,
      "fiscal_quarter": 4,
      "filing_id": "33333333-3333-3333-3333-333333333333"
    },
    {
      "metric": "eps",
      "value": "2131.0000",
      "unit": "KRW",
      "currency": null,
      "period": "FY2025",
      "period_kind": "duration",
      "fiscal_year": 2025,
      "fiscal_quarter": null,
      "filing_id": "33333333-3333-3333-3333-333333333333"
    }
  ],
  "citations": [
    {
      "id": "chunk-aaaa",
      "filing_source_id": "dart:stub-2",
      "excerpt": "매출은 전년 동기 대비 증가했습니다.",
      "anchor": {
        "section_title": "사업의 내용",
        "section_order": 2,
        "part_index": 0,
        "chunk_index": 4
      }
    },
    {
      "id": "chunk-bbbb",
      "filing_source_id": "dart:stub-2",
      "excerpt": "환율 영향은 제한적이었습니다.",
      "anchor": {
        "section_title": "사업의 내용",
        "section_order": 2,
        "part_index": 0,
        "chunk_index": 5
      }
    }
  ],
  "filing_sources": [
    {
      "id": "dart:stub-2",
      "source": "dart",
      "source_filing_id": "stub-2",
      "title": "분기보고서 (2025.12)",
      "url": "https://dart.fss.or.kr/report/stub-2",
      "filed_at": "2026-02-15"
    }
  ],
  "company_id": "11111111-1111-1111-1111-111111111111",
  "narrative_status": "ok",
  "blocked_reason": null
}
"""

private let answerBlockedJSON = """
{
  "answer": null,
  "figures": [
    {
      "metric": "revenue",
      "value": "1234567890123456789.0001",
      "unit": "KRW",
      "currency": "KRW",
      "period": "2025Q4",
      "period_kind": "duration",
      "fiscal_year": 2025,
      "fiscal_quarter": 4,
      "filing_id": "33333333-3333-3333-3333-333333333333"
    }
  ],
  "citations": [],
  "filing_sources": [],
  "company_id": "11111111-1111-1111-1111-111111111111",
  "narrative_status": "blocked",
  "blocked_reason": "evidence_integrity"
}
"""

private let answerNoResultsJSON = """
{
  "answer": null,
  "figures": [],
  "citations": [],
  "filing_sources": [],
  "company_id": "11111111-1111-1111-1111-111111111111",
  "narrative_status": "no_results",
  "blocked_reason": null
}
"""

// MARK: - Decoding tests

@Suite("API model decoding (snake_case)")
struct APIModelDecodingTests {

    @Test("CompanyDigest decodes all contract fields")
    func decodesCompanyDigest() throws {
        let decoder = APIClient.makeJSONDecoder()
        let digest = try decoder.decode(CompanyDigest.self, from: Data(companyDigestJSON.utf8))

        #expect(digest.companyId == "11111111-1111-1111-1111-111111111111")
        #expect(digest.companyName == "삼성전자")
        #expect(digest.period == "2026Q1")
        #expect(digest.summaryKo == "분기 요약입니다.")
        #expect(digest.summaryEn == "Quarterly summary.")
        #expect(digest.generatedAt == "2026-07-01T09:00:00Z")
        #expect(digest.metrics.count == 2)

        let revenue = try #require(digest.metrics.first)
        #expect(revenue.key == .revenue)
        #expect(revenue.value == 79.1)
        #expect(revenue.yoyDeltaPct == 11.2)
        #expect(revenue.source == .dart)
        #expect(revenue.filingSourceId == "dart:2026-report")

        let margin = try #require(digest.metrics.last)
        #expect(margin.key == .operatingMargin)
        #expect(margin.value == nil)
        #expect(margin.yoyDeltaPct == nil)
        #expect(margin.filingSourceId == "dart:2026-report")

        let filingSource = try #require(digest.filingSources.first)
        #expect(filingSource.id == "dart:2026-report")
        #expect(filingSource.source == .dart)
        #expect(filingSource.sourceFilingId == "2026-report")
        #expect(filingSource.filedAt == "2026-05-15")
    }
}

// MARK: - POST /answer decoding tests

@Suite("AnswerResponse decoding (3-state narrative_status)")
struct AnswerResponseDecodingTests {

    private let decoder = APIClient.makeJSONDecoder()

    @Test("ok: segments with citations plus lossless Decimal figures")
    func decodesOK() throws {
        let response = try decoder.decode(AnswerResponse.self, from: Data(answerOKJSON.utf8))

        #expect(response.narrativeStatus == .ok)
        #expect(response.companyId == UUID(uuidString: "11111111-1111-1111-1111-111111111111"))

        let answer = try #require(response.answer)
        #expect(answer.answerSegments.count == 2)
        let first = try #require(answer.answerSegments.first)
        #expect(first.text == "매출은 전년 동기 대비 증가했습니다.")
        #expect(first.citations == ["chunk-aaaa", "chunk-bbbb"])
        let second = try #require(answer.answerSegments.last)
        #expect(second.citations == ["chunk-bbbb"])

        #expect(response.figures.count == 2)
        let revenue = try #require(response.figures.first)
        #expect(revenue.metric == .revenue)
        let expectedRevenue = try #require(Decimal(string: "258935494000000.0000"))
        #expect(revenue.value == expectedRevenue)
        #expect(revenue.unit == "KRW")
        #expect(revenue.currency == "KRW")
        #expect(revenue.period == "2025Q4")
        #expect(revenue.periodKind == .duration)
        #expect(revenue.fiscalYear == 2025)
        #expect(revenue.fiscalQuarter == 4)
        #expect(revenue.filingId == UUID(uuidString: "33333333-3333-3333-3333-333333333333"))

        let eps = try #require(response.figures.last)
        #expect(eps.metric == .eps)
        let expectedEPS = try #require(Decimal(string: "2131.0000"))
        #expect(eps.value == expectedEPS)
        #expect(eps.currency == nil)
        #expect(eps.fiscalQuarter == nil)

        let citation = try #require(response.citations.first)
        #expect(citation.id == "chunk-aaaa")
        #expect(citation.filingSourceId == "dart:stub-2")
        #expect(citation.anchor.sectionTitle == "사업의 내용")
        #expect(citation.anchor.chunkIndex == 4)
        let filingSource = try #require(response.filingSources.first)
        #expect(filingSource.source == .dart)
        #expect(filingSource.filedAt == "2026-02-15")
        #expect(try response.makeEvidenceIndex()?.groups.count == 1)
    }

    @Test("blocked: answer withheld, figures track survives")
    func decodesBlocked() throws {
        let response = try decoder.decode(AnswerResponse.self, from: Data(answerBlockedJSON.utf8))

        #expect(response.narrativeStatus == .blocked)
        #expect(response.answer == nil)
        #expect(response.figures.count == 1)
        #expect(response.citations.isEmpty)
        #expect(response.filingSources.isEmpty)
        #expect(response.blockedReason == .evidenceIntegrity)

        // 19 significant digits: a Double round trip would corrupt this value,
        // so equality here proves the string -> Decimal path is lossless.
        let figure = try #require(response.figures.first)
        let exact = try #require(Decimal(string: "1234567890123456789.0001"))
        #expect(figure.value == exact)
    }

    @Test("no_results: raw enum value maps to .noResults despite key strategy")
    func decodesNoResults() throws {
        let response = try decoder.decode(AnswerResponse.self, from: Data(answerNoResultsJSON.utf8))

        #expect(response.narrativeStatus == .noResults)
        #expect(response.answer == nil)
        #expect(response.figures.isEmpty)
    }
}

// MARK: - FigureDisplay mapping tests

@Suite("FigureDisplay metric/unit humanization")
struct FigureDisplayTests {

    // (a) every known metric key -> KO and EN display name.
    @Test("known metric keys map to KO and EN names", arguments: [
        (ReportedMetric.revenue, "매출액", "Revenue"),
        (ReportedMetric.operatingIncome, "영업이익", "Operating Income"),
        (ReportedMetric.netIncome, "당기순이익", "Net Income"),
        (ReportedMetric.netIncomeAttributable, "지배기업 소유주지분 순이익", "Net Income (Attributable)"),
        (ReportedMetric.eps, "주당순이익(EPS)", "EPS"),
        (ReportedMetric.epsDiluted, "희석주당순이익", "Diluted EPS"),
    ])
    func mapsKnownMetrics(metric: ReportedMetric, ko: String, en: String) {
        #expect(FigureDisplay.metricName(metric, language: .ko) == ko)
        #expect(FigureDisplay.metricName(metric, language: .en) == en)
    }

    @Test("digest metric keys map to compact KO and EN names", arguments: [
        (FinancialMetric.revenue, "매출액", "Revenue"),
        (FinancialMetric.operatingIncome, "영업이익", "Operating Income"),
        (FinancialMetric.netIncome, "당기순이익", "Net Income"),
        (FinancialMetric.netIncomeAttributable, "지배기업 소유주지분 순이익", "Net Income (Attributable)"),
        (FinancialMetric.eps, "주당순이익", "EPS"),
        (FinancialMetric.epsDiluted, "희석주당순이익", "Diluted EPS"),
        (FinancialMetric.operatingMargin, "영업이익률", "Operating Margin"),
    ])
    func mapsDigestMetrics(metric: FinancialMetric, ko: String, en: String) {
        #expect(FigureDisplay.metricName(metric, language: .ko) == ko)
        #expect(FigureDisplay.metricName(metric, language: .en) == en)
    }

    // (b) every known unit key -> KO and EN display.
    @Test("known unit keys map to KO and EN display", arguments: [
        ("KRW", "원", "KRW"),
        ("USD", "USD", "USD"),
        ("KRW_PER_SHARE", "원/주", "KRW per share"),
        ("USD_PER_SHARE", "USD/주", "USD per share"),
    ])
    func mapsKnownUnits(key: String, ko: String, en: String) {
        #expect(FigureDisplay.unitName(key, language: .ko) == ko)
        #expect(FigureDisplay.unitName(key, language: .en) == en)
    }

    // (c) unknown unit key -> raw fallback, identical in both languages.
    @Test("unknown unit key falls back to the raw key")
    func unknownUnitFallsBack() {
        #expect(FigureDisplay.unitName("EUR", language: .ko) == "EUR")
        #expect(FigureDisplay.unitName("EUR", language: .en) == "EUR")
    }
}

// MARK: - APIClient request construction tests

@Suite("APIClient URLRequest construction")
struct APIClientRequestTests {

    private let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8001")!)

    @Test("Company search: GET /companies?q=")
    func companySearchRequest() throws {
        let request = try client.makeCompanySearchRequest(query: "삼성")
        let url = try #require(request.url)
        let components = try #require(URLComponents(url: url, resolvingAgainstBaseURL: false))

        #expect(request.httpMethod == "GET")
        #expect(components.path == "/companies")
        #expect(components.queryItems == [URLQueryItem(name: "q", value: "삼성")])
        #expect(request.httpBody == nil)
    }

    @Test("Digest: GET /companies/{id}/digest?lang=")
    func digestRequest() throws {
        let request = try client.makeDigestRequest(
            companyID: "11111111-1111-1111-1111-111111111111",
            language: .en
        )
        let url = try #require(request.url)
        let components = try #require(URLComponents(url: url, resolvingAgainstBaseURL: false))

        #expect(request.httpMethod == "GET")
        #expect(components.path == "/companies/11111111-1111-1111-1111-111111111111/digest")
        #expect(components.queryItems == [URLQueryItem(name: "lang", value: "en")])
    }

    @Test("Answer: POST /answer with snake_case body; nil period omitted")
    func answerRequest() throws {
        let companyId = try #require(UUID(uuidString: "11111111-1111-1111-1111-111111111111"))
        let request = try client.makeAnswerRequest(
            AnswerRequest(query: "최근 분기 매출은?", companyId: companyId)
        )
        let url = try #require(request.url)

        #expect(request.httpMethod == "POST")
        #expect(url.path() == "/answer")
        #expect(request.value(forHTTPHeaderField: "Content-Type") == "application/json")

        let body = try #require(request.httpBody)
        let object = try #require(
            try JSONSerialization.jsonObject(with: body) as? [String: Any]
        )
        #expect(object["query"] as? String == "최근 분기 매출은?")
        #expect(object["company_id"] as? String == "11111111-1111-1111-1111-111111111111")
        // Optional period == nil is encoded with encodeIfPresent -> key omitted.
        #expect(object["period"] == nil)

        let withPeriod = try client.makeAnswerRequest(
            AnswerRequest(query: "매출은?", companyId: companyId, period: "2025Q4")
        )
        let periodBody = try #require(withPeriod.httpBody)
        let periodObject = try #require(
            try JSONSerialization.jsonObject(with: periodBody) as? [String: Any]
        )
        #expect(periodObject["period"] as? String == "2025Q4")
    }

    @Test("Custom baseURL is honored")
    func customBaseURL() throws {
        let custom = APIClient(baseURL: URL(string: "http://localhost:9999")!)
        let request = try custom.makeCompanySearchRequest(query: "apple")
        let url = try #require(request.url)

        #expect(url.host() == "localhost")
        #expect(url.port == 9999)
    }
}

@Suite("FigureDisplay period titles and value abbreviation")
struct FigureDisplayFormattingTests {

    @Test("Period codes humanize per language, unknown shapes pass through")
    func periodTitles() {
        #expect(FigureDisplay.periodTitle("2023-annual", language: .ko) == "사업보고서 2023")
        #expect(FigureDisplay.periodTitle("2023-annual", language: .en) == "Annual Report 2023")
        #expect(FigureDisplay.periodTitle("2026Q1", language: .ko) == "2026년 1분기")
        #expect(FigureDisplay.periodTitle("2026Q1", language: .en) == "Q1 2026")
        // Out-of-range quarter and unknown shapes fall back verbatim.
        #expect(FigureDisplay.periodTitle("2026Q7", language: .ko) == "2026Q7")
        #expect(FigureDisplay.periodTitle("FY25", language: .en) == "FY25")
    }

    @Test("Large KRW/USD values abbreviate; small and per-share values stay exact")
    func valueAbbreviation() {
        #expect(
            FigureDisplay.formattedValue(258_935_494_000_000, unit: "KRW", language: .ko)
                == "258.9조 원"
        )
        #expect(
            FigureDisplay.formattedValue(6_566_976_000_000, unit: "KRW", language: .ko)
                == "6.6조 원"
        )
        #expect(
            FigureDisplay.formattedValue(650_000_000_000, unit: "KRW", language: .ko)
                == "6,500억 원"
        )
        #expect(
            FigureDisplay.formattedValue(258_935_494_000_000, unit: "KRW", language: .en)
                == "258.9T KRW"
        )
        #expect(
            FigureDisplay.formattedValue(391_035_000_000, unit: "USD", language: .en)
                == "391B USD"
        )
        // Negative values keep their sign through the scaling.
        #expect(
            FigureDisplay.formattedValue(-1_200_000_000_000, unit: "KRW", language: .ko)
                == "-1.2조 원"
        )
        // Per-share and small values stay exact.
        #expect(
            FigureDisplay.formattedValue(2_131, unit: "KRW_PER_SHARE", language: .ko)
                == "2,131원/주"
        )
        #expect(
            FigureDisplay.formattedValue(2_131, unit: "KRW", language: .ko) == "2,131원"
        )
    }

    @Test("Formatted values expose aligned number and unit roles")
    func valueParts() {
        let korean = FigureDisplay.formattedValueParts(
            333_600_000_000_000,
            unit: "KRW",
            language: .ko
        )
        #expect(korean.number == "333.6")
        #expect(korean.unit == "조 원")
        #expect(korean.combined == "333.6조 원")

        let english = FigureDisplay.formattedValueParts(
            391_035_000_000,
            unit: "USD",
            language: .en
        )
        #expect(english.number == "391")
        #expect(english.unit == "B USD")
        #expect(english.combined == "391B USD")
    }
}

@Suite("Korean company and market display")
struct KoreanDisplayTests {
    @Test("Common US company names and market names are localized")
    func localizedNames() {
        let apple = Company(
            id: UUID().uuidString,
            name: "Apple Inc.",
            nameEn: "Apple Inc.",
            ticker: "AAPL",
            market: .nasdaq,
            source: .sec
        )

        #expect(apple.koreanDisplayName == "애플")
        #expect(apple.koreanSecurityIdentifier == "티커 AAPL")

        let naver = Company(
            id: "naver",
            name: "NAVER",
            nameEn: "NAVER Corporation",
            ticker: "035420",
            market: .kospi,
            source: .dart
        )
        #expect(naver.koreanDisplayName == "네이버")
        #expect(naver.koreanSecurityIdentifier == "종목코드 035420")
        #expect(Market.kospi.koreanDisplayName == "코스피")
        #expect(Market.nasdaq.koreanDisplayName == "나스닥")
        #expect(Market.nyse.koreanDisplayName == "뉴욕증권거래소")
    }

    @Test("Unknown companies keep their disclosed name")
    func unknownNameFallback() {
        let company = Company(
            id: UUID().uuidString,
            name: "Example Holdings",
            nameEn: nil,
            ticker: "EXM",
            market: .nyse,
            source: .sec
        )

        #expect(company.koreanDisplayName == "Example Holdings")
        #expect(company.koreanSecurityIdentifier == "티커 EXM")
    }

    @Test("Korean numeric stock codes are explicitly labeled")
    func koreanStockCodeLabel() {
        let samsung = Company(
            id: UUID().uuidString,
            name: "삼성전자",
            nameEn: "Samsung Electronics",
            ticker: "005930",
            market: .kospi,
            source: .dart
        )

        #expect(samsung.koreanSecurityIdentifier == "종목코드 005930")
    }
}

@Suite("Digest Ledger metric hierarchy")
struct DigestMetricHierarchyTests {
    @Test("Revenue leads the folio regardless of API order")
    func revenueIsHeroMetric() {
        let metrics = [
            metric(.eps),
            metric(.netIncome),
            metric(.revenue),
            metric(.operatingIncome),
        ]

        #expect(DigestView.orderedMetrics(metrics).map(\.key) == [
            .revenue, .operatingIncome, .netIncome, .eps,
        ])
    }

    @Test("Unknown future ordering preserves API order after known metrics")
    func stableKnownMetricOrder() {
        let metrics = [
            metric(.operatingMargin),
            metric(.epsDiluted),
        ]

        #expect(DigestView.orderedMetrics(metrics).map(\.key) == [
            .epsDiluted, .operatingMargin,
        ])
    }

    private func metric(_ key: FinancialMetric) -> MetricCard {
        MetricCard(
            key: key,
            value: 1,
            unit: "KRW",
            yoyDeltaPct: nil,
            source: .dart,
            filingSourceId: "source"
        )
    }
}

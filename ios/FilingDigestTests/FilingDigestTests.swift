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

// MARK: - Sample payloads (snake_case, mirroring API CONTRACT v0.1)

private let companyDigestJSON = """
{
  "company_id": "11111111-1111-1111-1111-111111111111",
  "company_name": "삼성전자",
  "period": "2026Q1",
  "metrics": [
    {
      "key": "revenue",
      "label_ko": "매출액",
      "label_en": "Revenue",
      "value": 79.1,
      "unit": "조원",
      "yoy_delta_pct": 11.2,
      "source": "dart",
      "citation_id": "cit-1"
    },
    {
      "key": "operating_margin",
      "label_ko": "영업이익률",
      "label_en": "Operating margin",
      "value": null,
      "unit": "%",
      "yoy_delta_pct": null,
      "source": "dart",
      "citation_id": null
    }
  ],
  "summary_ko": "분기 요약입니다.",
  "summary_en": "Quarterly summary.",
  "citations": [
    {
      "id": "cit-1",
      "source": "dart",
      "title": "분기보고서 (2026.03)",
      "url": "https://dart.fss.or.kr/report/stub-1",
      "excerpt": "매출액은 ...",
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
        "citations": []
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
      "fiscal_year": 2025,
      "fiscal_quarter": null,
      "filing_id": "33333333-3333-3333-3333-333333333333"
    }
  ],
  "citations": [
    {
      "id": "chunk-aaaa",
      "source": "dart",
      "title": "분기보고서 (2025.12)",
      "url": "https://dart.fss.or.kr/report/stub-2",
      "excerpt": "매출은 ...",
      "filed_at": "2026-02-15"
    }
  ],
  "company_id": "11111111-1111-1111-1111-111111111111",
  "narrative_status": "ok"
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
      "fiscal_year": 2025,
      "fiscal_quarter": 4,
      "filing_id": "33333333-3333-3333-3333-333333333333"
    }
  ],
  "citations": [],
  "company_id": "11111111-1111-1111-1111-111111111111",
  "narrative_status": "blocked"
}
"""

private let answerNoResultsJSON = """
{
  "answer": null,
  "figures": [],
  "citations": [],
  "company_id": "11111111-1111-1111-1111-111111111111",
  "narrative_status": "no_results"
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
        #expect(revenue.labelKo == "매출액")
        #expect(revenue.labelEn == "Revenue")
        #expect(revenue.value == 79.1)
        #expect(revenue.yoyDeltaPct == 11.2)
        #expect(revenue.source == .dart)
        #expect(revenue.citationId == "cit-1")

        let margin = try #require(digest.metrics.last)
        #expect(margin.key == .operatingMargin)
        #expect(margin.value == nil)
        #expect(margin.yoyDeltaPct == nil)
        #expect(margin.citationId == nil)

        let citation = try #require(digest.citations.first)
        #expect(citation.id == "cit-1")
        #expect(citation.source == .dart)
        #expect(citation.filedAt == "2026-05-15")
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
        #expect(second.citations.isEmpty)

        #expect(response.figures.count == 2)
        let revenue = try #require(response.figures.first)
        #expect(revenue.metric == "revenue")
        let expectedRevenue = try #require(Decimal(string: "258935494000000.0000"))
        #expect(revenue.value == expectedRevenue)
        #expect(revenue.unit == "KRW")
        #expect(revenue.currency == "KRW")
        #expect(revenue.period == "2025Q4")
        #expect(revenue.fiscalYear == 2025)
        #expect(revenue.fiscalQuarter == 4)
        #expect(revenue.filingId == UUID(uuidString: "33333333-3333-3333-3333-333333333333"))

        let eps = try #require(response.figures.last)
        #expect(eps.metric == "eps")
        let expectedEPS = try #require(Decimal(string: "2131.0000"))
        #expect(eps.value == expectedEPS)
        #expect(eps.currency == nil)
        #expect(eps.fiscalQuarter == nil)

        let citation = try #require(response.citations.first)
        #expect(citation.id == "chunk-aaaa")
        #expect(citation.source == .dart)
        #expect(citation.filedAt == "2026-02-15")
    }

    @Test("blocked: answer withheld, figures track survives")
    func decodesBlocked() throws {
        let response = try decoder.decode(AnswerResponse.self, from: Data(answerBlockedJSON.utf8))

        #expect(response.narrativeStatus == .blocked)
        #expect(response.answer == nil)
        #expect(response.figures.count == 1)
        #expect(response.citations.isEmpty)

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
        ("revenue", "매출액", "Revenue"),
        ("operating_income", "영업이익", "Operating Income"),
        ("net_income", "당기순이익", "Net Income"),
        ("net_income_attributable", "지배기업 소유주지분 순이익", "Net Income (Attributable)"),
        ("eps", "주당순이익(EPS)", "EPS"),
        ("eps_diluted", "희석주당순이익", "Diluted EPS"),
    ])
    func mapsKnownMetrics(key: String, ko: String, en: String) {
        #expect(FigureDisplay.metricName(key, language: .ko) == ko)
        #expect(FigureDisplay.metricName(key, language: .en) == en)
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

    // (c) unknown metric key -> raw fallback, identical in both languages.
    @Test("unknown metric key falls back to the raw key")
    func unknownMetricFallsBack() {
        #expect(FigureDisplay.metricName("free_cash_flow", language: .ko) == "free_cash_flow")
        #expect(FigureDisplay.metricName("free_cash_flow", language: .en) == "free_cash_flow")
    }

    // (d) unknown unit key -> raw fallback, identical in both languages.
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

    @Test("Ingest: POST /ingest with snake_case keys")
    func ingestRequest() throws {
        let request = try client.makeIngestRequest(
            IngestRequest(
                companyId: "22222222-2222-2222-2222-222222222222",
                source: .sec,
                filingTypes: ["10-Q", "10-K"]
            )
        )
        let url = try #require(request.url)

        #expect(request.httpMethod == "POST")
        #expect(url.path() == "/ingest")

        let body = try #require(request.httpBody)
        let object = try #require(
            try JSONSerialization.jsonObject(with: body) as? [String: Any]
        )
        #expect(object["company_id"] as? String == "22222222-2222-2222-2222-222222222222")
        #expect(object["source"] as? String == "sec")
        #expect(object["filing_types"] as? [String] == ["10-Q", "10-K"])
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
}

//
//  FilingDigestTests.swift
//  FilingDigestTests
//
//  Swift Testing (import Testing) — no XCTest.
//  Covers: snake_case JSON decoding of CompanyDigest / ChatResponse, and
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

private let chatResponseJSON = """
{
  "answer": "Apple's revenue grew year over year.",
  "language": "en",
  "citations": [
    {
      "id": "cit-sec-1",
      "source": "sec",
      "title": "Form 10-Q",
      "url": "https://data.sec.gov/stub/10-Q",
      "excerpt": null,
      "filed_at": null
    }
  ]
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

    @Test("ChatResponse decodes, including citations with null fields")
    func decodesChatResponse() throws {
        let decoder = APIClient.makeJSONDecoder()
        let response = try decoder.decode(ChatResponse.self, from: Data(chatResponseJSON.utf8))

        #expect(response.answer == "Apple's revenue grew year over year.")
        #expect(response.language == .en)

        let citation = try #require(response.citations.first)
        #expect(citation.id == "cit-sec-1")
        #expect(citation.source == .sec)
        #expect(citation.excerpt == nil)
        #expect(citation.filedAt == nil)
    }
}

// MARK: - APIClient request construction tests

@Suite("APIClient URLRequest construction")
struct APIClientRequestTests {

    private let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8000")!)

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

    @Test("Chat: POST /chat with snake_case body; nil company_id omitted")
    func chatRequest() throws {
        let request = try client.makeChatRequest(
            ChatRequest(companyId: nil, question: "최근 실적은?", language: .ko)
        )
        let url = try #require(request.url)

        #expect(request.httpMethod == "POST")
        #expect(url.path() == "/chat")
        #expect(request.value(forHTTPHeaderField: "Content-Type") == "application/json")

        let body = try #require(request.httpBody)
        let object = try #require(
            try JSONSerialization.jsonObject(with: body) as? [String: Any]
        )
        #expect(object["question"] as? String == "최근 실적은?")
        #expect(object["language"] as? String == "ko")
        // Optional companyId == nil is encoded with encodeIfPresent -> key omitted.
        #expect(object["company_id"] == nil)
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

    @Test("Custom baseURL is honored")
    func customBaseURL() throws {
        let custom = APIClient(baseURL: URL(string: "http://localhost:9999")!)
        let request = try custom.makeCompanySearchRequest(query: "apple")
        let url = try #require(request.url)

        #expect(url.host() == "localhost")
        #expect(url.port == 9999)
    }
}

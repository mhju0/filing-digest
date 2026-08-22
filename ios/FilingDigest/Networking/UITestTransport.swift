#if DEBUG
import Foundation

enum UITestTransport {
    static var isEnabled: Bool {
        ProcessInfo.processInfo.arguments.contains("-ui-testing")
    }

    static func session() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [UITestURLProtocol.self]
        return URLSession(configuration: configuration)
    }
}

private final class UITestURLProtocol: URLProtocol {
    override class func canInit(with request: URLRequest) -> Bool { true }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let url = request.url else {
            client?.urlProtocol(self, didFailWithError: URLError(.badURL))
            return
        }

        let payload: String
        switch (request.httpMethod, url.path) {
        case ("GET", "/companies"):
            payload = Self.companiesJSON
        case ("GET", "/companies/11111111-1111-1111-1111-111111111111/digest"):
            payload = Self.digestJSON
        case ("POST", "/answer"):
            payload = Self.answerJSON
        default:
            client?.urlProtocol(self, didFailWithError: URLError(.unsupportedURL))
            return
        }

        let response = HTTPURLResponse(
            url: url,
            statusCode: 200,
            httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": "application/json"]
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(payload.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}

    private static let companiesJSON = #"""
    {
      "items": [{
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "삼성전자",
        "name_en": "Samsung Electronics",
        "ticker": "005930",
        "market": "KOSPI",
        "source": "dart"
      }],
      "total": 1
    }
    """#

    private static let digestJSON = #"""
    {
      "company_id": "11111111-1111-1111-1111-111111111111",
      "company_name": "삼성전자",
      "period": "FY2025",
      "metrics": [{
        "key": "revenue",
        "label_ko": "매출액",
        "label_en": "Revenue",
        "value": 300.9,
        "unit": "조원",
        "yoy_delta_pct": 3.2,
        "source": "dart",
        "filing_source_id": "dart:2025-report"
      }],
      "summary_ko": "사업보고서의 핵심 수치와 서술을 원문 근거와 함께 보여줍니다.",
      "summary_en": "Key filing metrics and narrative with source evidence.",
      "filing_sources": [{
        "id": "dart:2025-report",
        "source": "dart",
        "source_filing_id": "2025-report",
        "title": "사업보고서 (2025.12)",
        "url": "https://dart.fss.or.kr/",
        "filed_at": "2026-03-10"
      }],
      "generated_at": "2026-08-22T00:00:00Z"
    }
    """#

    private static let answerJSON = #"""
    {
      "answer": {
        "answer_segments": [{
          "text": "반도체와 모바일 사업이 핵심 사업 부문입니다.",
          "citations": ["chunk-business"]
        }]
      },
      "figures": [],
      "citations": [{
        "id": "chunk-business",
        "filing_source_id": "dart:2025-report",
        "excerpt": "회사는 반도체와 모바일 제품을 주요 사업으로 영위하고 있습니다.",
        "anchor": {
          "section_title": "사업의 내용",
          "section_order": 2,
          "part_index": 0,
          "chunk_index": 4
        }
      }],
      "filing_sources": [{
        "id": "dart:2025-report",
        "source": "dart",
        "source_filing_id": "2025-report",
        "title": "사업보고서 (2025.12)",
        "url": "https://dart.fss.or.kr/",
        "filed_at": "2026-03-10"
      }],
      "company_id": "11111111-1111-1111-1111-111111111111",
      "narrative_status": "ok",
      "blocked_reason": null
    }
    """#
}
#endif

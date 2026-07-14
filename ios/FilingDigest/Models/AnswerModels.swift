//
//  AnswerModels.swift
//  FilingDigest
//
//  Codable mirror of POST /answer (backend/app/schemas.py, app/llm/answer.py).
//  Same conventions as APIModels.swift: snake_case on the wire, camelCase in
//  Swift via key coding strategies; no hand-written CodingKeys except where a
//  custom decoder is required.
//
//  Principle (mirrors backend): figures are the authoritative numbers pulled
//  from structured filing APIs; the narrative track can be withheld
//  (narrative_status) while figures always survive.
//

import Foundation

// MARK: - Request

/// POST /answer request body. `company_id` is required.
/// `period` narrows the figures scope; nil means whole-company scope.
struct AnswerRequest: Encodable, Hashable, Sendable {
    let query: String
    let companyId: UUID
    let period: String?

    init(query: String, companyId: UUID, period: String? = nil) {
        self.query = query
        self.companyId = companyId
        self.period = period
    }
}

// MARK: - Response

/// Disposition of the narrative track. Raw values are spelled out because key
/// conversion strategies never touch enum values.
enum NarrativeStatus: String, Decodable, CaseIterable, Hashable, Sendable {
    /// Narrative generated and citation-checked.
    case ok = "ok"
    /// Empty retrieval: nothing to cite over, narrative not attempted.
    case noResults = "no_results"
    /// The number guard tripped on the generated prose; narrative suppressed.
    case blocked = "blocked"
}

/// Why an otherwise available narrative was withheld.
enum NarrativeBlockedReason: String, Decodable, CaseIterable, Hashable, Sendable {
    case numberGuard = "number_guard"
    case narrativeUnavailable = "narrative_unavailable"
    case evidenceIntegrity = "evidence_integrity"

    var userMessage: String {
        switch self {
        case .numberGuard:
            "수치 정확성 검증을 통과하지 못해 AI 서술을 표시하지 않습니다."
        case .narrativeUnavailable:
            "서술 생성 기능을 사용할 수 없어 확정 수치만 표시합니다."
        case .evidenceIntegrity:
            "인용 근거를 원문까지 안전하게 확인할 수 없어 AI 서술을 표시하지 않습니다."
        }
    }
}

/// One narrated span and the Citation ids backing it.
struct AnswerSegment: Decodable, Hashable, Sendable {
    let text: String
    let citations: [String]
}

/// Full LLM answer: an ordered list of citation-bearing segments.
struct Answer: Decodable, Hashable, Sendable {
    let answerSegments: [AnswerSegment]
}

/// One authoritative financial figure. `value` arrives as a JSON *string*
/// (pydantic serializes Decimal that way, e.g. "258935494000000.0000") and is
/// decoded via `Decimal(string:)` — never through Double, so numeric(24,4)
/// precision survives intact.
struct Figure: Decodable, Hashable, Sendable {
    let metric: ReportedMetric
    let value: Decimal
    let unit: String
    let currency: String?
    let period: String
    let periodKind: PeriodKind
    let fiscalYear: Int
    let fiscalQuarter: Int?
    let filingId: UUID

    /// Key names are camelCase because .convertFromSnakeCase rewrites the
    /// wire keys before matching them against these.
    private enum CodingKeys: String, CodingKey {
        case metric, value, unit, currency, period, periodKind
        case fiscalYear, fiscalQuarter, filingId
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        metric = try container.decode(ReportedMetric.self, forKey: .metric)
        let rawValue = try container.decode(String.self, forKey: .value)
        guard let decimal = Decimal(string: rawValue, locale: Locale(identifier: "en_US_POSIX")) else {
            throw DecodingError.dataCorruptedError(
                forKey: .value,
                in: container,
                debugDescription: "Figure.value is not a decimal string: \(rawValue)"
            )
        }
        value = decimal
        unit = try container.decode(String.self, forKey: .unit)
        currency = try container.decodeIfPresent(String.self, forKey: .currency)
        period = try container.decode(String.self, forKey: .period)
        periodKind = try container.decode(PeriodKind.self, forKey: .periodKind)
        fiscalYear = try container.decode(Int.self, forKey: .fiscalYear)
        fiscalQuarter = try container.decodeIfPresent(Int.self, forKey: .fiscalQuarter)
        filingId = try container.decode(UUID.self, forKey: .filingId)
    }
}

/// POST /answer response. `answer` is nil when narrative_status is
/// no_results or blocked; `figures` is always present (possibly empty) and
/// can be non-empty even under no_results — the two tracks are independent.
/// Citations remain claim-level evidence while Filing Sources are ordered,
/// deduplicated Corporate Filings a person can open. The backend always sends
/// both arrays, even under no_results/blocked.
struct AnswerResponse: Decodable, Hashable, Sendable {
    let answer: Answer?
    let figures: [Figure]
    let citations: [Citation]
    let filingSources: [FilingSource]
    let companyId: UUID
    let narrativeStatus: NarrativeStatus
    let blockedReason: NarrativeBlockedReason?
}

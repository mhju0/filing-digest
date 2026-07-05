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
/// conversion strategies never touch enum *values* (same rule as MetricKey).
enum NarrativeStatus: String, Decodable, CaseIterable, Hashable, Sendable {
    /// Narrative generated and citation-checked.
    case ok = "ok"
    /// Empty retrieval: nothing to cite over, narrative not attempted.
    case noResults = "no_results"
    /// The number guard tripped on the generated prose; narrative suppressed.
    case blocked = "blocked"
}

/// One narrated span and the chunk ids backing it. `citations` are bare
/// chunk_id strings that anchor into `AnswerResponse.citations` (resolved
/// title/source/url/filed_at) by matching `id`.
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
    let metric: String
    let value: Decimal
    let unit: String
    let currency: String?
    let period: String
    let fiscalYear: Int
    let fiscalQuarter: Int?
    let filingId: UUID

    /// Key names are camelCase because .convertFromSnakeCase rewrites the
    /// wire keys before matching them against these.
    private enum CodingKeys: String, CodingKey {
        case metric, value, unit, currency, period
        case fiscalYear, fiscalQuarter, filingId
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        metric = try container.decode(String.self, forKey: .metric)
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
        fiscalYear = try container.decode(Int.self, forKey: .fiscalYear)
        fiscalQuarter = try container.decodeIfPresent(Int.self, forKey: .fiscalQuarter)
        filingId = try container.decode(UUID.self, forKey: .filingId)
    }
}

/// POST /answer response. `answer` is nil when narrative_status is
/// no_results or blocked; `figures` is always present (possibly empty) and
/// can be non-empty even under no_results — the two tracks are independent.
/// `citations` resolves every chunk id cited across `answer.answerSegments`
/// to human-readable source metadata (mirrors `CompanyDigest.citations`);
/// the backend always sends a (possibly empty) array, even under
/// no_results/blocked.
struct AnswerResponse: Decodable, Hashable, Sendable {
    let answer: Answer?
    let figures: [Figure]
    let citations: [Citation]
    let companyId: UUID
    let narrativeStatus: NarrativeStatus
}

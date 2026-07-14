//
//  APIModels.swift
//  FilingDigest
//
//  Codable mirror of API CONTRACT v0.3.
//  JSON on the wire is snake_case; Swift properties are camelCase and rely on
//  JSONDecoder.keyDecodingStrategy = .convertFromSnakeCase /
//  JSONEncoder.keyEncodingStrategy = .convertToSnakeCase (see APIClient).
//
//  Principle (mirrors backend): numbers come only from structured filing APIs
//  (DART/SEC); the LLM only narrates; every claim carries a citation.
//  Date-like fields (filed_at, generated_at) are kept as String on purpose —
//  the app displays them verbatim and never parses them.
//

import Foundation

// MARK: - Shared enums

/// Response/request language. Raw values match the API contract ("ko" | "en").
enum Language: String, Codable, CaseIterable, Hashable, Sendable {
    case ko
    case en
}

/// Regulatory system that published a filing ("dart" | "sec").
///
/// This is deliberately distinct from ``FilingSource``, the openable filing
/// record presented to a person verifying evidence.
enum RegulatorySource: String, Codable, Hashable, Sendable {
    case dart
    case sec
}

/// Stock market. Raw values are uppercase in the contract, so they are spelled
/// out explicitly (key conversion strategies never touch enum *values*).
enum Market: String, Codable, Hashable, Sendable {
    case kospi = "KOSPI"
    case kosdaq = "KOSDAQ"
    case nyse = "NYSE"
    case nasdaq = "NASDAQ"
}

/// Canonical measures reported directly by a Corporate Filing.
enum ReportedMetric: String, Codable, CaseIterable, Hashable, Sendable {
    case revenue
    case operatingIncome = "operating_income"
    case netIncome = "net_income"
    case netIncomeAttributable = "net_income_attributable"
    case eps
    case epsDiluted = "eps_diluted"
}

/// Measures calculated from Reported Metrics rather than disclosed directly.
enum DerivedMetric: String, Codable, CaseIterable, Hashable, Sendable {
    case operatingMargin = "operating_margin"
}

/// Temporal shape of a Financial Fact.
enum PeriodKind: String, Codable, CaseIterable, Hashable, Sendable {
    case instant
    case duration
}

/// Digest cards can present either a Reported Metric or a Derived Metric while
/// preserving the existing single-string wire representation.
enum FinancialMetric: Hashable, Sendable {
    case reported(ReportedMetric)
    case derived(DerivedMetric)

    var rawValue: String {
        switch self {
        case .reported(let metric): metric.rawValue
        case .derived(let metric): metric.rawValue
        }
    }

    static let revenue = Self.reported(ReportedMetric.revenue)
    static let operatingIncome = Self.reported(ReportedMetric.operatingIncome)
    static let netIncome = Self.reported(ReportedMetric.netIncome)
    static let netIncomeAttributable = Self.reported(ReportedMetric.netIncomeAttributable)
    static let eps = Self.reported(ReportedMetric.eps)
    static let epsDiluted = Self.reported(ReportedMetric.epsDiluted)
    static let operatingMargin = Self.derived(DerivedMetric.operatingMargin)
}

extension FinancialMetric: Codable {
    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        let rawValue = try container.decode(String.self)
        if let reported = ReportedMetric(rawValue: rawValue) {
            self = .reported(reported)
        } else if let derived = DerivedMetric(rawValue: rawValue) {
            self = .derived(derived)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unknown financial metric: \(rawValue)"
            )
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(rawValue)
    }
}

// MARK: - Companies

/// GET /companies item.
struct Company: Codable, Identifiable, Hashable, Sendable {
    let id: String
    let name: String
    let nameEn: String?
    let ticker: String?
    let market: Market?
    let source: RegulatorySource
}

/// GET /companies?q= response envelope.
struct CompanySearchResponse: Codable, Sendable {
    let items: [Company]
    let total: Int
}

// MARK: - Digest

/// Location of a Filing Chunk within its Corporate Filing.
struct CitationAnchor: Codable, Hashable, Sendable {
    let sectionTitle: String?
    let sectionOrder: Int?
    let partIndex: Int?
    let chunkIndex: Int
}

/// Claim-level evidence pointing to one bounded Filing Chunk.
struct Citation: Codable, Identifiable, Hashable, Sendable {
    let id: String
    let filingSourceId: String
    let excerpt: String
    let anchor: CitationAnchor
}

/// Deduplicated, openable representation of one Corporate Filing.
struct FilingSource: Codable, Identifiable, Hashable, Sendable {
    let id: String
    let source: RegulatorySource
    let sourceFilingId: String
    let title: String
    let url: String
    /// ISO date string ("YYYY-MM-DD") or nil; displayed verbatim.
    let filedAt: String?

    /// Only absolute HTTP(S) links are openable evidence sources.
    var openableURL: URL? {
        guard let candidate = URL(string: url),
              ["http", "https"].contains(candidate.scheme?.lowercased() ?? ""),
              candidate.host != nil
        else { return nil }
        return candidate
    }
}

/// One metric tile in the digest grid. `value == nil` renders as a dash.
struct MetricCard: Codable, Hashable, Identifiable, Sendable {
    let key: FinancialMetric
    let labelKo: String
    let labelEn: String
    let value: Double?
    let unit: String
    let yoyDeltaPct: Double?
    let source: RegulatorySource
    let filingSourceId: String

    /// Stable identity for SwiftUI lists; the contract guarantees at most one
    /// card per metric key in a digest.
    var id: String { key.rawValue }

    /// Localized label following the KO/EN toggle.
    func label(for language: Language) -> String {
        language == .ko ? labelKo : labelEn
    }
}

/// GET /companies/{company_id}/digest response.
struct CompanyDigest: Codable, Hashable, Sendable {
    let companyId: String
    let companyName: String
    /// e.g. "2026Q1"
    let period: String
    let metrics: [MetricCard]
    /// nil when no narrative has been generated for this digest yet.
    let summaryKo: String?
    let summaryEn: String?
    let filingSources: [FilingSource]
    /// ISO8601 string; displayed verbatim.
    let generatedAt: String

    /// Localized summary following the KO/EN toggle. nil if not yet generated.
    func summary(for language: Language) -> String? {
        language == .ko ? summaryKo : summaryEn
    }
}

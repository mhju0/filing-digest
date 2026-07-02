//
//  APIModels.swift
//  FilingDigest
//
//  Codable mirror of API CONTRACT v0.1.
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

/// Origin of a filing-derived datum ("dart" | "sec").
enum FilingSource: String, Codable, Hashable, Sendable {
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

/// MetricCard.key values. Explicit raw values because value strings stay
/// snake_case regardless of the key-conversion strategy.
enum MetricKey: String, Codable, Hashable, Sendable {
    case revenue
    case operatingIncome = "operating_income"
    case netIncome = "net_income"
    case eps
    case operatingMargin = "operating_margin"
}

// MARK: - Companies

/// GET /companies item.
struct Company: Codable, Identifiable, Hashable, Sendable {
    let id: String
    let name: String
    let nameEn: String?
    let ticker: String?
    let market: Market?
    let source: FilingSource
}

/// GET /companies?q= response envelope.
struct CompanySearchResponse: Codable, Sendable {
    let items: [Company]
    let total: Int
}

// MARK: - Digest

/// A single citation. Every numeric claim in a digest links back to one of
/// these via MetricCard.citationId.
struct Citation: Codable, Identifiable, Hashable, Sendable {
    let id: String
    let source: FilingSource
    let title: String
    let url: String
    let excerpt: String?
    /// ISO date string ("YYYY-MM-DD") or nil; displayed verbatim.
    let filedAt: String?
}

/// One metric tile in the digest grid. `value == nil` renders as a dash.
struct MetricCard: Codable, Hashable, Identifiable, Sendable {
    let key: MetricKey
    let labelKo: String
    let labelEn: String
    let value: Double?
    let unit: String
    let yoyDeltaPct: Double?
    let source: FilingSource
    let citationId: String?

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
    let summaryKo: String
    let summaryEn: String
    let citations: [Citation]
    /// ISO8601 string; displayed verbatim.
    let generatedAt: String

    /// Localized summary following the KO/EN toggle.
    func summary(for language: Language) -> String {
        language == .ko ? summaryKo : summaryEn
    }
}

// MARK: - Chat

/// POST /chat request body.
struct ChatRequest: Codable, Hashable, Sendable {
    /// nil means "no specific company context" (encoded key omitted; the
    /// backend defaults to null).
    let companyId: String?
    let question: String
    let language: Language

    init(companyId: String? = nil, question: String, language: Language = .ko) {
        self.companyId = companyId
        self.question = question
        self.language = language
    }
}

/// POST /chat response body.
struct ChatResponse: Codable, Hashable, Sendable {
    let answer: String
    let language: Language
    let citations: [Citation]
}

// MARK: - Ingest

/// POST /ingest request body.
struct IngestRequest: Codable, Hashable, Sendable {
    let companyId: String
    let source: FilingSource
    let filingTypes: [String]?

    init(companyId: String, source: FilingSource, filingTypes: [String]? = nil) {
        self.companyId = companyId
        self.source = source
        self.filingTypes = filingTypes
    }
}

/// POST /ingest 202 response body.
struct IngestResponse: Codable, Hashable, Sendable {
    let jobId: String
    /// "queued" per contract v0.1; kept as String for forward compatibility.
    let status: String
}

//
//  AgentLedger.swift
//  FilingDigest
//
//  Filing Agent publishes a ledger of separately verified historical figures
//  with their calculations. Digest links a covered company to its section
//  there. `years` mirrors `ledger_years` in contracts/family-glossary.json.
//

import Foundation

enum AgentLedger {
    static let years: [String: [String]] = [
        "005930": ["2022", "2023"],
        "035420": ["2023"],
        "MSFT": ["2023", "2024"],
    ]

    static func url(ticker: String?, language: Language) -> URL? {
        guard let ticker = ticker?.uppercased(), years[ticker] != nil else { return nil }
        return URL(string: "https://filing-agent.vercel.app/?lang=\(language.rawValue)#ledger/ledger-\(ticker)")
    }

    static func title(_ language: Language) -> String {
        language == .ko ? "Filing Agent 수치 장부" : "Filing Agent ledger"
    }

    static func detail(ticker: String, language: Language) -> String? {
        guard let years = years[ticker.uppercased()], let first = years.first, let last = years.last else {
            return nil
        }
        let span = first == last ? first : "\(first)–\(last)"
        return language == .ko
            ? "\(span) 회계연도 검증 수치와 계산"
            : "Verified FY\(span) figures and calculations"
    }

    static func openHint(_ language: Language) -> String {
        language == .ko ? "Safari에서 Filing Agent를 엽니다" : "Opens Filing Agent in Safari"
    }
}

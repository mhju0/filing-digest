//
//  FigureDisplay.swift
//  FilingDigest
//
//  Presentation-layer mapping from canonical metrics and raw unit keys to
//  localized display names.
//
//  Principle: this is display-only. It never changes numeric values, never
//  affects decoding. ReportedMetric is exhaustive; unknown unit keys still fall
//  back to the raw string verbatim.
//
//  Bilingual by the same idiom the rest of the app uses (MetricCard.label(for:)):
//  a pure function parameterized by `Language`. Every call site passes an
//  explicit language — the /answer screen is Korean-first (always `.ko`),
//  DigestView drives it from its KO/EN toggle.
//

import Foundation

enum FigureDisplay {

    // MARK: - Tables (raw key -> (ko, en))

    /// Known unit keys.
    private static let unitNames: [String: (ko: String, en: String)] = [
        "KRW": ("원", "KRW"),
        "USD": ("USD", "USD"),
        "KRW_PER_SHARE": ("원/주", "KRW per share"),
        "USD_PER_SHARE": ("USD/주", "USD per share"),
    ]

    // MARK: - Explicit-language lookups (pure, deterministic)

    /// Humanized name for every canonical Reported Metric.
    static func metricName(_ metric: ReportedMetric, language: Language) -> String {
        let pair: (ko: String, en: String) = switch metric {
        case .revenue: ("매출액", "Revenue")
        case .operatingIncome: ("영업이익", "Operating Income")
        case .netIncome: ("당기순이익", "Net Income")
        case .netIncomeAttributable:
            ("지배기업 소유주지분 순이익", "Net Income (Attributable)")
        case .eps: ("주당순이익(EPS)", "EPS")
        case .epsDiluted: ("희석주당순이익", "Diluted EPS")
        }
        return language == .ko ? pair.ko : pair.en
    }

    /// Humanized unit name, or the raw key verbatim when unknown.
    static func unitName(_ key: String, language: Language) -> String {
        guard let pair = unitNames[key] else { return key }
        return language == .ko ? pair.ko : pair.en
    }

    // MARK: - Period titles

    /// Humanizes backend period codes for screen titles: "2023-annual" ->
    /// "사업보고서 2023" / "Annual Report 2023", "2026Q1" -> "2026년 1분기" /
    /// "Q1 2026". Unknown shapes fall back to the raw string verbatim.
    static func periodTitle(_ period: String, language: Language) -> String {
        if period.hasSuffix("-annual"), let year = Int(period.dropLast("-annual".count)) {
            return language == .ko ? "사업보고서 \(year)" : "Annual Report \(year)"
        }
        let parts = period.split(separator: "Q")
        if parts.count == 2, let year = Int(parts[0]), let quarter = Int(parts[1]),
           (1...4).contains(quarter) {
            return language == .ko ? "\(year)년 \(quarter)분기" : "Q\(quarter) \(year)"
        }
        return period
    }

    // MARK: - Value abbreviation

    /// Display string for a structured-API value: large KRW/USD amounts are
    /// abbreviated (조/억, T/B/M) so 15-digit values stay readable; everything
    /// else keeps the exact grouped number. Display-only — the exact value
    /// still lives in the model and callers may show it alongside.
    static func formattedValue(_ value: Double, unit: String, language: Language) -> String {
        let magnitude = abs(value)

        func scaled(_ divisor: Double, _ suffix: String) -> String {
            let n = (value / divisor).formatted(.number.precision(.fractionLength(0...1)))
            return "\(n)\(suffix)"
        }

        switch unit {
        case "KRW":
            if language == .ko {
                if magnitude >= 1e12 { return scaled(1e12, "조 원") }
                if magnitude >= 1e8 { return scaled(1e8, "억 원") }
            } else {
                if magnitude >= 1e12 { return scaled(1e12, "T KRW") }
                if magnitude >= 1e9 { return scaled(1e9, "B KRW") }
            }
        case "USD":
            if magnitude >= 1e12 { return scaled(1e12, language == .ko ? "조 달러" : "T USD") }
            if magnitude >= 1e9 { return scaled(1e9, language == .ko ? "B 달러" : "B USD") }
            if magnitude >= 1e6 { return scaled(1e6, language == .ko ? "M 달러" : "M USD") }
        default:
            break
        }

        let number = value.formatted(.number.precision(.fractionLength(0...2)))
        let unitText = unitName(unit, language: language)
        return unit.isEmpty ? number : "\(number)\(language == .ko ? "" : " ")\(unitText)"
    }
}

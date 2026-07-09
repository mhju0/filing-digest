//
//  FigureDisplay.swift
//  FilingDigest
//
//  Presentation-layer mapping from raw backend metric/unit keys to localized
//  display names. The wire values (Figure.metric, Figure.unit, MetricCard.unit)
//  are opaque strings; this is the single place that humanizes them for the UI.
//
//  Principle: this is display-only. It never changes numeric values, never
//  affects decoding, and unknown keys fall back to the raw string verbatim so
//  newly ingested metrics/units (e.g. Samsung keys beyond the core set) still
//  render — just un-humanized — instead of disappearing.
//
//  Bilingual by the same idiom the rest of the app uses (MetricCard.label(for:)):
//  a pure function parameterized by `Language`. Every call site passes an
//  explicit language — the /answer screen is Korean-first (always `.ko`),
//  DigestView drives it from its KO/EN toggle.
//

import Foundation

enum FigureDisplay {

    // MARK: - Tables (raw key -> (ko, en))

    /// Known metric keys. `net_income_attributable` / `eps_diluted` live here
    /// even though they are outside MetricKey's five enum cases, because
    /// Figure.metric is a free string and the backend can emit them.
    private static let metricNames: [String: (ko: String, en: String)] = [
        "revenue": ("매출액", "Revenue"),
        "operating_income": ("영업이익", "Operating Income"),
        "net_income": ("당기순이익", "Net Income"),
        "net_income_attributable": ("지배기업 소유주지분 순이익", "Net Income (Attributable)"),
        "eps": ("주당순이익(EPS)", "EPS"),
        "eps_diluted": ("희석주당순이익", "Diluted EPS"),
    ]

    /// Known unit keys.
    private static let unitNames: [String: (ko: String, en: String)] = [
        "KRW": ("원", "KRW"),
        "USD": ("USD", "USD"),
        "KRW_PER_SHARE": ("원/주", "KRW per share"),
        "USD_PER_SHARE": ("USD/주", "USD per share"),
    ]

    // MARK: - Explicit-language lookups (pure, deterministic)

    /// Humanized metric name, or the raw key verbatim when unknown.
    static func metricName(_ key: String, language: Language) -> String {
        guard let pair = metricNames[key] else { return key }
        return language == .ko ? pair.ko : pair.en
    }

    /// Humanized unit name, or the raw key verbatim when unknown.
    static func unitName(_ key: String, language: Language) -> String {
        guard let pair = unitNames[key] else { return key }
        return language == .ko ? pair.ko : pair.en
    }
}

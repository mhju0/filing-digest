import Foundation
import Testing
@testable import FilingDigest

@Suite("Financial vocabulary contract")
struct FinancialVocabularyContractTests {
    private struct Manifest: Decodable {
        let reportedMetrics: [String]
        let derivedMetrics: [String]
        let periodKinds: [String]
    }

    @Test("iOS vocabulary exactly matches the backend-owned manifest")
    func vocabularyMatchesManifest() throws {
        let repositoryRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let manifestURL = repositoryRoot
            .appendingPathComponent("contracts")
            .appendingPathComponent("financial-vocabulary.json")
        let data = try Data(contentsOf: manifestURL)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let manifest = try decoder.decode(Manifest.self, from: data)

        #expect(Set(ReportedMetric.allCases.map(\.rawValue)) == Set(manifest.reportedMetrics))
        #expect(Set(DerivedMetric.allCases.map(\.rawValue)) == Set(manifest.derivedMetrics))
        #expect(Set(PeriodKind.allCases.map(\.rawValue)) == Set(manifest.periodKinds))
    }

    private struct Glossary: Decodable {
        let metrics: [String: [String]]
        let companies: [String: [String]]
    }

    @Test("Display names match the family glossary shared with Filing Agent")
    func displayNamesMatchFamilyGlossary() throws {
        let repositoryRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let data = try Data(contentsOf: repositoryRoot
            .appendingPathComponent("contracts")
            .appendingPathComponent("family-glossary.json"))
        let glossary = try JSONDecoder().decode(Glossary.self, from: data)

        for metric in ReportedMetric.allCases {
            let names = try #require(glossary.metrics[metric.rawValue], "\(metric.rawValue)")
            #expect(FigureDisplay.metricName(metric, language: .ko) == names[0])
            #expect(FigureDisplay.metricName(metric, language: .en) == names[1])
        }
        for metric in DerivedMetric.allCases {
            let names = try #require(glossary.metrics[metric.rawValue], "\(metric.rawValue)")
            #expect(FigureDisplay.metricName(.derived(metric), language: .ko) == names[0])
            #expect(FigureDisplay.metricName(.derived(metric), language: .en) == names[1])
        }
        for (ticker, names) in glossary.companies {
            let company = Company(
                id: ticker, name: names[0], nameEn: nil, ticker: ticker, market: nil,
                source: ticker.allSatisfy(\.isNumber) ? .dart : .sec
            )
            #expect(company.displayName(.ko) == names[0], "\(ticker)")
            #expect(company.displayName(.en) == names[1], "\(ticker)")
        }
    }
}

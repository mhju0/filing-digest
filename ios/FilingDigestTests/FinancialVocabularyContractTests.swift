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
}

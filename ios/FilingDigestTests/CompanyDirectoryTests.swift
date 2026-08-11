import Foundation
import Testing
@testable import FilingDigest

@Suite("Company directory")
struct CompanyDirectoryTests {
    private static func company(
        _ name: String,
        nameEn: String? = nil,
        ticker: String? = nil,
        source: RegulatorySource = .dart
    ) -> Company {
        Company(
            id: UUID().uuidString,
            name: name,
            nameEn: nameEn,
            ticker: ticker,
            market: nil,
            source: source
        )
    }

    private var corpus: [Company] {
        [
            Self.company("삼성전자", nameEn: "SAMSUNG ELECTRONICS CO,.LTD", ticker: "005930"),
            Self.company("SK하이닉스", nameEn: "SK hynix Inc.", ticker: "000660"),
            Self.company("Apple Inc.", nameEn: "Apple Inc.", ticker: "AAPL", source: .sec),
            Self.company(
                "MICROSOFT CORP",
                nameEn: "MICROSOFT CORP",
                ticker: "MSFT",
                source: .sec
            ),
        ]
    }

    @Test("Empty and whitespace queries pass everything through")
    func emptyQuery() {
        let directory = CompanyDirectory(companies: corpus)

        #expect(directory.snapshot(query: "", recentStorage: "").visibleCompanies.count == 4)
        #expect(directory.snapshot(query: "   ", recentStorage: "").visibleCompanies.count == 4)
    }

    @Test("Filter matches Korean name, English name, and ticker, case-insensitively")
    func filterFields() {
        let directory = CompanyDirectory(companies: corpus)

        #expect(companies(matching: "삼성", in: directory) == ["삼성전자"])
        #expect(companies(matching: "hynix", in: directory) == ["SK하이닉스"])
        #expect(companies(matching: "msft", in: directory) == ["MICROSOFT CORP"])
        #expect(companies(matching: "애플", in: directory) == ["Apple Inc."])
        #expect(companies(matching: "카카오", in: directory).isEmpty)
    }

    @Test("All companies use their Korean display names for ordering")
    func ordering() {
        let snapshot = CompanyDirectory(companies: corpus).snapshot(query: "", recentStorage: "")

        #expect(snapshot.visibleCompanies.map(\.koreanDisplayName) == [
            "SK하이닉스", "마이크로소프트", "삼성전자", "애플",
        ])
    }

    @Test("Recent companies move to the front, deduplicate, and stay bounded")
    func recents() {
        let companies = corpus
        let first = companies[0].id
        let second = companies[1].id
        let third = companies[2].id
        let existing = CompanyDirectory.recordingVisit(to: second, in: first)
        #expect(existing == [second, first].joined(separator: ","))

        let repeated = CompanyDirectory.recordingVisit(to: first, in: existing)
        #expect(repeated == [first, second].joined(separator: ","))

        let bounded = CompanyDirectory.recordingVisit(to: third, in: repeated)
        #expect(bounded == [third, first].joined(separator: ","))
        let snapshot = CompanyDirectory(companies: companies).snapshot(
            query: "",
            recentStorage: bounded
        )
        #expect(snapshot.recentCompanies.map(\.id) == [third, first])
    }

    private func companies(matching query: String, in directory: CompanyDirectory) -> [String] {
        directory.snapshot(query: query, recentStorage: "").visibleCompanies.map(\.name)
    }
}

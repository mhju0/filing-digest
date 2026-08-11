import Foundation

/// Produces the ordered company index shown by the browse screen and owns the
/// compact persistence format for its recent-company history.
struct CompanyDirectory {
    struct Snapshot {
        let isFiltering: Bool
        let visibleCompanies: [Company]
        let recentCompanies: [Company]
    }

    private let companies: [Company]

    init(companies: [Company]) {
        self.companies = companies
    }

    /// Returns everything the browse screen needs for one query and one
    /// persisted recent-history value.
    func snapshot(query: String, recentStorage: String) -> Snapshot {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        let isFiltering = !trimmed.isEmpty
        let matchedCompanies = isFiltering
            ? companies.filter { matches($0, query: trimmed) }
            : companies
        let visibleCompanies = ordered(matchedCompanies)

        return Snapshot(
            isFiltering: isFiltering,
            visibleCompanies: visibleCompanies,
            recentCompanies: isFiltering ? [] : recentCompanies(from: recentStorage)
        )
    }

    /// Moves a company to the front, removes duplicates, and retains the two
    /// most recent companies without exposing the storage encoding to callers.
    static func recordingVisit(to companyID: String, in recentStorage: String) -> String {
        let existing = recentStorage.split(separator: ",").map(String.init)
        return ([companyID] + Array(existing.filter { $0 != companyID }.prefix(1)))
            .joined(separator: ",")
    }

    private func matches(_ company: Company, query: String) -> Bool {
        company.name.localizedCaseInsensitiveContains(query)
            || company.koreanDisplayName.localizedCaseInsensitiveContains(query)
            || (company.nameEn?.localizedCaseInsensitiveContains(query) ?? false)
            || (company.ticker?.localizedCaseInsensitiveContains(query) ?? false)
    }

    private func ordered(_ companies: [Company]) -> [Company] {
        companies.sorted {
            $0.koreanDisplayName.localizedStandardCompare($1.koreanDisplayName) == .orderedAscending
        }
    }

    private func recentIDs(from storage: String) -> [String] {
        storage.split(separator: ",").map(String.init)
    }

    private func recentCompanies(from storage: String) -> [Company] {
        let byID = Dictionary(uniqueKeysWithValues: companies.map { ($0.id, $0) })
        return recentIDs(from: storage).compactMap { byID[$0] }
    }
}

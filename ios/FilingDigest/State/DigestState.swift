import Combine
import Foundation

/// Owns digest request identity and preserves compatible content during refresh.
@MainActor
final class DigestState: ObservableObject {
    typealias FetchDigest = (String) async throws -> CompanyDigest

    @Published private(set) var digest: CompanyDigest?
    @Published private(set) var isLoading = false
    @Published private(set) var blockingError: String?
    @Published private(set) var refreshError: String?

    var isRefreshing: Bool { digest != nil && isLoading }

    private let fetchDigest: FetchDigest
    private var currentCompanyID: String?
    private var currentTask: Task<Void, Never>?
    private var generation = 0

    init(fetchDigest: @escaping FetchDigest) {
        self.fetchDigest = fetchDigest
    }

    func load(companyID: String) async {
        await request(companyID: companyID, force: false)
    }

    func refresh() async {
        guard let currentCompanyID else { return }
        await request(companyID: currentCompanyID, force: true)
    }

    func retry() async {
        guard let currentCompanyID else { return }
        await request(companyID: currentCompanyID, force: true)
    }

    func cancel() {
        generation += 1
        currentTask?.cancel()
        currentTask = nil
        isLoading = false
    }

    private func request(companyID: String, force: Bool) async {
        let isSameIntent = currentCompanyID == companyID
        if isSameIntent, let currentTask {
            await currentTask.value
            return
        }
        if isSameIntent, digest != nil, !force { return }

        currentTask?.cancel()
        generation += 1
        let requestGeneration = generation
        let preservesContent = isSameIntent && digest != nil
        currentCompanyID = companyID
        isLoading = true

        if preservesContent {
            refreshError = nil
        } else {
            digest = nil
            blockingError = nil
            refreshError = nil
        }

        let operation = fetchDigest
        let task = Task { @MainActor [weak self] in
            do {
                let result = try await operation(companyID)
                guard let self,
                      !Task.isCancelled,
                      self.generation == requestGeneration
                else { return }
                self.digest = result
                self.blockingError = nil
                self.refreshError = nil
                self.isLoading = false
                self.currentTask = nil
            } catch {
                guard let self, self.generation == requestGeneration else { return }
                self.isLoading = false
                self.currentTask = nil
                if preservesContent {
                    self.refreshError = error.localizedDescription
                } else {
                    self.blockingError = error.localizedDescription
                }
            }
        }
        currentTask = task
        await task.value
    }
}

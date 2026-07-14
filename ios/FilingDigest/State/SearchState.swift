import Combine
import Foundation

/// Owns asynchronous corpus loading independently from local query filtering.
@MainActor
final class SearchState: ObservableObject {
    typealias LoadCompanies = () async throws -> CompanySearchResponse

    @Published private(set) var companies: [Company] = []
    @Published private(set) var hasLoaded = false
    @Published private(set) var isLoading = false
    @Published private(set) var blockingError: String?
    @Published private(set) var refreshError: String?

    var isRefreshing: Bool { hasLoaded && isLoading }

    private let loadCompanies: LoadCompanies
    private var currentTask: Task<Void, Never>?
    private var generation = 0

    init(loadCompanies: @escaping LoadCompanies) {
        self.loadCompanies = loadCompanies
    }

    func loadIfNeeded() async {
        guard !hasLoaded else { return }
        await performLoad()
    }

    func refresh() async {
        await performLoad()
    }

    func retry() async {
        await performLoad()
    }

    func cancel() {
        generation += 1
        currentTask?.cancel()
        currentTask = nil
        isLoading = false
    }

    private func performLoad() async {
        if let currentTask {
            await currentTask.value
            return
        }

        generation += 1
        let requestGeneration = generation
        let preservesContent = hasLoaded
        isLoading = true
        if preservesContent {
            refreshError = nil
        } else {
            blockingError = nil
        }

        let operation = loadCompanies
        let task = Task { @MainActor [weak self] in
            do {
                let response = try await operation()
                guard let self,
                      !Task.isCancelled,
                      self.generation == requestGeneration
                else { return }
                self.companies = response.items
                self.hasLoaded = true
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

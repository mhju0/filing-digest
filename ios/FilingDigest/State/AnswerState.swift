import Combine
import Foundation

struct AnswerIntent: Equatable, Sendable {
    let query: String
    let companyID: UUID
    let period: String?
}

private enum AnswerStateError: LocalizedError {
    case invalidEvidence

    var errorDescription: String? {
        "서버 응답 형식이 앱과 맞지 않습니다."
    }
}

/// Owns Answer request identity, evidence validation, and latest-intent state.
@MainActor
final class AnswerState: ObservableObject {
    typealias SendAnswer = (String, UUID, String?) async throws -> AnswerResponse

    @Published private(set) var askedQuery = ""
    @Published private(set) var response: AnswerResponse?
    @Published private(set) var evidenceIndex: AnswerEvidenceIndex?
    @Published private(set) var isLoading = false
    @Published private(set) var blockingError: String?
    @Published private(set) var refreshError: String?

    var isRefreshing: Bool { response != nil && isLoading }

    private let sendAnswer: SendAnswer
    private var currentIntent: AnswerIntent?
    private var failedIntent: AnswerIntent?
    private var currentTask: Task<Void, Never>?
    private var generation = 0

    init(sendAnswer: @escaping SendAnswer) {
        self.sendAnswer = sendAnswer
    }

    func submit(query: String, companyID: UUID, period: String? = nil) async {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        let intent = AnswerIntent(query: trimmed, companyID: companyID, period: period)
        await request(intent)
    }

    /// Replays the exact failed intent, regardless of later draft-field edits.
    func retry() async {
        guard let intent = failedIntent ?? currentIntent else { return }
        await request(intent)
    }

    func cancel() {
        generation += 1
        currentTask?.cancel()
        currentTask = nil
        isLoading = false
    }

    private func request(_ intent: AnswerIntent) async {
        let isSameIntent = currentIntent == intent
        if isSameIntent, let currentTask {
            await currentTask.value
            return
        }

        currentTask?.cancel()
        generation += 1
        let requestGeneration = generation
        let preservesContent = isSameIntent && response != nil
        currentIntent = intent
        isLoading = true

        if preservesContent {
            refreshError = nil
        } else {
            failedIntent = nil
            askedQuery = intent.query
            response = nil
            evidenceIndex = nil
            blockingError = nil
            refreshError = nil
        }

        let operation = sendAnswer
        let task = Task { @MainActor [weak self] in
            do {
                let result = try await operation(intent.query, intent.companyID, intent.period)
                let index: AnswerEvidenceIndex?
                do {
                    index = try result.makeEvidenceIndex()
                } catch {
                    throw AnswerStateError.invalidEvidence
                }
                guard let self,
                      !Task.isCancelled,
                      self.generation == requestGeneration
                else { return }
                self.askedQuery = intent.query
                self.response = result
                self.evidenceIndex = index
                self.failedIntent = nil
                self.blockingError = nil
                self.refreshError = nil
                self.isLoading = false
                self.currentTask = nil
            } catch {
                guard let self, self.generation == requestGeneration else { return }
                self.failedIntent = intent
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

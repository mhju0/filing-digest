import Foundation
import Testing
@testable import FilingDigest

@Suite("Screen-specific asynchronous state")
@MainActor
struct AsyncStateTests {
    @Test("Search refresh retains the loaded corpus when refresh fails")
    func searchRefreshRetainsContent() async {
        let company = Company(
            id: UUID().uuidString,
            name: "삼성전자",
            nameEn: "Samsung Electronics",
            ticker: "005930",
            market: .kospi,
            source: .dart
        )
        let operation = ResultQueue<CompanySearchResponse>([
            .success(CompanySearchResponse(items: [company], total: 1)),
            .failure(TestFailure.offline),
        ])
        let state = SearchState(loadCompanies: { try await operation.next() })

        await state.loadIfNeeded()
        await state.refresh()

        #expect(state.companies == [company])
        #expect(state.blockingError == nil)
        #expect(state.refreshError != nil)
    }

    @Test("Digest ignores a superseded company's late response")
    func digestLatestIntentWins() async {
        let operation = DeferredDigestOperation()
        let state = DigestState(fetchDigest: { try await operation.run(companyID: $0) })

        let first = Task { await state.load(companyID: "company-a") }
        await Task.yield()
        let second = Task { await state.load(companyID: "company-b") }
        await operation.succeed(digest(companyID: "company-b"), for: "company-b")
        await second.value
        await operation.succeed(digest(companyID: "company-a"), for: "company-a")
        await first.value

        #expect(state.digest?.companyId == "company-b")
        #expect(state.blockingError == nil)
    }

    @Test("Answer presents only the latest query when an older response arrives last")
    func answerLatestIntentWins() async {
        let companyID = UUID()
        let firstResponseID = UUID()
        let secondResponseID = UUID()
        let operation = DeferredAnswerOperation()
        let state = AnswerState(sendAnswer: {
            try await operation.run(query: $0, companyID: $1, period: $2)
        })

        let first = Task { await state.submit(query: "first", companyID: companyID) }
        await Task.yield()
        let second = Task { await state.submit(query: "second", companyID: companyID) }
        await operation.succeed(answerResponse(companyID: secondResponseID), for: "second")
        await second.value
        await operation.succeed(answerResponse(companyID: firstResponseID), for: "first")
        await first.value

        #expect(state.askedQuery == "second")
        #expect(state.response?.companyId == secondResponseID)
        #expect(state.blockingError == nil)
    }

    @Test("Answer retry replays the failed intent and refresh failure retains success")
    func answerRetryAndRefreshRetention() async {
        let companyID = UUID()
        let operation = RecordingAnswerOperation([
            .failure(TestFailure.offline),
            .success(answerResponse(companyID: companyID)),
            .failure(TestFailure.offline),
        ])
        let state = AnswerState(sendAnswer: {
            try await operation.run(query: $0, companyID: $1, period: $2)
        })

        await state.submit(query: "original question", companyID: companyID)
        await state.retry()
        await state.submit(query: "original question", companyID: companyID)

        #expect(await operation.queries() == [
            "original question", "original question", "original question",
        ])
        #expect(state.askedQuery == "original question")
        #expect(state.response?.companyId == companyID)
        #expect(state.blockingError == nil)
        #expect(state.refreshError != nil)
    }

    private func digest(companyID: String) -> CompanyDigest {
        CompanyDigest(
            companyId: companyID,
            companyName: companyID,
            period: "2025-annual",
            metrics: [],
            summaryKo: nil,
            summaryEn: nil,
            filingSources: [],
            generatedAt: "2026-07-14T00:00:00Z"
        )
    }

    private func answerResponse(companyID: UUID) -> AnswerResponse {
        AnswerResponse(
            answer: nil,
            figures: [],
            citations: [],
            filingSources: [],
            companyId: companyID,
            narrativeStatus: .blocked,
            blockedReason: .narrativeUnavailable
        )
    }
}

private enum TestFailure: Error {
    case offline
}

private actor ResultQueue<Value: Sendable> {
    enum Step: @unchecked Sendable {
        case success(Value)
        case failure(Error)
    }

    private var steps: [Step]

    init(_ steps: [Step]) {
        self.steps = steps
    }

    func next() throws -> Value {
        guard !steps.isEmpty else { throw TestFailure.offline }
        switch steps.removeFirst() {
        case .success(let value): return value
        case .failure(let error): throw error
        }
    }
}

private actor DeferredDigestOperation {
    private var continuations: [String: CheckedContinuation<CompanyDigest, Error>] = [:]
    private var pending: [String: Result<CompanyDigest, Error>] = [:]

    func run(companyID: String) async throws -> CompanyDigest {
        if let result = pending.removeValue(forKey: companyID) {
            return try result.get()
        }
        return try await withCheckedThrowingContinuation { continuation in
            continuations[companyID] = continuation
        }
    }

    func succeed(_ digest: CompanyDigest, for companyID: String) {
        if let continuation = continuations.removeValue(forKey: companyID) {
            continuation.resume(returning: digest)
        } else {
            pending[companyID] = .success(digest)
        }
    }
}

private actor DeferredAnswerOperation {
    private var continuations: [String: CheckedContinuation<AnswerResponse, Error>] = [:]
    private var pending: [String: Result<AnswerResponse, Error>] = [:]

    func run(query: String, companyID: UUID, period: String?) async throws -> AnswerResponse {
        if let result = pending.removeValue(forKey: query) {
            return try result.get()
        }
        return try await withCheckedThrowingContinuation { continuation in
            continuations[query] = continuation
        }
    }

    func succeed(_ response: AnswerResponse, for query: String) {
        if let continuation = continuations.removeValue(forKey: query) {
            continuation.resume(returning: response)
        } else {
            pending[query] = .success(response)
        }
    }
}

private actor RecordingAnswerOperation {
    private var steps: [Result<AnswerResponse, Error>]
    private var recordedQueries: [String] = []

    init(_ steps: [Result<AnswerResponse, Error>]) {
        self.steps = steps
    }

    func run(query: String, companyID: UUID, period: String?) throws -> AnswerResponse {
        recordedQueries.append(query)
        guard !steps.isEmpty else { throw TestFailure.offline }
        return try steps.removeFirst().get()
    }

    func queries() -> [String] {
        recordedQueries
    }
}

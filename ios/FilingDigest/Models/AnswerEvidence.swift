import Foundation

enum AnswerEvidenceError: Error, Equatable, LocalizedError {
    case duplicateCitation(String)
    case duplicateFilingSource(String)
    case emptyNarrative
    case emptyExcerpt(String)
    case invalidFilingSourceURL(String)
    case missingCitation(String)
    case missingFilingSource(String)
    case missingNarrative
    case sourceOrderMismatch(expected: [String], actual: [String])
    case uncitedSegment(Int)
    case unusedCitation(String)

    var errorDescription: String? {
        switch self {
        case .duplicateCitation(let id):
            "중복된 인용 식별자입니다: \(id)"
        case .duplicateFilingSource(let id):
            "중복된 공시 출처 식별자입니다: \(id)"
        case .emptyNarrative:
            "완료된 답변에 서술 구간이 없습니다."
        case .emptyExcerpt(let id):
            "인용 근거 문장이 비어 있습니다: \(id)"
        case .invalidFilingSourceURL(let id):
            "열 수 없는 공시 출처입니다: \(id)"
        case .missingCitation(let id):
            "답변의 인용을 찾을 수 없습니다: \(id)"
        case .missingFilingSource(let id):
            "인용의 공시 출처를 찾을 수 없습니다: \(id)"
        case .missingNarrative:
            "완료된 답변에 검증할 서술이 없습니다."
        case .sourceOrderMismatch:
            "공시 출처 순서가 답변의 첫 인용 순서와 일치하지 않습니다."
        case .uncitedSegment(let index):
            "근거 인용이 없는 답변 구간입니다: \(index)"
        case .unusedCitation(let id):
            "답변에서 사용되지 않은 인용이 포함되어 있습니다: \(id)"
        }
    }
}

/// Validated presentation index for an answer's claim-level evidence.
///
/// Filing Sources keep the backend's first-appearance order. Citations retain
/// their own excerpt and anchor even when several resolve to the same source.
struct AnswerEvidenceIndex: Hashable, Sendable {
    struct Group: Identifiable, Hashable, Sendable {
        let filingSource: FilingSource
        let citations: [Citation]

        var id: String { filingSource.id }
    }

    let groups: [Group]
    private let sourceIndexByCitationID: [String: Int]

    init(
        answer: Answer,
        citations: [Citation],
        filingSources: [FilingSource]
    ) throws {
        guard !answer.answerSegments.isEmpty else {
            throw AnswerEvidenceError.emptyNarrative
        }
        for (index, segment) in answer.answerSegments.enumerated() where segment.citations.isEmpty {
            throw AnswerEvidenceError.uncitedSegment(index)
        }

        var sourcesByID: [String: FilingSource] = [:]
        for source in filingSources {
            guard sourcesByID.updateValue(source, forKey: source.id) == nil else {
                throw AnswerEvidenceError.duplicateFilingSource(source.id)
            }
            guard source.openableURL != nil else {
                throw AnswerEvidenceError.invalidFilingSourceURL(source.id)
            }
        }

        var citationsByID: [String: Citation] = [:]
        for citation in citations {
            guard citationsByID.updateValue(citation, forKey: citation.id) == nil else {
                throw AnswerEvidenceError.duplicateCitation(citation.id)
            }
            guard !citation.excerpt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                throw AnswerEvidenceError.emptyExcerpt(citation.id)
            }
            guard sourcesByID[citation.filingSourceId] != nil else {
                throw AnswerEvidenceError.missingFilingSource(citation.filingSourceId)
            }
        }

        var orderedSourceIDs: [String] = []
        var seenSourceIDs = Set<String>()
        var orderedCitationIDsBySource: [String: [String]] = [:]
        var seenCitationIDs = Set<String>()

        for segment in answer.answerSegments {
            for citationID in segment.citations {
                guard let citation = citationsByID[citationID] else {
                    throw AnswerEvidenceError.missingCitation(citationID)
                }
                if seenSourceIDs.insert(citation.filingSourceId).inserted {
                    orderedSourceIDs.append(citation.filingSourceId)
                }
                if seenCitationIDs.insert(citationID).inserted {
                    orderedCitationIDsBySource[citation.filingSourceId, default: []]
                        .append(citationID)
                }
            }
        }

        if let unusedID = citationsByID.keys.first(where: { !seenCitationIDs.contains($0) }) {
            throw AnswerEvidenceError.unusedCitation(unusedID)
        }

        let suppliedSourceIDs = filingSources.map(\.id)
        guard orderedSourceIDs == suppliedSourceIDs else {
            throw AnswerEvidenceError.sourceOrderMismatch(
                expected: orderedSourceIDs,
                actual: suppliedSourceIDs
            )
        }

        var sourceIndexByCitationID: [String: Int] = [:]
        var groups: [Group] = []
        for (offset, sourceID) in orderedSourceIDs.enumerated() {
            guard let source = sourcesByID[sourceID] else {
                throw AnswerEvidenceError.missingFilingSource(sourceID)
            }
            let groupCitations = try orderedCitationIDsBySource[sourceID, default: []].map { id in
                guard let citation = citationsByID[id] else {
                    throw AnswerEvidenceError.missingCitation(id)
                }
                sourceIndexByCitationID[id] = offset + 1
                return citation
            }
            groups.append(Group(filingSource: source, citations: groupCitations))
        }

        self.groups = groups
        self.sourceIndexByCitationID = sourceIndexByCitationID
    }

    func sourceIndex(forCitationID id: String) -> Int? {
        sourceIndexByCitationID[id]
    }
}

extension AnswerResponse {
    /// Builds a fail-closed evidence index for presentable narrative. Suppressed
    /// narrative states intentionally have no index while figures remain usable.
    func makeEvidenceIndex() throws -> AnswerEvidenceIndex? {
        guard narrativeStatus == .ok else { return nil }
        guard let answer else { throw AnswerEvidenceError.missingNarrative }
        return try AnswerEvidenceIndex(
            answer: answer,
            citations: citations,
            filingSources: filingSources
        )
    }
}

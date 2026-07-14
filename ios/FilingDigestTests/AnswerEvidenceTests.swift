import Foundation
import Testing
@testable import FilingDigest

@Suite("Answer evidence integrity")
struct AnswerEvidenceTests {
    @Test("multiple Citations retain their evidence while sharing one Filing Source number")
    func multipleCitationsShareFilingSource() throws {
        let firstSource = filingSource(id: "dart:2024-report", regulator: .dart)
        let secondSource = filingSource(id: "sec:0001-24", regulator: .sec)
        let first = citation(id: "chunk-1", filingSourceId: firstSource.id, excerpt: "first")
        let second = citation(id: "chunk-2", filingSourceId: firstSource.id, excerpt: "second")
        let third = citation(id: "chunk-3", filingSourceId: secondSource.id, excerpt: "third")
        let answer = Answer(answerSegments: [
            AnswerSegment(text: "Claim one", citations: [second.id]),
            AnswerSegment(text: "Claim two", citations: [first.id, third.id]),
        ])

        let index = try AnswerEvidenceIndex(
            answer: answer,
            citations: [first, second, third],
            filingSources: [firstSource, secondSource]
        )

        #expect(index.sourceIndex(forCitationID: first.id) == 1)
        #expect(index.sourceIndex(forCitationID: second.id) == 1)
        #expect(index.sourceIndex(forCitationID: third.id) == 2)
        #expect(index.groups.map(\.filingSource.id) == [firstSource.id, secondSource.id])
        #expect(index.groups[0].citations.map(\.excerpt) == ["second", "first"])
    }

    @Test("an unresolved Citation fails closed")
    func unresolvedCitationFailsClosed() {
        let source = filingSource(id: "dart:2024-report", regulator: .dart)
        let answer = Answer(answerSegments: [
            AnswerSegment(text: "Claim", citations: ["missing-chunk"]),
        ])

        #expect(throws: AnswerEvidenceError.missingCitation("missing-chunk")) {
            try AnswerEvidenceIndex(answer: answer, citations: [], filingSources: [source])
        }
    }

    @Test("a non-openable Filing Source fails closed")
    func nonOpenableSourceFailsClosed() {
        let invalidSource = FilingSource(
            id: "dart:2024-report",
            source: .dart,
            sourceFilingId: "2024-report",
            title: "Annual filing",
            url: "",
            filedAt: "2026-03-01"
        )
        let evidence = citation(
            id: "chunk-1",
            filingSourceId: invalidSource.id,
            excerpt: "evidence"
        )
        let answer = Answer(answerSegments: [
            AnswerSegment(text: "Claim", citations: [evidence.id]),
        ])

        #expect(throws: AnswerEvidenceError.invalidFilingSourceURL(invalidSource.id)) {
            try AnswerEvidenceIndex(
                answer: answer,
                citations: [evidence],
                filingSources: [invalidSource]
            )
        }
    }

    @Test("Filing Sources must follow first Citation appearance")
    func sourceOrderingIsValidated() {
        let firstSource = filingSource(id: "dart:first", regulator: .dart)
        let secondSource = filingSource(id: "sec:second", regulator: .sec)
        let first = citation(id: "chunk-1", filingSourceId: firstSource.id, excerpt: "first")
        let second = citation(id: "chunk-2", filingSourceId: secondSource.id, excerpt: "second")
        let answer = Answer(answerSegments: [
            AnswerSegment(text: "Claim", citations: [first.id, second.id]),
        ])

        #expect(throws: AnswerEvidenceError.sourceOrderMismatch(
            expected: [firstSource.id, secondSource.id],
            actual: [secondSource.id, firstSource.id]
        )) {
            try AnswerEvidenceIndex(
                answer: answer,
                citations: [first, second],
                filingSources: [secondSource, firstSource]
            )
        }
    }

    @Test("an ok narrative cannot contain zero segments")
    func emptyNarrativeFailsClosed() {
        #expect(throws: AnswerEvidenceError.emptyNarrative) {
            try AnswerEvidenceIndex(
                answer: Answer(answerSegments: []),
                citations: [],
                filingSources: []
            )
        }
    }

    @Test("every narrative segment must carry a Citation")
    func uncitedSegmentFailsClosed() {
        #expect(throws: AnswerEvidenceError.uncitedSegment(0)) {
            try AnswerEvidenceIndex(
                answer: Answer(answerSegments: [
                    AnswerSegment(text: "Unsupported claim", citations: []),
                ]),
                citations: [],
                filingSources: []
            )
        }
    }
}

private func citation(id: String, filingSourceId: String, excerpt: String) -> Citation {
    Citation(
        id: id,
        filingSourceId: filingSourceId,
        excerpt: excerpt,
        anchor: CitationAnchor(
            sectionTitle: "Business",
            sectionOrder: 1,
            partIndex: 0,
            chunkIndex: 2
        )
    )
}

private func filingSource(id: String, regulator: RegulatorySource) -> FilingSource {
    FilingSource(
        id: id,
        source: regulator,
        sourceFilingId: id.split(separator: ":").last.map(String.init) ?? id,
        title: "Annual filing",
        url: "https://example.com/\(id)",
        filedAt: "2026-03-01"
    )
}

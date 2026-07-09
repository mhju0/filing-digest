//
//  AnswerView.swift
//  FilingDigest
//
//  Single-shot Q&A against POST /answer for one company: question field on
//  top, then a 3-state render keyed on narrative_status (ok / blocked /
//  no_results). Figures are rendered in every state — they come from the
//  structured filing API and are independent of the narrative track, which
//  is the only thing the backend can withhold.
//

import SwiftUI

struct AnswerView: View {
    let client: APIClient
    let company: Company

    @State private var query = ""
    @State private var response: AnswerResponse?
    @State private var isLoading = false
    @State private var errorMessage: String?

    var body: some View {
        VStack(spacing: 0) {
            inputBar
            Divider()
            content
        }
        .navigationTitle(company.name)
        .navigationBarTitleDisplayMode(.inline)
    }

    // MARK: Input bar

    private var inputBar: some View {
        HStack(spacing: 8) {
            TextField("이 회사에 대해 질문하세요", text: $query, axis: .vertical)
                .lineLimit(1...4)
                .textFieldStyle(.roundedBorder)
                .onSubmit {
                    Task { await ask() }
                }
            Button {
                Task { await ask() }
            } label: {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.title2)
            }
            .disabled(!canAsk)
            .accessibilityLabel("질문 전송")
        }
        .padding()
    }

    private var canAsk: Bool {
        !isLoading && !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    // MARK: Content (loading / error / empty / result)

    @ViewBuilder
    private var content: some View {
        if isLoading {
            ProgressView("답변 생성 중…")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let errorMessage {
            ContentUnavailableView {
                Label("오류", systemImage: "exclamationmark.triangle")
            } description: {
                Text(errorMessage)
            } actions: {
                Button("다시 시도") {
                    Task { await ask() }
                }
                .buttonStyle(.borderedProminent)
            }
        } else if let response {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    resultContent(response)
                }
                .padding()
            }
        } else {
            ContentUnavailableView(
                "공시 기반 Q&A",
                systemImage: "questionmark.bubble",
                description: Text("질문하면 공시 인용이 붙은 답변과 확정 수치를 보여줍니다.")
            )
        }
    }

    // MARK: 3-state result

    @ViewBuilder
    private func resultContent(_ response: AnswerResponse) -> some View {
        switch response.narrativeStatus {
        case .ok:
            if let answer = response.answer {
                narrativeSection(answer, citations: response.citations)
            }
        case .blocked:
            blockedNotice
        case .noResults:
            noResultsNotice
        }
        figuresSection(response.figures)
    }

    @ViewBuilder
    private func narrativeSection(_ answer: Answer, citations: [Citation]) -> some View {
        let filingIndex = Self.buildFilingIndex(citations: citations)
        Text("답변")
            .font(.headline)
        ForEach(Array(answer.answerSegments.enumerated()), id: \.offset) { _, segment in
            SegmentView(segment: segment, citationIndex: filingIndex.chunkToIndex)
        }
        if !filingIndex.ordered.isEmpty {
            sourcesSection(filingIndex.ordered)
        }
    }

    /// Sources section: one `CitationRow` per unique filing, numbered to match
    /// the `[n]` chips rendered inline in `SegmentView`.
    private func sourcesSection(_ filings: [Citation]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("출처")
                .font(.headline)
            VStack(spacing: 8) {
                ForEach(Array(filings.enumerated()), id: \.offset) { index, citation in
                    HStack(alignment: .top, spacing: 6) {
                        Text("[\(index + 1)]")
                            .font(.caption.bold())
                            .foregroundStyle(.secondary)
                        CitationRow(citation: citation)
                    }
                }
            }
        }
    }

    /// Dedupes `citations` down to unique source filings, in first-seen
    /// order, and maps every chunk_id to its filing's 1-based index. Filing
    /// identity is `url` when non-empty (filing.url is shared verbatim by
    /// every chunk of the same filing — backend/app/api/routes.py:301-311),
    /// falling back to `title|filedAt` for the rare empty-url case.
    private static func buildFilingIndex(
        citations: [Citation]
    ) -> (ordered: [Citation], chunkToIndex: [String: Int]) {
        var indexByKey: [String: Int] = [:]
        var ordered: [Citation] = []
        var chunkToIndex: [String: Int] = [:]
        for citation in citations {
            let key = citation.url.isEmpty ? "\(citation.title)|\(citation.filedAt ?? "")" : citation.url
            let index: Int
            if let existing = indexByKey[key] {
                index = existing
            } else {
                index = ordered.count + 1
                indexByKey[key] = index
                ordered.append(citation)
            }
            chunkToIndex[citation.id] = index
        }
        return (ordered, chunkToIndex)
    }

    /// Not an error: the number guard suppressed the prose while the figures
    /// track survived, so this points the user at the table below.
    private var blockedNotice: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "shield.lefthalf.filled")
                .foregroundStyle(Color.orange)
            VStack(alignment: .leading, spacing: 4) {
                Text("정확한 수치는 아래 표에서 확인하세요")
                    .font(.subheadline.bold())
                Text("수치 정확성을 위해 AI 서술에는 숫자를 표시하지 않습니다. 아래 확정 수치는 공시 원문에서 직접 가져온 값입니다.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(Color.orange.opacity(0.12))
        )
    }

    private var noResultsNotice: some View {
        ContentUnavailableView(
            "관련 공시를 찾지 못했습니다",
            systemImage: "doc.text.magnifyingglass",
            description: Text("이 질문에 인용할 공시 문단이 없어 답변을 생성하지 않았습니다.")
        )
    }

    @ViewBuilder
    private func figuresSection(_ figures: [Figure]) -> some View {
        if !figures.isEmpty {
            Text("확정 수치")
                .font(.headline)
            VStack(spacing: 8) {
                ForEach(Array(figures.enumerated()), id: \.offset) { _, figure in
                    FigureRow(figure: figure)
                }
            }
        }
    }

    // MARK: Action

    private func ask() async {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isLoading else { return }
        guard let companyId = UUID(uuidString: company.id) else {
            errorMessage = "회사 ID 형식이 올바르지 않습니다."
            return
        }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            response = try await client.sendAnswer(query: trimmed, companyId: companyId)
        } catch {
            response = nil
            errorMessage = error.localizedDescription
        }
    }
}

// MARK: - Segment

/// One narrated span plus compact `[n]` citation chips. Each chunk id in
/// `segment.citations` is resolved against `citationIndex` (chunk_id -> the
/// unique filing's 1-based position, built by `AnswerView.buildFilingIndex`)
/// into a small capsule; an unmatched id falls back to the raw chunk_id
/// capsule, which surfaces a citation-contract violation instead of hiding
/// it. The full per-filing detail lives once in the "출처" section below.
private struct SegmentView: View {
    let segment: AnswerSegment
    let citationIndex: [String: Int]

    private enum Chip: Hashable, Identifiable {
        case filing(Int)
        case raw(String)
        var id: Self { self }
    }

    private var chips: [Chip] {
        var seenIndices = Set<Int>()
        var seenRaw = Set<String>()
        var result: [Chip] = []
        for chunkID in segment.citations {
            if let index = citationIndex[chunkID] {
                if seenIndices.insert(index).inserted {
                    result.append(.filing(index))
                }
            } else if seenRaw.insert(chunkID).inserted {
                result.append(.raw(chunkID))
            }
        }
        return result
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(segment.text)
                .font(.body)
            if !chips.isEmpty {
                HStack(spacing: 6) {
                    ForEach(chips) { chip in
                        switch chip {
                        case .filing(let index):
                            Text("[\(index)]")
                                .font(.caption2.bold())
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(Capsule().fill(Color.accentColor.opacity(0.15)))
                                .foregroundStyle(Color.accentColor)
                        case .raw(let chunkID):
                            Text(chunkID)
                                .font(.caption2.monospaced())
                                .lineLimit(1)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 3)
                                .background(Capsule().fill(Color.accentColor.opacity(0.12)))
                                .foregroundStyle(Color.accentColor)
                        }
                    }
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(Color(.secondarySystemBackground))
        )
    }
}

// MARK: - Figure row

private struct FigureRow: View {
    let figure: Figure

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text(FigureDisplay.metricName(figure.metric, language: .ko))
                    .font(.subheadline.bold())
                Text(periodText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Text(valueText)
                .font(.body.monospacedDigit())
                .lineLimit(1)
                .minimumScaleFactor(0.6)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(Color(.secondarySystemBackground))
        )
    }

    private var periodText: String {
        if let quarter = figure.fiscalQuarter {
            return "\(figure.period) · FY\(figure.fiscalYear) Q\(quarter)"
        }
        return "\(figure.period) · FY\(figure.fiscalYear)"
    }

    /// Formats the Decimal directly (Decimal.FormatStyle) — no Double round
    /// trip, so numeric(24,4) precision is preserved end to end.
    private var valueText: String {
        let number = figure.value.formatted(
            .number.precision(.fractionLength(0...4)).grouping(.automatic)
        )
        let unitSuffix = figure.unit.isEmpty ? "" : " \(FigureDisplay.unitName(figure.unit, language: .ko))"
        return number + unitSuffix
    }
}

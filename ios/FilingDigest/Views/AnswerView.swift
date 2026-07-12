//
//  AnswerView.swift
//  FilingDigest
//
//  Single-shot Q&A against POST /answer for one company, Ledger system
//  (docs/design/DESIGN.md): the asked question renders as an editorial
//  pull-quote, narrative segments are plain paragraphs with square citation
//  markers, figures live in a green-bordered callout, and the input bar sits
//  at the bottom. 3-state render keyed on narrative_status (ok / blocked /
//  no_results). Figures are rendered in every state — they come from the
//  structured filing API and are independent of the narrative track, which
//  is the only thing the backend can withhold.
//

import SwiftUI

struct AnswerView: View {
    let client: APIClient
    let company: Company

    @State private var query = ""
    /// The question the current `response` answers — shown as the pull-quote.
    @State private var askedQuery = ""
    @State private var response: AnswerResponse?
    @State private var isLoading = false
    @State private var errorMessage: String?

    var body: some View {
        content
            .paperBackground()
            .safeAreaInset(edge: .bottom) { inputBar }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text(company.name)
                        .font(Theme.display(17))
                        .foregroundStyle(Theme.ink)
                }
            }
            .task {
                #if DEBUG
                // Screenshot automation: auto-ask (see ContentView).
                if let q = ProcessInfo.processInfo.environment["FD_QUERY"], response == nil {
                    query = q
                    await ask()
                }
                #endif
            }
    }

    // MARK: Input bar (bottom)

    private var inputBar: some View {
        HStack(spacing: 10) {
            TextField(
                response == nil ? "이 회사에 대해 질문하세요" : "이어서 질문하기",
                text: $query,
                axis: .vertical
            )
            .lineLimit(1...4)
            .font(.body)
            .foregroundStyle(Theme.ink)
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .overlay(
                RoundedRectangle(cornerRadius: 2)
                    .strokeBorder(Theme.hairline, lineWidth: 1)
            )
            .onSubmit {
                Task { await ask() }
            }

            Button {
                Task { await ask() }
            } label: {
                Image(systemName: "arrow.up")
                    .font(.body.weight(.semibold))
                    .foregroundStyle(Theme.paper)
                    .frame(width: 40, height: 40)
                    .background(Rectangle().fill(canAsk ? Theme.ink : Theme.inkMuted))
            }
            .disabled(!canAsk)
            .accessibilityLabel("질문 전송")
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 10)
        .background(Theme.paper)
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
                .buttonStyle(.bordered)
            }
        } else if let response {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    questionQuote
                    resultContent(response)
                }
                .padding(.horizontal, 20)
                .padding(.top, 12)
                .padding(.bottom, 8)
            }
        } else {
            ContentUnavailableView(
                "공시 기반 Q&A",
                systemImage: "questionmark.bubble",
                description: Text("질문하면 공시 인용이 붙은 답변과 확정 수치를 보여줍니다.")
            )
        }
    }

    /// The asked question as an editorial pull-quote: 2px ink rule + serif.
    private var questionQuote: some View {
        HStack(alignment: .top, spacing: 12) {
            Rectangle()
                .fill(Theme.ink)
                .frame(width: 2)
            Text(askedQuery)
                .font(.system(.title3, design: .serif))
                .italic()
                .foregroundStyle(Theme.ink)
        }
        .fixedSize(horizontal: false, vertical: true)
        .accessibilityLabel("질문: \(askedQuery)")
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
        SectionHeader(title: "답변")
        ForEach(Array(answer.answerSegments.enumerated()), id: \.offset) { _, segment in
            SegmentView(segment: segment, citationIndex: filingIndex.chunkToIndex)
        }
        if !filingIndex.ordered.isEmpty {
            sourcesSection(filingIndex.ordered)
        }
    }

    /// Sources section: one `CitationRow` per unique filing, numbered to match
    /// the square markers rendered inline in `SegmentView`.
    private func sourcesSection(_ filings: [Citation]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "출처")
            ForEach(Array(filings.enumerated()), id: \.offset) { index, citation in
                HStack(alignment: .top, spacing: 10) {
                    CitationMarker(index: index + 1)
                        .padding(.top, 14)
                    CitationRow(citation: citation)
                }
                Rectangle()
                    .fill(Theme.hairline)
                    .frame(height: 1)
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
                .foregroundStyle(Color.accentColor)
            VStack(alignment: .leading, spacing: 4) {
                Text("정확한 수치는 아래 표에서 확인하세요")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                Text("수치 정확성을 위해 AI 서술에는 숫자를 표시하지 않습니다. 아래 확정 수치는 공시 원문에서 직접 가져온 값입니다.")
                    .font(.caption)
                    .foregroundStyle(Theme.inkMuted)
            }
        }
        .ledgerCard(borderColor: Color.accentColor.opacity(0.5))
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
            VStack(alignment: .leading, spacing: 0) {
                Text("확정 수치 — 구조화 공시 데이터")
                    .font(Theme.sectionLabel)
                    .tracking(1)
                    .foregroundStyle(Color.accentColor)
                    .padding(.bottom, 4)
                ForEach(Array(figures.enumerated()), id: \.offset) { index, figure in
                    if index > 0 {
                        Rectangle()
                            .fill(Theme.hairline)
                            .frame(height: 1)
                    }
                    FigureRow(figure: figure)
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .overlay(
                RoundedRectangle(cornerRadius: 2)
                    .strokeBorder(Color.accentColor.opacity(0.6), lineWidth: 1)
            )
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
        askedQuery = trimmed
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

/// One narrated paragraph plus square citation markers in a wrapping row.
/// Each chunk id in `segment.citations` is resolved against `citationIndex`
/// (chunk_id -> the unique filing's 1-based position, built by
/// `AnswerView.buildFilingIndex`) into a `CitationMarker`; an unmatched id
/// falls back to the raw chunk_id, which surfaces a citation-contract
/// violation instead of hiding it. The full per-filing detail lives once in
/// the "출처" section below.
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
                .foregroundStyle(Theme.ink)
                .lineSpacing(6)
            if !chips.isEmpty {
                FlowLayout(spacing: 6) {
                    ForEach(chips) { chip in
                        switch chip {
                        case .filing(let index):
                            CitationMarker(index: index)
                        case .raw(let chunkID):
                            Text(chunkID)
                                .font(.caption2.monospaced())
                                .lineLimit(1)
                                .foregroundStyle(Theme.inkMuted)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .overlay(Rectangle().strokeBorder(Theme.hairline, lineWidth: 1))
                        }
                    }
                }
            }
        }
        .padding(.bottom, 4)
    }
}

// MARK: - Figure row

private struct FigureRow: View {
    let figure: Figure

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(FigureDisplay.metricName(figure.metric, language: .ko))
                    .font(.subheadline)
                    .foregroundStyle(Theme.ink)
                Text(periodText)
                    .font(.caption2.monospaced())
                    .foregroundStyle(Theme.inkMuted)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                Text(abbreviatedText)
                    .font(.title3.weight(.semibold))
                    .monospacedDigit()
                    .foregroundStyle(Theme.ink)
                    .lineLimit(1)
                    .minimumScaleFactor(0.6)
                if let exact = exactText {
                    Text(exact)
                        .font(.caption2.monospaced())
                        .foregroundStyle(Theme.inkMuted)
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                }
            }
        }
        .padding(.vertical, 10)
        .accessibilityElement(children: .combine)
    }

    private var periodText: String {
        let title = FigureDisplay.periodTitle(figure.period, language: .ko)
        if let quarter = figure.fiscalQuarter {
            return "\(title) · FY\(figure.fiscalYear) Q\(quarter)"
        }
        return "\(title) · FY\(figure.fiscalYear)"
    }

    /// Abbreviated display value (조/억) — readable at a glance.
    private var abbreviatedText: String {
        FigureDisplay.formattedValue(
            NSDecimalNumber(decimal: figure.value).doubleValue,
            unit: figure.unit,
            language: .ko
        )
    }

    /// The exact structured-API value (lossless Decimal), shown under the
    /// abbreviation whenever abbreviating actually dropped digits — the
    /// authoritative track stays fully inspectable.
    private var exactText: String? {
        let exact = figure.value.formatted(
            .number.precision(.fractionLength(0...4)).grouping(.automatic)
        )
        // Same no-space KO join as FigureDisplay.formattedValue, so an
        // unabbreviated value compares equal and the duplicate line hides.
        let unitText = figure.unit.isEmpty
            ? ""
            : FigureDisplay.unitName(figure.unit, language: .ko)
        let full = "\(exact)\(unitText)"
        return full == abbreviatedText ? nil : full
    }
}

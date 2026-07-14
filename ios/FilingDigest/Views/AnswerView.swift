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

    @StateObject private var state: AnswerState
    @State private var query = ""

    init(client: APIClient, company: Company) {
        self.client = client
        self.company = company
        _state = StateObject(wrappedValue: AnswerState(sendAnswer: {
            try await client.sendAnswer(query: $0, companyId: $1, period: $2)
        }))
    }

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
            .onDisappear { state.cancel() }
    }

    // MARK: Input bar (bottom)

    private var inputBar: some View {
        HStack(spacing: 10) {
            TextField(
                state.response == nil ? "이 회사에 대해 질문하세요" : "이어서 질문하기",
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
        UUID(uuidString: company.id) != nil
            && !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    // MARK: Content (loading / error / empty / result)

    @ViewBuilder
    private var content: some View {
        if let response = state.response {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    questionQuote
                    requestStatus
                    resultContent(response)
                }
                .padding(.horizontal, 20)
                .padding(.top, 12)
                .padding(.bottom, 8)
            }
        } else if state.isLoading {
            ProgressView("답변 생성 중…")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let blockingError = state.blockingError {
            ContentUnavailableView {
                Label("오류", systemImage: "exclamationmark.triangle")
            } description: {
                Text(blockingError)
            } actions: {
                Button("다시 시도") {
                    Task { await state.retry() }
                }
                .buttonStyle(.bordered)
            }
        } else {
            ContentUnavailableView(
                "공시 기반 Q&A",
                systemImage: "questionmark.bubble",
                description: Text("질문하면 공시 인용이 붙은 답변과 확정 수치를 보여줍니다.")
            )
        }
    }

    @ViewBuilder
    private var requestStatus: some View {
        if state.isRefreshing {
            ProgressView("답변을 새로 생성하는 중…")
                .font(.caption)
                .foregroundStyle(Theme.inkMuted)
        }
        if let refreshError = state.refreshError {
            Label(refreshError, systemImage: "exclamationmark.circle")
                .font(.caption)
                .foregroundStyle(Theme.inkMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// The asked question as an editorial pull-quote: 2px ink rule + serif.
    private var questionQuote: some View {
        HStack(alignment: .top, spacing: 12) {
            Rectangle()
                .fill(Theme.ink)
                .frame(width: 2)
            Text(state.askedQuery)
                .font(.system(.title3, design: .serif))
                .italic()
                .foregroundStyle(Theme.ink)
        }
        .fixedSize(horizontal: false, vertical: true)
        .accessibilityLabel("질문: \(state.askedQuery)")
    }

    // MARK: 3-state result

    @ViewBuilder
    private func resultContent(_ response: AnswerResponse) -> some View {
        switch response.narrativeStatus {
        case .ok:
            if let answer = response.answer, let evidenceIndex = state.evidenceIndex {
                narrativeSection(answer, evidenceIndex: evidenceIndex)
            }
        case .blocked:
            blockedNotice(reason: response.blockedReason)
        case .noResults:
            noResultsNotice
        }
        figuresSection(response.figures)
    }

    @ViewBuilder
    private func narrativeSection(_ answer: Answer, evidenceIndex: AnswerEvidenceIndex) -> some View {
        SectionHeader(title: "답변")
        ForEach(Array(answer.answerSegments.enumerated()), id: \.offset) { _, segment in
            SegmentView(segment: segment, evidenceIndex: evidenceIndex)
        }
        if !evidenceIndex.groups.isEmpty {
            sourcesSection(evidenceIndex.groups)
        }
    }

    /// Sources section: one group per Filing Source, numbered to match
    /// the square markers rendered inline in `SegmentView`.
    private func sourcesSection(_ groups: [AnswerEvidenceIndex.Group]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "출처")
            ForEach(Array(groups.enumerated()), id: \.element.id) { index, group in
                VStack(alignment: .leading, spacing: 8) {
                    HStack(alignment: .top, spacing: 10) {
                        CitationMarker(index: index + 1)
                            .padding(.top, 14)
                        FilingSourceRow(filingSource: group.filingSource)
                    }
                    ForEach(group.citations) { citation in
                        CitationEvidenceRow(citation: citation)
                            .padding(.leading, 34)
                    }
                }
                Rectangle()
                    .fill(Theme.hairline)
                    .frame(height: 1)
            }
        }
    }

    /// Not an error: the number guard suppressed the prose while the figures
    /// track survived, so this points the user at the table below.
    private func blockedNotice(reason: NarrativeBlockedReason?) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "shield.lefthalf.filled")
                .foregroundStyle(Color.accentColor)
            VStack(alignment: .leading, spacing: 4) {
                Text("정확한 수치는 아래 표에서 확인하세요")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                Text(reason?.userMessage ?? "AI 서술을 표시할 수 없어 아래 확정 수치만 제공합니다.")
                    .font(.caption)
                    .foregroundStyle(Theme.inkMuted)
            }
        }
        .ledgerCard(borderColor: Color.accentColor.opacity(0.5))
        .accessibilityElement(children: .combine)
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
                    .accessibilityAddTraits(.isHeader)
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
        guard !trimmed.isEmpty, let companyId = UUID(uuidString: company.id) else { return }
        await state.submit(query: trimmed, companyID: companyId)
    }
}

// MARK: - Evidence preview

private struct CitationEvidenceRow: View {
    let citation: Citation

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(citation.excerpt)
                .font(.caption)
                .foregroundStyle(Theme.ink)
                .lineLimit(4)
            Text(anchorText)
                .font(.caption2.monospaced())
                .foregroundStyle(Theme.inkMuted)
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }

    private var anchorText: String {
        var parts: [String] = []
        if let sectionTitle = citation.anchor.sectionTitle, !sectionTitle.isEmpty {
            parts.append(sectionTitle)
        }
        if let sectionOrder = citation.anchor.sectionOrder {
            parts.append("section \(sectionOrder)")
        }
        if let partIndex = citation.anchor.partIndex {
            parts.append("part \(partIndex)")
        }
        parts.append("chunk \(citation.anchor.chunkIndex)")
        return parts.joined(separator: " · ")
    }
}

// MARK: - Segment

/// One narrated paragraph plus square citation markers in a wrapping row.
/// Each Citation id resolves through the validated evidence index to the
/// backend-ordered Filing Source marker. Invalid evidence never reaches this
/// view because AnswerState fails closed.
private struct SegmentView: View {
    let segment: AnswerSegment
    let evidenceIndex: AnswerEvidenceIndex

    private var sourceIndices: [Int] {
        var seenIndices = Set<Int>()
        var result: [Int] = []
        for chunkID in segment.citations {
            if let index = evidenceIndex.sourceIndex(forCitationID: chunkID),
               seenIndices.insert(index).inserted {
                result.append(index)
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
            if !sourceIndices.isEmpty {
                FlowLayout(spacing: 6) {
                    ForEach(sourceIndices, id: \.self) { index in
                        CitationMarker(index: index)
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
        let kind = figure.periodKind == .instant ? "기준일" : "기간"
        if let quarter = figure.fiscalQuarter {
            return "\(title) · \(kind) · FY\(figure.fiscalYear) Q\(quarter)"
        }
        return "\(title) · \(kind) · FY\(figure.fiscalYear)"
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

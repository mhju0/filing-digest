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
    @State private var selectedEvidence: EvidenceSelection?
    /// Only consulted where the figures track is supplementary; a withheld
    /// narrative expands it unconditionally.
    @State private var figuresExpanded = false

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
                    Text("\(company.koreanDisplayName) / 답변")
                        .font(Theme.display(.headline))
                        .foregroundStyle(Theme.ink)
                        .lineLimit(1)
                }
            }
            .toolbarBackground(Theme.paper, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .sheet(item: $selectedEvidence) { selection in
                EvidenceSheet(selection: selection)
                    .presentationDetents([.medium, .large])
                    .presentationDragIndicator(.visible)
                    .presentationBackground(Theme.paper)
            }
            .onDisappear { state.cancel() }
    }

    // MARK: Input bar (bottom)

    private var inputBar: some View {
        HStack(spacing: 10) {
            TextField(
                // "이어서" promised a thread the backend does not keep: every
                // /answer call is single-shot and carries no history.
                state.response == nil ? "이 회사에 대해 질문하세요" : "다른 질문하기",
                text: $query,
                axis: .vertical
            )
            .lineLimit(1...4)
            .submitLabel(.send)
            .font(.body)
            .foregroundStyle(Theme.ink)
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .overlay(
                RoundedRectangle(cornerRadius: 2)
                    .strokeBorder(Theme.border, lineWidth: 1)
            )
            .onSubmit {
                Task { await ask(query) }
            }

            Button {
                Task { await ask(query) }
            } label: {
                Group {
                    if state.isLoading {
                        ProgressView()
                            .tint(Theme.paper)
                    } else {
                        Image(systemName: "arrow.up")
                            .font(.body.weight(.semibold))
                            .foregroundStyle(Theme.paper)
                    }
                }
                .frame(width: 44, height: 44)
                .background(Rectangle().fill(canAsk ? Theme.ink : Theme.border))
            }
            .disabled(!canAsk)
            .accessibilityLabel("질문 전송")
        }
        .readableWidth()
        .padding(.horizontal, Theme.pageInset)
        .padding(.vertical, 10)
        .background(Theme.paper)
        .overlay(alignment: .top) {
            Rectangle().fill(Theme.hairline).frame(height: 1)
        }
    }

    private var canAsk: Bool {
        UUID(uuidString: company.id) != nil
            && !state.isLoading
            && !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    // MARK: Content (loading / error / empty / result)

    @ViewBuilder
    private var content: some View {
        if let response = state.response {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.sectionSpacing) {
                    questionQuote
                    requestStatus
                    resultContent(response) { markerIndex in
                        if let evidenceIndex = state.evidenceIndex,
                           let group = evidenceIndex.group(atSourceIndex: markerIndex) {
                            selectedEvidence = EvidenceSelection(index: markerIndex, group: group)
                        }
                    }
                }
                .padding(.horizontal, Theme.pageInset)
                .padding(.top, 12)
                .padding(.bottom, 8)
                .readableWidth()
            }
        } else if state.isLoading {
            pendingAnswer
        } else if let blockingError = state.blockingError {
            ContentUnavailableView {
                Label("답변을 가져오지 못했습니다", systemImage: "exclamationmark.triangle")
            } description: {
                Text(blockingError)
            } actions: {
                Button("다시 시도") {
                    Task { await state.retry() }
                }
                .buttonStyle(.ledger)
            }
        } else {
            starter
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

    /// Questions that ask for a number come back `blocked` by design, so an
    /// empty screen plus a text field invites exactly the question the guard
    /// will withhold. These are the shapes that retrieve well against an
    /// annual report, and they teach the rule faster than any explanation.
    /// Korean phrasing also works against the SEC corpus — KURE-v1 is one
    /// cross-lingual embedding space.
    private static let suggestedQuestions = [
        "주요 사업 부문은 무엇인가요",
        "주요 리스크 요인은 무엇인가요",
        "연구개발 조직은 어떻게 구성되어 있나요",
    ]

    private var starter: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 8) {
                    Image(systemName: "text.quote")
                        .font(.title)
                        .foregroundStyle(Theme.inkMuted)
                    Text("공시에 있는 것만 답합니다")
                        .font(Theme.display(.title3))
                        .foregroundStyle(Theme.ink)
                    Text("답변의 모든 문장에 원문 인용이 붙습니다. 근거를 찾지 못하면 답하지 않습니다.")
                        .font(.subheadline)
                        .foregroundStyle(Theme.inkMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.top, 28)
                .accessibilityElement(children: .combine)

                SectionHeader(title: "이렇게 물어보세요")

                VStack(spacing: 9) {
                    ForEach(Self.suggestedQuestions, id: \.self) { question in
                        Button {
                            Task { await ask(question) }
                        } label: {
                            suggestionRow(question)
                        }
                        .buttonStyle(.plain)
                        .disabled(UUID(uuidString: company.id) == nil)
                    }
                }
            }
            .padding(.horizontal, Theme.pageInset)
            .padding(.bottom, 8)
            .readableWidth()
        }
    }

    private func suggestionRow(_ question: String) -> some View {
        HStack(spacing: 10) {
            Text("?")
                .font(.caption.weight(.bold))
                .foregroundStyle(Color.accentColor)
            Text(question)
                .font(.subheadline)
                .foregroundStyle(Theme.ink)
                .multilineTextAlignment(.leading)
            Spacer(minLength: 8)
        }
        .padding(.horizontal, 13)
        .padding(.vertical, 11)
        .frame(minHeight: 44)
        .contentShape(Rectangle())
        .overlay(
            RoundedRectangle(cornerRadius: 2)
                .strokeBorder(Theme.border, lineWidth: 1)
        )
    }

    /// Generation takes several seconds. Replacing the screen with a bare
    /// spinner took the question away with it, so the reader spent the wait
    /// with nothing to look at and no reminder of what they had asked.
    private var pendingAnswer: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                questionQuote
                VStack(alignment: .leading, spacing: 12) {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("답변 생성 중")
                            .font(Theme.sectionLabel)
                            .tracking(1.2)
                            .foregroundStyle(Theme.inkMuted)
                    }
                    Rectangle().fill(Theme.hairline).frame(height: 1)
                }
                .padding(.top, 8)
                VStack(alignment: .leading, spacing: 10) {
                    ForEach([1.0, 0.96, 0.88, 0.58], id: \.self) { fraction in
                        Rectangle()
                            .fill(Theme.hairline)
                            .frame(height: 11)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .scaleEffect(x: fraction, anchor: .leading)
                    }
                }
                Text("공시 원문에서 근거를 찾는 중입니다.")
                    .font(.caption)
                    .foregroundStyle(Theme.inkMuted)
            }
            .padding(.horizontal, Theme.pageInset)
            .padding(.top, 12)
            .readableWidth()
            .accessibilityElement(children: .combine)
            .accessibilityLabel("답변 생성 중. 질문: \(state.askedQuery)")
        }
    }

    /// The asked question as an editorial pull-quote: 2px ink rule + serif.
    private var questionQuote: some View {
        HStack(alignment: .top, spacing: 12) {
            Rectangle()
                .fill(Theme.ink)
                .frame(width: 2)
            Text("“\(state.askedQuery)”")
                .font(.system(.title3, design: .serif, weight: .semibold))
                .foregroundStyle(Theme.ink)
        }
        .fixedSize(horizontal: false, vertical: true)
        .accessibilityLabel("질문: \(state.askedQuery)")
    }

    // MARK: 3-state result

    @ViewBuilder
    private func resultContent(
        _ response: AnswerResponse,
        onCitationTap: @escaping (Int) -> Void
    ) -> some View {
        switch response.narrativeStatus {
        case .ok:
            if let answer = response.answer, let evidenceIndex = state.evidenceIndex {
                narrativeSection(answer, evidenceIndex: evidenceIndex, onCitationTap: onCitationTap)
            }
            // The prose answered the question; these are reference material.
            figuresSection(response.figures, collapsible: true)
        case .blocked:
            blockedNotice(reason: response.blockedReason)
            // The narrative was withheld, so these values ARE the answer.
            figuresSection(response.figures, collapsible: false)
        case .noResults:
            noResultsNotice
            // Nothing matched the question, so the whole-company figures are
            // unrelated to it — showing them open reads as a wrong answer.
            figuresSection(response.figures, collapsible: true)
        }
    }

    @ViewBuilder
    private func narrativeSection(
        _ answer: Answer,
        evidenceIndex: AnswerEvidenceIndex,
        onCitationTap: @escaping (Int) -> Void
    ) -> some View {
        SectionHeader(
            title: "답변 / 주장 \(answer.answerSegments.count)개",
            detail: "근거 확인됨"
        )
        ForEach(Array(answer.answerSegments.enumerated()), id: \.offset) { _, segment in
            SegmentView(
                segment: segment,
                evidenceIndex: evidenceIndex,
                onCitationTap: onCitationTap
            )
        }
    }

    /// Not an error: the number guard suppressed the prose while the figures
    /// track survived, so this points the user at the table below.
    private func blockedNotice(reason: NarrativeBlockedReason?) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "shield.lefthalf.filled")
                .foregroundStyle(Color.accentColor)
            VStack(alignment: .leading, spacing: 4) {
                Text("숫자는 AI가 쓰지 않습니다")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                Text(reason?.userMessage ?? "이 답변의 서술은 보류하고, 공시 원문 수치만 보여줍니다.")
                    .font(.caption)
                    .foregroundStyle(Theme.inkMuted)
            }
        }
        .ledgerCard(borderColor: Color.accentColor)
        .accessibilityElement(children: .combine)
    }

    private var noResultsNotice: some View {
        ContentUnavailableView(
            "공시에서 근거를 찾지 못했습니다",
            systemImage: "doc.text.magnifyingglass",
            description: Text("인용할 문단이 없어 답하지 않았습니다. 이 회사의 사업, 리스크, 조직에 대해 물어보세요.")
        )
    }

    /// Whole-company figures, which is every reporting period the corpus holds
    /// for this company — 18 rows for a three-year Samsung ingest. Under a
    /// one-paragraph answer that reads as the answer, so a supplementary
    /// track collapses behind its own count and a load-bearing one does not.
    @ViewBuilder
    private func figuresSection(_ figures: [Figure], collapsible: Bool) -> some View {
        if !figures.isEmpty {
            let showsRows = !collapsible || figuresExpanded
            VStack(alignment: .leading, spacing: 0) {
                if collapsible {
                    Button {
                        withAnimation(.snappy(duration: 0.22)) { figuresExpanded.toggle() }
                    } label: {
                        figuresHeader(count: figures.count, chevron: true)
                    }
                    .accessibilityLabel("공시 원문 수치 \(figures.count)건")
                    .accessibilityHint(figuresExpanded ? "접기" : "펼치기")
                } else {
                    figuresHeader(count: figures.count, chevron: false)
                }

                if showsRows {
                    ForEach(Array(figures.enumerated()), id: \.offset) { index, figure in
                        if index > 0 {
                            Rectangle()
                                .fill(Theme.hairline)
                                .frame(height: 1)
                        }
                        FigureRow(figure: figure)
                    }
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, showsRows ? 8 : 0)
            .frame(maxWidth: .infinity, alignment: .leading)
            .overlay(
                RoundedRectangle(cornerRadius: 2)
                    .strokeBorder(Color.accentColor, lineWidth: 1)
            )
        }
    }

    /// "확정 수치 — 구조화 공시 데이터" named the pipeline, not the thing.
    private func figuresHeader(count: Int, chevron: Bool) -> some View {
        HStack(spacing: 8) {
            Text("공시 원문 수치")
                .font(Theme.sectionLabel)
                .tracking(1)
            Spacer(minLength: 8)
            Text("\(count)건")
                .font(.caption.monospacedDigit())
            if chevron {
                Image(systemName: figuresExpanded ? "chevron.up" : "chevron.down")
                    .font(.caption2.weight(.semibold))
            }
        }
        .foregroundStyle(Color.accentColor)
        .frame(minHeight: 44)
        .contentShape(Rectangle())
        .accessibilityAddTraits(.isHeader)
    }

    // MARK: Action

    private func ask(_ text: String) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, let companyId = UUID(uuidString: company.id) else { return }
        // Clearing here, not after the response, is what makes a second
        // question possible without selecting and deleting the first one.
        // A failure is still replayable: AnswerState keeps the failed intent.
        query = ""
        figuresExpanded = false
        await state.submit(query: trimmed, companyID: companyId)
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
    let onCitationTap: (Int) -> Void

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
                .font(.system(.body, design: .serif))
                .foregroundStyle(Theme.ink)
                .lineSpacing(7)
            if !sourceIndices.isEmpty {
                FlowLayout(spacing: 6) {
                    ForEach(sourceIndices, id: \.self) { index in
                        // A footnote mark that does not take you to the
                        // footnote is the one affordance this app cannot
                        // afford to fake — evidence is the product.
                        Button { onCitationTap(index) } label: {
                            CitationMarker(index: index)
                                .frame(minWidth: 44, minHeight: 44)
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("근거 \(index)번")
                        .accessibilityHint("해당 공시 근거를 엽니다")
                    }
                }
                // 44pt hit areas around 16pt marks would otherwise leave a
                // gutter under every paragraph.
                .padding(.vertical, -12)
            }
        }
        .padding(.bottom, 4)
    }
}

// MARK: - Evidence sheet

private struct EvidenceSelection: Identifiable {
    let index: Int
    let group: AnswerEvidenceIndex.Group

    var id: String { group.id }
}

private struct EvidenceSheet: View {
    let selection: EvidenceSelection

    @Environment(\.dismiss) private var dismiss
    @State private var openFiling: OpenableFiling?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.sectionSpacing) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("근거 \(selection.index.formatted(.number.precision(.integerLength(2))))")
                            .font(Theme.sectionLabel)
                            .monospacedDigit()
                            .foregroundStyle(Theme.inkMuted)
                        Text(evidenceTitle)
                            .font(Theme.display(.title3))
                            .foregroundStyle(Theme.ink)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityAddTraits(.isHeader)

                    ForEach(selection.group.citations) { citation in
                        EvidenceExcerpt(citation: citation)
                    }

                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        Text(selection.group.filingSource.title)
                            .font(.caption)
                            .foregroundStyle(Theme.inkMuted)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 8)
                        SourceBadge(source: selection.group.filingSource.source)
                    }

                    Button {
                        openFiling = OpenableFiling(selection.group.filingSource)
                    } label: {
                        HStack {
                            Text("공시 원문에서 보기")
                            Spacer(minLength: 8)
                            Image(systemName: "arrow.up.forward")
                        }
                    }
                    .buttonStyle(.ledgerFilled)
                }
                .padding(.horizontal, Theme.pageInset)
                .padding(.top, 16)
                .padding(.bottom, 32)
                .readableWidth()
            }
            .paperBackground()
            .navigationTitle("근거 확인")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { dismiss() } label: {
                        Image(systemName: "xmark")
                            .frame(width: 44, height: 44)
                    }
                    .accessibilityLabel("근거 닫기")
                }
            }
            .toolbarBackground(Theme.paper, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .filingSourceSheet($openFiling)
        }
        .tint(Color.accentColor)
    }

    private var evidenceTitle: String {
        selection.group.citations.compactMap(\.anchor.sectionTitle).first
            ?? selection.group.filingSource.title
    }
}

private struct EvidenceExcerpt: View {
    let citation: Citation

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("“\(citation.excerpt)”")
                .font(.system(.body, design: .serif))
                .foregroundStyle(Theme.ink)
                .lineSpacing(7)
                .fixedSize(horizontal: false, vertical: true)
            Text(anchorText)
                .font(.caption.monospaced())
                .foregroundStyle(Theme.inkMuted)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(Rectangle().strokeBorder(Theme.border, lineWidth: 1))
        .accessibilityElement(children: .combine)
    }

    private var anchorText: String {
        var parts: [String] = []
        if let sectionOrder = citation.anchor.sectionOrder {
            parts.append("원문 구역 \(sectionOrder)")
        }
        if let partIndex = citation.anchor.partIndex {
            parts.append("부분 \(partIndex)")
        }
        parts.append("문단 \(citation.anchor.chunkIndex)")
        return parts.joined(separator: " · ")
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
            return "\(title) · \(kind) · 회계연도 \(figure.fiscalYear) · \(quarter)분기"
        }
        return "\(title) · \(kind) · 회계연도 \(figure.fiscalYear)"
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

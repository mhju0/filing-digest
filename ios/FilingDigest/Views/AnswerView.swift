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
    /// Chosen on the digest's KO/EN toggle; the app's one language control.
    let language: Language

    @StateObject private var state: AnswerState
    @State private var query = ""
    @State private var selectedEvidence: EvidenceSelection?
    /// Only consulted where the figures track is supplementary; a withheld
    /// narrative expands it unconditionally.
    @State private var figuresExpanded = false

    init(client: APIClient, company: Company, language: Language) {
        self.client = client
        self.company = company
        self.language = language
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
                    Text(AnswerCopy.title(companyName: company.displayName(language), language: language))
                        .font(Theme.display(.headline))
                        .foregroundStyle(Theme.ink)
                        .lineLimit(1)
                }
            }
            .toolbarBackground(Theme.paper, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .sheet(item: $selectedEvidence) { selection in
                EvidenceSheet(selection: selection, language: language)
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
                AnswerCopy.inputPlaceholder(hasResponse: state.response != nil, language: language),
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
            .accessibilityIdentifier("answer-question")

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
            .accessibilityLabel(language == .ko ? "질문 전송" : "Send question")
            .accessibilityIdentifier("answer-submit")
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
                .accessibilityIdentifier("answer-result")
            }
        } else if state.isLoading {
            pendingAnswer
        } else if let blockingError = state.blockingError {
            ContentUnavailableView {
                Label(
                    language == .ko ? "답변을 가져오지 못했습니다" : "Couldn't get an answer",
                    systemImage: "exclamationmark.triangle"
                )
            } description: {
                Text(blockingError)
            } actions: {
                Button(language == .ko ? "다시 시도" : "Try again") {
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
            ProgressView(language == .ko ? "답변을 새로 생성하는 중…" : "Generating a new answer…")
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
    /// cross-lingual embedding space. The answer follows the question's
    /// language, so English mode suggests English questions.

    private var starter: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 8) {
                    Image(systemName: "text.quote")
                        .font(.title)
                        .foregroundStyle(Theme.inkMuted)
                    Text(AnswerCopy.starterTitle(language))
                        .font(Theme.display(.title3))
                        .foregroundStyle(Theme.ink)
                    Text(AnswerCopy.starterBody(language))
                        .font(.subheadline)
                        .foregroundStyle(Theme.inkMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.top, 28)
                .accessibilityElement(children: .combine)

                SectionHeader(title: AnswerCopy.suggestionsTitle(language))

                VStack(spacing: 9) {
                    ForEach(AnswerCopy.suggestedQuestions(language), id: \.self) { question in
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
                        Text(language == .ko ? "답변 생성 중" : "Generating answer")
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
                Text(language == .ko ? "공시 원문에서 근거를 찾는 중입니다." : "Searching the filing for evidence.")
                    .font(.caption)
                    .foregroundStyle(Theme.inkMuted)
            }
            .padding(.horizontal, Theme.pageInset)
            .padding(.top, 12)
            .readableWidth()
            .accessibilityElement(children: .combine)
            .accessibilityLabel(
                language == .ko
                    ? "답변 생성 중. 질문: \(state.askedQuery)"
                    : "Generating answer. Question: \(state.askedQuery)"
            )
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
        .accessibilityLabel(
            language == .ko ? "질문: \(state.askedQuery)" : "Question: \(state.askedQuery)"
        )
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
            title: AnswerCopy.claims(answer.answerSegments.count, language: language),
            detail: AnswerCopy.evidenceVerified(language)
        )
        ForEach(Array(answer.answerSegments.enumerated()), id: \.offset) { _, segment in
            SegmentView(
                segment: segment,
                evidenceIndex: evidenceIndex,
                language: language,
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
                Text(language == .ko ? "숫자는 AI가 쓰지 않습니다" : "AI does not write the numbers")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                Text(reason?.userMessage(language) ?? AnswerCopy.blockedFallback(language))
                    .font(.caption)
                    .foregroundStyle(Theme.inkMuted)
            }
        }
        .ledgerCard(borderColor: Color.accentColor)
        .accessibilityElement(children: .combine)
    }

    private var noResultsNotice: some View {
        ContentUnavailableView(
            AnswerCopy.noResultsTitle(language),
            systemImage: "doc.text.magnifyingglass",
            description: Text(AnswerCopy.noResultsBody(language))
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
                    .accessibilityLabel(
                        "\(AnswerCopy.figuresTitle(language)) \(AnswerCopy.figureCount(figures.count, language: language))"
                    )
                    .accessibilityHint(
                        language == .ko
                            ? (figuresExpanded ? "접기" : "펼치기")
                            : (figuresExpanded ? "Collapse" : "Expand")
                    )
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
                        FigureRow(figure: figure, language: language)
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
            Text(AnswerCopy.figuresTitle(language))
                .font(Theme.sectionLabel)
                .tracking(1)
            Spacer(minLength: 8)
            Text(AnswerCopy.figureCount(count, language: language))
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

enum AnswerCopy {
    static func title(companyName: String, language: Language) -> String {
        language == .ko ? "\(companyName) / 답변" : "\(companyName) / Answer"
    }

    static func inputPlaceholder(hasResponse: Bool, language: Language) -> String {
        switch (language, hasResponse) {
        case (.ko, false): "이 회사에 대해 질문하세요"
        case (.ko, true): "다른 질문하기"
        case (.en, false): "Ask about this company"
        case (.en, true): "Ask another question"
        }
    }

    static func starterTitle(_ language: Language) -> String {
        language == .ko ? "공시에 있는 것만 답합니다" : "Answers come only from the filings"
    }

    static func starterBody(_ language: Language) -> String {
        language == .ko
            ? "답변의 모든 문장에 원문 인용이 붙습니다. 근거를 찾지 못하면 답하지 않습니다."
            : "Every sentence in an answer cites the original filing. Without evidence, there is no answer."
    }

    static func suggestionsTitle(_ language: Language) -> String {
        language == .ko ? "이렇게 물어보세요" : "Try asking"
    }

    static func suggestedQuestions(_ language: Language) -> [String] {
        language == .ko
            ? [
                "주요 사업 부문은 무엇인가요",
                "주요 리스크 요인은 무엇인가요",
                "연구개발 조직은 어떻게 구성되어 있나요",
            ]
            : [
                "What are the main business segments?",
                "What are the main risk factors?",
                "How is research and development organized?",
            ]
    }

    static func claims(_ count: Int, language: Language) -> String {
        language == .ko
            ? "답변 / 주장 \(count)개"
            : "Answer / \(count) \(count == 1 ? "claim" : "claims")"
    }

    static func evidenceVerified(_ language: Language) -> String {
        language == .ko ? "근거 확인됨" : "Evidence verified"
    }

    static func blockedFallback(_ language: Language) -> String {
        language == .ko
            ? "이 답변의 서술은 보류하고, 공시 원문 수치만 보여줍니다."
            : "The written answer is withheld; only figures from the filing are shown."
    }

    static func noResultsTitle(_ language: Language) -> String {
        language == .ko ? "공시에서 근거를 찾지 못했습니다" : "No evidence found in the filings"
    }

    static func noResultsBody(_ language: Language) -> String {
        language == .ko
            ? "인용할 문단이 없어 답하지 않았습니다. 이 회사의 사업, 리스크, 조직에 대해 물어보세요."
            : "There was no passage to cite, so there is no answer. Ask about this company's business, risks or organization."
    }

    static func figuresTitle(_ language: Language) -> String {
        language == .ko ? "공시 원문 수치" : "Figures from the filing"
    }

    static func figureCount(_ count: Int, language: Language) -> String {
        language == .ko ? "\(count)건" : "\(count)"
    }

    static func evidenceNumber(_ index: Int, language: Language) -> String {
        let number = index.formatted(.number.precision(.integerLength(2)))
        return language == .ko ? "근거 \(number)" : "Evidence \(number)"
    }

    static func evidenceTitle(_ language: Language) -> String {
        language == .ko ? "근거 확인" : "Evidence"
    }

    static func openFiling(_ language: Language) -> String {
        language == .ko ? "공시 원문에서 보기" : "View in the original filing"
    }

    static func anchor(sectionOrder: Int?, partIndex: Int?, chunkIndex: Int, language: Language) -> String {
        var parts: [String] = []
        if let sectionOrder {
            parts.append(language == .ko ? "원문 구역 \(sectionOrder)" : "Section \(sectionOrder)")
        }
        if let partIndex {
            parts.append(language == .ko ? "부분 \(partIndex)" : "Part \(partIndex)")
        }
        parts.append(language == .ko ? "문단 \(chunkIndex)" : "Paragraph \(chunkIndex)")
        return parts.joined(separator: " · ")
    }

    static func figurePeriod(
        title: String,
        isInstant: Bool,
        fiscalYear: Int,
        quarter: Int?,
        language: Language
    ) -> String {
        let kind = language == .ko
            ? (isInstant ? "기준일" : "기간")
            : (isInstant ? "As of" : "Period")
        let year = language == .ko ? "\(fiscalYear) 회계연도" : "FY\(fiscalYear)"
        guard let quarter else { return "\(title) · \(kind) · \(year)" }
        return "\(title) · \(kind) · \(year) · " + (language == .ko ? "\(quarter)분기" : "Q\(quarter)")
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
    let language: Language
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
                        .accessibilityLabel(language == .ko ? "근거 \(index)번" : "Evidence \(index)")
                        .accessibilityHint(
                            language == .ko ? "해당 공시 근거를 엽니다" : "Opens the evidence from this filing"
                        )
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
    let language: Language

    @Environment(\.dismiss) private var dismiss
    @State private var openFiling: OpenableFiling?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.sectionSpacing) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(AnswerCopy.evidenceNumber(selection.index, language: language))
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
                        EvidenceExcerpt(citation: citation, language: language)
                    }

                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        Text(selection.group.filingSource.title)
                            .font(.caption)
                            .foregroundStyle(Theme.inkMuted)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 8)
                        SourceBadge(source: selection.group.filingSource.source, language: language)
                    }

                    Button {
                        openFiling = OpenableFiling(selection.group.filingSource)
                    } label: {
                        HStack {
                            Text(AnswerCopy.openFiling(language))
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
            .navigationTitle(AnswerCopy.evidenceTitle(language))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { dismiss() } label: {
                        Image(systemName: "xmark")
                            .frame(width: 44, height: 44)
                    }
                    .accessibilityLabel(language == .ko ? "근거 닫기" : "Close evidence")
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
    let language: Language

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
        AnswerCopy.anchor(
            sectionOrder: citation.anchor.sectionOrder,
            partIndex: citation.anchor.partIndex,
            chunkIndex: citation.anchor.chunkIndex,
            language: language
        )
    }
}

// MARK: - Figure row

private struct FigureRow: View {
    let figure: Figure
    let language: Language

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(FigureDisplay.metricName(figure.metric, language: language))
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
                if let reading = koreanReading {
                    Text(reading)
                        .font(.caption2)
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
        AnswerCopy.figurePeriod(
            title: FigureDisplay.periodTitle(figure.period, language: language),
            isInstant: figure.periodKind == .instant,
            fiscalYear: figure.fiscalYear,
            quarter: figure.fiscalQuarter,
            language: language
        )
    }

    /// Abbreviated display value (조/억) — readable at a glance.
    private var abbreviatedText: String {
        FigureDisplay.formattedValue(
            NSDecimalNumber(decimal: figure.value).doubleValue,
            unit: figure.unit,
            language: language
        )
    }

    /// The exact structured-API value (lossless Decimal), shown under the
    /// abbreviation whenever abbreviating actually dropped digits — the
    /// authoritative track stays fully inspectable.
    private var exactText: String? {
        let exact = FigureDisplay.typographicSign(figure.value.formatted(
            .number.precision(.fractionLength(0...4)).grouping(.automatic)
        ))
        // Same join as FigureDisplay.formattedValue (no space in KO), so an
        // unabbreviated value compares equal and the duplicate line hides.
        let unitText = figure.unit.isEmpty
            ? ""
            : FigureDisplay.unitName(figure.unit, language: language)
        let separator = figure.unit.isEmpty || language == .ko ? "" : " "
        let full = "\(exact)\(separator)\(unitText)"
        return full == abbreviatedText ? nil : full
    }

    /// The exact won amount restated in 조/억 units for Korean readers,
    /// shown only when the abbreviation dropped digits.
    private var koreanReading: String? {
        guard language == .ko, figure.unit == "KRW", exactText != nil else { return nil }
        return FigureDisplay.koreanUnitReading(figure.value)
    }
}

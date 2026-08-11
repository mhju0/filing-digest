//
//  DigestView.swift
//  FilingDigest
//
//  Company digest, Ledger system (docs/design/DESIGN.md): editorial company
//  header, filing-context line, 2-column hairline metric cards with
//  abbreviated values, summary and sources under small-caps section rules.
//  Metric values are structured-API numbers only; every card links to a
//  openable Filing Source via filingSourceId. value == nil renders as a dash.
//
//  The digest payload always contains both label_ko/label_en, so metric labels
//  switch locally without refetching. summary_ko/summary_en may be nil (no
//  narrative generated yet); the summary section is hidden in that case.
//

import SwiftUI

struct DigestView: View {
    let client: APIClient
    let company: Company

    @StateObject private var state: DigestState
    @State private var language: Language = .ko
    @State private var openFiling: OpenableFiling?
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    init(client: APIClient, company: Company) {
        self.client = client
        self.company = company
        _state = StateObject(wrappedValue: DigestState(fetchDigest: {
            try await client.fetchDigest(companyID: $0)
        }))
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.sectionSpacing) {
                companyHeader

                if let digest = state.digest {
                    requestStatus
                    digestContent(digest)
                } else if state.isLoading {
                    ProgressView("불러오는 중…")
                        .frame(maxWidth: .infinity)
                        .padding(.top, 40)
                } else if let blockingError = state.blockingError {
                    ContentUnavailableView {
                        Label("요약을 불러오지 못했습니다", systemImage: "exclamationmark.triangle")
                    } description: {
                        Text(blockingError)
                    } actions: {
                        Button("다시 시도") {
                            Task { await state.retry() }
                        }
                        .buttonStyle(.ledger)
                    }
                    .frame(maxWidth: .infinity, minHeight: 380)
                }
            }
            .padding(.horizontal, Theme.pageInset)
            .padding(.top, 4)
            .padding(.bottom, 24)
            .readableWidth()
        }
        .paperBackground()
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                // The wordmark belongs to the root screen. Once you are inside
                // a company, the useful thing to hold at the top is which
                // company — the serif header scrolls away, this does not.
                Text(company.koreanDisplayName)
                    .font(Theme.display(.headline))
                    .foregroundStyle(Theme.ink)
                    .lineLimit(1)
            }
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink {
                    AnswerView(client: client, company: company)
                } label: {
                    Image(systemName: "questionmark.bubble")
                }
                .accessibilityLabel("이 회사에 질문하기")
            }
        }
        .toolbarBackground(Theme.paper, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .filingSourceSheet($openFiling)
        .task(id: company.id) { await state.load(companyID: company.id) }
        .refreshable { await state.refresh() }
        .onDisappear { state.cancel() }
    }

    // MARK: Header

    private var companyHeader: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .center) {
                Text(
                    [company.koreanSecurityIdentifier, company.market?.koreanDisplayName]
                        .compactMap(\.self)
                        .joined(separator: " / ")
                )
                .font(.caption.monospaced())
                .foregroundStyle(Theme.inkMuted)
                Spacer(minLength: 8)
                SourceBadge(source: company.source)
            }
            Text(company.koreanDisplayName)
                .font(Theme.display(.title))
                .foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.top, 8)
        .accessibilityElement(children: .combine)
    }

    // MARK: Digest content

    @ViewBuilder
    private var requestStatus: some View {
        if state.isRefreshing {
            ProgressView("새로 고치는 중…")
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

    @ViewBuilder
    private func digestContent(_ digest: CompanyDigest) -> some View {
        periodRow(digest)

        if digest.metrics.isEmpty && digest.summary(for: language) == nil
            && digest.filingSources.isEmpty {
            ContentUnavailableView(
                "아직 요약할 공시가 없습니다",
                systemImage: "doc.text",
                description: Text("이 회사의 공시가 수집되면 핵심 수치와 요약이 표시됩니다.")
            )
            .padding(.top, 20)
        }

        let orderedMetrics = Self.orderedMetrics(digest.metrics)
        if let heroMetric = orderedMetrics.first {
            let sourcesByID = Dictionary(
                digest.filingSources.map { ($0.id, $0) },
                uniquingKeysWith: { first, _ in first }
            )
            let heroFiling = sourcesByID[heroMetric.filingSourceId].flatMap(OpenableFiling.init)

            if let heroFiling {
                Button { openFiling = heroFiling } label: {
                    HeroMetricView(metric: heroMetric, language: language, isOpenable: true)
                }
                .buttonStyle(.ledgerRow)
                .accessibilityHint("이 수치가 실린 공시를 엽니다")
            } else {
                HeroMetricView(metric: heroMetric, language: language, isOpenable: false)
            }

            supportingMetrics(
                Array(orderedMetrics.dropFirst()),
                sourcesByID: sourcesByID
            )
        }

        NavigationLink {
            AnswerView(client: client, company: company)
        } label: {
            HStack(spacing: 10) {
                Text("이 회사에 질문하기")
                    .font(.subheadline.weight(.semibold))
                Spacer(minLength: 8)
                Image(systemName: "arrow.right").font(.caption)
            }
            .foregroundStyle(Color.accentColor)
            .padding(.horizontal, 14)
            .frame(minHeight: 48)
            .contentShape(Rectangle())
            .overlay(Rectangle().strokeBorder(Color.accentColor, lineWidth: 1))
        }
        .buttonStyle(.ledgerRow)

        if let summary = digest.summary(for: language) {
            VStack(alignment: .leading, spacing: 12) {
                SectionHeader(title: "핵심 요약", detail: "01")
                Text(summary)
                    .font(.system(.body, design: .serif))
                    .foregroundStyle(Theme.ink)
                    .lineSpacing(7)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }

        if !digest.filingSources.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                SectionHeader(title: "근거 공시", detail: "\(digest.filingSources.count)")
                ForEach(digest.filingSources) { filingSource in
                    FilingSourceRow(filingSource: filingSource) { openFiling = $0 }
                    Rectangle()
                        .fill(Theme.hairline)
                        .frame(height: 1)
                }
            }
        }
    }

    @ViewBuilder
    private func periodRow(_ digest: CompanyDigest) -> some View {
        Group {
            if dynamicTypeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 12) {
                    Text(filingContext(digest))
                    languagePicker
                }
            } else {
                HStack(alignment: .center, spacing: 12) {
                    Text(filingContext(digest))
                    Spacer(minLength: 8)
                    languagePicker
                }
            }
        }
        .font(.subheadline)
        .foregroundStyle(Theme.inkMuted)
        .padding(.vertical, 8)
        .overlay(alignment: .top) {
            Rectangle().fill(Theme.hairline).frame(height: 1)
        }
        .overlay(alignment: .bottom) {
            Rectangle().fill(Theme.hairline).frame(height: 1)
        }
    }

    private var languagePicker: some View {
        Picker("표시 언어", selection: $language) {
            Text("한국어").tag(Language.ko)
            Text("영어").tag(Language.en)
        }
        .pickerStyle(.segmented)
        .frame(maxWidth: 156)
    }

    @ViewBuilder
    private func supportingMetrics(
        _ metrics: [MetricCard],
        sourcesByID: [String: FilingSource]
    ) -> some View {
        if !metrics.isEmpty {
            if dynamicTypeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(metrics) { metric in
                        supportingMetric(metric, source: sourcesByID[metric.filingSourceId])
                        Rectangle().fill(Theme.hairline).frame(height: 1)
                    }
                }
            } else {
                let columnCount = min(3, metrics.count)
                LazyVGrid(
                    columns: Array(
                        repeating: GridItem(.flexible(), spacing: 0),
                        count: columnCount
                    ),
                    spacing: 18
                ) {
                    ForEach(Array(metrics.enumerated()), id: \.element.id) { index, metric in
                        supportingMetric(metric, source: sourcesByID[metric.filingSourceId])
                            .padding(.horizontal, 10)
                            .overlay(alignment: .trailing) {
                                if (index + 1).isMultiple(of: columnCount) == false,
                                   index < metrics.count - 1 {
                                    Rectangle().fill(Theme.hairline).frame(width: 1)
                                }
                            }
                    }
                }
                .padding(.horizontal, -10)
            }
        }
    }

    @ViewBuilder
    private func supportingMetric(_ metric: MetricCard, source: FilingSource?) -> some View {
        if let source, let openable = OpenableFiling(source) {
            Button { openFiling = openable } label: {
                SupportingMetricView(metric: metric, language: language, isOpenable: true)
            }
            .buttonStyle(.ledgerRow)
            .accessibilityHint("이 수치가 실린 공시를 엽니다")
        } else {
            SupportingMetricView(metric: metric, language: language, isOpenable: false)
        }
    }

    static func orderedMetrics(_ metrics: [MetricCard]) -> [MetricCard] {
        metrics.enumerated().sorted { lhs, rhs in
            let lhsRank = metricRank(lhs.element.key)
            let rhsRank = metricRank(rhs.element.key)
            return lhsRank == rhsRank ? lhs.offset < rhs.offset : lhsRank < rhsRank
        }.map(\.element)
    }

    private static func metricRank(_ metric: FinancialMetric) -> Int {
        if metric == .revenue { return 0 }
        if metric == .operatingIncome { return 1 }
        if metric == .netIncome { return 2 }
        if metric == .netIncomeAttributable { return 3 }
        if metric == .eps { return 4 }
        if metric == .epsDiluted { return 5 }
        if metric == .operatingMargin { return 6 }
        return 7
    }

    /// The source row below already carries the filing date. Keeping this line
    /// to the humanized reporting period prevents an awkward wrap beside the
    /// language control on compact phones.
    private func filingContext(_ digest: CompanyDigest) -> String {
        FigureDisplay.periodTitle(digest.period, language: language)
    }
}

// MARK: - Metrics

private struct HeroMetricView: View {
    let metric: MetricCard
    let language: Language
    let isOpenable: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(metric.label(for: language))
                    .font(.subheadline)
                    .foregroundStyle(Theme.inkMuted)
                Spacer(minLength: 8)
                if isOpenable {
                    Image(systemName: "arrow.up.forward")
                        .font(.caption)
                        .foregroundStyle(Theme.inkMuted)
                        .accessibilityHidden(true)
                }
            }
            HStack(alignment: .firstTextBaseline, spacing: 7) {
                Text(valueParts.number)
                    .font(.system(.largeTitle, design: .default, weight: .semibold))
                    .monospacedDigit()
                    .foregroundStyle(Theme.ink)
                    .lineLimit(1)
                    .minimumScaleFactor(0.65)
                if !valueParts.unit.isEmpty {
                    Text(valueParts.unit)
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(Theme.ink)
                        .fixedSize()
                }
            }
            if let delta = metric.yoyDeltaPct {
                Text(deltaText(delta))
                    .font(.caption)
                    .monospacedDigit()
                    .foregroundStyle(delta >= 0 ? Color.accentColor : Theme.negative)
            }
            VStack(spacing: 3) {
                Rectangle().fill(Theme.hairline).frame(height: 1)
                Rectangle().fill(Theme.hairline).frame(height: 1)
            }
        }
        .padding(.top, 4)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    private var valueParts: FormattedFigureValue {
        guard let value = metric.value else {
            return FormattedFigureValue(number: "—", unit: "", separator: "")
        }
        return FigureDisplay.formattedValueParts(value, unit: metric.unit, language: language)
    }

    private func deltaText(_ delta: Double) -> String {
        let formatted = delta.formatted(.number.precision(.fractionLength(0...1)))
        return delta >= 0 ? "↑ 전년 대비 \(formatted)%" : "↓ 전년 대비 \(formatted)%"
    }
}

private struct SupportingMetricView: View {
    let metric: MetricCard
    let language: Language
    let isOpenable: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(metric.label(for: language))
                    .font(.caption)
                    .foregroundStyle(Theme.inkMuted)
                    .lineLimit(2)
                if isOpenable {
                    Spacer(minLength: 0)
                    Image(systemName: "arrow.up.forward")
                        .font(.caption2)
                        .foregroundStyle(Theme.inkMuted)
                        .accessibilityHidden(true)
                }
            }
            Text(valueText)
                .font(.subheadline.weight(.semibold))
                .monospacedDigit()
                .foregroundStyle(Theme.ink)
                .lineLimit(1)
                .minimumScaleFactor(0.65)
            if let delta = metric.yoyDeltaPct {
                Text(delta >= 0 ? "+\(formattedDelta(delta))%" : "\(formattedDelta(delta))%")
                    .font(.caption2)
                    .monospacedDigit()
                    .foregroundStyle(delta >= 0 ? Color.accentColor : Theme.negative)
            } else {
                Text("변동률 없음")
                    .font(.caption2)
                    .foregroundStyle(Theme.inkMuted)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 68, alignment: .leading)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    private var valueText: String {
        guard let value = metric.value else { return "—" }
        return FigureDisplay.formattedValue(value, unit: metric.unit, language: language)
    }

    private func formattedDelta(_ delta: Double) -> String {
        delta.formatted(.number.precision(.fractionLength(0...1)))
    }
}

// MARK: - Filing Source row

/// Source row shared by DigestView and AnswerView: hairline-separated,
/// mono metadata line. The whole row opens the filing, not just the title —
/// a two-line title is a poor tap target for the app's central action.
struct FilingSourceRow: View {
    let filingSource: FilingSource
    var onOpen: ((OpenableFiling) -> Void)?

    @ViewBuilder
    var body: some View {
        if let openable = OpenableFiling(filingSource), let onOpen {
            Button { onOpen(openable) } label: { row(isOpenable: true) }
                .buttonStyle(.ledgerRow)
                .accessibilityHint("공시 원문을 앱에서 엽니다")
        } else {
            row(isOpenable: false)
        }
    }

    private func row(isOpenable: Bool) -> some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(filingSource.title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(isOpenable ? Color.accentColor : Theme.ink)
                    .multilineTextAlignment(.leading)
                HStack(spacing: 5) {
                    if let filedAt = filingSource.filedAt {
                        Text(filedAt).font(.caption.monospaced())
                    }
                    if isOpenable {
                        Text(filingSource.filedAt == nil ? "앱에서 열기" : "· 앱에서 열기")
                            .font(.caption)
                    }
                }
                .foregroundStyle(Theme.inkMuted)
            }
            Spacer(minLength: 8)
            SourceBadge(source: filingSource.source)
        }
        .padding(.vertical, 12)
        .frame(minHeight: 44)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}

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

    private let columns = [
        GridItem(.flexible(), spacing: 12),
        GridItem(.flexible(), spacing: 12),
    ]

    init(client: APIClient, company: Company) {
        self.client = client
        self.company = company
        _state = StateObject(wrappedValue: DigestState(fetchDigest: {
            try await client.fetchDigest(companyID: $0)
        }))
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
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
            .padding(.horizontal, 20)
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
                Text(company.name)
                    .font(Theme.display(.headline))
                    .foregroundStyle(Theme.ink)
                    .lineLimit(1)
            }
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink {
                    AnswerView(client: client, company: company)
                } label: {
                    Label("질문", systemImage: "questionmark.bubble")
                }
            }
        }
        .filingSourceSheet($openFiling)
        .task(id: company.id) { await state.load(companyID: company.id) }
        .refreshable { await state.refresh() }
        .onDisappear { state.cancel() }
    }

    // MARK: Header

    private var companyHeader: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(company.name)
                    .font(Theme.display(.title))
                    .foregroundStyle(Theme.ink)
                Spacer()
                SourceBadge(source: company.source)
            }
            Text(
                [company.nameEn != company.name ? company.nameEn : nil,
                 company.ticker,
                 company.market?.rawValue]
                    .compactMap(\.self)
                    .joined(separator: " · ")
            )
            .font(.caption)
            .foregroundStyle(Theme.inkMuted)
            .lineLimit(1)
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
        HStack(alignment: .firstTextBaseline) {
            Text(filingContext(digest))
                .font(.subheadline)
                .foregroundStyle(Theme.inkMuted)
            Spacer()
            Picker("언어", selection: $language) {
                Text("한국어").tag(Language.ko)
                Text("EN").tag(Language.en)
            }
            .pickerStyle(.segmented)
            .frame(width: 140)
        }

        if digest.metrics.isEmpty && digest.summary(for: language) == nil
            && digest.filingSources.isEmpty {
            ContentUnavailableView(
                "아직 요약할 공시가 없습니다",
                systemImage: "doc.text",
                description: Text("이 회사의 공시가 수집되면 핵심 수치와 요약이 표시됩니다.")
            )
            .padding(.top, 20)
        }

        if !digest.metrics.isEmpty {
            // Every card already carries the Filing Source its value came
            // from; the card just never did anything with it. Deduplicated
            // by contract, but never trap the UI on unexpected server data.
            let sourcesByID = Dictionary(
                digest.filingSources.map { ($0.id, $0) },
                uniquingKeysWith: { first, _ in first }
            )
            LazyVGrid(columns: columns, spacing: 12) {
                ForEach(digest.metrics) { metric in
                    if let source = sourcesByID[metric.filingSourceId],
                       let openable = OpenableFiling(source) {
                        Button { openFiling = openable } label: {
                            MetricCardView(metric: metric, language: language, isOpenable: true)
                        }
                        .buttonStyle(.plain)
                        .accessibilityHint("이 수치가 실린 공시를 엽니다")
                    } else {
                        MetricCardView(metric: metric, language: language, isOpenable: false)
                    }
                }
            }
        }

        if let summary = digest.summary(for: language) {
            SectionHeader(title: language == .ko ? "요약" : "SUMMARY")
            Text(summary)
                .font(.body)
                .foregroundStyle(Theme.ink)
                .lineSpacing(6)
        }

        if !digest.filingSources.isEmpty {
            SectionHeader(title: language == .ko ? "출처" : "SOURCES")
            VStack(alignment: .leading, spacing: 0) {
                ForEach(digest.filingSources) { filingSource in
                    FilingSourceRow(filingSource: filingSource) { openFiling = $0 }
                    Rectangle()
                        .fill(Theme.hairline)
                        .frame(height: 1)
                }
            }
        }
    }

    /// "사업보고서 2023 · 공시 2024-03-12" — humanized period plus the filing
    /// date of the first Filing Source (all metrics of a v0.3 digest come from a
    /// single filing).
    private func filingContext(_ digest: CompanyDigest) -> String {
        let title = FigureDisplay.periodTitle(digest.period, language: language)
        guard let filedAt = digest.filingSources.first?.filedAt else { return title }
        return language == .ko ? "\(title) · 공시 \(filedAt)" : "\(title) · filed \(filedAt)"
    }
}

// MARK: - Metric card

private struct MetricCardView: View {
    let metric: MetricCard
    let language: Language
    let isOpenable: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 4) {
                Text(metric.label(for: language))
                    .font(.caption)
                    .foregroundStyle(Theme.inkMuted)
                    .lineLimit(1)
                if isOpenable {
                    Spacer(minLength: 0)
                    // A bordered box that does nothing reads as broken; this
                    // is the smallest mark that says the box goes somewhere.
                    Image(systemName: "arrow.up.forward")
                        .font(.caption2)
                        .foregroundStyle(Theme.inkMuted)
                        .accessibilityHidden(true)
                }
            }
            Text(valueText)
                .font(.title3.weight(.semibold))
                .monospacedDigit()
                .foregroundStyle(Theme.ink)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            if let delta = metric.yoyDeltaPct {
                Text(deltaText(delta))
                    .font(.caption.weight(.semibold))
                    .monospacedDigit()
                    .foregroundStyle(delta >= 0 ? Color.accentColor : Theme.negative)
            }
        }
        .ledgerCard()
        .accessibilityElement(children: .combine)
    }

    /// nil value -> dash, per contract UI rule. Large KRW/USD values are
    /// abbreviated for readability (display-only; the wire value is exact).
    private var valueText: String {
        guard let value = metric.value else { return "—" }
        return FigureDisplay.formattedValue(value, unit: metric.unit, language: language)
    }

    private func deltaText(_ delta: Double) -> String {
        let formatted = delta.formatted(.number.precision(.fractionLength(0...1)))
        return delta >= 0 ? "YoY +\(formatted)%" : "YoY \(formatted)%"
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
                .buttonStyle(.plain)
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

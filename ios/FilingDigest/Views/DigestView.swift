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
                        Label("오류", systemImage: "exclamationmark.triangle")
                    } description: {
                        Text(blockingError)
                    } actions: {
                        Button("다시 시도") {
                            Task { await state.retry() }
                        }
                        .buttonStyle(.bordered)
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 4)
            .padding(.bottom, 24)
        }
        .paperBackground()
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text("FILING DIGEST")
                    .font(Theme.sectionLabel)
                    .tracking(2)
                    .foregroundStyle(Theme.inkMuted)
            }
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink {
                    AnswerView(client: client, company: company)
                } label: {
                    Label("질문", systemImage: "questionmark.bubble")
                }
            }
        }
        .task(id: company.id) { await state.load(companyID: company.id) }
        .refreshable { await state.refresh() }
        .onDisappear { state.cancel() }
    }

    // MARK: Header

    private var companyHeader: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(company.name)
                    .font(Theme.display(30))
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
            LazyVGrid(columns: columns, spacing: 12) {
                ForEach(digest.metrics) { metric in
                    MetricCardView(metric: metric, language: language)
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
                    FilingSourceRow(filingSource: filingSource)
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

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(metric.label(for: language))
                .font(.caption)
                .foregroundStyle(Theme.inkMuted)
                .lineLimit(1)
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
                    .foregroundStyle(delta >= 0 ? Color.accentColor : Color(red: 0.72, green: 0.2, blue: 0.15))
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
/// title links to the original filing, mono metadata line.
struct FilingSourceRow: View {
    let filingSource: FilingSource

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                if let url = filingSource.openableURL {
                    Link(filingSource.title, destination: url)
                        .font(.subheadline.weight(.semibold))
                } else {
                    Text(filingSource.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Theme.ink)
                }
                if let filedAt = filingSource.filedAt {
                    Text(filedAt)
                        .font(.caption.monospaced())
                        .foregroundStyle(Theme.inkMuted)
                }
            }
            Spacer()
            SourceBadge(source: filingSource.source)
        }
        .padding(.vertical, 12)
    }
}

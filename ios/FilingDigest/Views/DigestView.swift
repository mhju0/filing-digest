//
//  DigestView.swift
//  FilingDigest
//
//  Company digest, Ledger system (docs/design/DESIGN.md): editorial company
//  header, filing-context line, 2-column hairline metric cards with
//  abbreviated values, summary and sources under small-caps section rules.
//  Metric values are structured-API numbers only; every card links to a
//  citation via citationId. value == nil renders as a dash.
//
//  The digest payload always contains both label_ko/label_en, so metric labels
//  switch locally without refetching. summary_ko/summary_en may be nil (no
//  narrative generated yet); the summary section is hidden in that case.
//

import SwiftUI

struct DigestView: View {
    let client: APIClient
    let company: Company

    @State private var digest: CompanyDigest?
    @State private var language: Language = .ko
    @State private var isLoading = false
    @State private var errorMessage: String?

    private let columns = [
        GridItem(.flexible(), spacing: 12),
        GridItem(.flexible(), spacing: 12),
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                companyHeader

                if let digest {
                    digestContent(digest)
                } else if isLoading {
                    ProgressView("불러오는 중…")
                        .frame(maxWidth: .infinity)
                        .padding(.top, 40)
                } else if let errorMessage {
                    ContentUnavailableView {
                        Label("오류", systemImage: "exclamationmark.triangle")
                    } description: {
                        Text(errorMessage)
                    } actions: {
                        Button("다시 시도") {
                            Task { await load() }
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
        .task {
            await load()
        }
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
            && digest.citations.isEmpty {
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

        if !digest.citations.isEmpty {
            SectionHeader(title: language == .ko ? "출처" : "SOURCES")
            VStack(alignment: .leading, spacing: 0) {
                ForEach(digest.citations) { citation in
                    CitationRow(citation: citation)
                    Rectangle()
                        .fill(Theme.hairline)
                        .frame(height: 1)
                }
            }
        }
    }

    /// "사업보고서 2023 · 공시 2024-03-12" — humanized period plus the filing
    /// date of the first citation (all metrics of a v0.2 digest come from a
    /// single filing).
    private func filingContext(_ digest: CompanyDigest) -> String {
        let title = FigureDisplay.periodTitle(digest.period, language: language)
        guard let filedAt = digest.citations.first?.filedAt else { return title }
        return language == .ko ? "\(title) · 공시 \(filedAt)" : "\(title) · filed \(filedAt)"
    }

    private func load() async {
        guard digest == nil, !isLoading else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            digest = try await client.fetchDigest(companyID: company.id, language: language)
        } catch {
            errorMessage = error.localizedDescription
        }
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

// MARK: - Citation row

/// Source row shared by DigestView and AnswerView: hairline-separated,
/// title links to the original filing, mono metadata line.
struct CitationRow: View {
    let citation: Citation

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                if let url = URL(string: citation.url), !citation.url.isEmpty {
                    Link(citation.title, destination: url)
                        .font(.subheadline.weight(.semibold))
                } else {
                    Text(citation.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Theme.ink)
                }
                if let filedAt = citation.filedAt {
                    Text(filedAt)
                        .font(.caption.monospaced())
                        .foregroundStyle(Theme.inkMuted)
                }
                if let excerpt = citation.excerpt {
                    Text(excerpt)
                        .font(.caption)
                        .foregroundStyle(Theme.inkMuted)
                        .lineLimit(2)
                }
            }
            Spacer()
            SourceBadge(source: citation.source)
        }
        .padding(.vertical, 12)
    }
}

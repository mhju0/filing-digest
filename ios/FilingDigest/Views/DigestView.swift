//
//  DigestView.swift
//  FilingDigest
//
//  Company digest: metric card grid (LazyVGrid) + KO/EN segmented toggle +
//  summary + citations. Metric values are structured-API numbers only; every
//  card links to a citation via citationId. value == nil renders as a dash.
//
//  The digest payload always contains both label_ko/label_en and
//  summary_ko/summary_en, so the language toggle switches locally without
//  refetching.
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
        GridItem(.adaptive(minimum: 150), spacing: 12)
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Picker("언어", selection: $language) {
                    Text("한국어").tag(Language.ko)
                    Text("English").tag(Language.en)
                }
                .pickerStyle(.segmented)

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
                        .buttonStyle(.borderedProminent)
                    }
                }
            }
            .padding()
        }
        .navigationTitle(company.name)
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await load()
        }
    }

    @ViewBuilder
    private func digestContent(_ digest: CompanyDigest) -> some View {
        HStack {
            Text(digest.period)
                .font(.title3.bold())
            Spacer()
            SourceBadge(source: company.source)
        }

        LazyVGrid(columns: columns, spacing: 12) {
            ForEach(digest.metrics) { metric in
                MetricCardView(metric: metric, language: language)
            }
        }

        Text(language == .ko ? "요약" : "Summary")
            .font(.headline)
        Text(digest.summary(for: language))
            .font(.body)

        Text(language == .ko ? "인용" : "Citations")
            .font(.headline)
        VStack(alignment: .leading, spacing: 10) {
            ForEach(digest.citations) { citation in
                CitationRow(citation: citation)
            }
        }

        Text("generated_at: \(digest.generatedAt)")
            .font(.caption2)
            .foregroundStyle(.secondary)
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
                .foregroundStyle(.secondary)
                .lineLimit(1)
            Text(valueText)
                .font(.title3.bold())
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            if let delta = metric.yoyDeltaPct {
                Text(deltaText(delta))
                    .font(.caption.bold())
                    .foregroundStyle(delta >= 0 ? Color.green : Color.red)
            } else {
                Text("YoY —")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(Color(.secondarySystemBackground))
        )
    }

    /// nil value -> dash, per contract UI rule.
    private var valueText: String {
        guard let value = metric.value else { return "—" }
        let number = value.formatted(.number.precision(.fractionLength(0...2)))
        return metric.unit.isEmpty ? number : "\(number) \(metric.unit)"
    }

    private func deltaText(_ delta: Double) -> String {
        let formatted = delta.formatted(.number.precision(.fractionLength(0...1)))
        return delta >= 0 ? "YoY +\(formatted)%" : "YoY \(formatted)%"
    }
}

// MARK: - Citation row

/// Citation cell shared by DigestView and ChatView.
struct CitationRow: View {
    let citation: Citation

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .top) {
                if let url = URL(string: citation.url) {
                    Link(citation.title, destination: url)
                        .font(.subheadline.bold())
                } else {
                    Text(citation.title)
                        .font(.subheadline.bold())
                }
                Spacer()
                SourceBadge(source: citation.source)
            }
            if let filedAt = citation.filedAt {
                Text(filedAt)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let excerpt = citation.excerpt {
                Text(excerpt)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(Color(.secondarySystemBackground))
        )
    }
}

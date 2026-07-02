//
//  SearchView.swift
//  FilingDigest
//
//  Company search: search field + result list; selecting a row pushes
//  DigestView. Handles loading / error / empty states explicitly.
//

import SwiftUI

struct SearchView: View {
    let client: APIClient

    @State private var query = ""
    @State private var results: [Company] = []
    @State private var isLoading = false
    @State private var errorMessage: String?
    /// Distinguishes "no results for this query" from "not searched yet".
    @State private var hasSearched = false

    var body: some View {
        NavigationStack {
            content
                .navigationTitle("검색")
                .searchable(text: $query, prompt: "회사 이름 또는 티커")
                .onSubmit(of: .search) {
                    Task { await search() }
                }
                .navigationDestination(for: Company.self) { company in
                    DigestView(client: client, company: company)
                }
        }
    }

    @ViewBuilder
    private var content: some View {
        if isLoading {
            ProgressView("검색 중…")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let errorMessage {
            ContentUnavailableView {
                Label("오류", systemImage: "exclamationmark.triangle")
            } description: {
                Text(errorMessage)
            } actions: {
                Button("다시 시도") {
                    Task { await search() }
                }
                .buttonStyle(.borderedProminent)
            }
        } else if hasSearched && results.isEmpty {
            ContentUnavailableView.search(text: query)
        } else if results.isEmpty {
            ContentUnavailableView(
                "회사 검색",
                systemImage: "building.2",
                description: Text("회사 이름 또는 티커로 검색해 공시 요약을 확인하세요.")
            )
        } else {
            List(results) { company in
                NavigationLink(value: company) {
                    CompanyRow(company: company)
                }
            }
            .listStyle(.plain)
        }
    }

    private func search() async {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let response = try await client.searchCompanies(query: trimmed)
            results = response.items
            hasSearched = true
        } catch {
            results = []
            hasSearched = false
            errorMessage = error.localizedDescription
        }
    }
}

// MARK: - Row

private struct CompanyRow: View {
    let company: Company

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(company.name)
                .font(.headline)
            if let nameEn = company.nameEn, nameEn != company.name {
                Text(nameEn)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            HStack(spacing: 6) {
                if let ticker = company.ticker {
                    Text(ticker)
                        .font(.caption.monospaced())
                }
                if let market = company.market {
                    Text(market.rawValue)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                SourceBadge(source: company.source)
            }
        }
        .padding(.vertical, 2)
    }
}

/// Small "DART"/"SEC" tag reused across screens.
struct SourceBadge: View {
    let source: FilingSource

    var body: some View {
        Text(source.rawValue.uppercased())
            .font(.caption2.bold())
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(source == .dart ? Color.blue.opacity(0.15) : Color.green.opacity(0.15))
            .foregroundStyle(source == .dart ? Color.blue : Color.green)
            .clipShape(Capsule())
    }
}

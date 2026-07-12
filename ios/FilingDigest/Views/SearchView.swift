//
//  SearchView.swift
//  FilingDigest
//
//  Company search: editorial header + hairline search field + result rows
//  separated by hairline rules (Ledger system, docs/design/DESIGN.md).
//  Handles loading / error / empty states explicitly.
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
    @FocusState private var searchFocused: Bool

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    header
                    searchField
                    content
                }
                .padding(.horizontal, 20)
                .padding(.top, 8)
            }
            .paperBackground()
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("FILING DIGEST")
                        .font(Theme.sectionLabel)
                        .tracking(2)
                        .foregroundStyle(Theme.inkMuted)
                }
            }
            .navigationBarTitleDisplayMode(.inline)
            .navigationDestination(for: Company.self) { company in
                DigestView(client: client, company: company)
            }
            .task {
                #if DEBUG
                // Screenshot automation: pre-run a search (see ContentView).
                if let q = ProcessInfo.processInfo.environment["FD_QUERY"], query.isEmpty {
                    query = q
                    await search()
                }
                #endif
            }
        }
        .tint(Color.accentColor)
    }

    // MARK: Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("공시를 읽다")
                .font(Theme.display(34))
                .foregroundStyle(Theme.ink)
            HStack(spacing: 10) {
                Rectangle()
                    .fill(Theme.ink)
                    .frame(width: 2)
                Text("DART · SEC 공시 기반, 인용으로 검증된 요약")
                    .font(.subheadline)
                    .foregroundStyle(Theme.inkMuted)
            }
            .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.top, 12)
    }

    // MARK: Search field

    private var searchField: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(Theme.inkMuted)
            TextField("회사 이름 또는 티커", text: $query)
                .font(.body)
                .foregroundStyle(Theme.ink)
                .focused($searchFocused)
                .submitLabel(.search)
                .autocorrectionDisabled()
                .onSubmit {
                    Task { await search() }
                }
            if !query.isEmpty {
                Button {
                    query = ""
                    searchFocused = true
                } label: {
                    Image(systemName: "xmark")
                        .font(.caption)
                        .foregroundStyle(Theme.inkMuted)
                }
                .accessibilityLabel("검색어 지우기")
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .overlay(
            RoundedRectangle(cornerRadius: 2)
                .strokeBorder(searchFocused ? Theme.ink : Theme.hairline, lineWidth: 1)
        )
        .accessibilityElement(children: .contain)
    }

    // MARK: Content states

    @ViewBuilder
    private var content: some View {
        if isLoading {
            ProgressView("검색 중…")
                .frame(maxWidth: .infinity)
                .padding(.top, 60)
        } else if let errorMessage {
            ContentUnavailableView {
                Label("오류", systemImage: "exclamationmark.triangle")
            } description: {
                Text(errorMessage)
            } actions: {
                Button("다시 시도") {
                    Task { await search() }
                }
                .buttonStyle(.bordered)
            }
        } else if hasSearched && results.isEmpty {
            ContentUnavailableView.search(text: query)
                .padding(.top, 20)
        } else if results.isEmpty {
            ContentUnavailableView(
                "회사 검색",
                systemImage: "building.2",
                description: Text("회사 이름 또는 티커로 검색해 공시 요약을 확인하세요.")
            )
            .padding(.top, 20)
        } else {
            VStack(alignment: .leading, spacing: 0) {
                SectionHeader(title: "결과")
                ForEach(results) { company in
                    NavigationLink(value: company) {
                        CompanyRow(company: company)
                    }
                    .buttonStyle(.plain)
                    Rectangle()
                        .fill(Theme.hairline)
                        .frame(height: 1)
                }
            }
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
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(company.name)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                if let nameEn = company.nameEn, nameEn != company.name {
                    Text(nameEn)
                        .font(.caption)
                        .foregroundStyle(Theme.inkMuted)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
                if company.ticker != nil || company.market != nil {
                    Text(
                        [company.ticker, company.market?.rawValue]
                            .compactMap(\.self)
                            .joined(separator: " · ")
                    )
                    .font(.caption.monospaced())
                    .foregroundStyle(Theme.inkMuted)
                }
            }
            Spacer()
            SourceBadge(source: company.source)
            Image(systemName: "arrow.right")
                .font(.caption.weight(.light))
                .foregroundStyle(Theme.inkMuted)
        }
        .padding(.vertical, 14)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}

/// Small square "DART"/"SEC" tag reused across screens: 1px border, no fill.
/// DART carries the accent; SEC stays ink — one accent color does real work.
struct SourceBadge: View {
    let source: FilingSource

    var body: some View {
        Text(source.rawValue.uppercased())
            .font(.caption2.weight(.semibold))
            .tracking(0.5)
            .padding(.horizontal, 6)
            .padding(.vertical, 3)
            .foregroundStyle(source == .dart ? Color.accentColor : Theme.ink)
            .overlay(
                Rectangle()
                    .strokeBorder(
                        source == .dart ? Color.accentColor : Theme.hairline,
                        lineWidth: 1
                    )
            )
            .accessibilityLabel("출처 \(source.rawValue.uppercased())")
    }
}

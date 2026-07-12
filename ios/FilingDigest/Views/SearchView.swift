//
//  SearchView.swift
//  FilingDigest
//
//  Browse-first home (Ledger system, docs/design/DESIGN.md): the whole
//  corpus loads immediately, grouped by source (DART / SEC), and the search
//  field filters the list as you type — no "search found nothing" dead end
//  while the corpus is small. Loading / error / empty states are explicit.
//

import SwiftUI

struct SearchView: View {
    let client: APIClient

    @State private var companies: [Company] = []
    @State private var query = ""
    @State private var isLoading = false
    @State private var errorMessage: String?
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
                await load()
                #if DEBUG
                // Screenshot automation: pre-filled filter (see ContentView).
                if let q = ProcessInfo.processInfo.environment["FD_QUERY"], query.isEmpty {
                    query = q
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

    // MARK: Search field (filters the loaded list)

    private var searchField: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(Theme.inkMuted)
            TextField("회사 이름 또는 티커로 필터", text: $query)
                .font(.body)
                .foregroundStyle(Theme.ink)
                .focused($searchFocused)
                .autocorrectionDisabled()
            if !query.isEmpty {
                Button {
                    query = ""
                    searchFocused = true
                } label: {
                    Image(systemName: "xmark")
                        .font(.caption)
                        .foregroundStyle(Theme.inkMuted)
                }
                .accessibilityLabel("필터 지우기")
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
            ProgressView("불러오는 중…")
                .frame(maxWidth: .infinity)
                .padding(.top, 60)
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
        } else if companies.isEmpty {
            ContentUnavailableView(
                "아직 수집된 회사가 없습니다",
                systemImage: "building.2",
                description: Text("백엔드에서 공시를 수집하면 여기에 표시됩니다.")
            )
            .padding(.top, 20)
        } else {
            let visible = Self.filter(companies, query: query)
            if visible.isEmpty {
                ContentUnavailableView.search(text: query)
                    .padding(.top, 20)
            } else {
                companyList(visible)
            }
        }
    }

    private func companyList(_ visible: [Company]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Self.grouped(visible), id: \.source) { group in
                SectionHeader(title: group.source == .dart ? "DART — 한국 공시" : "SEC — 미국 공시")
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(group.companies) { company in
                        NavigationLink(value: company) {
                            CompanyRow(company: company)
                        }
                        .buttonStyle(.plain)
                        Rectangle()
                            .fill(Theme.hairline)
                            .frame(height: 1)
                    }
                }
                .padding(.bottom, 12)
            }
        }
    }

    // MARK: Pure helpers (unit-tested)

    /// Case-insensitive substring filter over name / English name / ticker —
    /// the same fields the backend's /companies?q= matches, so the local
    /// filter and a server search agree.
    static func filter(_ companies: [Company], query: String) -> [Company] {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return companies }
        return companies.filter { company in
            company.name.localizedCaseInsensitiveContains(trimmed)
                || (company.nameEn?.localizedCaseInsensitiveContains(trimmed) ?? false)
                || (company.ticker?.localizedCaseInsensitiveContains(trimmed) ?? false)
        }
    }

    /// Stable DART-then-SEC grouping, preserving server order within a group.
    static func grouped(_ companies: [Company]) -> [(source: FilingSource, companies: [Company])] {
        [FilingSource.dart, .sec].compactMap { source in
            let members = companies.filter { $0.source == source }
            return members.isEmpty ? nil : (source, members)
        }
    }

    private func load() async {
        guard companies.isEmpty, !isLoading else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            // Empty query -> the full corpus (backend treats "" as match-all).
            let response = try await client.searchCompanies(query: "")
            companies = response.items
        } catch {
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

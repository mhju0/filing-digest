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

    @StateObject private var state: SearchState
    @State private var query = ""
    @AppStorage("recentCompanyIDs") private var recentCompanyIDsStorage = ""
    @FocusState private var searchFocused: Bool

    init(client: APIClient) {
        self.client = client
        _state = StateObject(wrappedValue: SearchState(loadCompanies: {
            try await client.searchCompanies(query: "")
        }))
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.sectionSpacing) {
                    // A blocking failure means there is nothing to browse or
                    // filter, so the browse chrome would only be furniture
                    // around a dead end. Give the whole screen to the recovery.
                    if let blockingError = state.blockingError, !state.hasLoaded {
                        connectionFailure(blockingError)
                    } else {
                        header
                        searchField
                        requestStatus
                        content
                    }
                }
                .padding(.horizontal, Theme.pageInset)
                .padding(.top, 8)
                .padding(.bottom, 32)
                .readableWidth()
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
            .toolbarBackground(Theme.paper, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
            .navigationDestination(for: Company.self) { company in
                DigestView(client: client, company: company)
                    .onAppear { recordRecent(company) }
            }
            .task { await state.loadIfNeeded() }
            .refreshable { await state.refresh() }
            .onDisappear { state.cancel() }
        }
        .tint(Color.accentColor)
    }

    // MARK: Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(state.hasLoaded ? "수집된 공시 / 회사 \(state.companies.count)곳" : "수집된 공시")
                .font(Theme.sectionLabel)
                .monospacedDigit()
                .foregroundStyle(Theme.inkMuted)
            Text("공시를,\n읽을 수 있게.")
                .font(Theme.display(.largeTitle))
                .foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
            Text("구조화된 수치. 인용된 설명. 원문까지 한 번에.")
                .font(.subheadline)
                .foregroundStyle(Theme.inkMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.top, 12)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }

    // MARK: Search field (filters the loaded list)

    private var searchField: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(Theme.inkMuted)
            TextField("회사 또는 티커", text: $query)
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
                        // 44pt hit area; the glyph itself stays small.
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .accessibilityLabel("필터 지우기")
            }
        }
        .padding(.leading, 14)
        .padding(.trailing, query.isEmpty ? 14 : 0)
        .padding(.vertical, query.isEmpty ? 12 : 4)
        .frame(minHeight: 52)
        .overlay(
            RoundedRectangle(cornerRadius: 2)
                .strokeBorder(searchFocused ? Theme.ink : Theme.border, lineWidth: 1)
        )
        .accessibilityElement(children: .contain)
    }

    // MARK: Content states

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

    /// Full-screen recovery for "the corpus never arrived".
    private func connectionFailure(_ message: String) -> some View {
        ContentUnavailableView {
            Label("공시를 불러오지 못했습니다", systemImage: "network.slash")
        } description: {
            Text(message)
        } actions: {
            Button("다시 시도") {
                Task { await state.retry() }
            }
            .buttonStyle(.ledger)
        }
        .frame(maxWidth: .infinity, minHeight: 460)
    }

    @ViewBuilder
    private var content: some View {
        if state.hasLoaded {
            if state.companies.isEmpty {
                ContentUnavailableView(
                    "아직 수집된 공시가 없습니다",
                    systemImage: "building.2",
                    description: Text("공시를 수집하면 회사 목록이 여기에 표시됩니다.")
                )
                .padding(.top, 20)
            } else {
                let visible = Self.filter(state.companies, query: query)
                if visible.isEmpty {
                    noMatch
                } else {
                    companyList(visible)
                }
            }
        } else if state.isLoading {
            ProgressView("불러오는 중…")
                .frame(maxWidth: .infinity)
                .padding(.top, 60)
        }
    }

    /// The corpus is a fixed, small set. The system's stock "No Results"
    /// reads as a failure and is localized to the device language, not the
    /// app's — so say what is actually here instead.
    private var noMatch: some View {
        ContentUnavailableView {
            Label("‘\(query)’는 수집 목록에 없습니다", systemImage: "magnifyingglass")
        } description: {
            Text("지금 이 앱에는 \(state.companies.count)개 회사의 공시가 수집되어 있습니다.")
        } actions: {
            Button("전체 목록 보기") {
                query = ""
                searchFocused = false
            }
            .buttonStyle(.ledger)
        }
        .padding(.top, 20)
    }

    @ViewBuilder
    private func companyList(_ visible: [Company]) -> some View {
        if query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            let recent = Self.recentCompanies(
                in: visible,
                storedIDs: Self.recentIDs(from: recentCompanyIDsStorage)
            )

            VStack(alignment: .leading, spacing: 0) {
                SectionHeader(title: "최근 본 회사", detail: "\(recent.count)")
                if recent.isEmpty {
                    Text("회사를 열면 최근 본 순서대로 여기에 표시됩니다.")
                        .font(.subheadline)
                        .foregroundStyle(Theme.inkMuted)
                        .padding(.vertical, 16)
                } else {
                    ForEach(Array(recent.enumerated()), id: \.element.id) { index, company in
                        NavigationLink(value: company) {
                            FeaturedCompanyRow(company: company, rank: index + 1)
                        }
                        .buttonStyle(.ledgerRow)
                        rowDivider
                    }
                }
            }

            VStack(alignment: .leading, spacing: 0) {
                let ordered = Self.ordered(visible)
                SectionHeader(title: "전체 회사", detail: "\(ordered.count)")
                ForEach(ordered) { company in
                    NavigationLink(value: company) {
                        CompactCompanyRow(company: company)
                    }
                    .buttonStyle(.ledgerRow)
                    rowDivider
                }
            }
        } else {
            VStack(alignment: .leading, spacing: 0) {
                let ordered = Self.ordered(visible)
                SectionHeader(title: "검색 결과", detail: "\(ordered.count)")
                ForEach(ordered) { company in
                    NavigationLink(value: company) {
                        FeaturedCompanyRow(company: company)
                    }
                    .buttonStyle(.ledgerRow)
                    rowDivider
                }
            }
        }
    }

    private var rowDivider: some View {
        Rectangle()
            .fill(Theme.hairline)
            .frame(height: 1)
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
                || company.koreanDisplayName.localizedCaseInsensitiveContains(trimmed)
                || (company.nameEn?.localizedCaseInsensitiveContains(trimmed) ?? false)
                || (company.ticker?.localizedCaseInsensitiveContains(trimmed) ?? false)
        }
    }

    static func ordered(_ companies: [Company]) -> [Company] {
        companies.sorted {
            $0.koreanDisplayName.localizedStandardCompare($1.koreanDisplayName) == .orderedAscending
        }
    }

    static func recentIDs(from storage: String) -> [String] {
        storage.split(separator: ",").map(String.init)
    }

    static func updatedRecentIDs(opening companyID: String, existing: [String]) -> [String] {
        [companyID] + Array(existing.filter { $0 != companyID }.prefix(1))
    }

    static func recentCompanies(in companies: [Company], storedIDs: [String]) -> [Company] {
        let byID = Dictionary(uniqueKeysWithValues: companies.map { ($0.id, $0) })
        return storedIDs.compactMap { byID[$0] }
    }

    private func recordRecent(_ company: Company) {
        recentCompanyIDsStorage = Self.updatedRecentIDs(
            opening: company.id,
            existing: Self.recentIDs(from: recentCompanyIDsStorage)
        ).joined(separator: ",")
    }
}

// MARK: - Rows

private struct FeaturedCompanyRow: View {
    let company: Company
    var rank: Int? = nil

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            if let rank {
                Text(rank.formatted(.number.precision(.integerLength(2))))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(Color.accentColor)
                    .frame(width: 24, alignment: .leading)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(company.koreanDisplayName)
                    .font(Theme.display(.body))
                    .foregroundStyle(Theme.ink)
                if company.ticker != nil || company.market != nil {
                    Text(
                        [company.ticker, company.market?.koreanDisplayName]
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
        .frame(minHeight: 64)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}

private struct CompactCompanyRow: View {
    let company: Company

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(company.koreanDisplayName)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Theme.ink)
                .multilineTextAlignment(.leading)
            Spacer(minLength: 8)
            Text(company.ticker ?? "—")
                .font(.caption.monospaced())
                .foregroundStyle(Theme.inkMuted)
            SourceBadge(source: company.source)
            Image(systemName: "chevron.right")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(Theme.inkMuted)
        }
        .padding(.vertical, 12)
        .frame(minHeight: 52)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}

/// Small square "DART"/"SEC" tag reused across screens: 1px border, no fill.
/// DART carries the accent; SEC stays ink — one accent color does real work.
struct SourceBadge: View {
    let source: RegulatorySource

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
                        source == .dart ? Color.accentColor : Theme.border,
                        lineWidth: 1
                    )
            )
            .accessibilityLabel("출처 \(source.rawValue.uppercased())")
    }
}

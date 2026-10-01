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
    /// Shared with DigestView so the KO/EN toggle carries across screens.
    @State private var language: Language = .ko
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
                ToolbarItem(placement: .topBarTrailing) {
                    Button(SearchCopy.otherLanguageName(language)) {
                        language = language == .ko ? .en : .ko
                    }
                    .font(.subheadline)
                    .frame(minWidth: 44, minHeight: 44)
                    .accessibilityLabel(SearchCopy.languageSwitchLabel(language))
                }
            }
            .toolbarBackground(Theme.paper, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
            .navigationDestination(for: Company.self) { company in
                DigestView(client: client, company: company, language: $language)
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
            Text(SearchCopy.corpusLabel(count: state.hasLoaded ? state.companies.count : nil, language: language))
                .font(Theme.sectionLabel)
                .monospacedDigit()
                .foregroundStyle(Theme.inkMuted)
            Text(SearchCopy.headline(language))
                .font(Theme.display(.largeTitle))
                .foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
            Text(SearchCopy.subhead(language))
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
            TextField(SearchCopy.fieldPrompt(language), text: $query)
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
                .accessibilityLabel(language == .ko ? "필터 지우기" : "Clear filter")
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
            ProgressView(language == .ko ? "새로 고치는 중…" : "Refreshing…")
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
            Label(language == .ko ? "공시를 불러오지 못했습니다" : "Couldn't load filings", systemImage: "network.slash")
        } description: {
            Text(message)
        } actions: {
            Button(language == .ko ? "다시 시도" : "Try again") {
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
                    language == .ko ? "아직 수집된 공시가 없습니다" : "No filings collected yet",
                    systemImage: "building.2",
                    description: Text(language == .ko
                        ? "공시를 수집하면 회사 목록이 여기에 표시됩니다."
                        : "Companies appear here once their filings are collected.")
                )
                .padding(.top, 20)
            } else {
                let snapshot = CompanyDirectory(companies: state.companies).snapshot(
                    query: query,
                    recentStorage: recentCompanyIDsStorage
                )
                if snapshot.visibleCompanies.isEmpty {
                    noMatch
                } else {
                    companyList(snapshot)
                }
            }
        } else if state.isLoading {
            ProgressView(language == .ko ? "불러오는 중…" : "Loading…")
                .frame(maxWidth: .infinity)
                .padding(.top, 60)
        }
    }

    /// The corpus is a fixed, small set. The system's stock "No Results"
    /// reads as a failure and is localized to the device language, not the
    /// app's — so say what is actually here instead.
    private var noMatch: some View {
        ContentUnavailableView {
            Label(SearchCopy.noMatchTitle(query: query, language: language), systemImage: "magnifyingglass")
        } description: {
            Text(SearchCopy.noMatchDetail(count: state.companies.count, language: language))
        } actions: {
            Button(language == .ko ? "전체 목록 보기" : "Show all companies") {
                query = ""
                searchFocused = false
            }
            .buttonStyle(.ledger)
        }
        .padding(.top, 20)
    }

    @ViewBuilder
    private func companyList(_ snapshot: CompanyDirectory.Snapshot) -> some View {
        if !snapshot.isFiltering {
            VStack(alignment: .leading, spacing: 0) {
                SectionHeader(title: language == .ko ? "최근 본 회사" : "Recently viewed", detail: "\(snapshot.recentCompanies.count)")
                if snapshot.recentCompanies.isEmpty {
                    Text(language == .ko
                        ? "회사를 열면 최근 본 순서대로 여기에 표시됩니다."
                        : "Companies you open appear here, most recent first.")
                        .font(.subheadline)
                        .foregroundStyle(Theme.inkMuted)
                        .padding(.vertical, 16)
                } else {
                    ForEach(Array(snapshot.recentCompanies.enumerated()), id: \.element.id) { index, company in
                        NavigationLink(value: company) {
                            FeaturedCompanyRow(company: company, language: language, rank: index + 1)
                        }
                        .buttonStyle(.ledgerRow)
                        .accessibilityIdentifier("company-\(company.ticker ?? company.id)")
                        rowDivider
                    }
                }
            }

            VStack(alignment: .leading, spacing: 0) {
                SectionHeader(title: language == .ko ? "전체 회사" : "All companies", detail: "\(snapshot.visibleCompanies.count)")
                ForEach(snapshot.visibleCompanies) { company in
                    NavigationLink(value: company) {
                        CompactCompanyRow(company: company, language: language)
                    }
                    .buttonStyle(.ledgerRow)
                    .accessibilityIdentifier("company-\(company.ticker ?? company.id)")
                    rowDivider
                }
            }
        } else {
            VStack(alignment: .leading, spacing: 0) {
                SectionHeader(title: language == .ko ? "검색 결과" : "Results", detail: "\(snapshot.visibleCompanies.count)")
                ForEach(snapshot.visibleCompanies) { company in
                    NavigationLink(value: company) {
                        FeaturedCompanyRow(company: company, language: language)
                    }
                    .buttonStyle(.ledgerRow)
                    .accessibilityIdentifier("company-\(company.ticker ?? company.id)")
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

    private func recordRecent(_ company: Company) {
        recentCompanyIDsStorage = CompanyDirectory.recordingVisit(
            to: company.id,
            in: recentCompanyIDsStorage
        )
    }
}

// MARK: - Rows

private struct FeaturedCompanyRow: View {
    let company: Company
    let language: Language
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
                Text(company.displayName(language))
                    .font(Theme.display(.body))
                    .foregroundStyle(Theme.ink)
                if company.ticker != nil || company.market != nil {
                    Text(
                        [company.securityIdentifier(language), company.market?.displayName(language)]
                            .compactMap(\.self)
                            .joined(separator: " · ")
                    )
                    .font(.caption.monospaced())
                    .foregroundStyle(Theme.inkMuted)
                }
            }
            Spacer()
            SourceBadge(source: company.source, language: language)
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
    let language: Language

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(company.displayName(language))
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                    .multilineTextAlignment(.leading)
                if let identifier = company.securityIdentifier(language) {
                    Text(identifier)
                        .font(.caption.monospaced())
                        .foregroundStyle(Theme.inkMuted)
                }
            }
            Spacer(minLength: 8)
            SourceBadge(source: company.source, language: language)
            Image(systemName: "chevron.right")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(Theme.inkMuted)
        }
        .padding(.vertical, 12)
        .frame(minHeight: 60)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Copy

enum SearchCopy {
    static func corpusLabel(count: Int?, language: Language) -> String {
        guard let count else { return language == .ko ? "수집된 공시" : "Collected filings" }
        return language == .ko
            ? "수집된 공시 / 회사 \(count)곳"
            : "Collected filings / \(count) \(count == 1 ? "company" : "companies")"
    }

    static func headline(_ language: Language) -> String {
        language == .ko ? "공시를,\n읽을 수 있게." : "Filings,\nmade readable."
    }

    static func subhead(_ language: Language) -> String {
        language == .ko
            ? "구조화된 수치. 인용된 설명. 원문까지 한 번에."
            : "Structured figures, cited explanations and the original filing in one place."
    }

    static func fieldPrompt(_ language: Language) -> String {
        language == .ko ? "회사 또는 티커" : "Company or ticker"
    }

    static func noMatchTitle(query: String, language: Language) -> String {
        language == .ko ? "‘\(query)’는 수집 목록에 없습니다" : "‘\(query)’ isn't in the collection"
    }

    static func noMatchDetail(count: Int, language: Language) -> String {
        language == .ko
            ? "지금 이 앱에는 \(count)개 회사의 공시가 수집되어 있습니다."
            : "This app currently holds filings for \(count) \(count == 1 ? "company" : "companies")."
    }

    /// The switch names the language it switches to.
    static func otherLanguageName(_ language: Language) -> String {
        language == .ko ? "English" : "한국어"
    }

    static func languageSwitchLabel(_ language: Language) -> String {
        language == .ko ? "영어로 보기" : "Show in Korean"
    }
}

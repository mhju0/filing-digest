//
//  ContentView.swift
//  FilingDigest
//
//  Root view: company search (-> digest).
//  State management is intentionally simple: one shared APIClient value is
//  passed down, SearchView owns its @State and calls async functions directly.
//
//  Screenshot automation (DEBUG only): FD_SCREEN=digest|answer plus
//  FD_COMPANY_ID / FD_COMPANY_NAME / FD_SOURCE jump straight to a deep screen
//  so simulator screenshots don't need scripted taps. FD_QUERY is consumed by
//  SearchView (pre-filled search) and AnswerView (auto-asked question).
//

import SwiftUI

struct ContentView: View {
    /// Single client instance shared by the app; APIClient is a stateless
    /// value type, so passing it by value is safe.
    private let client = APIClient()

    var body: some View {
        #if DEBUG
        if let screen = ProcessInfo.processInfo.environment["FD_SCREEN"],
           let company = Self.companyFromEnvironment() {
            NavigationStack {
                switch screen {
                case "answer":
                    AnswerView(client: client, company: company)
                default:
                    DigestView(client: client, company: company)
                }
            }
        } else {
            SearchView(client: client)
        }
        #else
        SearchView(client: client)
        #endif
    }

    #if DEBUG
    private static func companyFromEnvironment() -> Company? {
        let env = ProcessInfo.processInfo.environment
        guard let id = env["FD_COMPANY_ID"], let name = env["FD_COMPANY_NAME"] else {
            return nil
        }
        let source: FilingSource = env["FD_SOURCE"] == "sec" ? .sec : .dart
        return Company(
            id: id,
            name: name,
            nameEn: env["FD_COMPANY_NAME_EN"],
            ticker: env["FD_TICKER"],
            market: nil,
            source: source
        )
    }
    #endif
}

//
//  ContentView.swift
//  FilingDigest
//
//  Root view: company search (-> digest).
//  State management is intentionally simple: one shared APIClient value is
//  passed down, SearchView owns its @State and calls async functions directly.
//

import SwiftUI

struct ContentView: View {
    /// Single client instance shared by the app; APIClient is a stateless
    /// value type, so passing it by value is safe.
    private let client = APIClient()

    var body: some View {
        SearchView(client: client)
    }
}

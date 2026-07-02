//
//  ContentView.swift
//  FilingDigest
//
//  Root tab layout: company search (-> digest) and chat.
//  State management is intentionally simple: one shared APIClient value is
//  passed down, each screen owns its @State and calls async functions directly.
//

import SwiftUI

struct ContentView: View {
    /// Single client instance shared by both tabs; APIClient is a stateless
    /// value type, so passing it by value is safe.
    private let client = APIClient()

    var body: some View {
        TabView {
            SearchView(client: client)
                .tabItem {
                    Label("검색", systemImage: "magnifyingglass")
                }

            ChatView(client: client)
                .tabItem {
                    Label("챗", systemImage: "bubble.left.and.bubble.right")
                }
        }
    }
}

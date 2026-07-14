//
//  ContentView.swift
//  FilingDigest
//
//  Root view: company search (-> digest).
//  One shared APIClient value is passed down; each screen owns a dedicated
//  asynchronous state module with an endpoint-operation testing seam.
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

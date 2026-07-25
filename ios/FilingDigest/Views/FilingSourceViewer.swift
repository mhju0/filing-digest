//
//  FilingSourceViewer.swift
//  FilingDigest
//
//  In-app viewer for an openable Filing Source.
//
//  Verifying a claim against its original disclosure is the whole point of
//  this app, and a plain Link ejected the reader into Safari to do it — so
//  the one loop the product exists for was the one loop that left the app.
//  SFSafariViewController keeps it inside without costing a dependency:
//  SafariServices ships with the system, so the zero-third-party rule holds.
//

import SafariServices
import SwiftUI

/// A Filing Source resolved to something presentable. `id` is the Filing
/// Source's own id, so `.sheet(item:)` re-presents correctly when the reader
/// taps a different source without dismissing first.
struct OpenableFiling: Identifiable, Hashable {
    let id: String
    let url: URL

    /// Filing Sources without an absolute HTTP(S) URL are not evidence a
    /// person can inspect, and never become openable.
    init?(_ filingSource: FilingSource) {
        guard let url = filingSource.openableURL else { return nil }
        self.id = filingSource.id
        self.url = url
    }
}

struct FilingSourceViewer: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        let configuration = SFSafariViewController.Configuration()
        configuration.entersReaderIfAvailable = false
        let controller = SFSafariViewController(url: url, configuration: configuration)
        controller.preferredControlTintColor = UIColor(Color.accentColor)
        controller.preferredBarTintColor = UIColor(Theme.paper)
        controller.dismissButtonStyle = .close
        return controller
    }

    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}
}

extension View {
    /// Presents a Filing Source over the current screen.
    func filingSourceSheet(_ filing: Binding<OpenableFiling?>) -> some View {
        sheet(item: filing) { openable in
            FilingSourceViewer(url: openable.url)
                .ignoresSafeArea()
        }
    }
}

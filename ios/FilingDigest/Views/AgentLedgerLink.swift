//
//  AgentLedgerLink.swift
//  FilingDigest
//
//  A row linking a covered company to its section of Filing Agent's ledger.
//  Renders nothing for companies the ledger doesn't cover.
//

import SwiftUI

struct AgentLedgerLink: View {
    let ticker: String?
    let language: Language

    var body: some View {
        if let ticker, let url = AgentLedger.url(ticker: ticker, language: language),
           let detail = AgentLedger.detail(ticker: ticker, language: language) {
            Link(destination: url) {
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(AgentLedger.title(language))
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(Color.accentColor)
                        Text(detail)
                            .font(.caption)
                            .foregroundStyle(Theme.inkMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer(minLength: 8)
                    Image(systemName: "arrow.up.forward")
                        .font(.caption)
                        .foregroundStyle(Color.accentColor)
                }
                .padding(.vertical, 12)
                .frame(minHeight: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(.ledgerRow)
            .accessibilityElement(children: .combine)
            .accessibilityHint(AgentLedger.openHint(language))
            .accessibilityIdentifier("agent-ledger-link")
        }
    }
}

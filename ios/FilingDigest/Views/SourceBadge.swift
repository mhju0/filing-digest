import SwiftUI

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

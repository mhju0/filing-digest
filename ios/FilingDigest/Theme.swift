//
//  Theme.swift
//  FilingDigest
//
//  "Ledger" design tokens and shared components (docs/design/DESIGN.md).
//  Editorial annual-report system: paper ground, ink type, one ledger-green
//  accent, 1px hairline borders instead of gray-fill cards, serif display,
//  tabular numerals for every figure. System fonts only (New York / SF Pro /
//  SF Mono) — the zero-third-party rule holds.
//

import SwiftUI

enum Theme {
    // MARK: Colors (asset catalog, dark variants included)

    static let paper = Color("Paper")
    static let ink = Color("Ink")
    static let inkMuted = Color("InkMuted")
    /// Decorative rule between rows and under section labels. Ink at low
    /// opacity so it adapts per mode. WCAG 1.4.11 does not apply — these
    /// separate content, they never identify a control.
    static let hairline = ink.opacity(0.25)
    /// Boundary of an interactive component (text field, tappable card,
    /// outlined button/badge). Held at >= 3:1 against Paper in both modes,
    /// because in the Ledger system the border IS the affordance.
    static let border = Color("Border")
    /// Downward change on a figure. Sign is always in the text too — color
    /// never carries the meaning alone.
    static let negative = Color("Negative")

    // MARK: Type

    /// Serif display for company names and key titles (New York).
    ///
    /// Bound to a text style rather than a fixed point size: a fixed `size:`
    /// ignores Dynamic Type entirely, which inverted the hierarchy at
    /// accessibility sizes (the subheadline grew past the headline).
    static func display(_ style: Font.TextStyle, weight: Font.Weight = .semibold) -> Font {
        .system(style, design: .serif, weight: weight)
    }

    /// Letter-spaced small-caps style is approximated with an uppercased
    /// caption + tracking; SwiftUI has no true small caps for Korean anyway.
    static let sectionLabel = Font.caption.weight(.semibold)
}

// MARK: - Shared components

/// Small-caps section header over a thin rule ("요약", "출처", …).
/// `detail` carries a count or scope on the trailing edge — the corpus is
/// bounded, and saying how bounded is what keeps browsing honest.
struct SectionHeader: View {
    let title: String
    var detail: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(title)
                    .font(Theme.sectionLabel)
                    .tracking(1.2)
                if let detail {
                    Spacer(minLength: 8)
                    Text(detail)
                        .font(Theme.sectionLabel)
                        .monospacedDigit()
                }
            }
            .foregroundStyle(Theme.inkMuted)
            Rectangle()
                .fill(Theme.hairline)
                .frame(height: 1)
        }
        .padding(.top, 8)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }
}

/// Outlined ledger button. The system `.bordered` capsule was the one
/// component that broke the square, hairline vocabulary everywhere else,
/// and it only ever appeared on error screens — the worst place to look
/// like a different app.
struct LedgerButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(Color.accentColor)
            .padding(.horizontal, 18)
            .frame(minHeight: 44)
            .contentShape(Rectangle())
            .overlay(
                RoundedRectangle(cornerRadius: 2)
                    .strokeBorder(Color.accentColor, lineWidth: 1)
            )
            .opacity(configuration.isPressed ? 0.55 : 1)
    }
}

extension ButtonStyle where Self == LedgerButtonStyle {
    static var ledger: LedgerButtonStyle { LedgerButtonStyle() }
}

/// Square citation marker — the brand's smallest unit. Filled ledger green
/// with paper numeral, mirroring the app icon's green square.
struct CitationMarker: View {
    let index: Int

    var body: some View {
        Text("\(index)")
            .font(.caption2.weight(.bold).monospacedDigit())
            .foregroundStyle(Theme.paper)
            .padding(.horizontal, 5)
            .padding(.vertical, 2)
            .background(Rectangle().fill(Color.accentColor))
            .accessibilityLabel("출처 \(index)번")
    }
}

/// Minimal wrapping row for citation markers — HStack that breaks lines.
/// Closes the "citation chips use a plain HStack" limitation once answers
/// cite more than a handful of filings.
struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x > 0, x + size.width > maxWidth {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
        let width = maxWidth.isFinite ? maxWidth : max(0, x - spacing)
        return CGSize(width: width, height: y + rowHeight)
    }

    func placeSubviews(
        in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()
    ) {
        var x = bounds.minX
        var y = bounds.minY
        var rowHeight: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x > bounds.minX, x + size.width > bounds.maxX {
                x = bounds.minX
                y += rowHeight + spacing
                rowHeight = 0
            }
            subview.place(at: CGPoint(x: x, y: y), proposal: .unspecified)
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}

extension View {
    /// Ledger card: hairline border on paper, near-square corners.
    /// Replaces the gray-fill rounded card.
    func ledgerCard(padding: CGFloat = 12, borderColor: Color = Theme.border) -> some View {
        self
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.paper)
            .overlay(
                RoundedRectangle(cornerRadius: 2)
                    .strokeBorder(borderColor, lineWidth: 1)
            )
    }

    /// Full-screen paper ground behind scrollable content.
    func paperBackground() -> some View {
        self.background(Theme.paper.ignoresSafeArea())
    }

    /// Caps the measure and centres what is left. Nothing here was bounded,
    /// so in landscape and on iPad a paragraph ran the full width of the
    /// display — the one thing an annual-report system must never do.
    func readableWidth(_ limit: CGFloat = 640) -> some View {
        self
            .frame(maxWidth: limit)
            .frame(maxWidth: .infinity)
    }
}

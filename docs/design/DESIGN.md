# Filing Digest Visual System

The implemented “Ledger” system borrows the visual language of annual reports
and research journals: paper, ink, ruled structure, restrained typography, and
one green accent. The source of truth is `ios/FilingDigest/Theme.swift` plus the
asset catalog; this document records the durable design rules.

## Principles

- Information density comes from typography and alignment, not filled cards.
- Financial values use tabular or monospaced numerals.
- Citation markers are visible, compact, and consistent across answer and
  source views.
- Color never carries meaning alone; labels, signs, and source names remain.
- Loading, empty, error, blocked, and no-results states are first-class screens.

## Tokens

| Role | Light | Dark | Implementation |
|---|---|---|---|
| Paper | `#F7F4EE` | `#14130F` | `Color("Paper")` |
| Ink | `#1A1917` | `#ECE9E3` | `Color("Ink")` |
| Muted ink | `#6B6965` | asset variant | `Color("InkMuted")` |
| Ledger green | `#1D5C45` | `#3E8E6E` | `AccentColor` |
| Hairline | ink at 25% | adaptive | `Theme.hairline` |

The app uses system fonts only:

- Serif system design for company names and display headings.
- Default system body for prose.
- Monospaced digits for figures, periods, and metadata.
- Tracked caption text for section labels.

## Components

- **Ledger card:** paper background, one-point hairline, two-point corner radius,
  no shadow or gradient.
- **Citation marker:** green square with a high-contrast numeric label and an
  explicit accessibility label.
- **Source badge:** outlined DART/SEC tag; DART uses the accent while SEC uses ink.
- **Section header:** tracked caption with a horizontal rule.
- **Question quote:** serif italic text beside a two-point vertical rule.
- **Figure callout:** green outline, exact structured values, and readable
  abbreviated display values.

## Interaction and accessibility

- Company browsing is the initial state; filtering happens locally after the
  bounded corpus is loaded.
- Digest language changes locally because both summaries and metric labels are
  included in the response.
- Answer UI renders all three backend states (`ok`, `blocked`, `no_results`) and
  keeps structured figures visible independently of prose.
- Controls have explicit accessibility labels, content is grouped where useful,
  and citation markers wrap rather than overflow horizontally.
- Dynamic Type can scale body content; large numeric rows use a minimum scale
  factor to avoid clipping.

Current product captures are maintained in `docs/screenshots/`. Brand marks used
by the README are `docs/design/logos/mark_light.png` and `mark_dark.png`; the app
icon lives in the asset catalog.

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
- Anything that can be tapped looks tapped-able. In a system with no fills and
  no shadows, the border is the affordance, so it is held to a contrast
  minimum rather than chosen by eye.

## Tokens

| Role | Light | Dark | Contrast (light / dark) | Implementation |
|---|---|---|---|---|
| Paper | `#F7F4EE` | `#14130F` | ground | `Color("Paper")` |
| Ink | `#1A1917` | `#ECE9E3` | 16.0:1 / 15.3:1 | `Color("Ink")` |
| Muted ink | `#6B6965` | `#8A877F` | 5.0:1 / 5.2:1 | `Color("InkMuted")` |
| Ledger green | `#1D5C45` | `#3E8E6E` | 7.2:1 / 4.7:1 | `AccentColor` |
| Border | `#8F8B82` | `#6E6B64` | 3.1:1 / 3.5:1 | `Theme.border` |
| Negative | `#A32A1E` | `#E8776A` | 6.6:1 / 6.4:1 | `Theme.negative` |
| Hairline | ink at 25% | ink at 25% | n/a | `Theme.hairline` |

Ratios are measured against Paper in the matching appearance. Text tokens clear
WCAG 2.1 SC 1.4.3 (4.5:1). `Theme.border` clears SC 1.4.11 (3:1) because it
bounds interactive components. `Theme.hairline` is exempt: it separates content
and never identifies a control, so the two roles are separate tokens rather
than one shared gray.

The app uses system fonts only:

- Serif system design for company names and display headings, bound to a text
  style through `Theme.display(_:weight:)`. A fixed `size:` ignores Dynamic
  Type and inverts the hierarchy at accessibility sizes.
- Default system body for prose.
- Monospaced digits for figures, periods, and metadata.
- Tracked caption text for section labels.

## Components

- **Ledger card:** paper background, one-point border, two-point corner radius,
  no shadow or gradient.
- **Ledger button:** the same square, outlined vocabulary as everything else.
  Replaces the system bordered capsule, which appeared only on error screens —
  the worst place to look like a different app.
- **Citation marker:** green square with a high-contrast numeric label and an
  explicit accessibility label. Tapping one scrolls to the Filing Source it
  resolves to.
- **Source badge:** outlined DART/SEC tag; DART uses the accent while SEC uses ink.
- **Section header:** tracked caption with a horizontal rule, plus an optional
  trailing count. The corpus is bounded; saying how bounded keeps browsing honest.
- **Question quote:** serif italic text beside a two-point vertical rule.
- **Figure callout:** green outline, exact structured values, and readable
  abbreviated display values.
- **Filing source sheet:** `SFSafariViewController` tinted to Paper and accent.
  The original disclosure opens in the app rather than handing the reader to
  Safari and losing their place.
- **Answer skeleton:** the asked question stays on screen above ruled
  placeholder bars, so a slow answer reads as pending rather than lost.

## Interaction and accessibility

- Company browsing is the initial state; filtering happens locally after the
  bounded corpus is loaded.
- Digest language changes locally because both summaries and metric labels are
  included in the response.
- Every metric card carries the Filing Source its value came from and opens it;
  an `arrow.up.forward` mark distinguishes an openable card from a bordered box
  that does nothing.
- Q&A is reachable from a named control on the digest body, not only from a
  toolbar glyph. The starter screen offers questions the guards can actually
  answer, so the first attempt is not a guess.
- Answer UI renders all three backend states (`ok`, `blocked`, `no_results`) and
  keeps structured figures visible independently of prose. Figures collapse only
  when narrative succeeded; when prose is blocked they stay expanded, because
  they are then the entire answer.
- Error copy names what happened before what to do about it, and never leads
  with a host, port, or status code.
- Controls have explicit accessibility labels, content is grouped where useful,
  and citation markers wrap rather than overflow horizontally.
- Tap targets are at least 44 points. Dynamic Type scales body content; large
  numeric rows use a minimum scale factor to avoid clipping.
- Measure is capped at 640 points and centered, so a paragraph never runs the
  full width in landscape or on iPad.

Current product captures are maintained in `docs/screenshots/`. Brand marks used
by the README are `docs/design/logos/mark_light.png` and `mark_dark.png`; the app
icon lives in the asset catalog.

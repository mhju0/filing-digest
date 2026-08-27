# Filing Digest UI/UX Explorations

This design lab compares four product directions before any shipped SwiftUI
is changed. Open `index.html` directly, or serve this directory with a local
static server, to switch between the concepts and inspect all twelve screens.

## Product evidence used

- [Verified] The app begins with a locally filtered, source-grouped company
  corpus in `ios/FilingDigest/Views/SearchView.swift`.
- [Verified] A company digest combines structured metric cards, bilingual
  narrative, and openable filing sources in
  `ios/FilingDigest/Views/DigestView.swift`.
- [Verified] Q&A is single-shot rather than conversational, and every displayed
  narrative segment resolves to filing evidence in
  `ios/FilingDigest/Views/AnswerView.swift`.
- [Verified] Structured figures survive independently when generated narrative
  is blocked; the answer view renders `ok`, `blocked`, and `no_results` states.
- [Verified] The current Ledger system uses paper, ink, ruled structure,
  restrained typography, and one green accent in
  `ios/FilingDigest/Theme.swift` and `docs/design/DESIGN.md`.
- [Verified] The app targets iOS 17+, uses SwiftUI, and has no third-party iOS
  packages according to `README.md`.

## Shared product rules

All four directions keep the same non-negotiable product model:

1. Financial figures and generated prose remain visibly distinct.
2. A claim never becomes a visual dead end: its citation leads to an evidence
   excerpt, then to the original regulator filing.
3. DART and SEC remain explicit identity context, not decorative badges.
4. Loading, blocked, no-results, and source-unavailable states retain a clear
   recovery path.
5. Mobile controls use at least 44-point targets, text scales with Dynamic Type,
   and color is never the only status signal.

## Concept comparison

| Concept | Product thesis | Primary entry | Information density | Best fit | Main tradeoff |
|---|---|---|---|---|---|
| A — Ledger Focus | Reading a filing should feel like reading a precise edited brief | Bounded company index | Medium | Safest refinement of the existing product | Discovery remains list-led |
| B — Research Desk | The app is an analyst workspace with evidence always adjacent | Search/command field | High | Repeated comparison and source inspection | Densest learning curve |
| C — Signal Brief | Users want the few changes worth reading before the whole filing | Latest briefing feed | Low–medium | Fast scanning and return visits | Requires a defensible “what changed” model |
| D — Evidence Thread | The shortest path to value is asking, then traversing the proof | Question composer | Medium | Demonstrating citation integrity | Company browsing becomes secondary |

## New flows

### A — Ledger Focus

`Index → company folio → metric or question → inline citation → source drawer → regulator filing`

- The browse screen becomes a real index: recent companies sit above a compact,
  alphabetized corpus.
- The company screen uses one strong metric band instead of equal-weight cards.
- The answer keeps prose linear and opens evidence in a bottom drawer so the
  reader does not lose the claim being checked.

### B — Research Desk

`Command search → workspace → select figure/claim → persistent evidence pane → filing`

- Search, recent companies, and saved research live in one command surface.
- Compact width uses a three-tab workspace: `Brief`, `Ask`, `Sources`.
- Regular width becomes a `NavigationSplitView`: company list, working document,
  and persistent evidence inspector. Selection state is shared across columns.

### C — Signal Brief

`Latest changes → company briefing → explain a signal → evidence sheet → filing`

- Home is a briefing feed rather than a company directory; a visible “All
  companies” route preserves exhaustive browsing.
- Company metrics are ordered by change magnitude and accompanied by plain
  labels such as “revenue accelerated” rather than color alone.
- Suggested questions are contextual actions attached to a signal, reducing the
  chance of an unsupported first question.

### D — Evidence Thread

`Choose company + ask → answer claims → evidence thread → source map → filing`

- Company selection and suggested question shapes are combined in the initial
  composer.
- Each answer paragraph is a claim block with an attached evidence path; source
  markers do not sit in a detached footnote area.
- A source map groups multiple claim excerpts under one filing before the user
  opens the regulator document.

## Adaptive layout logic

| Width class | A — Ledger Focus | B — Research Desk | C — Signal Brief | D — Evidence Thread |
|---|---|---|---|---|
| Compact | Single reading column; evidence as bottom sheet | Three workspace tabs; inspector as sheet | Feed + bottom navigation | Composer/answer stack; source map as sheet |
| Regular | Centered reading column with index rail | Three-column split view | Two-column feed and company rail | Answer canvas plus pinned evidence rail |
| Landscape | 680pt reading measure; metrics form a horizontal band | Working document keeps priority over both sidebars | Cards form a two-column masonry grid | Claim blocks stay 640pt; source rail remains fixed |

## Motion and accessibility logic

- Use 150–250 ms state transitions, limited to opacity and translation.
- Preserve the selected company, question, scroll position, and evidence item
  when moving back.
- Loading longer than 300 ms keeps the submitted question visible and replaces
  answer lines with a skeleton.
- Respect Reduce Motion by cross-fading instead of sliding sheets or columns.
- Source markers include number, filing name, and action text so neither color
  nor icon shape carries meaning alone.

## Files

- `index.html` — semantic structure and all twelve mock screens
- `styles.css` — shared layout plus four isolated visual systems
- `app.js` — concept switching and keyboard-accessible tab state

# Audit correction and narrow-screen follow-up, 2026-11-04

Read this alongside the [full product audit](2026-11-04-product-audit.md).

## Correction to A8: dark playback hover

[Verified] The original dark hover already resolved `color: var(--paper)` to
`#171714` against `background: var(--accent)` at `#93c7ae`, about 9.43:1 contrast.
The original audit's dark-hover defect claim was incorrect. The extra hover
selector introduced in PR #41 was redundant and is removed by this follow-up.
No dark-hover contrast improvement is claimed. A8's separately observed keyboard
skip-link stacking and control-height findings remain valid.

## Follow-up wrapping

[Verified] Additional native Chrome inspection found isolated final words in the
family, closing and narrative-card paragraphs. Scoped balanced wrapping fixes
those observed paragraphs while preserving the Ledger layout and Korean word
boundaries. The inspected 393px KO/light and EN/dark layouts and desktop views
have no clipping or isolated final words in those paragraphs. This is selected
rendered coverage, not a certification of every viewport or font substitution.

[Verified] This follow-up changes CSS and this correction record only. The
security/financial implementation independently reviewed at
`0dca547a0edcc904e6f6535d75389955c5e5f945` is unchanged. Its required
[backend and iOS CI](https://github.com/mhju0/filing-digest/actions/runs/41749371796)
passed before [PR #41](https://github.com/mhju0/filing-digest/pull/41) merged.
The first [Pages deployment](https://github.com/mhju0/filing-digest/actions/runs/41750549684)
completed successfully. Final publication and follow-up checks are reported
separately in the follow-up PR and release evidence.

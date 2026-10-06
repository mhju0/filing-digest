# Portfolio maintenance closeout · 2026-10-06

v0.5.2 closes the owner-approved claim-accuracy and documentation pass.
API v0.4 and schema v0.3 are unchanged. No ingestion, persistence, retrieval,
guard or authentication semantics changed. The public artifact is the recorded
GitHub Pages walkthrough; the local backend is not deployed publicly.

## Changes

- API description now states citation integrity and recognized financial-expression
  checks, not universal citations or sentence-level semantic proof.
- README gives the three-company, 14-answer/10-retrieval evaluation scope and
  selective extraction limits.
- Added [maintenance policy](../MAINTENANCE.md) and closed active feature work in
  the roadmap. Updated product version metadata consistently to v0.5.2.
- Existing separate contact/privacy page and recordings are retained.

## Evidence boundaries

Release CI exercises offline contracts, real isolated PostgreSQL suites and iOS
simulator tests. iOS UI tests use mock transport; CI is not live-provider quality
proof. The latest inspected live evaluation is October 3, not a fresh release run.
Physical-device, full accessibility and cold-start/live rehearsal remain preparation
items. Independent semantic evaluation is deferred, not claimed complete.

CI and deployed-file verification are recorded with the GitHub release and private
recruiting closeout after successful publication. Historical audits are preserved.

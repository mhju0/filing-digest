# Maintenance release

The owner closed feature development on 2026-10-06 with the v0.5.2 patch release.
API v0.4 and schema v0.3 remain unchanged. The iPhone app and backend are local
and single-user. GitHub Pages serves a recorded walkthrough with no API calls.

## Maintenance scope

Fix reproducible bugs, security issues, inaccurate documentation and broken
links. Necessary dependency updates require checks proportionate to the change.
Embedding/model/retrieval changes need a representative corpus and live evaluation;
fixture CI alone is insufficient. New features, coverage expansion and hosted
operation require an explicit owner decision to reopen development.

## Boundaries and dated evidence

- Structured figures bypass narrative generation. Citation integrity and recognized
  financial-expression checks do not establish sentence-level semantic support.
- Supported prose extraction covers DART DSD and selected SEC Item 1/Item 7.
  Unsupported DART formats are skipped. The 0.42 cutoff is not a confidence score.
- The [September 9 local corpus](COVERAGE.md) had 18 companies and 23 filings.
  A fresh checkout starts empty; the owner's database and keys are not distributed.
- The October 3 Gemini Flash-Lite evaluation passed 24 cases over that corpus;
  questions cover Apple, Microsoft and Samsung Electronics only. Fourteen are
  full-answer cases and ten are retrieval cases. Hit@1/Hit@3/MRR measure retrieval,
  not general financial accuracy. See [evaluation method](../backend/evals/README.md).
- Offline and database CI checks are separate from live provider evaluation.
  iOS UI tests use mock transport. Physical-device/full accessibility and fresh
  live/cold-start readiness are not certified by this patch release.
- There is no API authentication. Keep the backend local; Host/origin checks are
  not user identity or tenant isolation. Hosted multi-user operation is deferred.

## Before a live interview

Follow [DEVELOPMENT.md](DEVELOPMENT.md). Verify backend health, expected ingested
corpus and one real provider response; use the simulator on loopback when practical.
Show a qualitative answer, citation excerpt and original filing, then a structured
figure. Arbitrary questions are not guaranteed to trigger a rehearsed failure state.
A physical iPhone requires deliberately configured trusted private LAN access.
Keep the recorded public walkthrough as a fallback; hide credentials when sharing.

## Deferred preparation

Independent semantic review, questions beyond the three evaluation companies,
physical-device/accessibility coverage and stopped-service startup rehearsal are
recorded preparation opportunities, not completed release evidence. Personal
ownership answers and external profile/credential cleanup belong in private
recruiting records.

## Evidence and licensing

See [release closeout](audits/2026-10-06-maintenance.md), the [roadmap](ROADMAP.md),
and historical qualification in [RELEASE_READINESS.md](RELEASE_READINESS.md).
This is a portfolio source release, not an App Store release or a new hosted API.
The repository remains all rights reserved for portfolio review.

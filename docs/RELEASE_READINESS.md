# Release qualification · 2026-09-09

The owner approved the remediation sequence and company expansion after the
[initial joint audit](https://github.com/mhju0/filing-agent/blob/main/docs/audits/2026-09-09-release-readiness/README.md).
That audit is a historical baseline; this record describes the follow-up.

- Financial prose guards now cover the five audit probes, spelled amounts,
  currency prefixes and hidden format characters. Route regressions confirm
  that blocked prose preserves exact structured figures. The guard recognizes
  expressions; it is not a universal entailment or hallucination detector.
- Compose ports bind to loopback; HTTP host validation rejects unknown hosts.
- A Python 3.11 dependency lock was installed in a fresh environment and the
  working environment. KURE retrieval passed against existing vectors on the
  qualified Torch version. Dependency auditing found no known vulnerabilities.
- Current-schema backup/restore preserved row fingerprints. Legacy v0.2
  migration tests preserve Decimal facts, reject missing identity atomically,
  and require reindexing. Matching PostgreSQL 16 clients are documented.
- Native cited-answer, large-text and accessibility-audit flows passed. Muted
  text contrast was increased after a repeatable native contrast-audit failure.
  Physical-device VoiceOver and every screen at every text size remain unverified.
- [Ten companies were qualified and added](COVERAGE.md), preserving every
  original row. English/Korean retrieval was checked for each. Targeted Solar
  runs exposed both blocked financial prose and language failures; prompt fixes
  and regressions address the observed language cases. Mixed-language inference
  remains a heuristic, and these runs do not replace the historical full eval.

CI runs backend, disposable PostgreSQL and native iOS tests. Review dependency
advisories and public demo/source links monthly. Re-run affected parser, source,
retrieval and answer checks after changes; requalify backups before migrations.
No scheduled ingestion or hosted live inference is added.

The public walkthrough remains an honestly labelled recording of the earlier
corpus. Filing Agent retains its separate three-company snapshot and local-only
inference. Neither project claims a hosted multi-user service.

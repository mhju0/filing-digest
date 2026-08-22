# Live evaluation harness

The eval harness calls the running HTTP API; it does not import service
functions. It is a manual quality check, not a CI gate, because full-tier cases
use the configured Solar account and retrieval cases require an ingested corpus.

From `backend/`, after starting PostgreSQL and uvicorn:

```bash
../.venv/bin/python evals/run_eval.py
../.venv/bin/python evals/run_eval.py --tier retrieval
../.venv/bin/python evals/run_eval.py --tier full
../.venv/bin/python evals/run_eval.py --only ok-apple-business-en
../.venv/bin/python evals/run_eval.py --base-url http://127.0.0.1:8001
```

The command prints a summary and writes the full response set to the ignored
`evals/reports/` directory. It exits nonzero when a case fails.

## How cases are judged

- The checked-in golden set contains 24 cases: 14 full API cases and 10
  retrieval cases.
- Retrieval cases declare the exact canonical filing period and maximum
  acceptable rank. Reports include per-case rank, Hit@1, Hit@3, reciprocal
  rank, and the aggregate Hit@1, Hit@3, and MRR values.
- Qualitative full cases declare one exact narrative state. Numeric full cases
  may allow the two safe states (`ok` and `blocked`) only when they also declare
  a required figure-presence or figure-absence contract. A case cannot accept
  all three narrative states.
- An `ok` response must contain narrative text whose segment citations resolve
  through the response citations to Filing Sources. Cases may additionally pin
  the expected regulator and source filing identifier.
- Wrong-year cases assert that the unavailable metric/period is absent from
  `figures`; numeric cases assert that the expected metric/period is present.

`SearchHit.filing_period` comes from the owning filing row, so retrieval cases
remain valid when a fresh database assigns different filing and chunk UUIDs.
The harness reports `MISSING_FILING_PERIOD` and exits nonzero if a result omits
that value; it cannot silently pass an unverified retrieval case.

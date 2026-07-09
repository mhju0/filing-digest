# Offline eval harness

Calls the live API (httpx), never service functions directly. Start the
backend first: `uvicorn app.main:app --reload --port 8001` (from `backend/`,
with `docker compose up -d db` running).

```
python evals/run_eval.py                    # all cases against http://127.0.0.1:8001
python evals/run_eval.py --tier retrieval    # POST /search cases only
python evals/run_eval.py --tier full         # POST /answer cases only
python evals/run_eval.py --only ok-apple-revenue-fy2025-en
python evals/run_eval.py --base-url http://127.0.0.1:8001
```

Prints a PASS/FAIL summary table (with a `top1_score` column for retrieval
cases) and writes the full run (including every `/search` hit's raw score,
for post-hoc threshold analysis) to `reports/eval_<timestamp>.json`. Exit
code is 1 if any case fails — a local habit check, not a CI gate.

## Judgment philosophy

A live full-tier run showed `narrative_status` is non-deterministic across
identical-shape numeric queries — the same query class landed on
`ok`/`blocked`/`no_results` across runs (7/14 passed,
`reports/eval_20260709T092340Z.json`). Full-tier cases therefore judge state
against an `expected_states` allow-list, not a single value. For
numeric-metric queries the real, deterministic contract isn't the narrative
state at all — it's that `figures` (pulled straight from `financials`, never
through the LLM) contains the queried metric/period row, so those cases also
carry an `expected_figure` check that verifies that directly.

## Updating the filing map

`run_eval.py`'s `FILING_FY_MAP` is a **static** `filing_id -> "FYxxxx"`
mapping used only by retrieval-tier cases' `expected_filing_hint` checks. It
replaced a per-run `POST /answer` call (3 LLM calls, ~10s/company) that used
to derive this at runtime. Because it's static, it does **not** auto-update
when filings are re-ingested or a new fiscal year is added — a case whose
`/search` results include a filing_id absent from the map is flagged
`UNKNOWN_FILING` (not `FAIL`) as a signal to refresh the map.

Apple's 3 filing_ids (`a764e853-...`, `b74ffd49-...`, `d5fe2fb7-...`) are
currently **unmapped** — the report used to build the map only recorded
retrieval tier results, not a `/answer` response with `figures`, so their
individual fiscal years could not be confirmed offline. To resolve, run the
following once `docker compose up -d db` is running:

```
docker exec -i filing-digest-db-1 psql -U filing_digest -d filing_digest -c \
  "SELECT id, period, filed_at, title FROM filings WHERE id IN (
     'a764e853-7275-4d90-bd00-9c50271c5f1a',
     'b74ffd49-c05b-4bbc-a629-8784ea8fa490',
     'd5fe2fb7-6189-4d33-b31d-2d096ee80377'
   );"
```

Then add the resulting `id -> "FY" + period`-derived entries to
`FILING_FY_MAP` in `run_eval.py`.

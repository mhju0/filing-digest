# DART Integration Notes

Concise reference for the response behaviors encoded in
`backend/app/clients/dart.py`. The source code and offline fixtures are the
executable contract; this document explains the non-obvious source quirks.

## 1. Company codes (`corpCode.xml`)

- The endpoint returns a ZIP containing `CORPCODE.xml`.
- Only rows with a nonblank `stock_code` are retained in the local lookup cache.
- XML is parsed from bytes with `defusedxml` so the declaration controls decoding
  without enabling external entities.
- The parsed listed-company subset is cached at
  `backend/data/corpcode_snapshot.json`; it is regenerable and ignored by Git.
- DART error responses are plain XML rather than ZIP files.

## 2. Filing list (`list.json`)

- `rcept_no` is the DART filing natural key.
- `report_nm` may contain trailing padding and is stripped.
- `rcept_dt` is parsed from `YYYYMMDD`; malformed dates become `None` rather than
  aborting a complete result page.
- The client fetches one bounded page. Callers needing a larger history must
  paginate explicitly.
- The ingestion CLI selects the latest annual report (`사업보고서`), including
  correction-prefixed report names.

## 3. Structured financials (`fnlttSinglAcntAll.json`)

This endpoint is the only DART source for displayed financial values.

- Stable `account_id` values map to revenue, operating income, net income,
  attributable net income, basic EPS, and diluted EPS.
- Amounts are parsed as integers and EPS as `Decimal`; ambiguous values are
  skipped rather than coerced.
- Profit/loss accounts can repeat across statement types. The IS row is retained
  to avoid duplicate metric writes.
- Annual current-period and prior-period values become separate financial rows,
  enabling YoY display without extracting numbers from filing prose.
- DART amount rows use KRW; EPS uses a per-share unit.

## 4. Filing documents (`document.xml`)

The endpoint returns a ZIP containing a body document and sometimes attachments.

- The exact `{rcept_no}.xml` member is preferred; attachments are listed for
  diagnostics but are not ingested.
- Documents are decoded as strict UTF-8 first, then CP949. DART xforms documents
  can declare EUC-KR while actually containing UTF-8 bytes.
- DSD and xforms formats are detected from their root markers. Only DSD prose is
  parsed; unsupported xforms documents are skipped.
- DSD is not consistently well-formed XML. The parser repairs narrowly scoped
  literal ampersands, prose angle quotations, and doubled attribute quotes
  before passing content to `defusedxml`.
- Every `TABLE` subtree is excluded. Only paragraph text becomes retrieval
  content, preserving the structured-number boundary.
- Whole-document parsing is used. Very large documents emit a warning.

## 5. Status and error handling

- `000`: success.
- `013`: no data; returned as an empty result.
- Other DART status codes raise `DartApiError` without including the API key.
- Requests use bounded connect/read timeouts.
- `crtfc_key` is carried only in request parameters and is redacted from httpx
  log records by `backend/app/logging_config.py`.

## 6. Persistence mapping

- `companies.dart_corp_code` is the company natural key.
- `filings.rcept_no` is the filing natural key and citation URL input.
- `financials` contains exact structured facts linked to a filing.
- `filing_chunks` contains table-free prose plus receipt/section metadata.
- Company, filing, financial, and chunk writes for one ingestion run share an
  explicit transaction; chunk replacement is delete-then-insert and idempotent.

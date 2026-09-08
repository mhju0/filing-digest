# Qualified local coverage

As of 2026-09-09, the owner's local corpus contains **18 companies, 23 filings,
153 financial records and 2,057 indexed chunks**. A fresh checkout starts empty;
this database is not distributed. Search filters the ingested roster. It does
not discover or ingest arbitrary companies from the app.

The original eight are Samsung Electronics, SK Hynix, NAVER, Hyundai Motor,
Apple, Microsoft, NVIDIA and Tesla. Screenshots and the public walkthrough were
recorded with that earlier corpus. Ten companies were added after isolated
qualification:

| Company | Ticker | Annual filing identity | Financial records | Indexed chunks |
|---|---|---|---:|---:|
| Kia | `000270` | [20260312001224](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260312001224) | 10 | 82 |
| Samsung SDI | `006400` | [20260310002954](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260310002954) | 8 | 96 |
| LG Chem | `051910` | [20260313001195](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260313001195) | 10 | 91 |
| LG Electronics | `066570` | [20260313000662](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260313000662) | 10 | 115 |
| Samsung Biologics | `207940` | [20260312001119](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260312001119) | 4 | 79 |
| Amazon | `AMZN` | [0001018724-26-000004](https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/amzn-20251231.htm) | 5 | 71 |
| Costco | `COST` | [0000909832-25-000101](https://www.sec.gov/Archives/edgar/data/909832/000090983225000101/cost-20250831.htm) | 5 | 49 |
| Alphabet | `GOOGL` | [0001652044-26-000018](https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/goog-20251231.htm) | 5 | 84 |
| Meta | `META` | [0001628280-26-003942](https://www.sec.gov/Archives/edgar/data/1326801/000162828026003942/meta-20251231.htm) | 5 | 95 |
| Walmart | `WMT` | [0000104169-26-000055](https://www.sec.gov/Archives/edgar/data/104169/000010416926000055/wmt-20260131.htm) | 5 | 104 |

All 67 added financial amounts matched a DART consolidated response or SEC
company-facts entry for the filing. The [machine-readable report](coverage-expansion-2026-09-09.json)
records source identities, values, matching labels/tags and bilingual retrieval
counts. This is value reconciliation, not an independent accounting audit or
proof that every metric maps correctly across every issuer. Comparative values
inside an annual report do not establish complete historical-report coverage.

Korean and English business queries returned relevant excerpts for all ten
companies. Separate Solar answer checks exposed wrong-language answers and
financial prose that was correctly blocked. Prompt language handling was
corrected; a blocked narrative still returns structured figures. These targeted
runs are not a new full-evaluation pass or a guarantee of generation quality.

Costco required dash-separated SEC headings; Amazon required retaining
heading-only tables and ignoring inline cross-references as boundaries. LG Chem
required repair of doubled XML attribute quotes. All have parser regressions;
financial tables remain excluded from prose extraction.

Before promotion, the original corpus was dumped with PostgreSQL 16 tools and
restored to a disposable database. Original and restored row fingerprints
matched. After adding ten companies through normal ingestion/indexing, all
original row fingerprints were unchanged. A private backup is retained.

Filing Agent's three-company, separately qualified snapshot is unchanged.
Expanding Digest does not automatically qualify additional Agent answers.

## Repeat the qualification

Use the ingestion commands in the [README](../README.md#ingest-data) against a
new disposable database. Record the exact filing identity: `--ticker` selects the
latest available annual filing and may select a different one later. Compare
stored values with the regulator response for that identity, check complete
indexing, inspect Korean and English search excerpts, and exercise both a cited
answer and figures-only recovery. Back up and restore-test the working corpus
before promotion. Do not overwrite existing filings to reproduce this report.

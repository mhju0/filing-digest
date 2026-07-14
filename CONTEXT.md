# Filing Digest

This context turns corporate filings into structured financial evidence, concise digests, and answers whose claims remain traceable to the original filing.

## Filings and Evidence

**Regulated Company**:
A legal entity identified within one regulatory source. Similar names or tickers do not establish identity across regulatory sources.
_Avoid_: Ticker, issuer record

**Company Identity**:
The combination of a regulatory source and that source's immutable company identifier.
_Avoid_: Company name, ticker

**Corporate Filing**:
An official disclosure published by a Regulated Company through a regulatory filing system.
_Avoid_: Report, document

**Filing Identity**:
The combination of a regulatory source and that source's immutable filing identifier. It identifies the same Corporate Filing across repeated ingestion.
_Avoid_: Database ID, URL, title

**Filing Chunk**:
A specific excerpt from a Corporate Filing that can serve as evidence for a claim.
_Avoid_: Citation, source

**Citation**:
A reference from a specific answer claim to the Filing Chunk that supports it, including a bounded evidence excerpt and its available location anchor. Multiple Citations may resolve to the same Filing Source.
_Avoid_: Filing, source, document

**Filing Source**:
The deduplicated, user-facing representation of a Corporate Filing with a stable link a person can open to inspect the original disclosure.
_Avoid_: Citation, chunk

**Normalized Filing**:
A complete, source-independent, authoritative snapshot of one Corporate Filing, including its identity, Financial Facts, and Filing Chunks. A partially assembled filing is not a Normalized Filing.
_Avoid_: Parsed filing, filing payload

**Financial Fact**:
A reported financial value identified by a Reported Metric and reporting period, preserving its disclosed currency and scale within a Corporate Filing.
_Avoid_: Figure, number, metric

**Reporting Period**:
The temporal scope of a Financial Fact: either an instant at a specific date or a duration between a start and end date. Labels such as quarters and fiscal years are presentations of that scope, not its identity.
_Avoid_: Period label, quarter string

**Reported Metric**:
A canonical financial measure whose value is disclosed directly in a Corporate Filing.
_Avoid_: Derived metric, source taxonomy name

**Derived Metric**:
A financial measure calculated from one or more Financial Facts, with its input facts and reporting period remaining traceable.
_Avoid_: Financial fact, reported metric

**Indexed Filing**:
A Normalized Filing whose Filing Chunks are ready for semantic retrieval. A Normalized Filing may be available for digests and structured facts before it becomes an Indexed Filing.
_Avoid_: Saved filing, embedded filing

**Evidence Integrity Failure**:
A state in which a claim's Citation cannot resolve through its Filing Chunk to an openable Filing Source. The claim is not safe to present as grounded evidence.
_Avoid_: No results, missing citation

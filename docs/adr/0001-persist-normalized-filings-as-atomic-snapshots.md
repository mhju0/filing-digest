# Persist Normalized Filings as Atomic Snapshots

Each source adapter produces a complete Normalized Filing identified by its Filing Identity. The persistence module replaces its Financial Facts and Filing Chunks as one authoritative transaction, rolling back partial writes; indexing begins only after commit and has separate readiness because it is derived and retryable. This was chosen over merging partial records or indexing inside the transaction so removed evidence cannot remain stale and indexing failure cannot discard an otherwise valid filing.

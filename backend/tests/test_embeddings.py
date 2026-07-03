"""Offline tests for the embedding invariants (no model load, no DB, no network).

The heavy pieces -- loading KURE-v1 and writing to Postgres -- are covered by the
live end-to-end verification, not here. What is unit-tested is the two invariants
that must hold regardless of the model: the 1024-dim guard
(:func:`~app.embeddings.kure._finalize_vectors`) and the positional id<->vector
alignment (:func:`~app.embeddings.backfill.align_ids_with_vectors`), both pure.
"""

import pytest

from app.db.models import EMBEDDING_DIM
from app.embeddings.backfill import _batched, align_ids_with_vectors
from app.embeddings.kure import _finalize_vectors

# -- _finalize_vectors: dimension invariant -----------------------------------


def test_finalize_vectors_accepts_correct_dim_and_returns_float_lists() -> None:
    rows = [[0.1] * EMBEDDING_DIM, [0.2] * EMBEDDING_DIM]
    out = _finalize_vectors(rows)
    assert len(out) == 2
    assert all(len(r) == EMBEDDING_DIM for r in out)
    assert all(isinstance(x, float) for r in out for x in r)


def test_finalize_vectors_raises_on_wrong_dim() -> None:
    # A model that ever returns != 1024 must be rejected, not silently written.
    with pytest.raises(ValueError, match="dim mismatch"):
        _finalize_vectors([[0.0] * (EMBEDDING_DIM - 1)])


def test_finalize_vectors_reports_offending_row_index() -> None:
    rows = [[0.0] * EMBEDDING_DIM, [0.0] * (EMBEDDING_DIM + 2)]
    with pytest.raises(ValueError, match="row 1"):
        _finalize_vectors(rows)


def test_finalize_vectors_empty_is_empty() -> None:
    assert _finalize_vectors([]) == []


# -- align_ids_with_vectors: positional alignment invariant -------------------


def test_align_ids_with_vectors_pairs_positionally() -> None:
    ids = ["a", "b", "c"]
    vecs = [[1.0], [2.0], [3.0]]
    assert align_ids_with_vectors(ids, vecs) == [("a", [1.0]), ("b", [2.0]), ("c", [3.0])]


def test_align_ids_with_vectors_raises_on_count_mismatch() -> None:
    # Fewer vectors than ids would otherwise zip-to-shorter and misassign silently.
    with pytest.raises(ValueError, match="count mismatch"):
        align_ids_with_vectors(["a", "b", "c"], [[1.0], [2.0]])


def test_align_ids_with_vectors_empty() -> None:
    assert align_ids_with_vectors([], []) == []


# -- _batched: order-preserving batching --------------------------------------


def test_batched_splits_in_order() -> None:
    assert _batched([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_batched_single_batch_when_size_exceeds_len() -> None:
    assert _batched([1, 2, 3], 10) == [[1, 2, 3]]


def test_batched_empty() -> None:
    assert _batched([], 4) == []


def test_batched_rejects_nonpositive_size() -> None:
    with pytest.raises(ValueError):
        _batched([1, 2], 0)

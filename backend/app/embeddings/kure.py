"""KURE-v1 (nlpai-lab/KURE-v1) loader + pure text-embedding interface.

The one place the embedding *model* lives. Everything above it (the backfill
orchestrator, any future query-time encoder) calls :func:`embed_texts` and never
touches sentence-transformers directly.

Design (settled in the Step A smoke, do not relitigate):

- **1024-dim, cross-lingual (KO/EN) space.** Matches ``vector(1024)`` in
  init.sql and ``EMBEDDING_DIM`` in app.db.models. A drift is a hard error
  (:func:`_finalize_vectors`), never a silent truncation into the column.
- **normalize_embeddings=True is fixed** as an invariant, not a caller choice:
  the stored vectors must be unit-norm so a cosine (``<=>``) search over them is
  correct. (KURE-v1's default encode already normalizes; we pin it anyway.)
- **device=cpu**: deployment-representative (the backfill runs in a Linux
  container with no MPS/CUDA), and the 99-chunk workload is trivially fast there.

The model is heavy to load (torch graph + weights, several seconds cold), so it
is lazy-loaded once and cached for the process lifetime -- never re-loaded per
call/batch. Importing this module stays cheap and network-free (the torch /
sentence-transformers import happens inside :func:`_load_model`), so the offline
test suite can import app code with no model download.
"""

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Sequence

from app.config import get_settings
from app.db.models import EMBEDDING_DIM

if TYPE_CHECKING:  # import only for type hints; never at runtime import time
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# CPU is the deployment-representative device (see module docstring).
_DEVICE = "cpu"

# sentence-transformers / transformers / huggingface log verbosely at load time
# (model-card banners, progress bars). Raise their threshold once, on import, so
# our own logging stays the signal. (Our code logs via the logging module only.)
for _noisy in ("sentence_transformers", "transformers", "huggingface_hub"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


def _hf_cache_root() -> Path:
    """Resolve the HF Hub cache root the same way huggingface_hub does.

    Reimplemented from env vars rather than imported, because huggingface_hub
    must not be imported until *after* HF_HUB_OFFLINE is decided (see
    :func:`_configure_offline_mode`) -- it reads that env var into a
    module-level constant at import time.
    """
    if cache := os.environ.get("HF_HUB_CACHE"):
        return Path(cache)
    home = os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
    return Path(home) / "hub"


def _model_snapshot_dir(model_name: str, cache_root: Path) -> Path:
    """The on-disk snapshots dir huggingface_hub would use for ``model_name``."""
    return cache_root / f"models--{model_name.replace('/', '--')}" / "snapshots"


def _is_model_cached(model_name: str, cache_root: Path) -> bool:
    """True if a complete local snapshot of ``model_name`` already exists.

    A snapshot revision dir with no files (partial/interrupted download)
    does not count -- treating that as cached would force an offline load
    that then fails to find the actual weight files.

    TODO: this only checks that the revision dir is non-empty, not that the
    required weight file (e.g. model.safetensors) is present -- a partially
    corrupted cache could still pass. Tighten if that's ever observed.
    """
    snapshots = _model_snapshot_dir(model_name, cache_root)
    if not snapshots.is_dir():
        return False
    return any(
        revision.is_dir() and any(revision.iterdir()) for revision in snapshots.iterdir()
    )


def _configure_offline_mode() -> None:
    """Set HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE before the first HF Hub import.

    Must run before ``from sentence_transformers import SentenceTransformer``:
    huggingface_hub reads these env vars into module-level constants at
    import time, so setting them afterwards has no effect. Uses
    ``setdefault`` so an operator-supplied HF_HUB_OFFLINE always wins.

    When the model is not yet cached (first run on a clean machine/CI), both
    are left unset so the initial download can still happen --
    ``embedding_offline_first=False`` skips this check entirely for
    environments that always want a network freshness check.
    """
    settings = get_settings()
    if not settings.embedding_offline_first:
        return
    if _is_model_cached(settings.embedding_model, _hf_cache_root()):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        logger.info("embedding model cache found locally; HF Hub offline mode enabled")
    else:
        logger.info(
            "embedding model not cached locally; allowing network for initial download"
        )


@lru_cache(maxsize=1)
def _load_model() -> "SentenceTransformer":
    """Load KURE-v1 once (cold load is seconds) and cache it for the process.

    The ``sentence_transformers`` import is deliberately inside the function:
    importing this module must not pull in torch or reach the network, so tests
    and lightweight callers stay fast and offline.
    """
    _configure_offline_mode()
    from sentence_transformers import SentenceTransformer

    model_name = get_settings().embedding_model
    logger.info("loading embedding model %s on %s (one-time)", model_name, _DEVICE)
    model = SentenceTransformer(model_name, device=_DEVICE)
    logger.info(
        "embedding model loaded (max_seq_length=%s)",
        getattr(model, "max_seq_length", None),
    )
    return model


def _finalize_vectors(
    vectors: Iterable[Sequence[float]], expected_dim: int = EMBEDDING_DIM
) -> list[list[float]]:
    """Validate embedding dimension and convert to ``list[list[float]]`` (pure).

    ``vectors`` is a 2-D array (numpy) or a sequence of sequences. Every row must
    have exactly ``expected_dim`` components; a mismatch raises ``ValueError``
    rather than letting a wrong-width vector reach the ``vector(1024)`` column,
    where it would fail obscurely (or worse, if dims ever changed, silently). The
    dimension invariant is split out here so it is unit-testable without loading
    the multi-second model.
    """
    result = [[float(x) for x in row] for row in vectors]
    for i, row in enumerate(result):
        if len(row) != expected_dim:
            raise ValueError(
                f"embedding dim mismatch at row {i}: got {len(row)}, "
                f"expected {expected_dim}"
            )
    return result


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed ``texts`` into normalized 1024-dim vectors, order-preserving.

    Returns one vector per input text, in the same order (positional 1:1). Empty
    input returns ``[]`` without loading the model. ``normalize_embeddings=True``
    is fixed (unit-norm invariant); the returned dimension is guarded to
    :data:`~app.db.models.EMBEDDING_DIM`.
    """
    if not texts:
        return []
    model = _load_model()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return _finalize_vectors(vectors)

"""Central logging setup + secret masking.

The DART API key travels as a ``crtfc_key`` query-string parameter. Our own
``dart.py`` logs already print ``crtfc_key=***``, but third-party libraries do
not know the key is a secret: httpx logs every request at INFO level as
``HTTP Request: GET https://...?crtfc_key=<real key>...`` -- the full URL,
which leaks the key past our own masking.

Fix (preferred over silencing httpx): a reusable ``logging.Filter`` that masks
the ``crtfc_key`` value in *any* log record's final message. The filter is
attached at the **source logger** (``httpx``/``httpcore``) -- i.e. the logger
that actually emits the leaking record. A filter on a logger runs inside
``Logger.handle`` *before* ``callHandlers``, so it mutates the record in place
once and every handler that later sees the record (root handlers, and crucially
pytest's dynamically-added live-log handler ``_LiveLoggingStreamHandler``) emits
the already-redacted text. httpx keeps logging (useful for debugging); only the
secret value is redacted.

Why not the root logger's *handlers* alone (the previous approach): a handler
filter only covers the specific handler instances present when it was attached,
so any handler added later -- pytest live-log among them -- bypasses it. And a
filter on the root *logger* would not help either: ancestor-logger filters do
NOT run for records propagated up from a child logger such as ``httpx`` (only
the originating logger's own filters run). Masking at the source logger is the
one point that covers every downstream output path. Root-handler attachment is
kept as defence-in-depth.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Loggers known to emit the DART API key in cleartext (the full request URL is
# logged at INFO). Masking is installed on these at logger level so the record
# itself is redacted before any handler -- including pytest's live-log handler --
# renders it. httpcore is included defensively (it can log connection URLs too).
_SECRET_LEAKING_LOGGERS = ("httpx", "httpcore")

# Match ``crtfc_key=<value>`` and capture only the value. The value is URL-safe
# and ends at the next ``&`` (further param), ``#`` (fragment), whitespace, or a
# surrounding quote. Case-insensitive so an oddly-cased key name is still caught.
_CRTFC_KEY_RE = re.compile(r"(crtfc_key=)([^&\s#\"']+)", re.IGNORECASE)

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s - %(message)s"


def mask_crtfc_key(text: str) -> str:
    """Replace every ``crtfc_key=<value>`` in ``text`` with ``crtfc_key=***``.

    Other query parameters are left untouched. Idempotent -- re-masking an
    already-masked string is a no-op. Pure (no logging state) so it is unit
    tested directly with a fake key.
    """
    return _CRTFC_KEY_RE.sub(r"\1***", text)


class CrtfcKeyMaskingFilter(logging.Filter):
    """Logging filter that redacts the DART API key from a record's message.

    Works regardless of whether the key sits in the format string or in an
    argument (e.g. httpx passes the URL as a ``%s`` arg): it masks the *rendered*
    message via :func:`mask_crtfc_key`, then pins the result onto the record so
    downstream formatters emit the redacted text. Always returns ``True`` -- it
    redacts, it never drops records. Reusable: attach to any logger or handler.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 -- never let masking break logging
            return True
        masked = mask_crtfc_key(message)
        if masked != message:
            # Replace msg with the already-rendered masked text and clear args so
            # the handler's formatter does not try to re-interpolate.
            record.msg = masked
            record.args = None
        return True


def install_source_logger_masking_filter() -> None:
    """Attach :class:`CrtfcKeyMaskingFilter` to each secret-leaking source logger.

    This is the primary defence. A filter on the *originating* logger runs in
    ``Logger.handle`` before the record is dispatched to any handler, so it
    redacts the record in place once and every downstream handler -- root
    handlers *and* pytest's dynamically-added live-log handler -- emits masked
    text. Idempotent: skips loggers that already carry the filter, so it is safe
    to call on every app start or client construction.
    """
    for name in _SECRET_LEAKING_LOGGERS:
        src = logging.getLogger(name)
        if not any(isinstance(f, CrtfcKeyMaskingFilter) for f in src.filters):
            src.addFilter(CrtfcKeyMaskingFilter())


def install_secret_masking_filter() -> None:
    """Attach :class:`CrtfcKeyMaskingFilter` to every root-logger handler.

    Defence-in-depth on top of :func:`install_source_logger_masking_filter`:
    covers records that reach the root handlers from loggers not in
    ``_SECRET_LEAKING_LOGGERS``. Note this cannot cover handlers added after the
    call (e.g. pytest live-log) -- that gap is exactly why the source-logger
    filter above is the primary mechanism. Idempotent -- skips handlers that
    already carry the filter, so it is safe to call more than once.
    """
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, CrtfcKeyMaskingFilter) for f in handler.filters):
            handler.addFilter(CrtfcKeyMaskingFilter())


def configure_logging(level: int = logging.INFO) -> None:
    """Initialise application logging and install the secret-masking filters.

    Single logging entry point for the app: sets up the root handler via
    ``basicConfig`` (a no-op if handlers already exist), then installs the
    ``crtfc_key`` masking filter at the source loggers (primary) and on the root
    handlers (defence-in-depth) so no secret can leak through httpx's or any
    other library's URL logging, on any output path. Idempotent.
    """
    logging.basicConfig(level=level, format=_LOG_FORMAT)
    install_source_logger_masking_filter()
    install_secret_masking_filter()

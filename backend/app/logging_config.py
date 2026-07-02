"""Central logging setup + secret masking.

The DART API key travels as a ``crtfc_key`` query-string parameter. Our own
``dart.py`` logs already print ``crtfc_key=***``, but third-party libraries do
not know the key is a secret: httpx logs every request at INFO level as
``HTTP Request: GET https://...?crtfc_key=<real key>...`` -- the full URL,
which leaks the key past our own masking.

Fix (preferred over silencing httpx): a reusable ``logging.Filter`` that masks
the ``crtfc_key`` value in *any* log record's final message. It is attached to
the root logger's handlers, so it fires for every record that reaches them --
httpx's request logs, our own logs, and anything else that happens to log a URL
containing the key. httpx keeps logging (useful for debugging); only the secret
value is redacted.
"""

import logging
import re

logger = logging.getLogger(__name__)

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


def install_secret_masking_filter() -> None:
    """Attach :class:`CrtfcKeyMaskingFilter` to every root-logger handler.

    Handler-level attachment (not logger-level) is deliberate: a filter on the
    root *logger* would not see records propagated from child loggers such as
    ``httpx``, whereas every propagated record still passes through the root
    *handlers*. Idempotent -- skips handlers that already carry the filter, so
    it is safe to call more than once.
    """
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, CrtfcKeyMaskingFilter) for f in handler.filters):
            handler.addFilter(CrtfcKeyMaskingFilter())


def configure_logging(level: int = logging.INFO) -> None:
    """Initialise application logging and install the secret-masking filter.

    Single logging entry point for the app: sets up the root handler via
    ``basicConfig`` (a no-op if handlers already exist) and then installs the
    ``crtfc_key`` masking filter so no secret can leak through httpx's or any
    other library's URL logging.
    """
    logging.basicConfig(level=level, format=_LOG_FORMAT)
    install_secret_masking_filter()

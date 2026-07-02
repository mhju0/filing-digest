"""Offline tests for crtfc_key (DART API key) log masking.

Guards the fix in app/logging_config.py: httpx logs full request URLs at INFO,
which would otherwise leak the API key that our own dart.py logs already mask.
No network and no real key -- a fake ``SECRET123`` value stands in for the key.
"""

import logging

from app.logging_config import (
    CrtfcKeyMaskingFilter,
    configure_logging,
    install_secret_masking_filter,
    mask_crtfc_key,
)

# Fake key -- never a real crtfc_key. Distinctive so leaks are easy to assert on.
_FAKE_KEY = "SECRET123"
_SAMPLE_URL = (
    f"https://opendart.fss.or.kr/api/list.json?crtfc_key={_FAKE_KEY}&corp_code=x"
)


def test_mask_crtfc_key_redacts_value_and_keeps_other_params() -> None:
    masked = mask_crtfc_key(_SAMPLE_URL)
    assert "crtfc_key=***" in masked
    assert _FAKE_KEY not in masked
    # Everything that is not the secret must survive untouched.
    assert "corp_code=x" in masked
    assert masked.startswith("https://opendart.fss.or.kr/api/list.json?")


def test_mask_crtfc_key_value_first_in_query() -> None:
    # crtfc_key as the trailing param (no following &) is still masked fully.
    text = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={_FAKE_KEY}"
    masked = mask_crtfc_key(text)
    assert masked.endswith("crtfc_key=***")
    assert _FAKE_KEY not in masked


def test_mask_crtfc_key_is_idempotent() -> None:
    once = mask_crtfc_key(_SAMPLE_URL)
    assert mask_crtfc_key(once) == once


def test_mask_crtfc_key_noop_without_key() -> None:
    text = "https://opendart.fss.or.kr/api/list.json?corp_code=x&bgn_de=20230101"
    assert mask_crtfc_key(text) == text


def test_filter_masks_value_passed_as_log_arg() -> None:
    """Mimic httpx: the URL is a ``%s`` arg, not part of the format string."""
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="HTTP Request: %s %s",
        args=("GET", _SAMPLE_URL),
        exc_info=None,
    )
    assert CrtfcKeyMaskingFilter().filter(record) is True
    message = record.getMessage()
    assert _FAKE_KEY not in message
    assert "crtfc_key=***" in message
    assert "corp_code=x" in message


def test_install_is_idempotent_on_root_handlers() -> None:
    configure_logging()
    install_secret_masking_filter()  # second call must not double-attach
    root = logging.getLogger()
    for handler in root.handlers:
        count = sum(
            isinstance(f, CrtfcKeyMaskingFilter) for f in handler.filters
        )
        assert count <= 1
    # At least one root handler carries the filter after configuration.
    assert any(
        any(isinstance(f, CrtfcKeyMaskingFilter) for f in h.filters)
        for h in root.handlers
    )


def test_caplog_sees_only_masked_httpx_style_log(caplog) -> None:
    """End-to-end through a handler: the captured record is already masked."""
    # caplog uses its own handler; attach the real filter to it to exercise the
    # same handler-level path production uses on the root handler.
    caplog.handler.addFilter(CrtfcKeyMaskingFilter())
    test_logger = logging.getLogger("httpx")
    with caplog.at_level(logging.INFO, logger="httpx"):
        test_logger.info("HTTP Request: %s %s", "GET", _SAMPLE_URL)
    assert _FAKE_KEY not in caplog.text
    assert "crtfc_key=***" in caplog.text

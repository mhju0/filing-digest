"""Offline tests for the deterministic number-in-narrative guard.

Pure unit tests: no Solar, no network, no DB. Answer objects are assembled
directly. Exercises find_number_violations/assert_number_free against the
suffix-anchored financial-number blocklist described in
:mod:`app.llm.number_guard`.
"""

import pytest

from app.llm.answer import Answer, AnswerSegment
from app.llm.number_guard import (
    NumberInNarrativeError,
    assert_number_free,
    find_number_violations,
)


def _answer(*segments: tuple[str, list[str]]) -> Answer:
    return Answer(
        answer_segments=[
            AnswerSegment(text=text, citations=citations) for text, citations in segments
        ]
    )


# --- BLOCK: financial numbers must be caught -------------------------------

@pytest.mark.parametrize(
    "token",
    [
        "258조 9,355억원",  # B1 currency, mixed magnitude + grouping + space
        "8조원",
        "16만원",
        "897,514백만원",
        "9.8조원",
        "14.3%",  # B2 percent
        "45%",
        "1%",
        "728%",
        "2배",  # B3 multiple
        "$5",  # B4 currency ($)
        "$5.2 billion",
        "$1,234,567",
        "$5B",
        "5 billion dollars",  # B5 currency (word/code)
        "3.2 million USD",
        "5 KRW",
        "10 EUR",
        "2x",  # B6 multiple (x)
        "2.5x",
        "3 times",  # B7 multiple (times)
    ],
)
def test_block_financial_number_is_flagged(token: str):
    violations = find_number_violations(_answer((token, ["c1"])))
    assert len(violations) == 1
    assert violations[0].segment_index == 0
    assert violations[0].token == token


# --- EXEMPT: innocent numeric tokens must NOT be caught --------------------

@pytest.mark.parametrize(
    "text",
    [
        "2023년",
        "2024~2026년",
        "제55기",
        "제27조",
        "제1항",
        "제1012호",
        "1월 13일",
        "232개",
        "11인",
        "2회",
        "1. 개요",  # line-head enumerator
        "031-200-1114",
        "삼성로 129",
        "Item 7",
        "10-K",
        "Q4",
        "the company grew one time this year",  # idiom, no digit before "time(s)"
        "see [1] for details",
    ],
)
def test_exempt_innocent_number_is_clean(text: str):
    assert find_number_violations(_answer((text, ["c1"]))) == []


# --- TRAP regressions ------------------------------------------------------

def test_trap_article_marker_not_currency():
    # ``제27조`` collides on 조 but has no 원 anchor -> must not fire B1.
    assert find_number_violations(_answer(("제27조에 따라 공시한다.", ["c1"]))) == []


def test_trap_word_containing_won_not_currency():
    # "직원" ends in 원, but no digit run reaches it -> must not false-positive.
    assert find_number_violations(_answer(("5명의 직원을 채용했다.", ["c1"]))) == []


@pytest.mark.parametrize(
    "text",
    [
        "제3조 원칙",
        "27조 원본",
        "8조 원가",
        "제55조 원화",
    ],
)
def test_trap_magnitude_run_then_separate_won_word_not_currency(text: str):
    # A magnitude run (조/억/만/천/백) followed by whitespace and a DIFFERENT word
    # that happens to start with 원 (원칙/원본/원가/원화) must not match -- only a
    # 원 immediately adjoining the digit run (no intervening space) counts.
    assert find_number_violations(_answer((text, ["c1"]))) == []


def test_single_digit_currency_still_flagged():
    violations = find_number_violations(_answer(("이체 수수료는 5원이다.", ["c1"])))
    assert len(violations) == 1
    assert violations[0].token == "5원"


# --- NFKC normalization ----------------------------------------------------

def test_fullwidth_digit_normalized_then_flagged():
    # Full-width ８ collapses to 8 under NFKC; token is reported in normalized form.
    violations = find_number_violations(_answer(("８조원 규모다.", ["c1"])))
    assert len(violations) == 1
    assert violations[0].token == "8조원"


# --- segment_index precision ----------------------------------------------

def test_only_polluted_segment_is_named():
    answer = _answer(
        ("전기 대비 실적이 개선되었다.", ["c1"]),
        ("영업이익은 8조원 수준이다.", ["c2"]),
        ("향후 전망은 긍정적이다.", ["c3"]),
    )
    violations = find_number_violations(answer)
    assert len(violations) == 1
    assert violations[0].segment_index == 1
    assert violations[0].token == "8조원"


def test_multiple_numbers_in_one_segment_ordered_by_position():
    answer = _answer(("매출 258조 9,355억원, 성장률 14.3%, 2배 증가.", ["c1"]))
    violations = find_number_violations(answer)
    tokens = [v.token for v in violations]
    assert tokens == ["258조 9,355억원", "14.3%", "2배"]
    # ordered by start position within the segment
    assert [v.span[0] for v in violations] == sorted(v.span[0] for v in violations)


# --- English patterns (B4-B7) ----------------------------------------------

def test_mixed_ko_en_sentence_with_dollar_amount_blocks():
    answer = _answer(("올해 매출은 $5.2 billion 수준으로 추정된다.", ["c1"]))
    violations = find_number_violations(answer)
    assert len(violations) == 1
    assert violations[0].token == "$5.2 billion"


def test_korean_patterns_still_block_alongside_english_rules():
    # Regression: adding English patterns must not disturb existing KO rules.
    answer = _answer(("매출 8조원, 성장률 14.3%, 2배 증가.", ["c1"]))
    tokens = [v.token for v in find_number_violations(answer)]
    assert tokens == ["8조원", "14.3%", "2배"]


def test_english_multiplier_and_currency_together_ordered_by_position():
    answer = _answer(
        ("Revenue grew 3 times to $5.2 billion, or 3.2 million USD per store.", ["c1"]),
    )
    violations = find_number_violations(answer)
    tokens = [v.token for v in violations]
    assert "3 times" in tokens
    assert "$5.2 billion" in tokens
    assert "3.2 million USD" in tokens
    assert [v.span[0] for v in violations] == sorted(v.span[0] for v in violations)


# --- assert_number_free ----------------------------------------------------

def test_assert_number_free_passes_when_clean():
    answer = _answer(("2023년 제55기 실적이 개선되었다.", ["c1"]))
    assert assert_number_free(answer) is None


def test_assert_number_free_raises_on_dirty_answer():
    answer = _answer(("매출 258조 9,355억원, 성장률 14.3%, 2배 증가.", ["c1"]))
    with pytest.raises(NumberInNarrativeError) as exc_info:
        assert_number_free(answer)
    tokens = [v.token for v in exc_info.value.violations]
    assert "258조 9,355억원" in tokens
    assert "14.3%" in tokens
    assert "2배" in tokens

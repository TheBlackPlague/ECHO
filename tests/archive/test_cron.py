from __future__ import annotations

from datetime import datetime

import pytest

import echo.archive.cron as cron_module
from echo.archive.cron import CronExpression, seconds_until_next_minute


@pytest.mark.parametrize(
    ("expression", "when", "expected"),
    [
        ("* * * * *", datetime(2026, 9, 1, 12, 34), True),
        ("15 3 1 1 *", datetime(2026, 1, 1, 3, 15), True),
        ("15 3 1 1 *", datetime(2026, 1, 1, 3, 16), False),
        ("*/15 8-10 * * *", datetime(2026, 6, 2, 9, 30), True),
        ("1,5,9 * * * *", datetime(2026, 6, 2, 9, 5), True),
        ("5/10 * * * *", datetime(2026, 6, 2, 9, 55), True),
        ("5/10 * * * *", datetime(2026, 6, 2, 9, 4), False),
        ("0 0 * * 0", datetime(2026, 9, 6, 0, 0), True),
        ("0 0 * * 7", datetime(2026, 9, 6, 0, 0), True),
        # When both day fields are restricted, standard cron uses OR semantics.
        ("0 0 10 * 1", datetime(2026, 9, 7, 0, 0), True),
        ("0 0 10 * 1", datetime(2026, 9, 10, 0, 0), True),
        ("0 0 10 * 1", datetime(2026, 9, 8, 0, 0), False),
        ("0 0 * * 1", datetime(2026, 9, 7, 0, 0), True),
        ("0 0 7 * *", datetime(2026, 9, 7, 0, 0), True),
        ("0 0 * 2 *", datetime(2026, 9, 7, 0, 0), False),
    ],
)
def test_parse_and_match(expression: str, when: datetime, expected: bool) -> None:
    assert CronExpression.parse(expression).matches(when) is expected


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "* * * *",
        "* * * * * *",
        "60 * * * *",
        "* 24 * * *",
        "* * 0 * *",
        "* * * 13 *",
        "* * * * 8",
        "-1 * * * *",
        "10-5 * * * *",
        "1- * * * *",
        "1-2-3 * * * *",
        "*/0 * * * *",
        "*/-1 * * * *",
        "*/x * * * *",
        "*//2 * * * *",
        "1,,2 * * * *",
        "x * * * *",
    ],
)
def test_invalid_expressions_raise(expression: str) -> None:
    with pytest.raises(ValueError):
        CronExpression.parse(expression)


def test_seconds_until_next_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return cls(2026, 9, 2, 12, 34, 45, 250_000).astimezone()

    monkeypatch.setattr(cron_module, "datetime", FixedDateTime)

    assert seconds_until_next_minute() == pytest.approx(14.75)

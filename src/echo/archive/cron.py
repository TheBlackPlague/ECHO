from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class _CronField:
    values: frozenset[int]
    wildcard: bool

    def matches(self, value: int) -> bool:
        return value in self.values


@dataclass(frozen=True, slots=True)
class CronExpression:
    minute: _CronField
    hour: _CronField
    day_of_month: _CronField
    month: _CronField
    day_of_week: _CronField

    @classmethod
    def parse(cls, expression: str) -> CronExpression:
        fields = expression.split()

        if len(fields) != 5: raise ValueError("Cron expression must contain exactly five fields")

        minute, hour, day_of_month, month, day_of_week = fields

        return cls(
            minute=_parse_field(minute, 0, 59),
            hour=_parse_field(hour, 0, 23),
            day_of_month=_parse_field(day_of_month, 1, 31),
            month=_parse_field(month, 1, 12),
            day_of_week=_parse_field(
                day_of_week,
                0,
                7,
                normalize=lambda value: 0 if value == 7 else value,
            ),
        )

    def matches(self, when: datetime) -> bool:
        if not self.minute.matches(when.minute): return False
        if not self.hour.matches(when.hour): return False
        if not self.month.matches(when.month): return False

        day_of_month_matches = self.day_of_month.matches(when.day)
        cron_day_of_week = (when.weekday() + 1) % 7
        day_of_week_matches = self.day_of_week.matches(cron_day_of_week)

        if self.day_of_month.wildcard and self.day_of_week.wildcard: return True
        if self.day_of_month.wildcard: return day_of_week_matches
        if self.day_of_week.wildcard: return day_of_month_matches

        return day_of_month_matches or day_of_week_matches


def seconds_until_next_minute() -> float:
    now = datetime.now().astimezone()
    next_minute = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    return max((next_minute - now).total_seconds(), 0.0)


def _parse_field(
    field: str,
    minimum: int,
    maximum: int,
    *,
    normalize: Callable[[int], int] | None = None,
) -> _CronField:
    normalizer = normalize or (lambda value: value)
    values: set[int] = set()

    for token in field.split(","):
        token = token.strip()
        if not token: raise ValueError("Cron field contains an empty item")

        base, has_step, step_text = token.partition("/")
        step = 1

        if has_step:
            if not step_text or "/" in step_text: raise ValueError(f"Invalid cron step: {token}")

            try:
                step = int(step_text)

            except ValueError as exc:
                raise ValueError(f"Invalid cron step: {step_text}") from exc

            if step < 1: raise ValueError("Cron step must be greater than zero")

        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            start_text, separator, end_text = base.partition("-")

            if not separator or not start_text or not end_text or "-" in end_text:
                raise ValueError(f"Invalid cron range: {base}")

            start = _parse_number(start_text)
            end = _parse_number(end_text)
        else:
            start = _parse_number(base)
            end = maximum if has_step else start

        if not minimum <= start <= maximum: raise ValueError(f"Cron value is outside {minimum}-{maximum}: {start}")
        if not minimum <= end <= maximum: raise ValueError(f"Cron value is outside {minimum}-{maximum}: {end}")
        if end < start: raise ValueError(f"Cron range must be ascending: {base}")

        values.update(normalizer(value) for value in range(start, end + 1, step))

    return _CronField(values=frozenset(values), wildcard=field == "*")


def _parse_number(value: str) -> int:
    try:
        return int(value)

    except ValueError as exc:
        raise ValueError(f"Invalid cron value: {value}") from exc

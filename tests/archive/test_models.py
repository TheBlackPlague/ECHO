from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, UTC

import echo.archive.models as models_module
from echo.archive.models import RunState
from .conftest import make_run


def test_terminal_states() -> None:
    assert {state for state in RunState if state.terminal} == {
        RunState.SUCCEEDED,
        RunState.FAILED,
        RunState.CANCELLED,
        RunState.INTERRUPTED,
    }


def test_duration_is_none_until_started(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert make_run(tmp_path).duration_seconds is None


def test_duration_uses_finished_at_and_never_goes_negative(tmp_path) -> None:  # type: ignore[no-untyped-def]
    started = datetime(2026, 9, 2, tzinfo=UTC)
    run = replace(
        make_run(tmp_path),
        started_at=started,
        finished_at=started + timedelta(seconds=2.5),
    )
    assert run.duration_seconds == 2.5
    assert replace(run, finished_at=started - timedelta(seconds=1)).duration_seconds == 0.0


def test_active_duration_uses_current_time(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    started = datetime(2026, 9, 2, tzinfo=UTC)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return cls(2026, 9, 2, 0, 0, 3, tzinfo=tz)

    monkeypatch.setattr(models_module, "datetime", FixedDateTime)
    assert replace(make_run(tmp_path), started_at=started).duration_seconds == 3.0

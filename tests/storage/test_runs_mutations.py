from __future__ import annotations

from dataclasses import replace
from datetime import datetime, UTC

import pytest
import pytest_asyncio

from echo.archive.models import RunState
from echo.core.config import StorageConfig
from echo.storage.database import Database
from echo.storage.errors import InvalidRunTransitionError, RunNotFoundError
from echo.storage.runs import RunRepository
from .storage_test_helpers import make_run


@pytest_asyncio.fixture
async def repository(tmp_path) -> RunRepository:
    config = StorageConfig(database=tmp_path / "echo.db", retained_output_bytes=8)
    database = Database(config)
    await database.initialize()
    return RunRepository(config, database)


@pytest.mark.asyncio
async def test_transition_updates_requested_fields(repository: RunRepository) -> None:
    queued = make_run(stdout="existing output", stderr="existing error")
    await repository.create(queued)
    started = datetime(2026, 4, 5, 6, 7, tzinfo=UTC)

    running = await repository.transition(
        queued.id,
        expected=(RunState.QUEUED, RunState.INTERRUPTED),
        state=RunState.RUNNING,
        started_at=started,
        return_code=0,
        error="message",
        stdout="short",
        stderr="tiny",
    )

    assert running == replace(
        queued,
        state=RunState.RUNNING,
        started_at=started,
        return_code=0,
        error="message",
        stdout="short",
        stderr="tiny",
    )


@pytest.mark.asyncio
async def test_transition_omitted_values_do_not_clear_existing_fields(
    repository: RunRepository,
) -> None:
    running = make_run(
        state=RunState.RUNNING,
        started_at=datetime(2026, 4, 5, tzinfo=UTC),
        stdout="kept",
        error="kept",
    )
    await repository.create(running)

    result = await repository.transition(
        running.id,
        expected=(RunState.RUNNING,),
        state=RunState.SUCCEEDED,
    )

    assert result.stdout == "kept"
    assert result.error == "kept"
    assert result.started_at == running.started_at


@pytest.mark.asyncio
async def test_transition_truncates_output_by_utf8_bytes(repository: RunRepository) -> None:
    await repository.create(make_run())
    output = "prefix-☃-final-output"

    result = await repository.transition(
        "run-1",
        expected=(RunState.QUEUED,),
        state=RunState.RUNNING,
        stdout=output,
    )

    assert result.stdout.startswith("[output truncated; final 8 bytes retained]\n")
    assert result.stdout.endswith("l-output")


@pytest.mark.asyncio
async def test_transition_rejects_empty_expected_states(repository: RunRepository) -> None:
    await repository.create(make_run())

    with pytest.raises(ValueError, match="At least one expected state"):
        await repository.transition("run-1", expected=(), state=RunState.RUNNING)

    assert (await repository.get("run-1")).state is RunState.QUEUED


@pytest.mark.asyncio
async def test_transition_rejects_wrong_current_state_without_mutation(
    repository: RunRepository,
) -> None:
    await repository.create(make_run())

    with pytest.raises(InvalidRunTransitionError, match="from queued to succeeded"):
        await repository.transition(
            "run-1",
            expected=(RunState.RUNNING,),
            state=RunState.SUCCEEDED,
            error="must not persist",
        )

    persisted = await repository.get("run-1")
    assert persisted.state is RunState.QUEUED
    assert persisted.error is None


@pytest.mark.asyncio
async def test_transition_missing_run_raises(repository: RunRepository) -> None:
    with pytest.raises(RunNotFoundError, match="Archive run not found: absent"):
        await repository.transition(
            "absent",
            expected=(RunState.QUEUED,),
            state=RunState.RUNNING,
        )


@pytest.mark.asyncio
async def test_progress_is_clamped_and_monotonic(repository: RunRepository) -> None:
    await repository.create(make_run(state=RunState.RUNNING))

    first = await repository.update_progress(
        "run-1", progress=120.0, files_added=10, files_verified=8
    )
    second = await repository.update_progress(
        "run-1", progress=-1.0, files_added=-2, files_verified=3
    )

    assert (first.progress, first.files_added, first.files_verified) == (100.0, 10, 8)
    assert (second.progress, second.files_added, second.files_verified) == (100.0, 10, 8)


@pytest.mark.asyncio
async def test_progress_on_non_running_run_returns_unchanged(repository: RunRepository) -> None:
    completed = make_run(state=RunState.SUCCEEDED, progress=55.0, files_added=2)
    await repository.create(completed)

    result = await repository.update_progress(
        completed.id, progress=90, files_added=10, files_verified=10
    )

    assert result == completed


@pytest.mark.asyncio
async def test_progress_missing_run_raises(repository: RunRepository) -> None:
    with pytest.raises(RunNotFoundError, match="Archive run not found: absent"):
        await repository.update_progress(
            "absent", progress=1, files_added=1, files_verified=1
        )


@pytest.mark.asyncio
async def test_recover_incomplete_only_interrupts_active_work(repository: RunRepository) -> None:
    states = tuple(RunState)
    for state in states:
        await repository.create(make_run(state.value, state=state))

    recovered = await repository.recover_incomplete()

    assert recovered == 2
    queued = await repository.get(RunState.QUEUED.value)
    running = await repository.get(RunState.RUNNING.value)
    assert queued.state is RunState.INTERRUPTED
    assert running.state is RunState.INTERRUPTED
    assert queued.finished_at is not None
    assert running.finished_at is not None
    assert queued.error == "ECHO stopped before the run completed"
    for terminal in (RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED, RunState.INTERRUPTED):
        assert (await repository.get(terminal.value)).state is terminal


@pytest.mark.asyncio
async def test_summary_includes_zeroes_for_every_state(repository: RunRepository) -> None:
    await repository.create(make_run("queued", state=RunState.QUEUED))
    await repository.create(make_run("failed-1", state=RunState.FAILED))
    await repository.create(make_run("failed-2", state=RunState.FAILED))

    summary = await repository.summary()

    assert set(summary) == set(RunState)
    assert summary[RunState.QUEUED] == 1
    assert summary[RunState.FAILED] == 2
    assert summary[RunState.RUNNING] == 0


@pytest.mark.parametrize(
    ("limit", "output", "expected"),
    [
        (0, "anything", ""),
        (8, "short", "short"),
        (4, "ab☃cd", "[output truncated; final 4 bytes retained]\n��cd"),
    ],
)
def test_retain_output_boundaries(tmp_path, limit, output, expected) -> None:
    config = StorageConfig(database=tmp_path / "echo.db", retained_output_bytes=limit)
    repository = RunRepository(config, Database(config))

    assert repository._retain_output(output) == expected
    assert repository._retain_output(None) is None

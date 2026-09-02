from __future__ import annotations


class ArchiverError(RuntimeError):
    """Base class for archive service errors."""


class ArchiverDisabledError(ArchiverError):
    pass


class ArchiverNotRunningError(ArchiverError):
    pass


class ArchivePlanNotFoundError(ArchiverError):
    pass


class ArchivePlanDisabledError(ArchiverError):
    pass


class ArchivePlanRunningError(ArchiverError):
    def __init__(self, plan_name: str, run_id: str) -> None:
        self.plan_name = plan_name
        self.run_id = run_id
        super().__init__(f"Archive plan is already active: {plan_name} (run {run_id})")


class ArchiveSourceError(ArchiverError):
    pass


class ArchiveRunNotCancellableError(ArchiverError):
    pass


class SchedulerError(RuntimeError):
    pass


class SchedulerConfigurationError(SchedulerError):
    pass

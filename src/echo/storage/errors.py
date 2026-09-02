from __future__ import annotations


class StorageError(RuntimeError):
    pass


class DatabaseUpgradeRequiredError(StorageError):
    pass


class DatabaseSchemaError(StorageError):
    pass


class RunNotFoundError(StorageError):
    pass


class InvalidRunTransitionError(StorageError):
    pass

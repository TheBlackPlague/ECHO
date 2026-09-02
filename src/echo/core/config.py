from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, SecretStr
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


DEFAULT_CONFIG_PATH = Path("config/config.yml")
CONFIG_PATH_ENV = "ECHO_CONFIG_FILE"
_PLAN_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def normalize_remote_path(value: str, *, field_name: str = "remote path") -> str:
    """Normalize and validate a path relative to the configured rclone remote."""
    normalized = value.strip().replace("\\", "/").strip("/")

    if not normalized: raise ValueError(f"{field_name} must not be empty")
    if "\0" in normalized: raise ValueError(f"{field_name} contains a null byte")

    path = PurePosixPath(normalized)

    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{field_name} must not contain relative path segments")

    if ":" in path.parts[0]: raise ValueError(f"{field_name} must be relative to the configured rclone remote")

    return path.as_posix()


class APIConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    root_path: str = "/echo"
    access_log: bool = True
    docs_enabled: bool = True
    cors_origins: list[str] = Field(default_factory=list)
    api_key: SecretStr | None = None
    web_password: SecretStr | None = None
    session_ttl_seconds: int = Field(default=86_400, ge=300, le=2_592_000)
    session_cookie_secure: bool = True

    @field_validator("root_path")
    @classmethod
    def normalize_root_path(cls, root_path: str) -> str:
        root_path = root_path.strip()

        if not root_path or root_path == "/": return ""
        if not root_path.startswith("/"): root_path = f"/{root_path}"

        parts = [part for part in root_path.split("/") if part]

        if any(part in {".", ".."} for part in parts):
            raise ValueError("api.root_path must not contain relative path segments")

        return f"/{'/'.join(parts)}"

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, api_key: SecretStr | None) -> SecretStr | None:
        if api_key is None: return None

        value = api_key.get_secret_value()

        if not value: return None
        if len(value) < 16: raise ValueError("api.api_key must contain at least 16 characters")

        return api_key

    @field_validator("web_password")
    @classmethod
    def normalize_web_password(cls, password: SecretStr | None) -> SecretStr | None:
        if password is None: return None

        value = password.get_secret_value()

        if not value: return None
        if len(value) < 12: raise ValueError("api.web_password must contain at least 12 characters")
        if len(value) > 4_096: raise ValueError("api.web_password must contain at most 4096 characters")

        return password

    @field_validator("cors_origins")
    @classmethod
    def normalize_origins(cls, origins: list[str]) -> list[str]:
        return list(dict.fromkeys(origin.strip().rstrip("/") for origin in origins if origin.strip()))

    @model_validator(mode="after")
    def validate_web_authentication(self) -> APIConfig:
        if self.web_password is not None and self.api_key is None:
            raise ValueError("api.api_key must be configured when api.web_password is set")

        return self


class LoggingConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    directory: Path = Path("logs")


class StorageConfig(BaseModel):
    database: Path = Path("data/echo.db")
    retained_output_bytes: int = Field(default=65_536, ge=0, le=10_000_000)


class S3UploadConfig(BaseModel):
    """Safe defaults for large local files uploaded to AWS S3."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    transfers: int = Field(default=1, ge=1, le=32)
    chunk_size_mib: int = Field(default=16, ge=5, le=5_120)
    upload_concurrency: int = Field(default=8, ge=1, le=64)
    max_buffer_memory_mib: int = Field(default=256, ge=32, le=8_192)

    @model_validator(mode="after")
    def validate_buffer_capacity(self) -> S3UploadConfig:
        minimum = self.chunk_size_mib * self.upload_concurrency
        if self.max_buffer_memory_mib < minimum:
            raise ValueError(
                "rclone.s3_upload.max_buffer_memory_mib must hold at least one "
                "multipart window (chunk_size_mib * upload_concurrency)"
            )

        return self


class RcloneConfig(BaseModel):
    binary: str = Field(default="rclone", min_length=1)
    config_file: Path | None = None
    remote: str | None = None
    bucket: str | None = None
    validation_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    s3_upload: S3UploadConfig = Field(default_factory=S3UploadConfig)

    @field_validator("binary")
    @classmethod
    def validate_binary(cls, binary: str) -> str:
        binary = binary.strip()
        if not binary: raise ValueError("rclone.binary must not be empty")

        return binary

    @field_validator("remote")
    @classmethod
    def normalize_remote(cls, remote: str | None) -> str | None:
        if remote is None: return None

        remote = remote.strip().rstrip(":")

        if any(character in remote for character in (":", "/", "\\")):
            raise ValueError("rclone.remote must be a remote name, not a path")

        return remote or None

    @field_validator("bucket")
    @classmethod
    def normalize_bucket(cls, bucket: str | None) -> str | None:
        if bucket is None: return None

        bucket = normalize_remote_path(bucket, field_name="rclone.bucket")
        if "/" in bucket: raise ValueError("rclone.bucket must be a bucket name, not a path")

        return bucket

    @model_validator(mode="after")
    def validate_archive_root(self) -> RcloneConfig:
        if bool(self.remote) != bool(self.bucket):
            raise ValueError("rclone.remote and rclone.bucket must be configured together")

        return self


class ArchivePlanConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    source: Path
    destination: str = Field(min_length=1)
    cron: str | None = None
    exclude: list[str] = Field(default_factory=list)
    enabled: bool = True
    verify_after_archive: bool = False

    @field_validator("name")
    @classmethod
    def normalize_name(cls, name: str) -> str:
        name = name.strip()

        if not _PLAN_NAME_PATTERN.fullmatch(name):
            raise ValueError(
                "archive plan name must start with an alphanumeric character and contain "
                "only letters, numbers, periods, underscores, or hyphens"
            )

        return name

    @field_validator("cron")
    @classmethod
    def normalize_cron(cls, cron: str | None) -> str | None:
        if cron is None: return None

        return " ".join(cron.split()) or None

    @field_validator("destination")
    @classmethod
    def normalize_destination(cls, destination: str) -> str:
        return normalize_remote_path(destination, field_name="archive plan destination")

    @field_validator("exclude")
    @classmethod
    def normalize_excludes(cls, excludes: list[str]) -> list[str]:
        return list(dict.fromkeys(pattern.strip() for pattern in excludes if pattern.strip()))


class ArchiveConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    max_concurrent_plans: int = Field(default=1, ge=1, le=32)
    shutdown_timeout_seconds: float = Field(default=30.0, ge=10, le=300)
    plans: list[ArchivePlanConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_plan_names(self) -> ArchiveConfig:
        names = [plan.name.casefold() for plan in self.plans]

        if len(names) != len(set(names)): raise ValueError("archive plan names must be unique (case-insensitive)")

        return self


class EchoConfig(BaseSettings):
    api: APIConfig = Field(default_factory=APIConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    rclone: RcloneConfig = Field(default_factory=RcloneConfig)
    archive: ArchiveConfig = Field(default_factory=ArchiveConfig)

    model_config = SettingsConfigDict(
        env_prefix="ECHO_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="forbid",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return env_settings, init_settings, file_secret_settings

    @model_validator(mode="after")
    def validate_archive_configuration(self) -> EchoConfig:
        if self.archive.enabled and not (self.rclone.remote and self.rclone.bucket):
            raise ValueError("rclone.remote and rclone.bucket must be set when archive.enabled is true")

        return self


def _resolve_config_path(path: str | Path | None) -> tuple[Path, bool]:
    if path is not None: return Path(path).expanduser(), True

    configured = os.getenv(CONFIG_PATH_ENV)
    if configured: return Path(configured).expanduser(), True

    return DEFAULT_CONFIG_PATH, False


def load_config(path: str | Path | None = None) -> EchoConfig:
    config_path, required = _resolve_config_path(path)

    if not config_path.exists():
        if required: raise FileNotFoundError(f"Config file not found: {config_path}")

        return EchoConfig()

    with config_path.open("r", encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}

    if not isinstance(payload, dict): raise ValueError("ECHO config must be a YAML mapping")

    return EchoConfig(**payload)


@lru_cache(maxsize=1)
def get_config() -> EchoConfig:
    return load_config()


def clear_config_cache() -> None:
    get_config.cache_clear()

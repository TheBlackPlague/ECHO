from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from echo.core.config import (
    APIConfig, ArchiveConfig, ArchivePlanConfig, clear_config_cache, EchoConfig, get_config, load_config,
    normalize_remote_path, RcloneConfig, S3UploadConfig
)


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ECHO_CONFIG_FILE", raising=False)
    clear_config_cache()
    yield
    clear_config_cache()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" backups/daily/ ", "backups/daily"),
        (r"backups\daily", "backups/daily"),
        ("one", "one"),
    ],
)
def test_normalize_remote_path(value: str, expected: str) -> None:
    assert normalize_remote_path(value) == expected


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("  / ", "must not be empty"),
        ("safe/../escape", "relative path segments"),
        ("remote:path", "must be relative"),
        ("safe/nu\0ll", "null byte"),
    ],
)
def test_normalize_remote_path_rejects_unsafe_values(value: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        normalize_remote_path(value, field_name="destination")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", ""),
        (" / ", ""),
        ("echo", "/echo"),
        (" /echo//api/ ", "/echo/api"),
    ],
)
def test_api_root_path_normalization(value: str, expected: str) -> None:
    assert APIConfig(root_path=value).root_path == expected


def test_api_root_path_rejects_parent_segments() -> None:
    with pytest.raises(ValidationError, match="relative path segments"):
        APIConfig(root_path="/echo/../admin")


def test_api_normalizes_secrets_and_cors_origins() -> None:
    config = APIConfig(
        api_key="a" * 16,
        web_password="b" * 12,
        cors_origins=[" https://one.test/ ", "https://one.test", "", "https://two.test///"],
    )

    assert config.api_key is not None
    assert config.api_key.get_secret_value() == "a" * 16
    assert config.web_password is not None
    assert config.web_password.get_secret_value() == "b" * 12
    assert config.cors_origins == ["https://one.test", "https://two.test"]


def test_api_empty_secrets_become_none() -> None:
    config = APIConfig(api_key="", web_password="")
    assert config.api_key is None
    assert config.web_password is None


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"api_key": "too-short"}, "at least 16"),
        ({"api_key": "a" * 16, "web_password": "too-short"}, "at least 12"),
        ({"api_key": "a" * 16, "web_password": "b" * 4097}, "at most 4096"),
        ({"web_password": "b" * 12}, "api.api_key must be configured"),
        ({"port": 0}, "greater than or equal to 1"),
        ({"port": 65_536}, "less than or equal to 65535"),
        ({"session_ttl_seconds": 299}, "greater than or equal to 300"),
        ({"session_ttl_seconds": 2_592_001}, "less than or equal to 2592000"),
    ],
)
def test_api_rejects_invalid_values(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        APIConfig(**kwargs)


def test_s3_upload_accepts_exact_buffer_window_and_forbids_extra_fields() -> None:
    config = S3UploadConfig(chunk_size_mib=32, upload_concurrency=4, max_buffer_memory_mib=128)
    assert config.max_buffer_memory_mib == 128

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        S3UploadConfig(unknown=True)


def test_s3_upload_rejects_insufficient_buffer_capacity() -> None:
    with pytest.raises(ValidationError, match="must hold at least one multipart window"):
        S3UploadConfig(chunk_size_mib=64, upload_concurrency=8, max_buffer_memory_mib=256)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"transfers": 0},
        {"transfers": 33},
        {"chunk_size_mib": 4},
        {"chunk_size_mib": 5_121},
        {"upload_concurrency": 0},
        {"upload_concurrency": 65},
        {"max_buffer_memory_mib": 31},
        {"max_buffer_memory_mib": 8_193},
    ],
)
def test_s3_upload_field_bounds(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        S3UploadConfig(**kwargs)


def test_rclone_normalizes_values() -> None:
    config = RcloneConfig(binary=" /usr/bin/rclone ", remote=" aws: ", bucket=" /bucket/ ")
    assert config.binary == "/usr/bin/rclone"
    assert config.remote == "aws"
    assert config.bucket == "bucket"


@pytest.mark.parametrize("value", [" ", "aws:path", "aws/path", r"aws\path"])
def test_rclone_rejects_invalid_binary_or_remote(value: str) -> None:
    field = "binary" if value == " " else "remote"
    with pytest.raises(ValidationError):
        RcloneConfig(**{field: value})


@pytest.mark.parametrize("kwargs", [{"remote": "aws"}, {"bucket": "bucket"}])
def test_rclone_requires_remote_and_bucket_together(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="must be configured together"):
        RcloneConfig(**kwargs)


def test_rclone_blank_remote_becomes_none_and_bucket_must_be_a_name() -> None:
    assert RcloneConfig(remote=" ").remote is None
    with pytest.raises(ValidationError, match="bucket name, not a path"):
        RcloneConfig(remote="aws", bucket="bucket/prefix")


def test_archive_plan_normalizes_all_user_facing_strings(tmp_path: Path) -> None:
    plan = ArchivePlanConfig(
        name=" Daily.Backup-1 ",
        source=tmp_path,
        destination=r" /archive\daily/ ",
        cron="  */5   * *  * * ",
        exclude=[" *.tmp ", "", "*.tmp", " cache/** "],
    )

    assert plan.name == "Daily.Backup-1"
    assert plan.destination == "archive/daily"
    assert plan.cron == "*/5 * * * *"
    assert plan.exclude == ["*.tmp", "cache/**"]


@pytest.mark.parametrize("name", ["_hidden", "-dash", ".dot", "has space", "name/child"])
def test_archive_plan_rejects_invalid_names(name: str) -> None:
    with pytest.raises(ValidationError, match="archive plan name"):
        ArchivePlanConfig(name=name, source=".", destination="backup")


def test_archive_plan_blank_cron_becomes_none_and_extra_is_forbidden() -> None:
    plan = ArchivePlanConfig(name="daily", source=".", destination="backup", cron="  ")
    assert plan.cron is None

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ArchivePlanConfig(name="daily", source=".", destination="backup", typo=True)


def test_archive_plan_names_are_case_insensitively_unique() -> None:
    plans = [
        ArchivePlanConfig(name="Daily", source=".", destination="one"),
        ArchivePlanConfig(name="daily", source=".", destination="two"),
    ]
    with pytest.raises(ValidationError, match=r"unique \(case-insensitive\)"):
        ArchiveConfig(plans=plans)


def test_echo_config_requires_archive_remote_when_enabled() -> None:
    with pytest.raises(ValidationError, match=r"must be set when archive\.enabled is true"):
        EchoConfig(archive={"enabled": True})

    config = EchoConfig(
        rclone={"remote": "aws", "bucket": "bucket"},
        archive={"enabled": True},
    )
    assert config.archive.enabled is True


def test_load_config_uses_defaults_when_implicit_default_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    config = load_config()
    assert config == EchoConfig()


def test_load_config_requires_explicit_or_environment_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing.yml"
    with pytest.raises(FileNotFoundError, match=str(missing)):
        load_config(missing)

    monkeypatch.setenv("ECHO_CONFIG_FILE", str(missing))
    with pytest.raises(FileNotFoundError, match=str(missing)):
        load_config()


@pytest.mark.parametrize("content", ["- item\n", "plain scalar\n"])
def test_load_config_rejects_non_mapping_yaml(tmp_path: Path, content: str) -> None:
    path = tmp_path / "config.yml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        load_config(path)


def test_load_config_reads_yaml_and_environment_has_priority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "config.yml"
    path.write_text(
        "api:\n  port: 8001\nlogging:\n  level: WARNING\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ECHO_API__PORT", "9000")

    config = load_config(path)
    assert config.api.port == 9000
    assert config.logging.level == "WARNING"


def test_load_config_treats_empty_yaml_as_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text("", encoding="utf-8")
    assert load_config(path) == EchoConfig()


def test_load_config_treats_empty_yaml_collection_as_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text("[]\n", encoding="utf-8")
    assert load_config(path) == EchoConfig()


def test_get_config_caches_until_cleared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.yml"
    path.write_text("api:\n  port: 8001\n", encoding="utf-8")
    monkeypatch.setenv("ECHO_CONFIG_FILE", str(path))

    first = get_config()
    path.write_text("api:\n  port: 8002\n", encoding="utf-8")
    assert get_config() is first
    assert get_config().api.port == 8001

    clear_config_cache()
    assert get_config().api.port == 8002

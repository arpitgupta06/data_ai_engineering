"""Unit tests for chainrisk.settings. No running services and no infra/.env needed."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from chainrisk.settings import Settings, get_settings

# Every variable Settings can read, so a developer's shell can't leak into a test.
SETTINGS_ENV_VARS = [
    "KAFKA_HOST",
    "KAFKA_PORT",
    "SCHEMA_REGISTRY_HOST",
    "SCHEMA_REGISTRY_PORT",
    "MINIO_HOST",
    "MINIO_PORT",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "AWS_REGION",
    "BRONZE_BUCKET",
    "ARTIFACTS_BUCKET",
    "POSTGRES_OPS_HOST",
    "POSTGRES_OPS_PORT",
    "POSTGRES_OPS_USER",
    "POSTGRES_OPS_PASSWORD",
    "POSTGRES_OPS_DB",
    "DUCKDB_PATH",
]

REQUIRED = {
    "minio_root_user": "minio-user",
    "minio_root_password": "minio-pass",
    "postgres_ops_user": "pg-user",
    "postgres_ops_password": "pg-pass",
    "postgres_ops_db": "pg-db",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for var in SETTINGS_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield
    get_settings.cache_clear()


def make(**overrides: object) -> Settings:
    # _env_file=None: ignore infra/.env so results don't depend on the machine.
    return Settings(_env_file=None, **{**REQUIRED, **overrides})  # type: ignore[arg-type]


def test_defaults_match_compose_host_ports() -> None:
    s = make()
    assert (s.kafka_host, s.kafka_port) == ("localhost", 19092)
    assert (s.schema_registry_host, s.schema_registry_port) == ("localhost", 18081)
    assert (s.minio_host, s.minio_port) == ("localhost", 9000)
    assert (s.postgres_ops_host, s.postgres_ops_port) == ("localhost", 5433)
    assert s.aws_region == "us-east-1"
    assert s.bronze_bucket == "chainrisk-bronze"
    assert s.artifacts_bucket == "chainrisk-artifacts"


def test_duckdb_default_path_is_under_project_data_dir() -> None:
    s = make()
    assert s.duckdb_path.name == "chainrisk.duckdb"
    assert s.duckdb_path.parent.name == "data"


def test_derived_endpoints() -> None:
    s = make()
    assert s.kafka_bootstrap_servers == "localhost:19092"
    assert s.schema_registry_url == "http://localhost:18081"
    assert s.s3_endpoint_url == "http://localhost:9000"


def test_derived_endpoints_follow_overrides() -> None:
    s = make(kafka_host="broker", kafka_port=9092, minio_host="s3.internal", minio_port=443)
    assert s.kafka_bootstrap_servers == "broker:9092"
    assert s.s3_endpoint_url == "http://s3.internal:443"


def test_postgres_dsn() -> None:
    assert make().postgres_ops_dsn == "postgresql://pg-user:pg-pass@localhost:5433/pg-db"


def test_postgres_dsn_percent_encodes_credentials() -> None:
    s = make(postgres_ops_user="a@b", postgres_ops_password="p:w/d?#%")
    assert s.postgres_ops_dsn == "postgresql://a%40b:p%3Aw%2Fd%3F%23%25@localhost:5433/pg-db"


def test_secrets_are_masked_in_repr() -> None:
    s = make()
    assert "minio-pass" not in repr(s)
    assert "pg-pass" not in repr(s)
    assert s.minio_root_password.get_secret_value() == "minio-pass"


@pytest.mark.parametrize("missing", list(REQUIRED))
def test_missing_required_field_fails(missing: str) -> None:
    values = {k: v for k, v in REQUIRED.items() if k != missing}
    with pytest.raises(ValidationError, match=missing):
        Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAFKA_HOST", "broker.example")
    monkeypatch.setenv("POSTGRES_OPS_PORT", "6543")
    s = make()
    assert s.kafka_host == "broker.example"
    assert s.postgres_ops_port == 6543


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    for field, value in REQUIRED.items():
        monkeypatch.setenv(field.upper(), value)
    get_settings.cache_clear()
    assert get_settings() is get_settings()


def test_unknown_keys_in_env_file_are_ignored(tmp_path: Path) -> None:
    # infra/.env is shared with docker compose; keys Settings doesn't model must not fail it.
    env_file = tmp_path / ".env"
    lines = [f"{k.upper()}={v}" for k, v in REQUIRED.items()] + ["COMPOSE_ONLY_KEY=1"]
    env_file.write_text("\n".join(lines))
    s = Settings(_env_file=env_file)
    assert s.postgres_ops_user == "pg-user"
    assert not hasattr(s, "compose_only_key")

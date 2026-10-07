"""Single source of truth for every hostname, port and credential.

Nothing else in the codebase reads os.environ or hardcodes an endpoint; callers
take a ``Settings`` and use its fields or derived properties. Phase 9 (AWS) should
only need changes in this file: swap the defaults / derived properties below, and
let real environment variables (which take precedence over the env file) supply
the rest.
"""

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# src/chainrisk/settings.py -> project root is two levels above the package.
# Assumes an editable install (uv sync), so __file__ sits in the repo. A non-editable
# install (wheel, container image) would resolve into site-packages and find no env
# file; revisit when Phase 9 containerises this and pass config via real env vars.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / "infra" / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        # infra/.env is shared with docker compose and may hold keys we don't model.
        extra="ignore",
    )

    # ── Kafka / Redpanda (host-side external listeners) ──────────
    kafka_host: str = "localhost"
    kafka_port: int = 19092
    schema_registry_host: str = "localhost"
    schema_registry_port: int = 18081

    # ── MinIO (local S3 stand-in) ────────────────────────────────
    minio_host: str = "localhost"
    minio_port: int = 9000
    minio_root_user: str
    minio_root_password: SecretStr
    aws_region: str = "us-east-1"
    bronze_bucket: str = "chainrisk-bronze"
    artifacts_bucket: str = "chainrisk-artifacts"

    # ── Operational Postgres ─────────────────────────────────────
    postgres_ops_host: str = "localhost"
    postgres_ops_port: int = 5433
    postgres_ops_user: str
    postgres_ops_password: SecretStr
    postgres_ops_db: str

    # ── DuckDB (embedded; a file, not a server) ──────────────────
    duckdb_path: Path = PROJECT_ROOT / "data" / "chainrisk.duckdb"

    # ── Derived ──────────────────────────────────────────────────
    @property
    def kafka_bootstrap_servers(self) -> str:
        return f"{self.kafka_host}:{self.kafka_port}"

    @property
    def schema_registry_url(self) -> str:
        return f"http://{self.schema_registry_host}:{self.schema_registry_port}"

    @property
    def s3_endpoint_url(self) -> str:
        return f"http://{self.minio_host}:{self.minio_port}"

    @property
    def postgres_ops_dsn(self) -> str:
        """libpq URI usable by psycopg. User and password are percent-encoded."""
        user = quote(self.postgres_ops_user, safe="")
        password = quote(self.postgres_ops_password.get_secret_value(), safe="")
        return (
            f"postgresql://{user}:{password}"
            f"@{self.postgres_ops_host}:{self.postgres_ops_port}/{self.postgres_ops_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # required fields come from env / file

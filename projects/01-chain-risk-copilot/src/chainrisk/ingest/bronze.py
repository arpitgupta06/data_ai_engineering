"""Ingest a fixed window of the AWS Public Blockchain Ethereum dataset into bronze.

Columns are pruned at ingest (ADR 0006): bronze holds what silver needs, not the raw
source bytes. The source is free and replayable, so a dropped column is one re-run away.
"""

from collections.abc import Collection, Sequence
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import NamedTuple

from pyarrow.fs import FileSystem, FileType, S3FileSystem
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    NonNegativeInt,
    PositiveInt,
    ValidationError,
)

from chainrisk.settings import Settings

SOURCE_BUCKET = "aws-public-blockchain"
SOURCE_REGION = "us-east-2"
SOURCE_PREFIX = "v1.0/eth"

# Fixed 7-day window (CRC-15). Seven partitions prove backfill, idempotency and
# completeness as well as seven hundred; parameterised ranges arrive with CRC-17.
WINDOW_START = date(2026, 10, 1)
WINDOW_DAYS = 7

# The single place that decides which source columns reach bronze. Order is the
# written column order.
#
# `date` is deliberately absent: the source partitions by date=YYYY-MM-DD, and bronze keeps
# that path partition. Carrying the column too would appear twice to hive-partition readers.
#
# `value` is float64 in the source: wei above 2^53 is already rounded upstream and
# cannot be recovered here (see docs/PRIMER.md).
# `last_modified` is S3 delivery metadata repeated on every row. It is dropped from the
# data and captured once per partition in the _SUCCESS marker, where a change signals AWS
# re-delivered the partition.
COLUMNS: dict[str, tuple[str, ...]] = {
    "blocks": (
        "number",
        "hash",
        "parent_hash",  # parent_hash -> hash chaining is how P3 detects a reorg
        "timestamp",
        "miner",
        "gas_used",
        "gas_limit",
        "base_fee_per_gas",
        "transaction_count",  # completeness cross-checks
        "size",
    ),
    "transactions": (
        "hash",
        "block_number",
        "block_hash",
        "transaction_index",
        "block_timestamp",
        "from_address",
        "to_address",
        "value",
        "receipt_gas_used",
        "receipt_effective_gas_price",  # x receipt_gas_used = fee actually paid
        "receipt_status",  # a failed transaction moved nothing; always filter on this
        "receipt_contract_address",
        "transaction_type",
    ),
    "token_transfers": (
        "token_address",
        "transaction_hash",
        "log_index",
        "from_address",
        "to_address",
        "value",
        "block_number",
        "block_hash",  # after a reorg block_number alone is ambiguous
        "block_timestamp",
    ),
}

TABLES: tuple[str, ...] = tuple(COLUMNS)

# Bronze layout, mirroring the source so a second chain fits without restructuring:
#   <bucket>/eth/<table>/date=YYYY-MM-DD/part-*.parquet
#   <bucket>/eth/<table>/date=YYYY-MM-DD/_SUCCESS
# Hive-style date= gives DuckDB and pyarrow partition pruning, which is why `date` is
# not also a column.
BRONZE_PREFIX = "eth"
MARKER_NAME = "_SUCCESS"
MARKER_VERSION = 1


def window_dates(start: date = WINDOW_START, days: int = WINDOW_DAYS) -> list[date]:
    return [start + timedelta(days=i) for i in range(days)]


def check_columns(table: str, source_columns: Collection[str]) -> None:
    """Fail loudly, naming the table, if configured columns have left the source schema.

    Reports every missing column at once, not one per re-run. The check is one-directional
    on purpose: configured <= source catches a rename that breaks us, while a column AWS
    *adds* is not an error. Detecting new fields is data-contract work (P6).
    """
    missing = [c for c in COLUMNS[table] if c not in source_columns]
    if missing:
        raise ValueError(f"{table}: configured columns not in source schema: {missing}")


def source_fs() -> S3FileSystem:
    """Anonymous client for the public AWS bucket. Never given our MinIO credentials."""
    return S3FileSystem(anonymous=True, region=SOURCE_REGION)


def bronze_fs(settings: Settings) -> S3FileSystem:
    """Client for the local MinIO bronze bucket.

    pyarrow wants a ``host:port`` connect string plus a separate ``scheme``, not the full
    URL boto3 takes: see ``Settings.s3_endpoint_host_port``. Kept separate from
    ``source_fs`` on purpose: the two sides differ in endpoint, region, credentials and
    signing.
    """
    return S3FileSystem(
        endpoint_override=settings.s3_endpoint_host_port,
        scheme="http",
        region=settings.aws_region,
        access_key=settings.minio_root_user,
        secret_key=settings.minio_root_password.get_secret_value(),
    )


# ── _SUCCESS marker ──────────────────────────────────────────────────────────────────
#
# A single-object PUT is atomic in S3 and MinIO: the object exists whole or not at all.
# So the marker, written as one object, is the commit point for a partition. A reader
# treats a partition without it as absent.
#
# Write order is load-bearing:
#   first run:  write data  -> write marker
#   re-ingest:  delete marker -> delete stale data -> write data -> write marker
# On re-ingest the marker goes FIRST. Cleaning the data before removing the marker would
# leave a window where the partition is marked complete but half-empty. With the marker
# gone the partition is already absent to every reader, so the rest of the window is safe.
#
# Every helper takes ``fs`` instead of building a client, so tests run against a
# LocalFileSystem on tmp_path with real files and no patching.


class CorruptMarkerError(ValueError):
    """A _SUCCESS object exists but cannot be trusted. Distinct from "no marker"."""


class Marker(BaseModel):
    """JSON body of ``_SUCCESS``: one object, one commit point, and a re-delivery signal."""

    # Forbid extras: with the default (ignore), a typo like "marker_vershun" would parse as
    # version 1 and a corrupt marker would read as current.
    model_config = ConfigDict(extra="forbid")

    marker_version: int = MARKER_VERSION
    table: str
    date: date
    rows: NonNegativeInt  # the verified count: rows read == rows written
    # Max across the source objects. A later value means AWS re-delivered the partition;
    # this is what replaces the per-row last_modified column dropped at ingest.
    source_last_modified: AwareDatetime
    source_files: PositiveInt
    source_bytes: NonNegativeInt
    # COLUMNS[table] at write time, so a partition written under an older selection is
    # detectable rather than silently stale (ADR-0006: "adding a column is a backfill").
    columns: list[str]
    ingested_at: AwareDatetime


class PartitionState(StrEnum):
    ABSENT = "absent"
    CURRENT = "current"
    STALE = "stale"


class Assessment(NamedTuple):
    state: PartitionState
    reason: str


def partition_dir(root: str, table: str, day: date) -> str:
    """``root`` is the bucket name for S3 and a directory for a local filesystem."""
    return f"{root}/{BRONZE_PREFIX}/{table}/date={day.isoformat()}"


def marker_path(root: str, table: str, day: date) -> str:
    return f"{partition_dir(root, table, day)}/{MARKER_NAME}"


def read_marker(fs: FileSystem, path: str) -> Marker | None:
    """None means there is no marker. A marker that exists but is bad raises.

    Collapsing a parse failure into None would make a damaged marker read as "partition
    absent" and silently re-ingest over it, hiding the corruption.
    """
    if fs.get_file_info(path).type == FileType.NotFound:
        return None
    with fs.open_input_stream(path) as stream:
        raw = stream.read()
    try:
        marker = Marker.model_validate_json(raw)
    except ValidationError as exc:
        raise CorruptMarkerError(f"{path}: unreadable marker: {exc}") from exc
    if marker.marker_version != MARKER_VERSION:
        raise CorruptMarkerError(
            f"{path}: marker_version {marker.marker_version}, expected {MARKER_VERSION}"
        )
    return marker


def write_marker(fs: FileSystem, path: str, marker: Marker) -> None:
    """Write the marker as a single object. Call this last, after the data is complete.

    The caller creates the parent directory on a local filesystem; S3 needs none.
    """
    with fs.open_output_stream(path) as stream:
        stream.write(marker.model_dump_json(indent=2).encode())


def delete_marker(fs: FileSystem, path: str) -> None:
    """Remove the marker if present. First step of a re-ingest."""
    if fs.get_file_info(path).type != FileType.NotFound:
        fs.delete_file(path)


def assess_partition(
    marker: Marker | None, columns: Sequence[str], source_last_modified: datetime
) -> Assessment:
    """Is this partition absent, current, or stale?

    CURRENT is a no-op; STALE is re-ingested, with the reason reported.
    """
    if source_last_modified.tzinfo is None:
        raise ValueError("source_last_modified must be timezone-aware")
    if marker is None:
        return Assessment(PartitionState.ABSENT, "no _SUCCESS marker")
    if marker.columns != list(columns):
        return Assessment(PartitionState.STALE, "column selection changed since ingest")
    if source_last_modified > marker.source_last_modified:
        return Assessment(PartitionState.STALE, "source was re-delivered by AWS")
    return Assessment(PartitionState.CURRENT, "already ingested")


def utc_now() -> datetime:
    return datetime.now(UTC)

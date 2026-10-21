"""Ingest a fixed window of the AWS Public Blockchain Ethereum dataset into bronze.

Columns are pruned at ingest (ADR 0006): bronze holds what silver needs, not the raw
source bytes. The source is free and replayable, so a dropped column is one re-run away.
"""

import contextlib
from collections.abc import Collection, Sequence
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import NamedTuple

import pyarrow as pa
import pyarrow.parquet as pq
from pyarrow.fs import (
    FileInfo,
    FileSelector,
    FileSystem,
    FileType,
    LocalFileSystem,
    S3FileSystem,
)
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


# ── Ingesting one partition ──────────────────────────────────────────────────────────
#
# Memory: the largest source file (transactions) is ~838 MB. Each file is streamed with
# iter_batches(columns=...) straight into a ParquetWriter on an output stream, rather than
# read_table(), which would materialise the whole pruned file at once. The floor is one
# row group of pruned columns in Arrow memory, not one batch: iter_batches decompresses a
# whole row group and slices batches out of it, so BATCH_ROWS is not the memory dial.
# The real peak is to be measured on the first full run, not estimated here.

BATCH_ROWS = 131_072


class IngestError(Exception):
    """A partition failed. The message names the table and date."""


class SourcePartitionMissingError(IngestError):
    pass


class RowCountMismatchError(IngestError):
    pass


class Outcome(StrEnum):
    INGESTED = "ingested"
    SKIPPED = "skipped"


class PartitionResult(NamedTuple):
    table: str
    day: date
    outcome: Outcome
    rows: int
    detail: str


def source_files(fs: FileSystem, table: str, day: date) -> list[FileInfo]:
    prefix = f"{SOURCE_BUCKET}/{SOURCE_PREFIX}/{table}/date={day.isoformat()}"
    # Recursive, so a file nested below date=... is still found; the .parquet filter below
    # decides what is picked up. allow_not_found turns a missing prefix into an empty list.
    infos = fs.get_file_info(FileSelector(prefix, recursive=True, allow_not_found=True))
    files = sorted(
        (i for i in infos if i.type == FileType.File and i.path.endswith(".parquet")),
        key=lambda i: i.path,
    )
    if not files:
        raise SourcePartitionMissingError(f"{table} {day}: no parquet files under {prefix}")
    return files


def _ensure_dir(fs: FileSystem, path: str) -> None:
    # A local filesystem will not create parents on write. S3 has no directories, and
    # create_dir there would leave empty "dir/" marker objects, so skip it.
    if isinstance(fs, LocalFileSystem):
        fs.create_dir(path, recursive=True)


def _copy_pruned(
    src_fs: FileSystem, dst_fs: FileSystem, source_path: str, dest_path: str, table: str
) -> tuple[int, int, int]:
    """Stream one file, keeping only COLUMNS[table].

    Returns (rows in the source footer, rows streamed, rows in the written footer). The
    three come from independent places, so agreement is evidence and not a tautology.
    """
    columns = list(COLUMNS[table])
    with src_fs.open_input_file(source_path) as handle:
        parquet = pq.ParquetFile(handle)
        check_columns(table, parquet.schema_arrow.names)
        expected = parquet.metadata.num_rows
        schema = pa.schema([parquet.schema_arrow.field(c) for c in columns])
        streamed = 0
        with (
            dst_fs.open_output_stream(dest_path) as out,
            # Explicit, not the library default: ADR-0006 lists compression among the
            # things rewriting changes, so it should not shift under a pyarrow upgrade.
            pq.ParquetWriter(out, schema, compression="snappy") as writer,
        ):
            for batch in parquet.iter_batches(batch_size=BATCH_ROWS, columns=columns):
                writer.write_batch(batch)
                streamed += batch.num_rows
    with dst_fs.open_input_file(dest_path) as written:
        written_rows = pq.ParquetFile(written).metadata.num_rows
    return expected, streamed, written_rows


def ingest_partition(
    src_fs: FileSystem, dst_fs: FileSystem, root: str, table: str, day: date
) -> PartitionResult:
    """Ingest one table-day into bronze. Raises IngestError subclasses on failure.

    A partition is complete only once _SUCCESS exists. Re-running a current partition is
    a no-op; a stale one (columns widened, or AWS re-delivered) is re-ingested.
    """
    files = source_files(src_fs, table, day)
    source_last_modified = max(f.mtime for f in files)

    path = marker_path(root, table, day)
    existing = read_marker(dst_fs, path)
    assessment = assess_partition(existing, COLUMNS[table], source_last_modified)
    if existing is not None and assessment.state is PartitionState.CURRENT:
        return PartitionResult(table, day, Outcome.SKIPPED, existing.rows, assessment.reason)

    directory = partition_dir(root, table, day)
    # Marker first: once it is gone the partition is absent to every reader, so cleaning
    # and rewriting the data objects below cannot expose a half-built partition.
    delete_marker(dst_fs, path)
    dst_fs.delete_dir_contents(directory, missing_dir_ok=True)
    _ensure_dir(dst_fs, directory)

    total = 0
    try:
        for index, source in enumerate(files):
            dest = f"{directory}/part-{index:05d}.parquet"
            expected, streamed, written = _copy_pruned(src_fs, dst_fs, source.path, dest, table)
            if not expected == streamed == written:
                raise RowCountMismatchError(
                    f"{table} {day}: {source.base_name}: source has {expected} rows, "
                    f"streamed {streamed}, wrote {written}"
                )
            total += written
    except Exception:
        # Best-effort tidy. Correctness does not depend on it: with no marker the
        # partition is absent, and the next run clears leftovers before writing. That is
        # also why Exception and not BaseException: Ctrl-C leaves data but no marker, which
        # reads as absent, by design.
        with contextlib.suppress(OSError):
            dst_fs.delete_dir_contents(directory, missing_dir_ok=True)
        raise

    write_marker(
        dst_fs,
        path,
        Marker(
            table=table,
            date=day,
            rows=total,
            source_last_modified=source_last_modified,
            source_files=len(files),
            source_bytes=sum(f.size for f in files),
            columns=list(COLUMNS[table]),
            ingested_at=utc_now(),
        ),
    )
    reason = assessment.reason if existing is not None else "new partition"
    return PartitionResult(table, day, Outcome.INGESTED, total, reason)

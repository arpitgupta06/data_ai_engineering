"""Profile every committed bronze partition and record what "normal" looked like (CRC-16).

    uv run python -m chainrisk.ingest.profile

One Parquet object per table-day in the artifacts bucket, one row per column:

    <artifacts>/profiles/bronze/eth/<table>/<YYYY-MM-DD>.parquet

The filename is not ``date=...`` on purpose: each row carries ``day`` itself so a profile
is self-describing, and a hive-style path would make a reader see that column twice (the
same reason bronze data does not carry ``date``). Query the window with a glob:

    SELECT table_name, day, null_rate FROM read_parquet('<root>/*/*.parquet')
    WHERE column_name = 'to_address'

Re-profiling a partition overwrites its object, so there is never a second entry. The
write is a single PUT, so a reader sees the old profile or the new one, never half of one.
That single PUT is the whole commit protocol, which is why a profile gets no ``_SUCCESS``
marker where a bronze partition does: a partition is many objects, and the marker is how
it commits as one. A killed run leaves the previous profile or none (an S3 upload that
never completes is never visible); only a local filesystem can leave a truncated file,
and that fails to open as Parquet instead of reading as wrong numbers. A profile is
derived data, so re-running is always the repair.
Nothing in a profile depends on when it was computed (``marker_ingested_at`` comes from the
marker), so profiling unchanged data twice produces the same rows and diffs clean.

A partition without ``_SUCCESS`` is skipped and reported, never profiled as empty. Its
previous profile, if any, is left in place; ``marker_ingested_at`` is what tells a reader
whether a profile still matches the data.
"""

import sys
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime
from enum import StrEnum
from typing import NamedTuple

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from pyarrow.fs import FileInfo, FileSelector, FileSystem, FileType

from chainrisk.ingest.bronze import (
    BATCH_ROWS,
    BRONZE_PREFIX,
    TABLES,
    bronze_fs,
    ensure_dir,
    marker_path,
    partition_dir,
    read_marker,
    window_dates,
)
from chainrisk.settings import get_settings

ARTIFACT_PREFIX = "profiles/bronze"

# Columns that hold an address: they get a distinct count. Kept in step with COLUMNS by
# test_every_address_column_in_bronze_gets_a_distinct_count, not by this comment.
ADDRESS_COLUMNS = frozenset(
    {"miner", "from_address", "to_address", "receipt_contract_address", "token_address"}
)

# Explicit, not inferred from the data: an all-null column or a zero-row partition must
# produce the same types as every other, or profiles stop unioning cleanly.
PROFILE_SCHEMA = pa.schema(
    [
        pa.field("table_name", pa.string(), nullable=False),
        pa.field("day", pa.date32(), nullable=False),
        pa.field("column_name", pa.string(), nullable=False),
        pa.field("dtype", pa.string(), nullable=False),
        # Partition-level facts, repeated on every column row: wide and boring beats a
        # second table to join.
        pa.field("rows", pa.int64(), nullable=False),
        pa.field("bytes_on_disk", pa.int64(), nullable=False),
        pa.field("marker_ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("null_count", pa.int64(), nullable=False),
        pa.field("null_rate", pa.float64()),  # null when rows == 0: a rate of nothing
        # Text columns only. The source writes "" where it means "none" (99.9% of
        # transactions.receipt_contract_address), which null_count cannot see.
        pa.field("empty_count", pa.int64()),
        # Numeric and timestamp columns only. Text, so one column holds every type; cast
        # to compare numerically (e.g. CAST(max_value AS BIGINT) for block_number).
        pa.field("min_value", pa.string()),
        pa.field("max_value", pa.string()),
        pa.field("distinct_count", pa.int64()),  # address columns only
    ]
)


class ProfileError(Exception):
    """A partition could not be profiled. The message names the table and date."""


class ColumnStats(NamedTuple):
    column: str
    dtype: str
    null_count: int
    empty_count: int | None
    min_value: str | None
    max_value: str | None
    distinct_count: int | None


class _Accumulator:
    """Running statistics for one column, fed one batch at a time."""

    def __init__(self, field: pa.Field) -> None:
        self.field = field
        self.nulls = 0
        self.empties: int | None = 0 if pa.types.is_string(field.type) else None
        self.low: pa.Scalar | None = None
        self.high: pa.Scalar | None = None
        self.track_range = (
            pa.types.is_integer(field.type)
            or pa.types.is_floating(field.type)
            or pa.types.is_timestamp(field.type)
        )
        # Exact distinct needs every value seen, so keep them in Arrow, not a Python set.
        self.uniques: pa.Array | None = (
            pa.array([], type=field.type) if field.name in ADDRESS_COLUMNS else None
        )

    def add(self, column: pa.Array) -> None:
        self.nulls += column.null_count
        if self.empties is not None:
            self.empties += pc.sum(pc.equal(column, "")).as_py() or 0
        if self.track_range:
            bounds = pc.min_max(column)
            low, high = bounds["min"], bounds["max"]
            if low.is_valid and (self.low is None or pc.less(low, self.low).as_py()):
                self.low = low
            if high.is_valid and (self.high is None or pc.greater(high, self.high).as_py()):
                self.high = high
        if self.uniques is not None:
            merged = pa.chunked_array([self.uniques, pc.unique(column.drop_null())])
            self.uniques = pc.unique(merged)

    def stats(self) -> ColumnStats:
        return ColumnStats(
            self.field.name,
            str(self.field.type),
            self.nulls,
            self.empties,
            _text(self.low),
            _text(self.high),
            None if self.uniques is None else len(self.uniques),
        )


def _text(value: pa.Scalar | None) -> str | None:
    return None if value is None else pc.cast(value, pa.string()).as_py()


def aggregate(
    schema: pa.Schema, batches: Iterable[pa.RecordBatch]
) -> tuple[int, list[ColumnStats]]:
    """Row count and per-column statistics. Pure: no filesystem, so it tests without one.

    ``schema`` is passed in rather than read off the first batch so that a partition with
    zero rows still yields one entry per column.
    """
    accumulators = [_Accumulator(field) for field in schema]
    rows = 0
    for batch in batches:
        rows += batch.num_rows
        for accumulator in accumulators:
            accumulator.add(batch.column(accumulator.field.name))
    return rows, [a.stats() for a in accumulators]


def profile_path(artifacts_root: str, table: str, day: date) -> str:
    return f"{artifacts_root}/{ARTIFACT_PREFIX}/{BRONZE_PREFIX}/{table}/{day.isoformat()}.parquet"


def part_files(fs: FileSystem, root: str, table: str, day: date) -> list[FileInfo]:
    selector = FileSelector(partition_dir(root, table, day), allow_not_found=True)
    return sorted(
        (
            i
            for i in fs.get_file_info(selector)
            if i.type == FileType.File and i.path.endswith(".parquet")
        ),
        key=lambda i: i.path,
    )


def _read_batches(fs: FileSystem, paths: Sequence[str]) -> Iterable[pa.RecordBatch]:
    for path in paths:
        with fs.open_input_file(path) as handle:
            yield from pq.ParquetFile(handle).iter_batches(batch_size=BATCH_ROWS)


def _common_schema(fs: FileSystem, paths: Sequence[str]) -> pa.Schema:
    schemas = []
    for path in paths:
        with fs.open_input_file(path) as handle:
            schemas.append(pq.ParquetFile(handle).schema_arrow)
    if any(not s.equals(schemas[0]) for s in schemas):
        raise ProfileError("part files disagree on schema")
    return schemas[0]


def to_table(
    table: str,
    day: date,
    rows: int,
    size: int,
    ingested_at: datetime,
    stats: Sequence[ColumnStats],
) -> pa.Table:
    return pa.table(
        {
            "table_name": [table] * len(stats),
            "day": [day] * len(stats),
            "column_name": [s.column for s in stats],
            "dtype": [s.dtype for s in stats],
            "rows": [rows] * len(stats),
            "bytes_on_disk": [size] * len(stats),
            "marker_ingested_at": [ingested_at] * len(stats),
            "null_count": [s.null_count for s in stats],
            "null_rate": [s.null_count / rows if rows else None for s in stats],
            "empty_count": [s.empty_count for s in stats],
            "min_value": [s.min_value for s in stats],
            "max_value": [s.max_value for s in stats],
            "distinct_count": [s.distinct_count for s in stats],
        },
        schema=PROFILE_SCHEMA,
    )


class Outcome(StrEnum):
    PROFILED = "profiled"
    SKIPPED = "skipped"
    FAILED = "failed"


class ProfileResult(NamedTuple):
    table: str
    day: date
    outcome: Outcome
    rows: int
    detail: str


def profile_partition(
    fs: FileSystem, root: str, artifacts_root: str, table: str, day: date
) -> ProfileResult:
    """Profile one table-day. A corrupt marker raises; a missing one is a skip.

    Raises ProfileError if what is on disk disagrees with the marker, because a profile of
    data the marker does not vouch for would be a baseline of something broken.
    """
    marker = read_marker(fs, marker_path(root, table, day))
    if marker is None:
        return ProfileResult(table, day, Outcome.SKIPPED, 0, "no _SUCCESS marker")

    parts = part_files(fs, root, table, day)
    if len(parts) != marker.source_files:
        raise ProfileError(
            f"{table} {day}: {len(parts)} part files, marker says {marker.source_files}"
        )
    paths = [p.path for p in parts]
    try:
        rows, stats = aggregate(_common_schema(fs, paths), _read_batches(fs, paths))
    except ProfileError as exc:
        raise ProfileError(f"{table} {day}: {exc}") from exc
    if rows != marker.rows:
        raise ProfileError(f"{table} {day}: read {rows} rows, marker says {marker.rows}")

    output = to_table(table, day, rows, sum(p.size for p in parts), marker.ingested_at, stats)
    destination = profile_path(artifacts_root, table, day)
    ensure_dir(fs, destination.rsplit("/", 1)[0])
    with fs.open_output_stream(destination) as stream:
        pq.write_table(output, stream, compression="snappy")
    return ProfileResult(table, day, Outcome.PROFILED, rows, f"{len(stats)} columns")


def profile_window(
    fs: FileSystem,
    root: str,
    artifacts_root: str,
    days: Sequence[date],
    tables: Sequence[str] = TABLES,
    on_start: Callable[[str, date], None] | None = None,
    on_result: Callable[[ProfileResult], None] | None = None,
) -> list[ProfileResult]:
    """Profile every table-day, recording each outcome. One bad partition never stops the rest.

    Mirrors run_window: any Exception is that partition failing, KeyboardInterrupt still
    propagates.
    """
    results: list[ProfileResult] = []
    for day in days:
        for table in tables:
            if on_start:
                on_start(table, day)
            try:
                result = profile_partition(fs, root, artifacts_root, table, day)
            except Exception as exc:
                result = ProfileResult(
                    table, day, Outcome.FAILED, 0, f"{type(exc).__name__}: {exc}"
                )
            results.append(result)
            if on_result:
                on_result(result)
    return results


def format_result(result: ProfileResult) -> str:
    label = {Outcome.PROFILED: "ok", Outcome.SKIPPED: "skip", Outcome.FAILED: "FAIL"}
    head = f"[{label[result.outcome]:<4}] {result.table:<16} {result.day}"
    if result.outcome is Outcome.PROFILED:
        return f"{head}  {result.rows:,} rows, {result.detail}"
    return f"{head}  {result.detail}"


def exit_code(results: Sequence[ProfileResult]) -> int:
    return 1 if any(r.outcome is Outcome.FAILED for r in results) else 0


def main() -> int:
    """Profile the fixed window. Exit 0 if no partition failed (skips are reported, not
    failures), 1 if any failed, 2 if a bucket could not be reached."""
    settings = get_settings()
    fs = bronze_fs(settings)  # the same MinIO client serves both buckets
    for bucket in (settings.bronze_bucket, settings.artifacts_bucket):
        try:
            reachable = fs.get_file_info(bucket).type == FileType.Directory
        except OSError as exc:
            print(f"cannot reach bucket {bucket!r} at {settings.s3_endpoint_url}: {exc}")
            return 2
        if not reachable:
            print(f"bucket {bucket!r} does not exist at {settings.s3_endpoint_url}")
            return 2

    days = window_dates()
    print(f"profiling {len(days)} days x {len(TABLES)} tables: {days[0]} .. {days[-1]}")
    results = profile_window(
        fs,
        settings.bronze_bucket,
        settings.artifacts_bucket,
        days,
        on_start=lambda table, day: print(f"      {table:<16} {day}  profiling ...", flush=True),
        on_result=lambda r: print(format_result(r), flush=True),
    )

    counts = {o: sum(r.outcome is o for r in results) for o in Outcome}
    print(
        f"\n{counts[Outcome.PROFILED]} profiled, {counts[Outcome.SKIPPED]} skipped, "
        f"{counts[Outcome.FAILED]} failed"
    )
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())

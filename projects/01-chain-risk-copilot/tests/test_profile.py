"""Unit tests for chainrisk.ingest.profile. No network, no MinIO, no infra/.env.

Bronze partitions are real Parquet files and a real _SUCCESS marker under tmp_path, written
by hand (not through ingest_partition) so a test controls exactly what the marker claims
and what is on disk: an all-null column, zero rows, a marker that lies, a corrupt marker.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pyarrow.fs import FileSystem, LocalFileSystem

from chainrisk.ingest import profile
from chainrisk.ingest.bronze import (
    COLUMNS,
    CorruptMarkerError,
    Marker,
    marker_path,
    partition_dir,
    write_marker,
)
from chainrisk.ingest.profile import (
    PROFILE_SCHEMA,
    ColumnStats,
    Outcome,
    ProfileError,
    aggregate,
    profile_partition,
    profile_path,
    profile_window,
)

TABLE, DAY = "blocks", date(2026, 10, 1)
INGESTED_AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

BLOCKS_SCHEMA = pa.schema(
    [
        ("number", pa.int64()),
        ("hash", pa.string()),
        ("parent_hash", pa.string()),
        ("timestamp", pa.timestamp("ns")),
        ("miner", pa.string()),
        ("gas_used", pa.int64()),
        ("gas_limit", pa.int64()),
        ("base_fee_per_gas", pa.int64()),
        ("transaction_count", pa.int64()),
        ("size", pa.int64()),
    ]
)
assert BLOCKS_SCHEMA.names == list(COLUMNS[TABLE])


def blocks(
    numbers: Sequence[int],
    miners: Sequence[str | None] | None = None,
    fees: Sequence[int | None] | None = None,
) -> pa.Table:
    n = len(numbers)
    return pa.table(
        {
            "number": numbers,
            "hash": [f"h{i}" for i in numbers],
            "parent_hash": [f"h{i - 1}" for i in numbers],
            "timestamp": [datetime(2026, 10, 1, 0, 0, i % 60) for i in numbers],
            "miner": miners if miners is not None else ["m"] * n,
            "gas_used": [1] * n,
            "gas_limit": [2] * n,
            "base_fee_per_gas": fees if fees is not None else [7] * n,
            "transaction_count": [0] * n,
            "size": [3] * n,
        },
        schema=BLOCKS_SCHEMA,
    )


@pytest.fixture
def fs() -> FileSystem:
    return LocalFileSystem()


@pytest.fixture
def root(tmp_path: Path) -> str:
    (tmp_path / "bronze").mkdir()
    return str(tmp_path / "bronze")


@pytest.fixture
def artifacts(tmp_path: Path) -> str:
    (tmp_path / "artifacts").mkdir()
    return str(tmp_path / "artifacts")


def write_bronze(
    root: str,
    files: Sequence[pa.Table],
    *,
    table: str = TABLE,
    day: date = DAY,
    marker_rows: int | None = None,
    marker_files: int | None = None,
) -> None:
    """Write part files, then a marker that tells the truth unless told otherwise."""
    directory = Path(partition_dir(root, table, day))
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.glob("part-*.parquet"):
        old.unlink()
    for index, data in enumerate(files):
        pq.write_table(data, directory / f"part-{index:05d}.parquet")
    write_marker(
        LocalFileSystem(),
        marker_path(root, table, day),
        Marker(
            table=table,
            date=day,
            rows=sum(t.num_rows for t in files) if marker_rows is None else marker_rows,
            source_last_modified=INGESTED_AT,
            source_files=len(files) if marker_files is None else marker_files,
            source_bytes=1,
            columns=list(COLUMNS[table]),
            ingested_at=INGESTED_AT,
        ),
    )


def read_profile(artifacts: str, table: str = TABLE, day: date = DAY) -> pa.Table:
    return pq.read_table(profile_path(artifacts, table, day))


def by_column(table: pa.Table) -> dict[str, dict[str, object]]:
    return {row["column_name"]: row for row in table.to_pylist()}


# -- aggregation: pure, no filesystem ---------------------------------------------------


def test_aggregate_counts_nulls_and_ranges() -> None:
    data = blocks([10, 11, 12], miners=["a", None, "a"], fees=[5, None, 9])
    rows, stats = aggregate(BLOCKS_SCHEMA, data.to_batches())
    got = {s.column: s for s in stats}
    assert rows == 3
    assert got["number"].min_value == "10"
    assert got["number"].max_value == "12"
    assert got["base_fee_per_gas"].null_count == 1
    assert got["base_fee_per_gas"].min_value == "5"
    assert got["base_fee_per_gas"].max_value == "9"
    assert got["timestamp"].min_value == "2026-10-01 00:00:10.000000000"
    assert got["timestamp"].max_value == "2026-10-01 00:00:12.000000000"


def test_every_address_column_in_bronze_gets_a_distinct_count() -> None:
    named_like_an_address = {
        column
        for columns in COLUMNS.values()
        for column in columns
        if column == "miner" or column.endswith("_address")
    }
    assert named_like_an_address == profile.ADDRESS_COLUMNS


def test_every_bounded_column_exists_in_bronze() -> None:
    every_column = {column for columns in COLUMNS.values() for column in columns}
    assert set(profile.BOUNDS) <= every_column


def test_out_of_range_counts_rows_outside_the_bound_and_leaves_them_in_place() -> None:
    uint256_ceiling = float(2**256)
    schema = pa.schema([("value", pa.float64()), ("gas_used", pa.int64())])
    data = pa.table(
        {
            "value": [0.0, 5e22, uint256_ceiling, -1.0, 1.9e202, None, 1.4e125],
            "gas_used": [1, 2, 3, 4, 5, 6, 7],
        },
        schema=schema,
    )
    rows, stats = aggregate(schema, data.to_batches(max_chunksize=3))
    got = {s.column: s for s in stats}
    assert rows == 7  # nothing is dropped
    assert got["value"].out_of_range_count == 3  # -1, 1.9e202, 1.4e125; the ceiling is allowed
    assert got["value"].max_value == "1.9e+202"
    assert got["gas_used"].out_of_range_count is None  # not in BOUNDS


def test_a_column_with_a_bound_but_no_rows_counts_zero() -> None:
    schema = pa.schema([("value", pa.float64())])
    _, stats = aggregate(schema, [])
    assert stats[0].out_of_range_count == 0


def test_distinct_count_exists_only_for_address_columns_and_ignores_nulls() -> None:
    data = blocks([1, 2, 3, 4], miners=["a", "b", "a", None])
    _, stats = aggregate(BLOCKS_SCHEMA, data.to_batches())
    got = {s.column: s for s in stats}
    assert got["miner"].distinct_count == 2
    assert got["number"].distinct_count is None
    assert got["hash"].distinct_count is None


def test_empty_strings_are_counted_apart_from_nulls_and_only_in_text_columns() -> None:
    data = blocks([1, 2, 3, 4], miners=["", "", None, "a"])
    _, stats = aggregate(BLOCKS_SCHEMA, data.to_batches(max_chunksize=3))
    got = {s.column: s for s in stats}
    assert got["miner"].empty_count == 2
    assert got["miner"].null_count == 1
    assert got["hash"].empty_count == 0
    assert got["number"].empty_count is None


def test_an_all_null_text_column_has_no_empty_strings() -> None:
    data = blocks([1, 2], miners=[None, None])
    _, stats = aggregate(BLOCKS_SCHEMA, data.to_batches())
    assert {s.column: s for s in stats}["miner"].empty_count == 0


def test_statistics_do_not_depend_on_how_rows_are_batched() -> None:
    data = blocks([5, 1, 9, 3, 7, 2], miners=["a", "b", "a", "c", None, "b"])
    whole = aggregate(BLOCKS_SCHEMA, data.to_batches(max_chunksize=100))
    split = aggregate(BLOCKS_SCHEMA, data.to_batches(max_chunksize=1))
    assert whole == split
    assert whole[0] == 6


def test_an_entirely_null_column_has_a_rate_of_one_and_no_range() -> None:
    data = blocks([1, 2, 3], miners=[None, None, None], fees=[None, None, None])
    rows, stats = aggregate(BLOCKS_SCHEMA, data.to_batches())
    got = {s.column: s for s in stats}
    assert rows == 3
    assert got["base_fee_per_gas"] == ColumnStats(
        "base_fee_per_gas", "int64", 3, None, None, None, None, None
    )
    assert got["miner"].null_count == 3
    assert got["miner"].distinct_count == 0


def test_zero_rows_still_yields_one_entry_per_column() -> None:
    rows, stats = aggregate(BLOCKS_SCHEMA, [])
    assert rows == 0
    assert [s.column for s in stats] == list(COLUMNS[TABLE])
    assert all(s.null_count == 0 and s.min_value is None for s in stats)
    assert {s.column: s.distinct_count for s in stats}["miner"] == 0


# -- one partition ----------------------------------------------------------------------


def test_profile_is_written_one_row_per_column_with_the_declared_schema(
    fs: FileSystem, root: str, artifacts: str
) -> None:
    write_bronze(root, [blocks([1, 2]), blocks([3])])
    result = profile_partition(fs, root, artifacts, TABLE, DAY)
    assert result.outcome is Outcome.PROFILED
    assert result.rows == 3

    written = read_profile(artifacts)
    assert written.schema.equals(PROFILE_SCHEMA)
    assert written.column("column_name").to_pylist() == list(COLUMNS[TABLE])
    row = by_column(written)["number"]
    assert row["table_name"] == TABLE
    assert row["day"] == DAY
    assert row["rows"] == 3
    assert row["min_value"] == "1"
    assert row["max_value"] == "3"
    assert row["marker_ingested_at"] == INGESTED_AT
    parts = Path(partition_dir(root, TABLE, DAY)).glob("*.parquet")
    expected_bytes = sum(p.stat().st_size for p in parts)
    assert row["bytes_on_disk"] == expected_bytes > 0


def test_all_null_and_zero_row_partitions_are_written_with_the_same_schema(
    fs: FileSystem, root: str, artifacts: str
) -> None:
    write_bronze(root, [blocks([1, 2], fees=[None, None])])
    profile_partition(fs, root, artifacts, TABLE, DAY)
    nulls = by_column(read_profile(artifacts))["base_fee_per_gas"]
    assert nulls["null_rate"] == 1.0
    assert nulls["min_value"] is None

    empty_day = date(2026, 10, 2)
    write_bronze(root, [blocks([])], day=empty_day)
    result = profile_partition(fs, root, artifacts, TABLE, empty_day)
    assert result.outcome is Outcome.PROFILED
    assert result.rows == 0
    empty = read_profile(artifacts, day=empty_day)
    assert empty.schema.equals(PROFILE_SCHEMA)
    assert empty.num_rows == len(COLUMNS[TABLE])
    assert set(empty.column("null_rate").to_pylist()) == {None}  # a rate of nothing


def test_a_partition_without_a_marker_is_skipped_not_profiled_as_empty(
    fs: FileSystem, root: str, artifacts: str
) -> None:
    write_bronze(root, [blocks([1, 2])])
    Path(marker_path(root, TABLE, DAY)).unlink()
    result = profile_partition(fs, root, artifacts, TABLE, DAY)
    assert result.outcome is Outcome.SKIPPED
    assert "_SUCCESS" in result.detail
    assert not Path(profile_path(artifacts, TABLE, DAY)).exists()


def test_a_corrupt_marker_raises_rather_than_reading_as_absent(
    fs: FileSystem, root: str, artifacts: str
) -> None:
    write_bronze(root, [blocks([1])])
    Path(marker_path(root, TABLE, DAY)).write_text("{not json")
    with pytest.raises(CorruptMarkerError):
        profile_partition(fs, root, artifacts, TABLE, DAY)
    assert not Path(profile_path(artifacts, TABLE, DAY)).exists()


def test_reprofiling_replaces_the_entry_instead_of_appending(
    fs: FileSystem, root: str, artifacts: str
) -> None:
    write_bronze(root, [blocks([1, 2])])
    profile_partition(fs, root, artifacts, TABLE, DAY)
    first = read_profile(artifacts)

    profile_partition(fs, root, artifacts, TABLE, DAY)
    assert read_profile(artifacts).equals(first)  # unchanged data: identical profile

    write_bronze(root, [blocks([1, 2, 3, 4])])  # the partition was re-ingested bigger
    profile_partition(fs, root, artifacts, TABLE, DAY)
    again = read_profile(artifacts)
    assert again.num_rows == len(COLUMNS[TABLE])
    assert set(again.column("rows").to_pylist()) == {4}
    assert len(list(Path(profile_path(artifacts, TABLE, DAY)).parent.iterdir())) == 1


def test_rows_that_disagree_with_the_marker_fail_and_write_nothing(
    fs: FileSystem, root: str, artifacts: str
) -> None:
    write_bronze(root, [blocks([1, 2])], marker_rows=5)
    with pytest.raises(ProfileError, match=r"blocks 2026-10-01.*read 2 rows, marker says 5"):
        profile_partition(fs, root, artifacts, TABLE, DAY)
    assert not Path(profile_path(artifacts, TABLE, DAY)).exists()


def test_a_missing_part_file_fails_naming_table_and_date(
    fs: FileSystem, root: str, artifacts: str
) -> None:
    write_bronze(root, [blocks([1, 2])], marker_files=2)
    with pytest.raises(ProfileError, match=r"blocks 2026-10-01: 1 part files, marker says 2"):
        profile_partition(fs, root, artifacts, TABLE, DAY)


def test_part_files_that_disagree_on_schema_fail(fs: FileSystem, root: str, artifacts: str) -> None:
    drifted = blocks([3]).drop_columns(["size"])
    write_bronze(root, [blocks([1, 2]), drifted])
    with pytest.raises(ProfileError, match=r"blocks 2026-10-01: part files disagree on schema"):
        profile_partition(fs, root, artifacts, TABLE, DAY)


# -- the window -------------------------------------------------------------------------


def test_one_bad_partition_is_recorded_and_the_rest_still_run(
    fs: FileSystem, root: str, artifacts: str
) -> None:
    days = [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)]
    write_bronze(root, [blocks([1])], day=days[0])
    write_bronze(root, [blocks([2])], day=days[1])
    Path(marker_path(root, TABLE, days[1])).write_text("{not json")
    # days[2] was never ingested: no marker, no data

    results = profile_window(fs, root, artifacts, days, [TABLE])
    assert [r.outcome for r in results] == [Outcome.PROFILED, Outcome.FAILED, Outcome.SKIPPED]
    assert "CorruptMarkerError" in results[1].detail
    assert Path(profile_path(artifacts, TABLE, days[0])).exists()
    assert profile.exit_code(results) == 1


def test_skips_alone_do_not_fail_the_run(fs: FileSystem, root: str, artifacts: str) -> None:
    results = profile_window(fs, root, artifacts, [DAY], [TABLE])
    assert [r.outcome for r in results] == [Outcome.SKIPPED]
    assert profile.exit_code(results) == 0


def test_ctrl_c_still_stops_the_run(
    monkeypatch: pytest.MonkeyPatch, fs: FileSystem, root: str, artifacts: str
) -> None:
    def interrupted(*args: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(profile, "profile_partition", interrupted)
    with pytest.raises(KeyboardInterrupt):
        profile_window(fs, root, artifacts, [DAY], [TABLE])

"""Unit tests for chainrisk.ingest.bronze. No network, no MinIO, no infra/.env.

The source is a directory tree under tmp_path, exposed through a SubTreeFileSystem so the
real source-listing path runs unchanged. The destination is a LocalFileSystem. Marker and
data files are real files, so "absent", "corrupt" and "left behind" are things the tests
look at on disk rather than things they mock.

The two writer faults (drop a batch, raise mid-stream) are the only patching: they inject a
failure the real library will not produce on demand. Each test that uses one asserts on
the observable result (exception, marker, files), not on the patch.
"""

import os
import time
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pyarrow.fs import FileSystem, LocalFileSystem, SubTreeFileSystem

from chainrisk.ingest import bronze
from chainrisk.ingest.bronze import (
    COLUMNS,
    SOURCE_BUCKET,
    SOURCE_PREFIX,
    TABLES,
    WINDOW_DAYS,
    WINDOW_START,
    CorruptMarkerError,
    Marker,
    Outcome,
    PartitionResult,
    PartitionState,
    RowCountMismatchError,
    SourcePartitionMissingError,
    assess_partition,
    check_columns,
    delete_marker,
    exit_code,
    format_result,
    ingest_partition,
    marker_path,
    partition_dir,
    read_marker,
    run_window,
    utc_now,
    window_dates,
    write_marker,
)

DAY = date(2026, 10, 1)
OTHER_DAY = date(2026, 10, 2)

# Present in the fake source, absent from COLUMNS: pruning must drop them. `date` mirrors
# the real source, which carries it as a column as well as a path partition.
SOURCE_ONLY_COLUMNS = ("last_modified", "input", "date")


# ── fixtures and helpers ─────────────────────────────────────────────────────────────


@pytest.fixture
def src_base(tmp_path: Path) -> Path:
    base = tmp_path / "source"
    base.mkdir()
    return base


@pytest.fixture
def src_fs(src_base: Path) -> FileSystem:
    return SubTreeFileSystem(str(src_base), LocalFileSystem())


@pytest.fixture
def dst_fs() -> FileSystem:
    return LocalFileSystem()


@pytest.fixture
def root(tmp_path: Path) -> str:
    path = tmp_path / "bronze"
    path.mkdir()
    return str(path)


def make_source(
    base: Path,
    table: str,
    day: date,
    *,
    files: int = 1,
    rows: int = 6,
    omit: Sequence[str] = (),
) -> list[Path]:
    """Write source parquet for one table-day: every configured column plus extras.

    The types are deliberately NOT representative: every column is int64, whereas the real
    source has `value` as float64, timestamps as timestamp[ns], and so on. These fixtures
    exercise plumbing (columns, counts, markers). They would not catch a type-handling bug
    in schema construction, so do not read them as a mirror of the source.
    """
    folder = base / SOURCE_BUCKET / SOURCE_PREFIX / table / f"date={day.isoformat()}"
    folder.mkdir(parents=True, exist_ok=True)
    names = [c for c in COLUMNS[table] if c not in omit] + list(SOURCE_ONLY_COLUMNS)
    paths = []
    for i in range(files):
        values = list(range(i * rows, (i + 1) * rows))
        path = folder / f"part-{i}.parquet"
        pq.write_table(pa.table(dict.fromkeys(names, values)), path)
        paths.append(path)
    return paths


def part_files(root: str, table: str, day: date) -> list[Path]:
    return sorted(Path(partition_dir(root, table, day)).glob("part-*.parquet"))


def bronze_rows(root: str, table: str, day: date) -> int:
    return sum(pq.ParquetFile(p).metadata.num_rows for p in part_files(root, table, day))


def marker_exists_on_disk(root: str, table: str, day: date) -> bool:
    return Path(marker_path(root, table, day)).exists()


class FaultyWriter:
    """Wraps the real ParquetWriter and fails on one chosen batch."""

    def __init__(self, real: Any, drop_at: int | None, raise_at: int | None, exc: BaseException):
        self._real = real
        self._drop_at = drop_at
        self._raise_at = raise_at
        self._exc = exc
        self._seen = 0

    def __enter__(self) -> "FaultyWriter":
        self._real.__enter__()
        return self

    def __exit__(self, *args: Any) -> Any:
        return self._real.__exit__(*args)

    def write_batch(self, batch: Any) -> None:
        index = self._seen
        self._seen += 1
        if index == self._raise_at:
            raise self._exc
        if index == self._drop_at:
            return  # silently lose this batch
        self._real.write_batch(batch)


def inject_writer_fault(
    monkeypatch: pytest.MonkeyPatch,
    *,
    drop_at: int | None = None,
    raise_at: int | None = None,
    exc: BaseException | None = None,
) -> None:
    """Make bronze's writer drop or fail on batch N. Uses 2-row batches so a small
    source file spans several."""
    real_writer = pq.ParquetWriter
    monkeypatch.setattr(bronze, "BATCH_ROWS", 2)
    monkeypatch.setattr(
        pq,
        "ParquetWriter",
        lambda *a, **k: FaultyWriter(
            real_writer(*a, **k), drop_at, raise_at, exc or RuntimeError("injected")
        ),
    )


# ── happy path ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("table", TABLES)
def test_ingest_writes_pruned_columns_and_a_marker(
    table: str, src_base: Path, src_fs: FileSystem, dst_fs: FileSystem, root: str
) -> None:
    make_source(src_base, table, DAY, files=2, rows=6)

    result = ingest_partition(src_fs, dst_fs, root, table, DAY)

    assert result.outcome is Outcome.INGESTED
    assert result.rows == 12
    parts = part_files(root, table, DAY)
    assert [p.name for p in parts] == ["part-00000.parquet", "part-00001.parquet"]
    for part in parts:
        assert pq.ParquetFile(part).schema_arrow.names == list(COLUMNS[table])
    marker = read_marker(dst_fs, marker_path(root, table, DAY))
    assert marker is not None
    assert (marker.table, marker.date, marker.rows) == (table, DAY, 12)
    assert marker.columns == list(COLUMNS[table])
    assert marker.source_files == 2
    assert marker.source_bytes == sum(
        p.stat().st_size
        for p in (src_base / SOURCE_BUCKET / SOURCE_PREFIX / table / f"date={DAY}").iterdir()
    )


def test_columns_config_drops_what_the_adr_says_to_drop() -> None:
    for table, columns in COLUMNS.items():
        assert "last_modified" not in columns, table  # captured in the marker instead
        assert "date" not in columns, table  # carried by the hive path partition
        assert len(set(columns)) == len(columns), table


# ── AC: completeness ─────────────────────────────────────────────────────────────────


def test_mismatched_row_count_fails_naming_table_and_date_and_leaves_no_marker(
    src_base: Path,
    src_fs: FileSystem,
    dst_fs: FileSystem,
    root: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_source(src_base, "transactions", DAY, rows=6)
    inject_writer_fault(monkeypatch, drop_at=1)

    with pytest.raises(RowCountMismatchError) as caught:
        ingest_partition(src_fs, dst_fs, root, "transactions", DAY)

    assert "transactions" in str(caught.value)
    assert str(DAY) in str(caught.value)
    assert not marker_exists_on_disk(root, "transactions", DAY)
    assert read_marker(dst_fs, marker_path(root, "transactions", DAY)) is None


# ── AC: missing source ───────────────────────────────────────────────────────────────


def test_missing_source_partition_fails_naming_table_and_date(
    src_fs: FileSystem, dst_fs: FileSystem, root: str
) -> None:
    with pytest.raises(SourcePartitionMissingError) as caught:
        ingest_partition(src_fs, dst_fs, root, "blocks", DAY)

    assert "blocks" in str(caught.value)
    assert str(DAY) in str(caught.value)
    assert not Path(partition_dir(root, "blocks", DAY)).exists()


def test_source_file_nested_below_the_date_folder_is_still_found(
    src_base: Path, src_fs: FileSystem, dst_fs: FileSystem, root: str
) -> None:
    (path,) = make_source(src_base, "blocks", DAY)
    nested = path.parent / "nested"
    nested.mkdir()
    path.rename(nested / path.name)

    assert ingest_partition(src_fs, dst_fs, root, "blocks", DAY).outcome is Outcome.INGESTED


def test_schema_drift_names_the_table_and_every_missing_column(
    src_base: Path, src_fs: FileSystem, dst_fs: FileSystem, root: str
) -> None:
    make_source(src_base, "transactions", DAY, omit=("receipt_status", "value"))

    with pytest.raises(ValueError, match="transactions") as caught:
        ingest_partition(src_fs, dst_fs, root, "transactions", DAY)

    assert "receipt_status" in str(caught.value)
    assert "value" in str(caught.value)
    assert not marker_exists_on_disk(root, "transactions", DAY)


def test_check_columns_accepts_extra_source_columns() -> None:
    # One-directional by design: a column AWS adds is not an error.
    check_columns("blocks", [*COLUMNS["blocks"], "brand_new_column"])


# ── AC: atomic partitions ────────────────────────────────────────────────────────────


def test_interrupted_write_leaves_no_marker_and_cleans_up(
    src_base: Path,
    src_fs: FileSystem,
    dst_fs: FileSystem,
    root: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_source(src_base, "blocks", DAY, rows=6)
    inject_writer_fault(monkeypatch, raise_at=1, exc=OSError("connection dropped"))

    with pytest.raises(OSError, match="connection dropped"):
        ingest_partition(src_fs, dst_fs, root, "blocks", DAY)

    assert not marker_exists_on_disk(root, "blocks", DAY)
    assert part_files(root, "blocks", DAY) == []


def test_killed_process_leaves_data_but_no_marker_and_the_next_run_recovers(
    src_base: Path,
    src_fs: FileSystem,
    dst_fs: FileSystem,
    root: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # KeyboardInterrupt is a BaseException: the tidy-up in ingest_partition does not run,
    # which is what a kill looks like. Data objects are left behind.
    make_source(src_base, "blocks", DAY, rows=6)
    with monkeypatch.context() as patch:
        inject_writer_fault(patch, raise_at=1, exc=KeyboardInterrupt())
        with pytest.raises(KeyboardInterrupt):
            ingest_partition(src_fs, dst_fs, root, "blocks", DAY)

    assert part_files(root, "blocks", DAY), "test setup: a half-written file should remain"
    assert not marker_exists_on_disk(root, "blocks", DAY)
    assert read_marker(dst_fs, marker_path(root, "blocks", DAY)) is None  # reads as absent

    result = ingest_partition(src_fs, dst_fs, root, "blocks", DAY)

    assert result.outcome is Outcome.INGESTED
    assert result.rows == 6
    assert bronze_rows(root, "blocks", DAY) == 6  # the leftovers did not add rows


class DecoyFooter:
    """Delegates to a real filesystem, but reading back any written part file returns a
    different, shorter file. The data written is correct; only the footer read disagrees."""

    def __init__(self, real: FileSystem, decoy: Path) -> None:
        self._real = real
        self._decoy = decoy

    def open_input_file(self, path: str) -> Any:
        if Path(path).name.startswith("part-"):
            return self._real.open_input_file(str(self._decoy))
        return self._real.open_input_file(path)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def test_written_count_is_read_back_from_the_destination_independently(
    src_base: Path, src_fs: FileSystem, dst_fs: FileSystem, root: str, tmp_path: Path
) -> None:
    # Falsifies the third leg of expected == streamed == written. Dropping a batch makes
    # streamed and written short together, so it cannot tell the legs apart. Here the
    # source count and the streamed count agree and only the destination footer differs;
    # if "written" were read from anywhere but the destination, this would pass silently.
    make_source(src_base, "blocks", DAY, rows=6)
    decoy = tmp_path / "decoy.parquet"
    pq.write_table(pa.table({"number": [1]}), decoy)
    Path(partition_dir(root, "blocks", DAY)).mkdir(parents=True)  # the proxy skips create_dir

    with pytest.raises(RowCountMismatchError) as caught:
        ingest_partition(src_fs, cast(FileSystem, DecoyFooter(dst_fs, decoy)), root, "blocks", DAY)

    assert "wrote 1" in str(caught.value)
    assert "source has 6" in str(caught.value)
    assert not marker_exists_on_disk(root, "blocks", DAY)


class KilledDuringCleanup:
    """Delegates to a real filesystem, but the process "dies" when stale data is cleared."""

    def __init__(self, real: FileSystem) -> None:
        self._real = real

    def delete_dir_contents(self, *args: Any, **kwargs: Any) -> None:
        raise KeyboardInterrupt

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def test_marker_is_removed_before_stale_data_is_touched(
    src_base: Path, src_fs: FileSystem, dst_fs: FileSystem, root: str
) -> None:
    # The invariant lives in the window between "delete the marker" and "delete the data".
    # Dying inside that window must leave the partition absent, never marked complete over
    # data that is being torn down. (Interrupting the later write would not tell the two
    # orders apart: by then both deletes have already happened.)
    (source,) = make_source(src_base, "blocks", DAY, rows=6)
    ingest_partition(src_fs, dst_fs, root, "blocks", DAY)
    later = time.time() + 3600
    os.utime(source, (later, later))  # AWS re-delivered: the partition is now stale

    with pytest.raises(KeyboardInterrupt):
        ingest_partition(src_fs, cast(FileSystem, KilledDuringCleanup(dst_fs)), root, "blocks", DAY)

    assert part_files(root, "blocks", DAY), "test setup: the old data should still be there"
    assert not marker_exists_on_disk(root, "blocks", DAY)
    assert read_marker(dst_fs, marker_path(root, "blocks", DAY)) is None


# ── AC: idempotency ──────────────────────────────────────────────────────────────────


def test_rerun_of_a_current_partition_is_a_noop(
    src_base: Path, src_fs: FileSystem, dst_fs: FileSystem, root: str
) -> None:
    make_source(src_base, "blocks", DAY, files=2, rows=6)
    ingest_partition(src_fs, dst_fs, root, "blocks", DAY)
    touched = [*part_files(root, "blocks", DAY), Path(marker_path(root, "blocks", DAY))]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in touched}

    result = ingest_partition(src_fs, dst_fs, root, "blocks", DAY)

    assert result.outcome is Outcome.SKIPPED
    assert result.rows == 12
    assert "already ingested" in result.detail
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in touched} == before
    assert bronze_rows(root, "blocks", DAY) == 12  # no duplicates


def test_redelivered_source_is_reingested_without_duplicating_rows(
    src_base: Path, src_fs: FileSystem, dst_fs: FileSystem, root: str
) -> None:
    (source,) = make_source(src_base, "blocks", DAY, rows=6)
    ingest_partition(src_fs, dst_fs, root, "blocks", DAY)
    later = time.time() + 3600
    os.utime(source, (later, later))

    result = ingest_partition(src_fs, dst_fs, root, "blocks", DAY)

    assert result.outcome is Outcome.INGESTED
    assert "re-delivered" in result.detail
    assert bronze_rows(root, "blocks", DAY) == 6


def test_widened_column_selection_is_reingested(
    src_base: Path,
    src_fs: FileSystem,
    dst_fs: FileSystem,
    root: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_source(src_base, "blocks", DAY)
    ingest_partition(src_fs, dst_fs, root, "blocks", DAY)
    monkeypatch.setitem(COLUMNS, "blocks", (*COLUMNS["blocks"], "input"))

    result = ingest_partition(src_fs, dst_fs, root, "blocks", DAY)

    assert result.outcome is Outcome.INGESTED
    assert "column selection changed" in result.detail
    assert "input" in pq.ParquetFile(part_files(root, "blocks", DAY)[0]).schema_arrow.names


def test_reingest_does_not_leave_parts_from_a_larger_previous_layout(
    src_base: Path, src_fs: FileSystem, dst_fs: FileSystem, root: str
) -> None:
    paths = make_source(src_base, "blocks", DAY, files=3, rows=2)
    ingest_partition(src_fs, dst_fs, root, "blocks", DAY)
    for extra in paths[1:]:
        extra.unlink()  # AWS now delivers one file
    later = time.time() + 3600
    os.utime(paths[0], (later, later))

    ingest_partition(src_fs, dst_fs, root, "blocks", DAY)

    assert [p.name for p in part_files(root, "blocks", DAY)] == ["part-00000.parquet"]
    assert bronze_rows(root, "blocks", DAY) == 2


# ── marker behaviour ─────────────────────────────────────────────────────────────────


def make_marker(**overrides: Any) -> Marker:
    fields: dict[str, Any] = {
        "table": "blocks",
        "date": DAY,
        "rows": 6,
        "source_last_modified": utc_now(),
        "source_files": 1,
        "source_bytes": 100,
        "columns": list(COLUMNS["blocks"]),
        "ingested_at": utc_now(),
    }
    return Marker(**{**fields, **overrides})


def test_marker_round_trips(dst_fs: FileSystem, root: str) -> None:
    path = marker_path(root, "blocks", DAY)
    Path(path).parent.mkdir(parents=True)
    marker = make_marker()

    write_marker(dst_fs, path, marker)

    assert read_marker(dst_fs, path) == marker


def test_missing_marker_reads_as_none(dst_fs: FileSystem, root: str) -> None:
    assert read_marker(dst_fs, marker_path(root, "blocks", DAY)) is None


@pytest.mark.parametrize(
    "body",
    [
        "{not json",
        "",
        '{"marker_version": 1}',  # valid JSON, required fields missing
    ],
)
def test_unreadable_marker_raises_instead_of_reading_as_absent(
    body: str, dst_fs: FileSystem, root: str
) -> None:
    path = Path(marker_path(root, "blocks", DAY))
    path.parent.mkdir(parents=True)
    path.write_text(body, encoding="utf-8")

    with pytest.raises(CorruptMarkerError):
        read_marker(dst_fs, str(path))


def test_marker_with_an_unknown_field_is_corrupt_not_current(dst_fs: FileSystem, root: str) -> None:
    path = Path(marker_path(root, "blocks", DAY))
    path.parent.mkdir(parents=True)
    body = make_marker().model_dump_json()[:-1] + ', "marker_vershun": 2}'
    path.write_text(body, encoding="utf-8")

    with pytest.raises(CorruptMarkerError):
        read_marker(dst_fs, str(path))


def test_marker_from_another_version_is_rejected(dst_fs: FileSystem, root: str) -> None:
    path = marker_path(root, "blocks", DAY)
    Path(path).parent.mkdir(parents=True)
    write_marker(dst_fs, path, make_marker(marker_version=bronze.MARKER_VERSION + 1))

    with pytest.raises(CorruptMarkerError, match="marker_version"):
        read_marker(dst_fs, path)


def test_corrupt_marker_fails_the_partition_and_is_not_silently_overwritten(
    src_base: Path, src_fs: FileSystem, dst_fs: FileSystem, root: str
) -> None:
    make_source(src_base, "blocks", DAY)
    path = Path(marker_path(root, "blocks", DAY))
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(CorruptMarkerError):
        ingest_partition(src_fs, dst_fs, root, "blocks", DAY)

    assert path.read_text(encoding="utf-8") == "{not json"
    assert part_files(root, "blocks", DAY) == []


def test_delete_marker_is_idempotent(dst_fs: FileSystem, root: str) -> None:
    path = marker_path(root, "blocks", DAY)
    Path(path).parent.mkdir(parents=True)
    write_marker(dst_fs, path, make_marker())

    delete_marker(dst_fs, path)
    delete_marker(dst_fs, path)

    assert read_marker(dst_fs, path) is None


def test_marker_rejects_nonsense_counts() -> None:
    with pytest.raises(ValueError):
        make_marker(rows=-1)
    with pytest.raises(ValueError):
        make_marker(source_files=0)
    with pytest.raises(ValueError):
        make_marker(source_last_modified=datetime(2026, 10, 1))  # naive


# ── assess_partition ─────────────────────────────────────────────────────────────────


def test_assess_absent_current_and_stale() -> None:
    marker = make_marker()
    columns = COLUMNS["blocks"]

    assert assess_partition(None, columns, utc_now()).state is PartitionState.ABSENT
    assert (
        assess_partition(marker, columns, marker.source_last_modified).state
        is PartitionState.CURRENT
    )
    redelivered = marker.source_last_modified.replace(year=marker.source_last_modified.year + 1)
    assert assess_partition(marker, columns, redelivered).state is PartitionState.STALE
    assert assess_partition(marker, columns[:-1], marker.source_last_modified).state is (
        PartitionState.STALE
    )


def test_assess_rejects_a_naive_source_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        assess_partition(make_marker(), COLUMNS["blocks"], datetime(2026, 10, 1))


# ── AC: a failed partition does not stop the others ──────────────────────────────────


def test_run_window_continues_past_a_failed_partition_and_reports_each(
    src_base: Path, src_fs: FileSystem, dst_fs: FileSystem, root: str
) -> None:
    make_source(src_base, "blocks", DAY)
    make_source(src_base, "blocks", date(2026, 10, 3))  # 2026-10-02 is deliberately missing
    seen: list[PartitionResult] = []

    results = run_window(
        src_fs,
        dst_fs,
        root,
        [DAY, OTHER_DAY, date(2026, 10, 3)],
        ("blocks",),
        on_result=seen.append,
    )

    assert [r.outcome for r in results] == [Outcome.INGESTED, Outcome.FAILED, Outcome.INGESTED]
    assert seen == results
    assert results[1].day == OTHER_DAY
    assert "SourcePartitionMissingError" in results[1].detail
    assert exit_code(results) == 1
    assert marker_exists_on_disk(root, "blocks", date(2026, 10, 3))


def test_run_window_reports_progress_before_each_partition(
    src_base: Path, src_fs: FileSystem, dst_fs: FileSystem, root: str
) -> None:
    make_source(src_base, "blocks", DAY)
    started: list[tuple[str, date]] = []

    run_window(
        src_fs, dst_fs, root, [DAY], ("blocks",), on_start=lambda t, d: started.append((t, d))
    )

    assert started == [("blocks", DAY)]


def test_run_window_exits_zero_when_everything_succeeds_or_is_skipped(
    src_base: Path, src_fs: FileSystem, dst_fs: FileSystem, root: str
) -> None:
    for table in TABLES:
        make_source(src_base, table, DAY)

    first = run_window(src_fs, dst_fs, root, [DAY])
    second = run_window(src_fs, dst_fs, root, [DAY])

    assert [r.outcome for r in first] == [Outcome.INGESTED] * len(TABLES)
    assert [r.outcome for r in second] == [Outcome.SKIPPED] * len(TABLES)
    assert exit_code(first) == exit_code(second) == 0


def test_run_window_turns_an_unexpected_error_into_a_failed_partition(
    src_base: Path,
    src_fs: FileSystem,
    dst_fs: FileSystem,
    root: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_source(src_base, "blocks", DAY)
    inject_writer_fault(monkeypatch, raise_at=0, exc=OSError("connection dropped"))

    (result,) = run_window(src_fs, dst_fs, root, [DAY], ("blocks",))

    assert result.outcome is Outcome.FAILED
    assert "OSError" in result.detail
    assert "connection dropped" in result.detail


def test_run_window_does_not_swallow_keyboard_interrupt(
    src_base: Path,
    src_fs: FileSystem,
    dst_fs: FileSystem,
    root: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_source(src_base, "blocks", DAY)
    inject_writer_fault(monkeypatch, raise_at=0, exc=KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        run_window(src_fs, dst_fs, root, [DAY], ("blocks",))


# ── window and reporting ─────────────────────────────────────────────────────────────


def test_default_window_is_seven_consecutive_days_from_the_start() -> None:
    days = window_dates()

    assert len(days) == WINDOW_DAYS == 7
    assert days[0] == WINDOW_START
    # A literal, not derived from the result: "every gap equals the first gap" would also
    # pass for a window that skips days.
    assert [b - a for a, b in zip(days, days[1:], strict=False)] == [timedelta(days=1)] * 6


def test_window_dates_takes_arguments_instead_of_needing_patches() -> None:
    assert window_dates(date(2026, 1, 30), 3) == [
        date(2026, 1, 30),
        date(2026, 1, 31),
        date(2026, 2, 1),
    ]


def test_format_result_names_table_date_and_outcome() -> None:
    ok = PartitionResult("blocks", DAY, Outcome.INGESTED, 7176, "new partition")
    failed = PartitionResult("blocks", DAY, Outcome.FAILED, 0, "OSError: boom")

    assert "blocks" in format_result(ok)
    assert str(DAY) in format_result(ok)
    assert "7,176 rows" in format_result(ok)
    assert "FAIL" in format_result(failed)
    assert "OSError: boom" in format_result(failed)


def test_partition_layout_is_hive_style_under_eth() -> None:
    assert partition_dir("bucket", "blocks", DAY) == "bucket/eth/blocks/date=2026-10-01"
    assert marker_path("bucket", "blocks", DAY) == "bucket/eth/blocks/date=2026-10-01/_SUCCESS"

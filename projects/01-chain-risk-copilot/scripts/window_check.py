"""Verify the bronze window against the source. Read-only.

    uv run python scripts/window_check.py

For every table-day, checks that the _SUCCESS marker rows == bronze footer rows ==
source footer rows, that bronze has one part file per source file, and that the written
columns equal COLUMNS[table]. Prints ok/BAD per partition and "MISMATCHES: n"; exit code
is 1 if any partition mismatched. The 2026-10-26 sprint-log figures came from this check.
"""

import sys
from collections.abc import Sequence
from datetime import date
from typing import NamedTuple

import pyarrow.parquet as pq
from pyarrow.fs import FileInfo, FileSelector, FileSystem, FileType

from chainrisk.ingest.bronze import (
    COLUMNS,
    TABLES,
    bronze_fs,
    marker_path,
    partition_dir,
    read_marker,
    source_files,
    source_fs,
    window_dates,
)
from chainrisk.settings import get_settings


class PartitionCheck(NamedTuple):
    table: str
    day: date
    ok: bool
    source_rows: int
    bronze_rows: int
    marker_rows: int | None


def footer_rows(fs: FileSystem, infos: Sequence[FileInfo]) -> int:
    """Total rows from the Parquet footers alone; no row data is read."""
    total = 0
    for info in infos:
        with fs.open_input_file(info.path) as handle:
            total += pq.ParquetFile(handle).metadata.num_rows
    return total


def check_partition(
    src_fs: FileSystem, dst_fs: FileSystem, root: str, table: str, day: date
) -> PartitionCheck:
    sources = source_files(src_fs, table, day)
    source_rows = footer_rows(src_fs, sources)

    selector = FileSelector(partition_dir(root, table, day), allow_not_found=True)
    parts = [i for i in dst_fs.get_file_info(selector) if i.path.endswith(".parquet")]
    bronze_rows = footer_rows(dst_fs, parts)
    columns_ok = True
    for part in parts:
        with dst_fs.open_input_file(part.path) as handle:
            columns_ok &= pq.ParquetFile(handle).schema_arrow.names == list(COLUMNS[table])

    marker = read_marker(dst_fs, marker_path(root, table, day))
    ok = (
        marker is not None
        and marker.rows == source_rows == bronze_rows
        and len(parts) == len(sources)
        and columns_ok
    )
    return PartitionCheck(table, day, ok, source_rows, bronze_rows, marker.rows if marker else None)


def format_check(check: PartitionCheck) -> str:
    label = "ok " if check.ok else "BAD"
    return (
        f"{label} {check.table:<16} {check.day} rows={check.bronze_rows:>10,} "
        f"src={check.source_rows:>10,} marker={check.marker_rows}"
    )


def main() -> int:
    settings = get_settings()
    src_fs, dst_fs = source_fs(), bronze_fs(settings)
    if dst_fs.get_file_info(settings.bronze_bucket).type != FileType.Directory:
        print(f"bronze bucket {settings.bronze_bucket!r} not found; is the stack up?")
        return 2

    mismatches = 0
    for table in TABLES:
        for day in window_dates():
            check = check_partition(src_fs, dst_fs, settings.bronze_bucket, table, day)
            mismatches += not check.ok
            print(format_check(check), flush=True)
    print("MISMATCHES:", mismatches)
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())

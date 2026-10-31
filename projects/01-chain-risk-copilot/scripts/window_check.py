"""Verify the bronze window against the source. Read-only.

    uv run python scripts/window_check.py

For every table-day, checks that the _SUCCESS marker rows == bronze footer rows ==
source footer rows, that bronze has one part file per source file, and that the written
columns equal COLUMNS[table]. Prints ok/BAD per partition and "MISMATCHES: n"; exit code
is 1 if any partition mismatched. The 2026-10-26 sprint-log figures came from this check.
"""

import sys
from collections.abc import Callable, Sequence
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
    detail: str = ""


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


def check_window(
    src_fs: FileSystem,
    dst_fs: FileSystem,
    root: str,
    days: Sequence[date],
    tables: Sequence[str] = TABLES,
    on_result: Callable[[PartitionCheck], None] | None = None,
) -> list[PartitionCheck]:
    """Check every table-day. One bad partition never stops the rest.

    Mirrors run_window: any Exception (missing source, corrupt marker, dropped connection)
    is that partition failing, recorded with its reason. KeyboardInterrupt still propagates.
    """
    checks: list[PartitionCheck] = []
    for table in tables:
        for day in days:
            try:
                check = check_partition(src_fs, dst_fs, root, table, day)
            except Exception as exc:
                check = PartitionCheck(
                    table, day, False, 0, 0, None, f"{type(exc).__name__}: {exc}"
                )
            checks.append(check)
            if on_result:
                on_result(check)
    return checks


def format_check(check: PartitionCheck) -> str:
    label = "ok " if check.ok else "BAD"
    if check.detail:
        return f"{label} {check.table:<16} {check.day} {check.detail}"
    return (
        f"{label} {check.table:<16} {check.day} rows={check.bronze_rows:>10,} "
        f"src={check.source_rows:>10,} marker={check.marker_rows}"
    )


def main() -> int:
    """Exit 0 if every partition matched, 1 if any did not, 2 if bronze is unreachable."""
    settings = get_settings()
    root = settings.bronze_bucket
    dst_fs = bronze_fs(settings)
    try:
        reachable = dst_fs.get_file_info(root).type == FileType.Directory
    except OSError as exc:
        print(f"cannot reach bronze bucket {root!r} at {settings.s3_endpoint_url}: {exc}")
        return 2
    if not reachable:
        print(f"bronze bucket {root!r} does not exist at {settings.s3_endpoint_url}")
        return 2

    checks = check_window(
        source_fs(),
        dst_fs,
        root,
        window_dates(),
        on_result=lambda c: print(format_check(c), flush=True),
    )
    mismatches = sum(not c.ok for c in checks)
    print("MISMATCHES:", mismatches)
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())

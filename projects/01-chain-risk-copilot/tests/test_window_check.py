"""Tests for scripts/window_check.py. Real files under tmp_path, no network, no MinIO."""

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pyarrow.fs import FileSystem, LocalFileSystem, SubTreeFileSystem

from chainrisk.ingest.bronze import (
    COLUMNS,
    SOURCE_BUCKET,
    SOURCE_PREFIX,
    ingest_partition,
    marker_path,
    partition_dir,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "window_check.py"
TABLE, DAY = "blocks", date(2026, 10, 1)


@pytest.fixture(scope="module")
def window_check() -> ModuleType:
    spec = importlib.util.spec_from_file_location("window_check_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def src_fs(tmp_path: Path) -> FileSystem:
    folder = tmp_path / "source" / SOURCE_BUCKET / SOURCE_PREFIX / TABLE / f"date={DAY}"
    folder.mkdir(parents=True)
    names = [*COLUMNS[TABLE], "last_modified"]
    pq.write_table(pa.table(dict.fromkeys(names, [1, 2, 3])), folder / "part-0.parquet")
    return SubTreeFileSystem(str(tmp_path / "source"), LocalFileSystem())


@pytest.fixture
def root(tmp_path: Path) -> str:
    (tmp_path / "bronze").mkdir()
    return str(tmp_path / "bronze")


def test_an_ingested_partition_checks_ok(
    window_check: ModuleType, src_fs: FileSystem, root: str
) -> None:
    dst = LocalFileSystem()
    ingest_partition(src_fs, dst, root, TABLE, DAY)
    check = window_check.check_partition(src_fs, dst, root, TABLE, DAY)
    assert check.ok
    assert check.source_rows == check.bronze_rows == check.marker_rows == 3


def test_a_partition_without_a_marker_is_bad(
    window_check: ModuleType, src_fs: FileSystem, root: str
) -> None:
    dst = LocalFileSystem()
    ingest_partition(src_fs, dst, root, TABLE, DAY)
    Path(marker_path(root, TABLE, DAY)).unlink()
    check = window_check.check_partition(src_fs, dst, root, TABLE, DAY)
    assert not check.ok
    assert check.marker_rows is None


def test_a_lost_part_file_is_bad(window_check: ModuleType, src_fs: FileSystem, root: str) -> None:
    dst = LocalFileSystem()
    ingest_partition(src_fs, dst, root, TABLE, DAY)
    next(Path(partition_dir(root, TABLE, DAY)).glob("part-*.parquet")).unlink()
    check = window_check.check_partition(src_fs, dst, root, TABLE, DAY)
    assert not check.ok
    assert check.bronze_rows == 0

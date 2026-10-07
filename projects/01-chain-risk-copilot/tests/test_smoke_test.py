"""Failure-path tests for scripts/smoke_test.py. No running services needed.

The smoke test exists to be trusted when something is broken, so these prove it fails
loudly: checks return a failing Result (never raise), results name the service and the
reason, hangs are reported, and the exit code is non-zero.
"""

import importlib.util
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar

import pytest
from botocore.exceptions import ClientError

from chainrisk.settings import Settings

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "smoke_test.py"
CREDS = {
    "minio_root_user": "u",
    "minio_root_password": "p",
    "postgres_ops_user": "u",
    "postgres_ops_password": "p",
    "postgres_ops_db": "d",
}


@pytest.fixture(scope="module")
def smoke() -> ModuleType:
    spec = importlib.util.spec_from_file_location("smoke_test_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # @dataclass needs its module importable by name
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, **CREDS)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def fast_timeouts(smoke: ModuleType, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(smoke, "TIMEOUT_S", 2)
    yield


# ── fakes ───────────────────────────────────────────────────────────
class FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self.payload, self.status = payload, status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self) -> dict[str, Any]:
        return self.payload


class FakePg:
    def __init__(self, wal_level: str | None) -> None:
        self.wal_level = wal_level

    def __enter__(self) -> "FakePg":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> "FakePg":
        return self

    def execute(self, _sql: str) -> None:
        return None

    def fetchone(self) -> tuple[str] | None:
        return None if self.wal_level is None else (self.wal_level,)


# ── Postgres ────────────────────────────────────────────────────────
@pytest.mark.parametrize("wal_level", ["replica", "minimal", None])
def test_postgres_connected_but_wrong_wal_level_fails(
    smoke: ModuleType, settings: Settings, monkeypatch: pytest.MonkeyPatch, wal_level: str | None
) -> None:
    monkeypatch.setattr(smoke.psycopg, "connect", lambda *a, **k: FakePg(wal_level))
    r = smoke.check_postgres(settings)
    assert not r.ok
    assert r.service == "postgres"
    assert "wal_level" in r.detail and "logical" in r.detail


def test_postgres_logical_passes(
    smoke: ModuleType, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(smoke.psycopg, "connect", lambda *a, **k: FakePg("logical"))
    assert smoke.check_postgres(settings).ok


def test_postgres_connection_error_is_a_failure_not_an_exception(
    smoke: ModuleType, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise ConnectionError("refused")

    monkeypatch.setattr(smoke.psycopg, "connect", boom)
    r = smoke.check_postgres(settings)
    assert not r.ok and "ConnectionError" in r.detail and "refused" in r.detail


# ── MinIO ───────────────────────────────────────────────────────────
class FakeS3:
    def __init__(self, *, missing_bucket: bool = False, corrupt: bool = False) -> None:
        self.missing_bucket, self.corrupt = missing_bucket, corrupt
        self.deleted: list[str] = []
        self.stored = b""

    def head_bucket(self, Bucket: str) -> None:  # noqa: N803 - boto3's casing
        if self.missing_bucket:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadBucket")

    def put_object(self, Bucket: str, Key: str, Body: bytes) -> None:  # noqa: N803
        self.stored = Body

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803

        class Body:
            def read(_self) -> bytes:  # noqa: N805
                return b"garbage" if self.corrupt else self.stored

        return {"Body": Body()}

    def delete_object(self, Bucket: str, Key: str) -> None:  # noqa: N803
        self.deleted.append(Key)


def test_minio_missing_bucket_fails_and_names_reason(
    smoke: ModuleType, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(smoke.boto3, "client", lambda *a, **k: FakeS3(missing_bucket=True))
    r = smoke.check_minio(settings)
    assert not r.ok and r.service == "minio" and "404" in r.detail


def test_minio_corrupt_readback_fails_and_still_cleans_up(
    smoke: ModuleType, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeS3(corrupt=True)
    monkeypatch.setattr(smoke.boto3, "client", lambda *a, **k: fake)
    r = smoke.check_minio(settings)
    assert not r.ok and "differs" in r.detail
    assert len(fake.deleted) == 1  # object removed even though verification failed


def test_minio_passes_against_a_well_behaved_store(
    smoke: ModuleType, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(smoke.boto3, "client", lambda *a, **k: FakeS3())
    assert smoke.check_minio(settings).ok


# ── Schema Registry ─────────────────────────────────────────────────
def patch_registry(
    smoke: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    post_status: int = 200,
    returned_schema: str | None = None,
) -> list[str]:
    deletes: list[str] = []
    registered = smoke.SMOKE_AVRO_SCHEMA

    monkeypatch.setattr(
        smoke.requests, "post", lambda *a, **k: FakeResponse({"id": 7}, post_status)
    )
    monkeypatch.setattr(
        smoke.requests,
        "get",
        lambda *a, **k: FakeResponse({"schema": returned_schema or registered}),
    )
    def fake_delete(url: str, **_k: object) -> FakeResponse:
        deletes.append(url)
        return FakeResponse({})

    monkeypatch.setattr(smoke.requests, "delete", fake_delete)
    return deletes


def test_registry_http_error_fails(
    smoke: ModuleType, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_registry(smoke, monkeypatch, post_status=500)
    r = smoke.check_schema_registry(settings)
    assert not r.ok and r.service == "schema-registry" and "500" in r.detail


def test_registry_schema_mismatch_fails_and_still_deletes_subject(
    smoke: ModuleType, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    deletes = patch_registry(smoke, monkeypatch, returned_schema='{"type": "string"}')
    r = smoke.check_schema_registry(settings)
    assert not r.ok and "differs" in r.detail
    assert len(deletes) == 2  # soft then permanent: no subject left behind


def test_registry_round_trip_passes(
    smoke: ModuleType, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_registry(smoke, monkeypatch)
    assert smoke.check_schema_registry(settings).ok


# ── Kafka ───────────────────────────────────────────────────────────
class FakeAdmin:
    def __init__(self, *_a: object, **_k: object) -> None:
        pass

    def create_topics(self, *_a: object, **_k: object) -> dict[str, Any]:
        return {}

    def poll(self, _t: float) -> int:
        return 0


def make_producer(*, unflushed: int) -> type:
    class FakeProducer:
        def __init__(self, *_a: object, **_k: object) -> None:
            pass

        def produce(self, *_a: object, **_k: object) -> None:
            return None

        def flush(self, _t: float) -> int:
            return unflushed

        def poll(self, _t: float) -> int:
            return 0

    return FakeProducer


def test_kafka_unacknowledged_produce_fails(
    smoke: ModuleType, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(smoke, "AdminClient", FakeAdmin)
    monkeypatch.setattr(smoke, "Producer", make_producer(unflushed=1))
    r = smoke.check_kafka(settings)
    assert not r.ok and r.service == "kafka" and "not acknowledged" in r.detail


def test_kafka_message_never_read_back_fails(
    smoke: ModuleType, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    class SilentConsumer:
        closed: ClassVar[bool] = False

        def __init__(self, *_a: object, **_k: object) -> None:
            pass

        def subscribe(self, _topics: list[str]) -> None:
            return None

        def poll(self, _t: float) -> None:
            return None

        def close(self) -> None:
            SilentConsumer.closed = True

    class AckingProducer(make_producer(unflushed=0)):  # type: ignore[misc]
        def produce(self, _topic: str, value: bytes, on_delivery: Callable[..., None]) -> None:
            on_delivery(None, None)

    monkeypatch.setattr(smoke, "AdminClient", FakeAdmin)
    monkeypatch.setattr(smoke, "Producer", AckingProducer)
    monkeypatch.setattr(smoke, "Consumer", SilentConsumer)
    r = smoke.check_kafka(settings)
    assert not r.ok and "did not read it back" in r.detail
    assert SilentConsumer.closed  # consumer closed on the failure path too


def test_kafka_unreachable_broker_fails_with_service_and_reason(
    smoke: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Real client, dead port: the genuine librdkafka failure path.
    dead = Settings(_env_file=None, kafka_port=1, **CREDS)  # type: ignore[arg-type]
    r = smoke.check_kafka(dead)
    assert not r.ok and r.service == "kafka" and r.detail


# ── DuckDB ──────────────────────────────────────────────────────────
def test_duckdb_unwritable_location_fails(smoke: ModuleType, tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    bad = Settings(_env_file=None, duckdb_path=blocker / "db.duckdb", **CREDS)  # type: ignore[arg-type]
    r = smoke.check_duckdb(bad)
    assert not r.ok and r.service == "duckdb" and r.detail


def test_duckdb_creates_missing_file(smoke: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "data" / "x.duckdb"
    ok = Settings(_env_file=None, duckdb_path=path, **CREDS)  # type: ignore[arg-type]
    r = smoke.check_duckdb(ok)
    assert r.ok and "created" in r.detail and path.exists()


# ── Runner ──────────────────────────────────────────────────────────
def test_runner_reports_a_raising_check_and_a_hung_check(
    smoke: ModuleType, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()

    def raises(_s: Settings) -> Any:
        raise RuntimeError("kaboom")

    def hangs(_s: Settings) -> Any:
        release.wait(30)

    def fine(_s: Settings) -> Any:
        return smoke.Result("fine", True, "ok")

    monkeypatch.setattr(smoke, "CHECKS", [("raises", raises), ("hangs", hangs), ("fine", fine)])
    monkeypatch.setattr(smoke, "DEADLINE_S", 1)
    try:
        results = {r.service: r for r in smoke.run_checks(settings)}
    finally:
        release.set()

    assert not results["raises"].ok and "RuntimeError: kaboom" in results["raises"].detail
    assert not results["hangs"].ok and "(hung)" in results["hangs"].detail
    assert results["fine"].ok  # one bad check doesn't take the others down


def test_deadline_exceeds_the_slowest_legitimate_check(smoke: ModuleType) -> None:
    assert smoke.DEADLINE_S >= smoke.MAX_SEQUENTIAL_OPS * smoke.TIMEOUT_S


def test_main_exit_code_and_output(
    smoke: ModuleType,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(smoke, "get_settings", lambda: settings)
    good = lambda _s: smoke.Result("alpha", True, "fine")  # noqa: E731
    bad = lambda _s: smoke.Result("beta", False, "because reasons")  # noqa: E731

    monkeypatch.setattr(smoke, "CHECKS", [("alpha", good)])
    assert smoke.main() == 0

    monkeypatch.setattr(smoke, "CHECKS", [("alpha", good), ("beta", bad)])
    assert smoke.main() == 1
    out = capsys.readouterr().out
    assert "FAIL  beta" in out and "because reasons" in out and "1/2 checks passed" in out


def test_main_exits_hard_when_a_check_hangs(
    smoke: ModuleType, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    exits: list[int] = []

    def raise_exit(code: int) -> None:
        exits.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(smoke, "get_settings", lambda: settings)
    monkeypatch.setattr(smoke, "CHECKS", [("stuck", lambda _s: release.wait(30))])
    monkeypatch.setattr(smoke, "DEADLINE_S", 1)
    monkeypatch.setattr(smoke.os, "_exit", raise_exit)
    try:
        with pytest.raises(SystemExit):
            smoke.main()
    finally:
        release.set()
    assert exits == [1]


def test_ctrl_c_exits_130_without_traceback(
    smoke: ModuleType,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def interrupt(_s: Settings) -> None:
        raise KeyboardInterrupt

    def raise_exit(code: int) -> None:
        raise SystemExit(code)

    monkeypatch.setattr(smoke, "get_settings", lambda: settings)
    monkeypatch.setattr(smoke, "run_checks", interrupt)
    monkeypatch.setattr(smoke.os, "_exit", raise_exit)
    with pytest.raises(SystemExit) as info:
        smoke.main()
    assert info.value.code == 130
    assert "Interrupted" in capsys.readouterr().err


def test_main_fails_cleanly_when_settings_cannot_load(
    smoke: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken() -> None:
        raise ValueError("no config")

    monkeypatch.setattr(smoke, "get_settings", broken)
    assert smoke.main() == 1
    assert "no config" in capsys.readouterr().err


# ── End to end: the real process, the real exit code ────────────────
def test_script_exits_nonzero_and_names_every_service_when_stack_is_down() -> None:
    env = {
        **os.environ,
        # Dead ports (win/linux both refuse port 1); creds supplied so no infra/.env is needed.
        "KAFKA_PORT": "1",
        "SCHEMA_REGISTRY_PORT": "1",
        "MINIO_PORT": "1",
        "POSTGRES_OPS_PORT": "1",
        "MINIO_ROOT_USER": "u",
        "MINIO_ROOT_PASSWORD": "p",
        "POSTGRES_OPS_USER": "u",
        "POSTGRES_OPS_PASSWORD": "p",
        "POSTGRES_OPS_DB": "d",
    }
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=90
    )
    assert proc.returncode == 1
    for service in ("kafka", "schema-registry", "minio", "postgres"):
        line = next(ln for ln in proc.stdout.splitlines() if f"FAIL  {service}" in ln)
        assert len(line.split(service, 1)[1].strip()) > 10  # a reason, not just a name
    assert "rdkafka" not in proc.stderr  # librdkafka noise stays off stderr

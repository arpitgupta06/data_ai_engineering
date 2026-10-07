"""Smoke test for the local data plane: Kafka, Schema Registry, MinIO, Postgres, DuckDB.

    uv run python scripts/smoke_test.py

Exit code 0 if every check passed, 1 otherwise. Every check is independent: one
failing (or hanging) never stops the others, so a run always reports all five.

Design notes
  * Each check catches its own exceptions and returns a Result; none can raise out.
  * Every network operation has a timeout, and each check also runs under a hard
    deadline as a backstop for anything that ignores its own timeout.
  * Connection details come from chainrisk.settings and nowhere else.
  * Leftovers: Kafka reuses one fixed topic; Schema Registry and MinIO clean up
    after themselves; DuckDB creates its (gitignored) file if absent — see check_duckdb.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import boto3
import duckdb
import psycopg
import requests
from botocore.config import Config
from confluent_kafka import Consumer, KafkaError, Producer
from confluent_kafka.admin import AdminClient, NewTopic  # type: ignore[attr-defined]

from chainrisk.settings import Settings, get_settings

TIMEOUT_S = 10  # per network operation
# Hard cap for the whole run. Derived, so it can't drift below what a slow-but-working
# check needs: the longest checks (Schema Registry, MinIO, Kafka) chain up to 4 operations.
MAX_SEQUENTIAL_OPS = 4
DEADLINE_S = MAX_SEQUENTIAL_OPS * TIMEOUT_S + 5

SMOKE_TOPIC = "smoke.test"
SMOKE_RETENTION_MS = 10 * 60 * 1000
SMOKE_SUBJECT = "_smoke-value"
SMOKE_AVRO_SCHEMA = json.dumps(
    {
        "type": "record",
        "name": "Smoke",
        "namespace": "chainrisk.smoke",
        "fields": [{"name": "id", "type": "string"}],
    }
)


@dataclass(frozen=True)
class Result:
    service: str
    ok: bool
    detail: str


def _pass(service: str, detail: str) -> Result:
    return Result(service, True, detail)


def _fail(service: str, detail: str) -> Result:
    return Result(service, False, detail)


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


class _LastMessage(logging.Handler):
    """Keeps librdkafka's most recent log line so it can explain a failure."""

    def __init__(self) -> None:
        super().__init__()
        self.last = ""

    def emit(self, record: logging.LogRecord) -> None:
        self.last = record.getMessage()


# ── Kafka ───────────────────────────────────────────────────────────
def check_kafka(s: Settings) -> Result:
    """Create topic explicitly, produce a unique message, consume it back."""
    name = "kafka"
    # librdkafka logs to stderr by default. Route it to a logger with no stream handler
    # and keep the last line: "Connect to 127.0.0.1:19092 failed" explains a timeout.
    capture = _LastMessage()
    logger = logging.Logger("smoke.rdkafka")
    logger.addHandler(capture)
    conf: dict[str, Any] = {"bootstrap.servers": s.kafka_bootstrap_servers, "logger": logger}

    polled: list[Any] = []  # clients whose queued log callbacks we can flush on failure

    def fail(detail: str) -> Result:
        for client in polled:
            client.poll(0)  # librdkafka log callbacks are delivered on poll
        return _fail(name, f"{detail} [librdkafka: {capture.last}]" if capture.last else detail)

    try:
        # Explicit creation, so the test doesn't depend on broker auto-create.
        admin = AdminClient({**conf, "socket.timeout.ms": TIMEOUT_S * 1000})
        polled.append(admin)
        futures = admin.create_topics(
            [
                NewTopic(
                    SMOKE_TOPIC,
                    num_partitions=1,
                    replication_factor=1,
                    # Reused topic: bound what accumulates so reads stay fast.
                    config={"retention.ms": str(SMOKE_RETENTION_MS)},
                )
            ],
            operation_timeout=TIMEOUT_S,
            request_timeout=TIMEOUT_S,
        )
        for future in futures.values():
            try:
                future.result(timeout=TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 - "already exists" is fine
                err = exc.args[0] if exc.args else None
                code = err.code() if isinstance(err, KafkaError) else None
                if code != KafkaError.TOPIC_ALREADY_EXISTS:
                    raise

        token = uuid.uuid4().hex
        delivery_errors: list[object] = []
        delivered = threading.Event()

        def on_delivery(err: object, _msg: object) -> None:
            if err is not None:
                delivery_errors.append(err)
            delivered.set()

        producer = Producer({**conf, "message.timeout.ms": TIMEOUT_S * 1000})
        polled.append(producer)
        producer.produce(SMOKE_TOPIC, value=token.encode(), on_delivery=on_delivery)
        remaining = producer.flush(TIMEOUT_S)  # produce() alone sends nothing
        if remaining or not delivered.is_set():
            return fail(f"produce not acknowledged ({remaining} message(s) unflushed)")
        if delivery_errors:
            return fail(f"delivery failed: {delivery_errors[0]}")

        # Fresh group + earliest: otherwise we'd subscribe and read nothing.
        consumer = Consumer(
            {
                **conf,
                "group.id": f"smoke-{uuid.uuid4().hex}",
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
            }
        )
        try:
            consumer.subscribe([SMOKE_TOPIC])
            deadline = time.monotonic() + TIMEOUT_S
            while time.monotonic() < deadline:
                msg = consumer.poll(1.0)
                if msg is None:
                    continue
                if msg.error():
                    return fail(f"consume error: {msg.error()}")
                if msg.value() == token.encode():  # topic is reused; match our message
                    return _pass(name, f"produced and consumed a message on '{SMOKE_TOPIC}'")
            return fail(f"produced OK but did not read it back within {TIMEOUT_S}s")
        finally:
            consumer.close()
    except Exception as exc:  # noqa: BLE001 - a check must never raise
        return fail(_describe(exc))


# ── Schema Registry ─────────────────────────────────────────────────
def check_schema_registry(s: Settings) -> Result:
    """Register a schema under a fixed subject, read it back, then delete the subject."""
    name = "schema-registry"
    base = s.schema_registry_url
    headers = {"Content-Type": "application/vnd.schemaregistry.v1+json"}
    try:
        resp = requests.post(
            f"{base}/subjects/{SMOKE_SUBJECT}/versions",
            json={"schema": SMOKE_AVRO_SCHEMA},
            headers=headers,
            timeout=TIMEOUT_S,
        )
        resp.raise_for_status()
        schema_id = resp.json()["id"]

        try:
            got = requests.get(f"{base}/schemas/ids/{schema_id}", timeout=TIMEOUT_S)
            got.raise_for_status()
            if json.loads(got.json()["schema"]) != json.loads(SMOKE_AVRO_SCHEMA):
                return _fail(name, f"schema {schema_id} read back differs from what was registered")
        finally:
            # Soft then permanent delete, so the registry doesn't accumulate subjects.
            # Best-effort: a cleanup failure shouldn't mask the real result.
            for params in ({}, {"permanent": "true"}):
                try:
                    requests.delete(
                        f"{base}/subjects/{SMOKE_SUBJECT}", params=params, timeout=TIMEOUT_S
                    )
                except requests.RequestException:
                    break
        return _pass(name, f"registered and read back schema id {schema_id}")
    except Exception as exc:  # noqa: BLE001
        return _fail(name, _describe(exc))


# ── MinIO ───────────────────────────────────────────────────────────
def check_minio(s: Settings) -> Result:
    """Verify the bronze bucket exists, then put/get/delete an object in artifacts."""
    name = "minio"
    try:
        client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,  # boto3 won't infer this
            aws_access_key_id=s.minio_root_user,
            aws_secret_access_key=s.minio_root_password.get_secret_value(),
            region_name=s.aws_region,  # ignored by MinIO, required by boto3
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},  # http://host/bucket, not bucket.host
                connect_timeout=TIMEOUT_S,
                read_timeout=TIMEOUT_S,
                retries={"max_attempts": 1},
            ),
        )
        client.head_bucket(Bucket=s.bronze_bucket)

        key = f"smoke/{uuid.uuid4().hex}"
        body = b"chainrisk-smoke"
        client.put_object(Bucket=s.artifacts_bucket, Key=key, Body=body)
        try:
            got = client.get_object(Bucket=s.artifacts_bucket, Key=key)["Body"].read()
        finally:
            client.delete_object(Bucket=s.artifacts_bucket, Key=key)
        if got != body:
            return _fail(name, "object read back differs from what was written")
        return _pass(
            name, f"'{s.bronze_bucket}' exists; put/get/delete OK in '{s.artifacts_bucket}'"
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(name, _describe(exc))


# ── Postgres ────────────────────────────────────────────────────────
def check_postgres(s: Settings) -> Result:
    """Connect and assert wal_level = logical (required for Debezium in Phase 5)."""
    name = "postgres"
    try:
        # connect_timeout applies per address ("localhost" tries ::1 then 127.0.0.1).
        with (
            psycopg.connect(s.postgres_ops_dsn, connect_timeout=TIMEOUT_S // 2) as conn,
            conn.cursor() as cur,
        ):
            cur.execute("SHOW wal_level")
            row = cur.fetchone()
        wal_level = row[0] if row else None
        if wal_level != "logical":
            return _fail(name, f"wal_level is {wal_level!r}, expected 'logical'")
        return _pass(name, f"{s.postgres_ops_host}:{s.postgres_ops_port} wal_level=logical")
    except Exception as exc:  # noqa: BLE001
        return _fail(name, _describe(exc))


# ── DuckDB ──────────────────────────────────────────────────────────
def check_duckdb(s: Settings) -> Result:
    """Open the DuckDB file (creating it if absent) and run a query.

    Decision: create. DuckDB is embedded, so "the service is up" just means the
    library can open a database file here. Failing on a missing file would make a
    clean clone unable to pass for a reason that isn't a fault. The side effect is
    small and harmless: an empty, gitignored file in data/ that the pipeline would
    create on first use anyway. We write nothing into it.
    """
    name = "duckdb"
    try:
        s.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
        existed = s.duckdb_path.exists()
        conn = duckdb.connect(str(s.duckdb_path))
        try:
            row = conn.execute("SELECT 42").fetchone()
        finally:
            conn.close()
        if row != (42,):
            return _fail(name, f"SELECT 42 returned {row!r}")
        state = "opened existing" if existed else "created"
        return _pass(name, f"{state} {s.duckdb_path.name}; query OK (duckdb {duckdb.__version__})")
    except Exception as exc:  # noqa: BLE001
        return _fail(name, _describe(exc))


# ── Runner ──────────────────────────────────────────────────────────
CHECKS: list[tuple[str, Callable[[Settings], Result]]] = [
    ("kafka", check_kafka),
    ("schema-registry", check_schema_registry),
    ("minio", check_minio),
    ("postgres", check_postgres),
    ("duckdb", check_duckdb),
]


def _line(r: Result, seconds: float, width: int) -> str:
    return f"  {'PASS' if r.ok else 'FAIL'}  {r.service:<{width}}  {r.detail} ({seconds:.1f}s)"


def run_checks(settings: Settings) -> list[Result]:
    """Run all checks concurrently, printing each result as it completes.

    The checks are independent, so the worst case is DEADLINE_S rather than their sum.
    Each runs in a daemon thread; one that hasn't reported by the deadline is recorded
    as hung. A hung thread can't be killed, so main() exits the process explicitly.
    """
    width = max(len(name) for name, _ in CHECKS)
    done: queue.Queue[tuple[Result, float]] = queue.Queue()
    started = time.monotonic()

    def target(service: str, fn: Callable[[Settings], Result]) -> None:
        try:
            result = fn(settings)
        except BaseException as exc:  # noqa: BLE001 - checks shouldn't raise; belt and braces
            result = _fail(service, _describe(exc))
        done.put((result, time.monotonic() - started))

    for service, fn in CHECKS:
        print(f"  ...   {service:<{width}}  checking", flush=True)
        threading.Thread(target=target, args=(service, fn), daemon=True).start()

    finished: dict[str, Result] = {}
    deadline = started + DEADLINE_S
    while len(finished) < len(CHECKS):
        try:
            result, seconds = done.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            break
        finished[result.service] = result
        print(_line(result, seconds, width), flush=True)

    for service, _ in CHECKS:
        if service not in finished:
            hung = _fail(service, f"no result within {DEADLINE_S}s (hung)")
            finished[service] = hung
            print(_line(hung, DEADLINE_S, width), flush=True)
    return [finished[service] for service, _ in CHECKS]


def main() -> int:
    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001 - e.g. missing infra/.env
        print(f"Cannot load settings: {exc}", file=sys.stderr)
        return 1

    print()
    try:
        results = run_checks(settings)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr, flush=True)
        os._exit(130)  # checks may be mid-call in native code; don't wait for them

    failed = [r for r in results if not r.ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    code = 1 if failed else 0
    if any("(hung)" in r.detail for r in failed):
        os._exit(code)  # a hung native call would otherwise keep the process alive
    return code


if __name__ == "__main__":
    sys.exit(main())

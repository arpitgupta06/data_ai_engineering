"""Bronze ingest as a scheduled, backfillable DAG (CRC-17).

One DAG run per calendar day; one task per table within it, so each task is exactly one
bronze partition (a table-day). A failed partition is cleared and retried on its own, without
re-running the week:  UI -> run for that date -> the failed task -> Clear.

This file only schedules. The ingest logic is chainrisk.ingest.bronze.ingest_table_day, which
has its own unit tests and needs no scheduler. A partition that is already current comes back
SKIPPED and the task succeeds, so re-running a date is a no-op (CRC-15's idempotency).

Backfill a date range (inclusive), from the repo root:

    docker compose -f projects/01-chain-risk-copilot/infra/docker-compose.yml exec \\
      airflow-scheduler airflow backfill create --dag-id bronze_ingest \\
      --from-date 2026-10-01 --to-date 2026-10-07
"""

from datetime import UTC, datetime, timedelta

from airflow.sdk import DAG, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException

from chainrisk.ingest.bronze import (
    TABLES,
    WINDOW_DAYS,
    WINDOW_START,
    format_result,
    ingest_table_day,
)

# Known cost: this top-level import pulls pyarrow (bronze.py imports it at module level), so
# the dag-processor loads pyarrow on every parse. Measured: it sits at ~283 MiB, against ~177
# MiB before the DAG existed. We accept it: one task at a time and seven runs make parse cost
# irrelevant here. The fix, if it ever matters: move TABLES, WINDOW_START and WINDOW_DAYS into
# a light module with no pyarrow import, and import ingest_table_day inside the task.

# start_date / end_date / catchup are set together, on purpose.
#
# The source window is fixed and historical (CRC-15: 2026-10-01 .. 2026-10-07). With catchup
# on and no end_date, unpausing would launch one run for every day since 2026-10-01 and keep
# adding a run per day to a dataset that does not grow: hundreds of runs, all no-ops or
# failures. end_date caps the schedule at the window's last day, so catchup=True launches
# exactly WINDOW_DAYS runs when the DAG is unpaused (which is how a clean `make up` lands the
# week in bronze), and then never again. Widening the window means editing WINDOW_DAYS in
# bronze.py, not this file.
START_DATE = datetime(WINDOW_START.year, WINDOW_START.month, WINDOW_START.day, tzinfo=UTC)
END_DATE = START_DATE + timedelta(days=WINDOW_DAYS - 1)

with DAG(
    dag_id="bronze_ingest",
    schedule="@daily",
    start_date=START_DATE,
    end_date=END_DATE,
    catchup=True,
    # Memory, not preference. The stack has 6 GB; Airflow idles around 0.75 GiB and one
    # transactions task peaks near 0.9 GiB, so one task at a time fits and two do not. Runs
    # are serialised for the same reason. compose also sets AIRFLOW__CORE__PARALLELISM=1 as a
    # backstop, so a wrong value here cannot OOM the stack. Raise both together, and only
    # after raising Docker's memory allocation.
    max_active_runs=1,
    max_active_tasks=1,
    default_args={
        # A network blip mid-partition is the expected failure. Re-ingest is safe: the
        # marker is deleted first and written last, so a retry never sees a half-built
        # partition as complete.
        "retries": 2,
        "retry_delay": timedelta(minutes=2),
    },
    tags=["bronze", "ingest"],
    doc_md=__doc__,
) as dag:
    # Measured on one transactions partition (1.5M rows): about 170 s. token_transfers has
    # more rows (4.3M on 2026-10-03) and has NOT been timed, so 20 minutes is roughly 2.5x
    # the extrapolated worst case, not a measured limit. The timeout exists to turn a hung
    # connection into a retryable failure; tighten it once token_transfers is timed.
    @task(execution_timeout=timedelta(minutes=20))  # type: ignore[untyped-decorator]  # airflow is Any
    def ingest(table: str) -> None:
        # The data interval's start is the partition date. A manually triggered run in
        # Airflow 3 has no logical date and no data interval, so there is no date to ingest;
        # fail at once and say why (AirflowFailException skips the retries, which could
        # not help). Scheduled and backfilled runs always have one.
        start = get_current_context().get("data_interval_start")
        if start is None:
            raise AirflowFailException(
                "this run has no data interval, so no partition date: manual triggers "
                "cannot pick a day. Use `airflow backfill create` (see the module docstring)."
            )
        day = start.date()
        result = ingest_table_day(table, day)
        print(format_result(result))

    for table in TABLES:
        ingest.override(task_id=f"ingest_{table}")(table)

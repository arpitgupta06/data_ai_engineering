# Airflow image for the bronze ingest DAG (CRC-17).
#
# The stock image has no pyarrow or pydantic-settings, which chainrisk.ingest.bronze imports,
# so the DAG would fail to import. Baking them in beats _PIP_ADDITIONAL_REQUIREMENTS, which
# reinstalls on every container start. apache-airflow is pinned to its own version so pip
# cannot upgrade Airflow itself while resolving these.
#
# The three pins below MUST equal uv.lock: the tests run in the venv, the DAG runs here, and
# bronze.py depends on pyarrow behaviour. tests/test_dockerfile_pins.py fails if they drift.
# To bump one: `uv lock --upgrade-package <name>`, then change the pin here to match.
# Source is mounted, not copied (see docker-compose.yml), so editing src/ needs no rebuild.
FROM apache/airflow:3.4.0@sha256:a8d9d19c563c768b2bc605ef26440f28a9164ea886064e99c599b057432ff572
RUN pip install --no-cache-dir "apache-airflow==3.4.0" \
    "pyarrow==26.0.0" "pydantic==2.13.5" "pydantic-settings==2.15.0"

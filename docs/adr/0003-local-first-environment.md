# ADR 0003 — Local-first environment, deliberate cloud promotion

**Status:** Accepted · 2026-09-04

## Context
Running Kafka, Airflow, Snowflake and AWS continuously on a personal account,
with a streaming ingest, accrues cost quickly — likely faster than the learning
accrues. Cost optimization is also an explicit goal of the program.

## Decision
The default development environment is local Docker Compose: Redpanda, MinIO,
Postgres, Airflow, dbt and DuckDB. Terraform for the AWS + Snowflake environment
is written from Phase 0 onward but applied deliberately, phase by phase, and
destroyed between sessions.

Cost guardrails are Phase 0 deliverables: AWS budget alarms and anomaly detection
before any apply; Snowflake XS warehouse with 60s auto-suspend and a resource
monitor with a hard cap; per-request LLM token accounting from the first agent commit.

## Consequences
- Fast iteration and near-zero idle spend.
- Two environments to keep in parity — parity itself becomes a tested property.
- Some behaviours (Snowflake RBAC, masking policies, S3 request costs) cannot be
  learned locally and must be exercised in the cloud phases.

## Rejected alternatives
- *Cloud-first* — most realistic, but burns credits continuously and slows the loop.
- *Hybrid local compute + cloud storage* — reasonable, and remains the fallback if
  local/cloud parity proves too costly to maintain.

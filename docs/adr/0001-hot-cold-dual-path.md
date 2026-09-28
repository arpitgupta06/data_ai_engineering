# ADR 0001 — Dual hot/cold ingestion path

**Status:** Accepted · 2026-09-04

## Context
The project is framed as "Real Time Blockchain". The nominated source,
`s3://aws-public-blockchain/v1.0/eth/`, is daily-partitioned Parquet refreshed in
batch. Building "real-time" on it would mean simulating streaming — the mechanics
would be exercised but the actual streaming problems (out-of-order events,
reorgs, finality, backpressure) would be absent or artificial.

## Decision
Two ingestion paths into the same bronze layer:
- **Hot:** Ethereum RPC/websocket → Kafka (Avro + Schema Registry) → bronze.
- **Cold:** S3 Parquet → Airflow backfill → bronze.

The Parquet data additionally serves as a deterministic **replay fixture** for
tests and CI, where non-determinism is unwanted.

## Consequences
- Reconciliation between the two paths becomes a first-class deliverable, and is
  the phase where completeness and correctness are genuinely proven.
- Requires an external RPC dependency with rate limits to design around.
- Bronze must be idempotent under both append and re-delivery.

## Rejected alternatives
- *Replay Parquet through Kafka only* — cheap and deterministic, but the streaming
  lessons would be fictional.
- *Batch only, streaming later* — defers the hardest and most valuable problems
  past the point where the model is already fixed.

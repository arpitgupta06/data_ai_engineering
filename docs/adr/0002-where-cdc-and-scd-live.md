# ADR 0002 — Where CDC and SCD actually live

**Status:** Accepted · 2026-09-04

## Context
The program aims to cover CDC and SCD. Blockchain data is append-only and
immutable: transactions are never updated or deleted. Applying CDC or SCD
directly to chain facts would be a contrived exercise.

## Decision
Place each pattern where the data genuinely has that shape.

- **SCD Type 2** → `dim_address` (entity labels, risk scores) and `dim_token`
  (symbol/decimals, which change on proxy upgrades). These change slowly and
  historical correctness materially affects an investigation's answer.
- **CDC** → the operational Postgres backing the copilot (watchlists, alert rules,
  analyst annotations), streamed via Debezium into Kafka and the warehouse.
- **Late-arriving data / corrections** → chain **reorgs** and the
  pending → confirmed → finalized lifecycle, rather than a synthetic CDC feed.

## Consequences
- Reorg handling becomes a named phase with its own tests and audit trail.
- "As-of" querying of labels is a hard requirement on gold, and a defined
  acceptance criterion.
- An operational Postgres is required earlier than a pure analytics build needs one.

## Rejected alternatives
- *Synthetic CDC feed over chain tables* — teaches the mechanics, not the judgement,
  and would not survive review by anyone who knows the domain.

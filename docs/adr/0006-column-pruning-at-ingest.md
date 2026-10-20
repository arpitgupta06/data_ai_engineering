# ADR 0006 — Column pruning at ingest: a deliberate deviation from bronze-as-raw

**Status:** Accepted · 2026-10-19 · Supersedes nothing · Relates to [ADR 0003](0003-local-first-environment.md), [ADR 0005](0005-minio-community-eol.md)

## Context

CRC-15 ingests Ethereum `blocks`, `transactions` and `token_transfers` from the AWS
Public Blockchain dataset (`s3://aws-public-blockchain/v1.0/eth/`, us-east-2,
anonymous access) into the local bronze bucket, `chainrisk-bronze`.

The usual rule for a bronze layer is that it holds the **raw bytes exactly as the
source published them**. The point is reprocessing: when silver's logic turns out
to be wrong, you rerun it against bronze and lose nothing.

Two facts pull against that here:

- **Disk is constrained.** Bronze lives in local MinIO on a developer machine
  (ADR 0003). Measured on one day's partition (compressed Parquet), pruning to the
  columns silver will need cuts the three tables from 1,334 MB to 713 MB (-47%):

  | Table | Source | Pruned | Saved |
  |---|---|---|---|
  | `blocks` | 5.6 MB | 1.1 MB | 80% |
  | `transactions` | 837.7 MB | 245.5 MB | 71% |
  | `token_transfers` | 490.8 MB | 466.3 MB | 5% |

  Figures come from the Parquet footer of one source file per table
  (`date=2026-10-01`): per-column `total_compressed_size` summed across row groups.
  Footer-only, so no data was downloaded to produce them.

  The saving is almost entirely `transactions`, where `input` (calldata) alone is
  540 MB/day and nothing on the roadmap reads it. In `blocks`, `logs_bloom` is 54% of
  the table. Had the total saving been closer to 5%, this deviation would not be
  worth making.
- **The source is replayable.** The bucket is free, anonymous, and holds full
  history from 2015-08-07. Nobody is charging for distribution (the AWS Open Data
  program covers it).

## Decision

**Read each source Parquet file, keep only the columns silver will need, and write
the result to bronze.** The column list per table lives in one place in code.

This breaks bronze-as-raw in two ways, and we accept both:

1. **A dropped column is gone from bronze.**
2. **Bronze is no longer byte-identical to what AWS published.** Rewriting Parquet
   with pyarrow changes row groups, compression and file metadata, even for the
   columns we keep.

Copying objects byte-for-byte with boto3 would preserve the raw bytes, but it
cannot drop columns. Reading and rewriting is the only way to prune, and that is
the trade this ADR records.

### Applied uniformly, even where it does not pay

The disk argument barely applies to `token_transfers`: only `last_modified` is dropped,
saving 24 MB of 491, and every other column in the table is load-bearing. We prune it
anyway, and the reason is not disk. One code path for all three tables, read, select
columns, rewrite, is simpler than a policy that byte-copies some tables and rewrites
others. That is an architectural choice, and it costs `token_transfers` its byte-identity
for a 5% saving.

### Why the deviation is safe here

The raw-bytes rule exists because it assumes the source is **unreplayable**: if
you do not keep the original, you cannot get it back. This source is the opposite.
A dropped column is one re-run away, at zero cost, from any date back to 2015.

So the two risks are not equal. The disk constraint is real and immediate. The
replay risk is not real for as long as the source stays free and public.

### What this costs

- **Reprocessing needs the network.** Recovering a dropped column means re-ingesting
  from AWS rather than rereading local bronze. Offline, we cannot do it.
- **Bronze is not an audit copy.** It is a pruned, rewritten derivative. Nothing
  downstream may treat it as evidence of what the source published.
- **Adding a column is a backfill.** Widening the column list means re-running
  every previously ingested partition, since `_SUCCESS` makes a finished date a
  no-op. That is cheap at seven partitions and costs more as the window grows.
- **Completeness checks compare row counts, not bytes.** Without byte-identity the
  per-partition check is "rows written equals rows read". It cannot detect
  corruption inside a kept column.

## Options considered

| Option | Why rejected |
|---|---|
| Copy objects byte-for-byte with boto3 (true raw bronze) | Keeps every column, so it spends the disk we are short of on columns nothing will read. |
| Ingest everything, prune in silver | Same disk cost as above; the pruning only moves downstream. |
| Prune at ingest, also keep raw bytes elsewhere | Defeats the point: the raw copy is the disk cost we are avoiding. |

## Consequences

- Bronze stores a documented subset of columns per table. The authoritative list is
  `COLUMNS` in `src/chainrisk/ingest/bronze.py`.
- Silver may rely only on those columns. Anything else requires widening the list
  and re-ingesting.
- Ingest authenticates to MinIO as the **root user**. That is acceptable for a local
  stack, but a scoped user with write access to `chainrisk-bronze` only is the
  intended shape. This is a known deferral to P6 (RBAC), not an oversight.
- The deviation is scoped to **this source**. It is not a general licence to prune
  bronze for sources that cannot be replayed.

## Triggers to revisit

Any one of these invalidates the decision:

1. The source starts charging, adds egress costs, or restricts anonymous access.
2. The source stops being replayable: history is removed or rewritten.
3. P9 moves to a vendor feed, where re-fetching costs money or is rate-limited.
4. Disk stops being the binding constraint (for example, bronze moves to real S3).
5. A downstream need appears for byte-level provenance of the source data.

On any trigger: switch to byte-for-byte copying for the affected source and prune in
silver instead.

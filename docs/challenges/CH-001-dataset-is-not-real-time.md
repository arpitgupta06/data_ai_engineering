# CH-001 · The "real-time" dataset is a nightly batch drop

| | |
|---|---|
| **Phase** | Design (pre-Phase 0) |
| **Jira** | CRC-1 (epic) |
| **ADR** | [0001 — Dual hot/cold ingestion path](../adr/0001-hot-cold-dual-path.md) |
| **Category** | streaming, requirements |
| **Status** | Mitigated |
| **Surfaced** | 2026-09-04 |
| **Closed** | — |

## Context

The project was specified as a "Real Time Blockchain dataset" pipeline, with
`s3://aws-public-blockchain/v1.0/eth/` named as the source. The whole point of
the build was to exercise real-time data handling — streaming ingestion, late
data, schema evolution under load.

## The problem

The nominated source is not real-time. It publishes **one Parquet file per table
per day, written nightly at approximately 01:45 UTC**. Worst-case data age is
therefore about 26 hours.

The consuming use case — deciding whether to accept a crypto deposit — has a
decision window measured in minutes. A 26-hour-old view of the chain cannot
answer it. Building "streaming" on top of a nightly file would have produced a
system that demonstrated streaming *mechanics* while exercising none of the
streaming *problems*: no out-of-order arrival, no reorgs, no backpressure, no
genuine late data.

## Why it was hard

The requirement and the named source were in direct conflict, and the conflict
was invisible until someone checked the source's actual delivery cadence. It
would have been entirely possible to build the whole pipeline without noticing —
the data is real, the volumes are real, and a daily file replayed through Kafka
*looks* like a stream on a dashboard.

The cost of not catching it was not a broken system. It was a system that taught
and proved the wrong thing.

## Options considered

| Option | Pros | Cons | Cost of being wrong |
|---|---|---|---|
| **A. Replay the Parquet through Kafka on a synthetic clock** | Zero external dependencies, deterministic, free, easy to test | Every streaming problem is simulated. No reorgs, no real late data, no rate limits | Build completes, but the central skill is never actually exercised |
| **B. Live node RPC/WebSocket only** | Genuinely real-time, real reorgs and finality | No history — cannot backfill, cannot reconcile, free-tier rate limits constrain throughput | No historical depth for tracing, which is the product's core function |
| **C. Both: live hot path + Parquet cold path** | Real streaming *and* full history; reconciliation between them becomes a first-class deliverable | Two ingestion paths to build and keep consistent; external RPC dependency with rate limits | Higher build cost, and reconciliation is genuinely hard |
| **D. Batch first, add streaming later** | Smallest first step | Defers the hardest problems until after the data model is frozen around batch assumptions | Retrofitting streaming onto a batch-shaped model is expensive |

## Decision

**Option C.** Live Ethereum RPC/WebSocket into Kafka as the hot path; the S3
Parquet as the cold path for history and reconciliation. The Parquet additionally
becomes the deterministic replay fixture for CI, where non-determinism is
unwanted — so Option A survives as a testing tool rather than a production design.

**What this cost:** roughly double the ingestion work, a hard external dependency
on a third-party RPC provider with free-tier rate limits, and a new class of
problem — proving two independently-sourced views of the same chain agree. That
reconciliation is now a named phase of its own (Phase 3).

## How we validated it

The source's cadence was confirmed directly from the AWS dataset schema
documentation rather than assumed: nightly delivery at ~01:45 UTC, Hive-style
`date=YYYY-MM-DD` partitions, history from 2015-08-07. The 26-hour worst case
follows arithmetically.

## Outcome

Not yet observed — decision taken 2026-09-04, recorded as ADR 0001. It will be
validated in Phase 3, when hot/cold reconciliation runs and the variance between
the two paths is measured. The acceptance criterion is zero unexplained variance
over a chosen window.

## What I'd do differently

Check the delivery cadence of every named source **before** writing any
architecture, not after. It took one documentation lookup and it changed the
shape of the entire system. I had already sketched a pipeline around the source
before verifying how the source actually behaved — that ordering was backwards.

Generalised: "real-time" in a source's marketing description means nothing. The
only number that matters is the observed lag between the event occurring and the
data being queryable, and it should be the first thing established about any
source.

---

## 90-second answer

> The brief called for a real-time blockchain pipeline and named a specific AWS
> public dataset as the source. Before designing anything I checked how that
> source actually delivers, and found it publishes one Parquet file per table per
> night at around 01:45 UTC — so worst case the data is twenty-six hours old.
>
> That mattered because the consumer was a compliance analyst deciding whether to
> accept a deposit, with a decision window of minutes. Day-old data cannot answer
> that question. And if we'd built on it anyway, we'd have had a pipeline that
> demonstrated streaming mechanics while exercising none of the actual streaming
> problems — no out-of-order events, no chain reorganisations, no backpressure.
>
> I considered three options: replay the files through Kafka on a fake clock,
> which is cheap and deterministic but simulates every hard part; go live-only
> from a node, which is genuinely real-time but gives you no history to trace
> against; or run both paths.
>
> I chose both — a live node feed as the hot path, the historical files as the
> cold path. That roughly doubled the ingestion work and added a dependency on a
> third-party RPC provider with rate limits. It also created a new problem I had
> to own: proving two independently-sourced views of the same chain agree. I made
> that reconciliation an explicit phase with a zero-unexplained-variance
> acceptance criterion rather than leaving it implicit.
>
> The lesson I took: a source's claimed freshness is marketing. Measure the lag
> between event and queryability before you design anything around it.

# Project 1 — Chain Risk Copilot

Compliance and risk teams at exchanges, custodians and crypto-native lenders must
decide, in minutes, whether a wallet is safe to transact with. Today that answer is
assembled by hand: block explorers in one tab, sanctions lists in another, an
internal spreadsheet of known addresses, and a written memo at the end. The
underlying chain data is public and real-time, but it is unlabelled, un-modelled,
and un-governed.

Build a platform that ingests Ethereum activity in real time, models it into a
governed warehouse enriched with entity labels and risk signals, and exposes a
copilot that answers investigative questions with traceable evidence.

- Converge a live chain stream with historical batch data and reconcile the two into one trusted record
- Enrich raw transactions with slowly-changing entity labels, token metadata and price context
- Let an analyst ask a question in natural language — "where did the funds in this wallet come from?" — and get a governed, evidence-backed answer
- Produce an audit-ready finding, never an opaque score

**Judging Focus:** Real World Relevance, Technical Execution, Solution Completeness

---

## Scope

**In scope:** Ethereum mainnet. Native ETH transfers and ERC-20 transfers.
Entity labelling, fund tracing to N hops, wallet risk scoring, evidence citation,
analyst watchlists and annotations.

**Out of scope (for now):** other chains, NFTs, DeFi protocol decoding,
cross-chain bridge attribution, production-grade sanctions screening.

## Sources

| Source | Type | Path |
|---|---|---|
| AWS Public Blockchain | Batch Parquet, `date=YYYY-MM-DD` partitions | `s3://aws-public-blockchain/v1.0/eth/` |
| Ethereum node | Live websocket/RPC | free-tier provider (TBD in Phase 2) |
| CoinGecko | REST, via Airbyte | token prices |
| Public label sets | REST/files, via Airbyte | exchange, bridge, mixer labels |
| Operational Postgres | CDC via Debezium | watchlists, analyst annotations |

### Verified facts about the cold source (checked 2026-09-04)

- Tables available: `blocks`, `transactions`, `token_transfers`, `logs`, `traces`, `contracts`
- Partitioning: Hive-style `date=YYYY-MM-DD`; history begins `2015-08-07`
- Delivery: one Parquet file per table per day, written nightly at **~01:45 UTC**
  → worst-case data age is ~26 hours, which is why the hot path exists (ADR 0001)
- Size: a single day of `transactions` is ~1.4 GB (~4,000 days of history)
- `value` is in wei as `DECIMAL(38,0)`; token decimals vary per token (USDT uses 6)
- ETH moved *by contracts* appears only in `traces`, not `transactions` — tracing
  built on `transactions` alone silently misses most laundering paths

## Definition of done

1. A block appears on chain and is queryable in gold within the agreed freshness SLO.
2. A reorg is detected and its effects are correctly reversed, with an audit trail.
3. Hot and cold paths reconcile to zero unexplained variance over a chosen window.
4. No gaps in block height across the full backfilled range — proven by test, not assertion.
5. Address labels are historically correct: a query as-of a past date returns the label that was true then.
6. The copilot answers a tracing question with cited transaction hashes and refuses cleanly when it cannot.
7. Warehouse access is role-separated, with analyst-level masking on restricted fields.
8. The whole stack stands up from a clean clone with one command.
9. CI gates every merge; the cloud environment is created and destroyed by Terraform.
10. An eval suite scores copilot accuracy, groundedness, and cost per query on a golden set.

## Open questions to resolve during Phase 0

- Which RPC provider and what free-tier rate limits does it impose?
- What finality depth do we treat as safe for Ethereum?
- Do we model ERC-20 transfers from logs at ingest, or defer decoding to silver?
- What is the freshness SLO we are willing to commit to and alert on?
- How far back do we backfill? Full history is ~4,000 days × multiple tables — a
  cost decision, not a technical one.
- Do we ingest `traces` from the start? It is required for correct tracing but
  materially increases volume.

# Primer — What this data is, and what problem we're solving

Read this before `BRIEF.md`. No blockchain knowledge assumed.

---

## Part 1 — What a blockchain actually is, in data terms

Forget cryptocurrency for a moment. A blockchain is a **database with three
unusual properties**:

1. **Append-only.** Rows are inserted, never updated, never deleted.
2. **Public.** Anyone can read the whole thing, from the first row to the newest.
3. **Pseudonymous.** Every row has a sender and a receiver, but they're 42-character
   hex strings — `0x28c6c06298d514db089934071355e5743bf21d60` — not names.
   There is no customer table. Nobody's identity is in the data.

That third property is the whole reason this project exists. The data is complete
and public, and yet nobody can tell you *who* did anything.

### The unit of batching: a block

Ethereum commits a new **block** roughly every 12 seconds. A block is a batch of
transactions that were accepted together. Think of it as a commit in a database
transaction log — it has a number (height), a hash, a pointer to its parent, and a
timestamp. Blocks form a chain: block 19,000,001's `parent_hash` is block
19,000,000's `hash`.

If you're a data engineer, the mental model is: **a Kafka topic that produces one
batch every 12 seconds, and has been running since July 2015.**

---

## Part 2 — The six tables

The AWS public dataset (`s3://aws-public-blockchain/v1.0/eth/`) normalises the chain
into six tables. Verified structure:

| Table | What it is | Familiar analogue |
|---|---|---|
| `blocks` | One row per block — number, hash, parent_hash, timestamp, miner, gas | Batch header / commit record |
| `transactions` | One row per transaction — hash, from_address, to_address, value, gas, block_number | The main fact table |
| `token_transfers` | One row per ERC-20 token movement (USDT, USDC, …) | Derived fact, decoded from `logs` |
| `logs` | Raw events emitted by smart contracts — address, topics[], data | The raw event stream |
| `traces` | Internal calls made *by contracts*, not by humans | Nested stored-procedure calls |
| `contracts` | Deployed contract code | Deployed application code |

**Partitioning:** `date=YYYY-MM-DD`, Hive style. History starts `2015-08-07`.

**Delivery:** one Parquet file per table per day, written nightly at **~01:45 UTC**.

**Size:** a single day of `transactions` is ~1.4 GB. There are ~4,000 days of history.

### Two details that matter more than they look

**`value` is not dollars.** It's in *wei* — 10^18 wei = 1 ETH — stored as
`DECIMAL(38,0)`. A transfer of 1 ETH is the integer `1000000000000000000`. Every
token has its own decimal precision too, and USDT uses 6 while most use 18. Get this
wrong and every number you report is off by a factor of a trillion. This is a real
data-quality problem, not a footnote.

**Money moves in `traces`, not just `transactions`.** When a human sends ETH, it
appears in `transactions`. When a *smart contract* moves ETH internally — which is
what happens in every exchange withdrawal, every DEX swap, every mixer — it appears
only in `traces`. If you build a fund-tracing tool on `transactions` alone, you will
miss most of the movement you're trying to trace, and you won't get an error. You'll
just get a wrong answer, confidently.

---

## Part 3 — The problem we're solving

### The scene

You work in compliance at a crypto exchange. A customer deposits 40 ETH. You have
minutes — not days — to decide: **accept it, freeze it, or file a suspicious
activity report.**

The money is traceable. Every hop is public. But what you can see is:

```
0x9f2a...  ──40 ETH──▶  [your exchange]
```

That's it. An address. No name, no country, no history attached to it.

### The questions you must answer

1. **Where did these funds come from?** Not one hop — five, ten. Funds are laundered
   by chaining hops through fresh addresses.
2. **Does any hop touch something bad?** A sanctioned address, a known mixer
   (Tornado Cash), an address from a documented exchange hack.
3. **How much exposure?** "3 hops back, 60% of these funds originate from an address
   attributed to a 2023 bridge exploit."
4. **What's the evidence?** A regulator will ask you to show your work. A risk score
   with no citations is worthless — worse than worthless, it's a liability.

### Why it is genuinely hard

**Identity is external and it moves.** The chain tells you `0x28c6...` sent money.
It never tells you that's Binance's hot wallet. That knowledge lives outside — in
label datasets, sanctions lists, and analyst research — and it **changes over time**.
An address that is `unknown` today may be labelled `Lazarus Group` next month.

This is the single most important point in the project:

> If you overwrite the label, you destroy your ability to answer *"what did we know
> at the time we approved this transaction?"* — which is exactly what an auditor
> asks. You need the label that was true **then**, not the label that is true now.

That is what SCD Type 2 is *for*. Not an exercise. The audit requirement.

**Scale.** ~1.2 million transactions a day, plus token transfers, plus traces. Tracing
five hops back means a graph traversal where each hop multiplies the candidate set.

**Timeliness.** The AWS dataset lands at 01:45 UTC for the *previous* day. Worst case
that's ~26 hours old. You cannot make a five-minute deposit decision on
twenty-six-hour-old data. Hence the live path — and hence ADR 0001.

**The chain can change its mind.** Occasionally a block is orphaned — a *reorg* — and
its transactions un-happen. If you approved a deposit based on a transaction that
gets reversed, you have a real problem. So "confirmed" isn't binary; there's a
pending → confirmed → finalized lifecycle you must model.

### What we're building

A platform that takes public, unlabelled, high-volume chain data and turns it into
**governed, labelled, historically-accurate, real-time data** — and a copilot on top
that answers an analyst's question with cited transaction hashes and an audit trail.

The data engineering makes the answer *possible*. The governance makes it
*defensible*. The AI makes it *fast*.

---

## Part 4 — Glossary for `BRIEF.md`

Terms used in the brief and roadmap, in plain language.

| Term | Meaning here |
|---|---|
| **Hot path** | Live data arriving continuously from an Ethereum node, seconds old |
| **Cold path** | Historical data loaded in bulk from the S3 Parquet files |
| **Reconciliation** | Proving the two paths agree — same blocks, same totals, no gaps |
| **Bronze / Silver / Gold** | Medallion layers: raw as-received / cleaned & conformed / business-ready dim + fct |
| **SCD Type 2** | Keeping history when a value changes — a new row with valid_from/valid_to rather than an overwrite |
| **CDC** | Change Data Capture — streaming inserts/updates/deletes out of an operational database as they happen |
| **Reorg** | Reorganisation — the chain discards a block it had accepted; its transactions un-happen |
| **Finality** | The depth at which a block is safe to treat as permanent |
| **Idempotency** | Running the same load twice produces the same result, not duplicates |
| **Backfill** | Loading historical data for date ranges you skipped or need to reprocess |
| **Freshness SLO** | A committed promise: "data is queryable within N seconds/minutes of the event" |
| **Data contract** | An enforced agreement on a table's schema and guarantees, so consumers don't break |
| **Lineage** | A recorded map of which data came from where, through which transformation |
| **RBAC** | Role-Based Access Control — who can see which tables and columns |
| **Masking** | Hiding or obscuring column values from roles not entitled to see them |
| **Groundedness** | Whether the agent's answer is actually supported by the data it retrieved, vs. invented |
| **Golden set** | A fixed set of questions with known-correct answers, used to score the agent on every change |
| **Hop** | One transfer of funds from one address to the next; tracing follows hops backwards |
| **Wei** | Smallest unit of ETH; 10^18 wei = 1 ETH |
| **ERC-20** | The standard interface tokens implement — USDT, USDC, and thousands of others |
| **Mixer** | A service that pools funds from many users to break the traceability of hops |

---

## Sources

- AWS Public Blockchain Datasets announcement — https://repost.aws/articles/AR3gztQGeSS8CfaKNNeyYwsQ
- Ethereum schema (AWS guidance) — https://github.com/aws-solutions-library-samples/guidance-for-digital-assets-on-aws/blob/main/analytics/consumer/schema/eth.md
- ethereum-etl Parquet schemas — https://github.com/blockchain-etl/ethereum-etl/tree/develop/schemas/aws/parquet
- Registry of Open Data on AWS — https://registry.opendata.aws/aws-public-blockchain/

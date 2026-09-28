# Blockchain Data Engineering — Beginner-Friendly Guide

This guide explains the blockchain concepts and data-engineering project context in simple language. It focuses on Ethereum blockchain data, fund tracing, compliance analytics, and a practical lakehouse/warehouse pipeline.

---

## 1. The project in one sentence

The project is building a **crypto compliance and fund-tracing data platform** on Ethereum data.

In simple terms, when someone deposits crypto into an exchange, the system should help answer:

> Where did this money come from, did it interact with risky addresses, and can we prove the answer with evidence?

---

## 2. Blockchain in data-engineering language

Forget cryptocurrency hype for a moment. Think of Ethereum as a giant public, append-only event database.

It has three unusual properties:

1. **Append-only** — new records are added over time; old history is not normally edited or deleted.
2. **Public** — anyone can inspect blocks, transactions, addresses, and smart-contract activity.
3. **Pseudonymous** — records contain wallet addresses such as `0x28c6...`, not a person's name, phone number, or country.

There is no built-in customer table:

```text
wallet_address → ?
```

The blockchain can show that address `0xABC` transferred money to address `0xDEF`, but it does not inherently tell us who owns either address.

### A useful mental model

Ethereum is like a Kafka topic that has emitted one batch approximately every 12 seconds since 2015.

```text
Ethereum network
    ↓
A new batch of events roughly every 12 seconds
    ↓
Blocks, transactions, logs, traces, token transfers
    ↓
Raw files or live streaming events
    ↓
Bronze → Silver → Gold data layers
    ↓
Risk analytics, tracing, dashboards, analyst copilot
```

Each batch is called a **block**.

---

## 3. Essential blockchain vocabulary

| Term | Simple meaning | Data-engineering analogy |
|---|---|---|
| Blockchain | A shared database made of ordered batches | Distributed append-only event store |
| Block | A batch of accepted activity | Kafka micro-batch or database commit |
| Block number / height | The sequential number of a block | Batch sequence number |
| Transaction | One submitted blockchain action, such as sending ETH | Main business event or fact row |
| Wallet address | Public account identifier such as `0xabc...` | Anonymous customer ID without a customer table |
| Hash | A cryptographic fingerprint of data | Immutable checksum or content ID |
| Parent hash | Hash of the preceding block | Pointer to the previous batch |
| ETH | Ethereum's native asset | A unit of value on Ethereum |
| Wei | The smallest ETH unit | Similar to storing money in the smallest unit, but much smaller: \(10^{18}\) wei = 1 ETH |
| Smart contract | A program deployed on Ethereum that can hold assets and execute rules | On-chain application/service with state and business logic |
| Token | An asset implemented by a smart contract, for example USDT or USDC | Separate asset type managed by contract rules |
| ERC-20 | The common standard used by many Ethereum tokens | A standard API/interface implemented by token contracts |
| Gas | Fee paid to execute a transaction | Compute/execution cost |
| Node | A machine running blockchain software | Distributed worker/database replica |
| Reorg | A recently seen block gets replaced or discarded | Late correction/rollback of recent ingested data |
| Finality | The point at which a block is safe to treat as permanent | Data is sufficiently stable for final reporting |
| Hop | One movement of funds between addresses | One edge in a transfer graph |

---

## 4. Blocks, transactions, and the chain

A **block** is a group of transactions accepted together.

Each block contains information such as:

- Block number
- Block hash
- Parent hash
- Timestamp
- Transactions included in the block
- Gas-related information

Blocks form a chain because each new block points to the previous one:

```text
Block 19,000,000
     hash: H1
          ↓
Block 19,000,001
 parent_hash: H1
     hash: H2
          ↓
Block 19,000,002
 parent_hash: H2
```

A **transaction** is one action submitted to Ethereum.

Examples:

- Alice sends ETH to Bob.
- A user swaps USDC for ETH using a decentralized exchange.
- A user interacts with an NFT contract.
- A contract transfers tokens internally.

A transaction has a stable identifier called a **transaction hash**, commonly written as `tx_hash` or `transaction_hash`.

---

## 5. The six Ethereum source tables

The Ethereum dataset is normalized into six important tables.

| Table | What it contains | Why it matters |
|---|---|---|
| `blocks` | One row per block: number, hash, parent hash, timestamp, miner/validator-related fields, gas | Validates chronology, completeness, lineage, and reorgs |
| `transactions` | One row per user-submitted transaction: sender, receiver, ETH value, gas, transaction hash, block number | Main fact table for direct ETH transfers and contract calls |
| `token_transfers` | Decoded ERC-20 token movements such as USDT and USDC | Required to trace token flows and stablecoins |
| `logs` | Raw events emitted by smart contracts | Source-level event stream; often used to derive business facts |
| `traces` | Internal calls and internal ETH movement initiated during contract execution | Critical for tracing fund movement through contracts, DeFi, exchanges, and mixers |
| `contracts` | Deployed smart-contract code | Helps identify what software/application an address represents |

### Simplified relationship between tables

```text
Block
  ↓
Transaction
  ↓
Smart contract execution
  ├── Traces: internal calls and ETH movements
  └── Logs: raw emitted contract events
          ↓
     Token transfers: decoded ERC-20 Transfer events
```

---

## 6. Why `transactions` alone are not enough

A direct ETH transfer may look like this:

```text
Alice wallet → Bob wallet
```

This can appear directly in the `transactions` table.

But real crypto activity often uses smart contracts:

```text
Alice wallet
   ↓
DEX smart contract
   ↓
Token contract
   ↓
Liquidity pool
   ↓
Another wallet or contract
```

The outer transaction may only say that Alice called a decentralized exchange contract. The internal steps can include multiple calls and money movements.

Those details appear in:

- `traces` for internal execution and internal ETH movements
- `logs` for raw smart-contract events
- `token_transfers` for decoded ERC-20 transfers

### Critical rule

If you use only `transactions`, your fund-tracing analysis can be incomplete.

You may miss:

- Internal ETH transfers
- Transfers caused by smart-contract logic
- DeFi swap paths
- Exchange withdrawal paths
- Mixer-related movement
- Stablecoin and other ERC-20 token activity

For a fund-flow project, `transactions` are important but are not the complete money-movement picture.

---

## 7. What is a fund-tracing hop?

A **hop** is one transfer of funds from one address to another.

```text
Risky wallet A → Wallet B → Wallet C → Exchange wallet
                   Hop 1     Hop 2      Hop 3
```

If an exchange receives money from Wallet C:

- A **one-hop** investigation checks Wallet C.
- A **two-hop** investigation checks Wallet C and Wallet B.
- A **three-hop** investigation checks Wallet C, Wallet B, and Wallet A.

Funds can be moved through several newly created addresses to make the original source harder to identify:

```text
Hack address → New wallet 1 → New wallet 2 → New wallet 3 → Exchange
```

In graph terms:

- **Nodes** = wallet addresses and smart contracts
- **Edges** = ETH or token transfers
- **Edge properties** = transaction hash, amount, asset, time, block number, transfer type
- **Node properties** = risk labels, ownership/attribution, address type, label history

---

## 8. The compliance use case

Imagine that a customer deposits 40 ETH into an exchange:

```text
Unknown wallet 0x9f2a... ── 40 ETH ──▶ Exchange deposit wallet
```

The exchange may have only a few minutes to decide whether to:

- Accept the deposit
- Freeze or hold the deposit temporarily
- Escalate it for compliance review
- File a suspicious activity report, where appropriate

The analyst needs answers such as:

1. Where did the funds originate over the last 3, 5, or 10 hops?
2. Did the path touch a sanctioned address, mixer, known exploit, or fraud-linked wallet?
3. What portion of the deposit is connected to that risk?
4. What exact evidence supports the result?

An example investigation conclusion could be:

```text
Deposit amount: 40 ETH
Potential high-risk exposure: 24 ETH
Exposure rate: 60%
Finding: 24 ETH has a three-hop traceable path to an address
labelled as associated with a documented bridge exploit.
Evidence: transaction hashes, address path, block numbers,
timestamps, and historically valid label records.
```

A score alone is not sufficient for compliance. The system must keep evidence that a regulator or auditor can inspect.

---

## 9. The identity problem

Blockchain is transparent, but addresses are not automatically linked to people or organizations.

The chain tells you:

```text
0xABC sent 10 ETH to 0xDEF
```

It does not tell you:

```text
0xABC belongs to Company X
0xDEF belongs to a sanctioned actor
```

That knowledge comes from external sources:

- Address-label providers
- Sanctions lists
- Exchange attribution data
- Security-research reports
- Internal compliance investigations
- Analyst-reviewed evidence

This means your platform needs an address-label dimension.

Example:

| address | label | risk_category | label_source | confidence |
|---|---|---|---|---|
| `0xabc...` | Example Exchange hot wallet | Exchange | External provider | High |
| `0xdef...` | Known mixer | Mixer | Research dataset | High |
| `0xghi...` | Unknown | Unknown | System default | N/A |

---

## 10. Why SCD Type 2 matters

Address labels can change over time.

For example:

- On 1 January, an address is unknown.
- On 15 March, an investigation links it to a high-risk entity.

Do **not** overwrite the old label. Instead, preserve historical versions using **Slowly Changing Dimension Type 2 (SCD Type 2)**.

```text
address: 0xABC
label: Unknown
valid_from: 2026-01-01
valid_to: 2026-03-14
is_current: false

address: 0xABC
label: High-risk entity
valid_from: 2026-03-15
valid_to: null
is_current: true
```

This allows you to answer two different questions correctly:

```text
What do we know now?
→ Use the current label.

What did we know at the time a deposit was approved?
→ Use the label version valid at that time.
```

This is vital for auditability. A system should not rewrite history just because new intelligence appears later.

### Suggested SCD Type 2 columns

```text
address_sk
address
label
entity_name
entity_type
risk_category
risk_score
label_source
source_url_or_reference
confidence
valid_from
valid_to
is_current
ingested_at
reviewed_by
```

---

## 11. Amounts: wei, ETH, and token decimals

A common blockchain data-quality problem is incorrect amount conversion.

### ETH and wei

Ethereum commonly stores ETH values in **wei**:

\[
1\ \text{ETH} = 10^{18}\ \text{wei}
\]

Example:

```text
Raw value: 1000000000000000000 wei
Actual amount: 1 ETH
```

Conceptual SQL:

```sql
CAST(value AS DECIMAL(38, 0)) / POWER(10, 18) AS eth_amount
```

### Token decimals

ERC-20 tokens have their own decimal precision.

Examples:

- USDT often uses 6 decimals.
- Many tokens use 18 decimals.
- Other tokens may use different values.

```text
Raw USDT amount: 2500000
Token decimals: 6
Actual amount: 2.5 USDT
```

Formula:

\[
\text{human-readable amount} =
\frac{\text{raw integer amount}}{10^{\text{token decimals}}}
\]

### Token metadata dimension

Create and maintain a token metadata table:

```text
token_contract_address
token_symbol
token_name
token_decimals
chain_id
is_verified
valid_from
valid_to
```

Never assume every token has 18 decimals. A wrong conversion can make reports wrong by factors of millions or trillions.

---

## 12. Scale and graph-explosion problem

Blockchain generates high data volume:

- Large numbers of transactions every day
- More token-transfer records
- Even more raw logs and traces
- Years of historical data

Tracing is a graph traversal problem. Candidate paths can expand quickly.

```text
One deposit
   ↓
One sending address
   ↓
Several upstream transfers
   ↓
Many upstream addresses
   ↓
Potentially very large search space
```

To control scale, apply clear limits and rules:

- Maximum hop count, for example 3 or 5
- Lookback time window, for example 30, 90, or 365 days
- Minimum transfer amount
- Asset coverage rules: ETH only, or ETH plus selected tokens
- Risk-based pruning: follow known/high-risk paths first
- Deduplication and cycle detection
- Partition pruning by date and chain/block range

---

## 13. Historical and real-time ingestion

A historical S3 dataset is useful for:

- Initial backfills
- Historical investigations
- Trend analysis
- Warehouse/lakehouse queries
- Reconciliation

But batch data may be around one day old. It cannot support a decision that must be made within a few minutes.

Use two data paths.

```text
Historical/cold path
AWS S3 Parquet files
    ↓
Batch ingestion
    ↓
Historical warehouse/lakehouse tables

Near-real-time/hot path
Ethereum node, RPC provider, or WebSocket stream
    ↓
Streaming ingestion
    ↓
Fast risk and operational tables
```

Then reconcile them:

```text
Live path records
        ↔
Historical batch records
```

The goal is to prove both sources ultimately agree on block ranges, transaction totals, hashes, and records.

---

## 14. Reorgs and finality

A blockchain can occasionally reorganize its newest blocks.

A **reorg** means that a block you previously observed may later be replaced by another valid block chain. Transactions in the removed/orphaned block may no longer be part of the canonical chain.

Therefore, do not treat every newly seen transaction as permanently final.

Use a lifecycle like:

```text
Pending → Confirmed → Finalized
```

### Practical data-model fields

```text
block_number
block_hash
parent_hash
transaction_hash
confirmation_count
block_status
is_canonical
orphaned_at
finalized_at
```

Your pipeline should be able to:

- Detect mismatched parent hashes
- Identify replaced/orphaned blocks
- Mark affected records as non-canonical or orphaned
- Reprocess recent block ranges
- Avoid making irreversible risk decisions from insufficiently confirmed events

---

## 15. Recommended medallion architecture

```text
                    Historical Ethereum data
                 AWS S3 Parquet, by date partition
                              ↓
                         Batch ingestion
                              ↓
                          Bronze layer
                 Raw blocks, txs, logs, traces
                              ↓
                 Cleaning and standardization
                              ↓
                          Silver layer
      Clean addresses, correct amounts, deduped records,
         normalized transfers, reorg-aware status
                              ↓
          Labels, sanctions, attribution enrichment
                              ↓
                           Gold layer
      Fund-flow graph, exposure facts, alerts, evidence
                              ↓
                   Dashboard / API / AI copilot
```

For fast decisions, add the hot streaming path and reconcile it later with the historical path.

---

## 16. Bronze, Silver, and Gold layers

### Bronze layer: raw and replayable

Keep source data as received.

Suggested contents:

- Raw `blocks`
- Raw `transactions`
- Raw `token_transfers`
- Raw `logs`
- Raw `traces`
- Raw `contracts`
- Source file and partition details
- Ingestion timestamp
- Batch/run ID
- Record hash or source checksum where useful

**Goal:** retain an immutable raw record so you can replay transformations later.

### Silver layer: clean and conformed

Make blockchain records safe and consistent for downstream users.

Suggested transformations:

- Convert addresses to lowercase
- Standardize timestamps and timezone treatment
- Parse and validate numeric values
- Convert wei/token units correctly
- Deduplicate using transaction hash, log index, trace identifiers, and source metadata
- Add block and transaction status
- Detect reorgs and canonical-chain changes
- Join token transfers to token-decimal metadata
- Build normalized ETH and token transfer records
- Detect missing partitions and unexpected volume changes

**Goal:** produce technically correct, reusable blockchain data.

### Gold layer: business-ready analytics

Build tables that directly answer compliance and analyst questions.

Potential models:

```text
dim_address_scd2
dim_token
dim_risk_category
dim_label_source
fct_block
fct_transaction
fct_eth_transfer
fct_token_transfer
fct_fund_flow_edge
fct_risk_exposure
fct_alert
fct_investigation_evidence
```

**Goal:** let analysts ask business questions without understanding raw smart-contract event structures.

---

## 17. Suggested canonical transfer model

A useful design is to create one normalized transfer fact/edge model that combines direct ETH transfers, internal ETH transfers, and token transfers.

Example conceptual schema:

```text
transfer_id
chain_id
block_number
block_hash
block_timestamp
transaction_hash
transfer_type              -- direct_eth / internal_eth / erc20_token
from_address
to_address
asset_contract_address     -- null for native ETH
asset_symbol
raw_amount
asset_decimals
amount_normalized
usd_value_at_transfer_time -- optional, if reliable price data exists
trace_path                 -- for internal contract movement
log_index                  -- for token transfer events
is_canonical
finality_status
ingested_at
```

This gives graph traversal logic a consistent interface:

```text
from_address → to_address
```

regardless of whether the movement came from a direct transaction, an internal trace, or an ERC-20 token event.

---

## 18. Evidence and auditability

For every risk finding, retain evidence—not just the final score.

An investigation record should be able to preserve:

- Deposit transaction hash
- Block number and timestamp
- All addresses in the traced path
- Every transaction or trace hash used in the path
- Hop number and direction
- Asset and amount at each movement
- Address label and risk category
- Label source and confidence
- SCD Type 2 version valid at decision time
- Risk-rule version used
- Time the decision was made
- Analyst actions and disposition

Example evidence chain:

```text
Deposit transaction: 0xDepositTx

Hop 1:
0xWalletC → Exchange
Transaction: 0xTxC
Amount: 10 ETH

Hop 2:
0xWalletB → 0xWalletC
Transaction: 0xTxB
Amount: 6 ETH

Hop 3:
0xRiskyWalletA → 0xWalletB
Transaction: 0xTxA
Amount: 6 ETH

Address label:
0xRiskyWalletA = Known bridge exploit
Label source = Analyst-reviewed intelligence feed
Label validity = active at time of decision
```

The AI copilot should only summarize these stored facts. It should not invent paths, labels, or citations.

---

## 19. Practical build order

Build the project in this sequence:

1. Ingest the six source tables with partition-aware and idempotent batch loading.
2. Create a canonical block model using block number, hash, and parent hash.
3. Add completeness, uniqueness, and reorg checks.
4. Build clean direct ETH transfer facts from `transactions`.
5. Add ERC-20 transfer facts using `token_transfers` and token metadata.
6. Add internal ETH movements from `traces`.
7. Build a normalized transfer-edge model for graph traversal.
8. Create address labels and track changes with SCD Type 2.
9. Implement N-hop backward tracing with clear time, value, and depth limits.
10. Create risk-exposure, alert, and evidence models.
11. Add a live streaming/hot path after historical correctness and reconciliation are reliable.
12. Build the analyst UI, API, dashboard, or grounded AI copilot on top.

---

## 20. Common mistakes to avoid

- Treating a wallet address as a verified real-world identity.
- Using only the `transactions` table for fund tracing.
- Forgetting that ERC-20 token transfers are separate from ETH transfers.
- Assuming all tokens have 18 decimal places.
- Treating raw values as ETH, USD, or human-readable token quantities.
- Overwriting labels instead of preserving SCD Type 2 history.
- Ignoring reorgs and treating recent blocks as permanently final.
- Creating duplicate records when a partition or stream batch is reprocessed.
- Building graph traversals without hop, time, and amount constraints.
- Storing only a risk score instead of preserving the evidence and rule version behind it.
- Building real-time alerting before validating historical data quality and reconciliation.

---

## 21. Final takeaway

Your role is to turn public but anonymous Ethereum activity into **trusted, business-ready, historically accurate, explainable data**.

The system should:

- Ingest blockchain data reliably
- Understand direct, internal, and token-based transfers
- Trace fund movement through multiple hops
- Enrich addresses using external labels
- Preserve label history with SCD Type 2
- Handle data scale, decimals, reorgs, and freshness
- Calculate risk exposure
- Preserve transaction-level evidence for audit and compliance

In one sentence:

> You are building a data platform that follows crypto money flows, identifies possible risk connections, and produces an answer that analysts can verify and defend.

# Data & AI Engineering Program

Industry-level projects taken from development to production, each exercising
both Data Engineering and AI Engineering at architect level.

| | |
|---|---|
| Roadmap & phase ladder | [`docs/PROGRAM_ROADMAP.md`](docs/PROGRAM_ROADMAP.md) |
| Primer — start here if blockchain is new | [`docs/PRIMER.md`](docs/PRIMER.md) |
| Architecture | [`docs/architecture.png`](docs/architecture.png) |
| Decisions | [`docs/adr/`](docs/adr/) |

## Projects

| # | Project | Status |
|---|---|---|
| 01 | [Chain Risk Copilot](projects/01-chain-risk-copilot/) — real-time Ethereum platform + AML copilot | Phase 0 |
| 02 | IT/OT Predictive Maintenance | not started |
| 03 | Patient / Member 360 | not started |
| 04 | Customer 360 & Next Best Action | not started |

## Prerequisites

- **Docker Desktop** with the WSL2 backend, at least 6 GB of memory allocated
- **git**
- **uv** — `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`

No local Python, Kafka, or Postgres install is needed; everything runs in containers.

## Quick start

From a clean clone, three commands:

```
make setup     # creates infra/.env, installs dependencies and pre-commit hooks
make up        # starts the stack, returns once every service is healthy
make smoke     # end-to-end check of all five services; exit 0 = good
```

On Windows without `make`, use `.\dev.ps1 setup`, `.\dev.ps1 up`, `.\dev.ps1 smoke`; every
target below exists there too.

`make setup` copies `infra/.env.example` to `infra/.env` only if the latter is absent, so it is
safe to re-run and never overwrites your values. The example values are fine for a local stack.

| Target | What it does |
|---|---|
| `make help` | List the targets |
| `make setup` | Create `infra/.env` if absent, `uv sync --all-groups`, install pre-commit hooks |
| `make up` / `make down` | Start (and wait for healthy) / stop, **keeping data** |
| `make ps` / `make logs` | Service status / follow logs (`make logs SERVICE=redpanda`) |
| `make smoke` | Run the end-to-end smoke test |
| `make ingest` | Ingest the 7-day window into bronze (needs the stack up) |
| `make profile` | Profile every committed bronze partition into `chainrisk-artifacts` (see below) |
| `make fmt` / `make lint` / `make test` | Format / ruff + mypy / unit tests (no stack needed) |
| `make clean` | **Destroys data**: `down -v` removes the stack's volumes. The only target that does |

### Bronze profiles

`make profile` writes one Parquet object per table-day, one row per column, to
`chainrisk-artifacts/profiles/bronze/eth/<table>/<YYYY-MM-DD>.parquet`. Each row has the
partition's `rows` and `bytes_on_disk`, and per column `null_count`, `null_rate`,
`empty_count` (text columns only: the source writes `''` for "none", which a null rate
cannot see), `out_of_range_count` (columns with a hard limit in `BOUNDS`; today `value`
above 2^256, which is undecodable, see CH-006), `min_value`/`max_value` (numeric and
timestamp columns, stored as text) and `distinct_count` (address columns only).
Re-running replaces the object, never appends. A partition with no `_SUCCESS` is skipped
and listed, never profiled as empty. Exit code 1 means a partition
failed (skips do not); 2 means a bucket was unreachable.

`min_value` and `max_value` are text: cast integer columns with `BIGINT`, but `value` with
`DOUBLE`, because floats are stored in scientific notation (`5e+22`).

Read the whole window from DuckDB (credentials are the `MINIO_ROOT_*` values in `infra/.env`):

```sql
INSTALL httpfs; LOAD httpfs;
SET s3_endpoint='localhost:9000'; SET s3_use_ssl=false; SET s3_url_style='path';
SET s3_access_key_id='...'; SET s3_secret_access_key='...';
SELECT day, null_rate FROM read_parquet('s3://chainrisk-artifacts/profiles/bronze/eth/*/*.parquet')
WHERE table_name = 'transactions' AND column_name = 'to_address' ORDER BY day;
```

`marker_ingested_at` is the bronze marker's timestamp: if it differs from the partition's
current marker, the profile describes data that has since been re-ingested.

### Ports

All published on `localhost`.

| Port | Service |
|---|---|
| 19092 | Kafka API (Redpanda, external listener) |
| 18081 | Schema Registry |
| 18082 | HTTP Proxy (pandaproxy) |
| 19644 | Redpanda Admin API |
| 8085 | Redpanda Console — <http://localhost:8085> |
| 9000 | MinIO S3 API |
| 9001 | MinIO console — <http://localhost:9001> |
| 5433 | Postgres (ops) |
| 8080 | reserved for Airflow |

Inside the compose network, containers use `redpanda:9092` rather than `localhost:19092`.

## Layout

```
Makefile, dev.ps1           developer entrypoint (see Quick start)
docs/                       program docs, ADRs, architecture
projects/
  01-chain-risk-copilot/
    infra/                  docker-compose stack, .env, service init
    src/chainrisk/          application code
    dags/                   Airflow DAGs
    dbt/                    dbt project
    terraform/              cloud infrastructure
    scripts/                operational scripts
    tests/
```

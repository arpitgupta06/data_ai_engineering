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

## Layout

```
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

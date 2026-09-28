# Sprint 1 — Foundations

**Dates:** week of 2026-09-14 → 2026-09-20
**Epic:** CRC-1 · Phase 0 — Foundations
**Sprint goal:** *A clean clone of this repository brings up a verified local data
plane with one command.*

Committed: **10 points** · Stretch: **5 points**

> Create these in Jira in the order listed and a fresh CRC project will assign the
> keys shown. If your numbering drifts, note the real keys here — the branch names
> must match whatever Jira actually gives you.

---

## CRC-2 · Bootstrap repository scaffold — `Done`

**Type:** Task · **Points:** 2 · **Status:** Done (pre-process)

Repository hygiene and Python packaging, committed directly to `main` before the
ways-of-working document existed. Recorded so the board reflects reality.

**Delivered:** `.gitattributes`, `.editorconfig`, `.gitignore`,
`.pre-commit-config.yaml`, root `README.md`, `pyproject.toml` (uv), package skeleton.

---

## CRC-3 · Local data plane stands up via Docker Compose

**Type:** Story · **Points:** 5

> As an engineer joining this project, I want to bring up Kafka, object storage and
> the operational database with one command, so that I can start building pipelines
> without installing anything locally.

**Acceptance criteria**

1. `docker compose up -d` from `projects/01-chain-risk-copilot/infra` starts all services and every one reaches `healthy`.
2. A Kafka client on the **host** can list topics at `localhost:19092`.
3. The Schema Registry answers on `localhost:18081`.
4. Redpanda Console loads at `http://localhost:8085`.
5. MinIO console loads at `http://localhost:9001`; buckets `chainrisk-bronze` and `chainrisk-artifacts` exist.
6. `psql` on the host connects at `localhost:5433`, and `SHOW wal_level;` returns `logical`.
7. No credential appears in any tracked file; all come from `infra/.env`, with `infra/.env.example` committed.
8. Every image tag is pinned — no `latest`.
9. `docker compose down` preserves data; `docker compose down -v` removes it.

**Subtasks**

| Key | Subtask | Branch |
|---|---|---|
| CRC-4 | Redpanda service with dual internal/external listeners + Schema Registry | `feature/CRC-4-redpanda-dual-listeners` |
| CRC-5 | Redpanda Console on port 8085 | `feature/CRC-5-redpanda-console` |
| CRC-6 | MinIO service + one-shot bucket bootstrap with `mc` | `feature/CRC-6-minio-buckets` |
| CRC-7 | Postgres (ops) with `wal_level=logical` for future CDC | `feature/CRC-7-postgres-ops-logical` |
| CRC-8 | `.env.example`, env wiring, healthchecks and `depends_on` conditions | `chore/CRC-8-env-and-healthchecks` |

**Notes** — the dual-listener config (`--kafka-addr` vs `--advertise-kafka-addr`) is
the part that usually breaks: containers connect fine while the host cannot. The
`minio-init` container must wait on a MinIO **healthcheck**, not just `depends_on`,
or it races and fails intermittently.

---

## CRC-9 · Smoke test proves every service end to end

**Type:** Story · **Points:** 3

> As an engineer, I want a single command that proves the whole stack actually
> works, so that "it's up" means something more than green containers.

**Acceptance criteria**

1. `make smoke` exits 0 when the stack is healthy and non-zero when any check fails.
2. Checks, each reported as a separate pass/fail line: produce and consume a Kafka message round-trip; register and read back a schema in the Schema Registry; write and read an object in `chainrisk-bronze`; connect to ops Postgres and assert `wal_level=logical`; open a DuckDB file and run a query.
3. Failures name the service and the reason — not a bare stack trace.
4. Runs from the host, using `chainrisk.settings`, with no hardcoded connection strings.

**Subtasks**

| Key | Subtask | Branch |
|---|---|---|
| CRC-10 | `src/chainrisk/settings.py` — typed settings loaded from `infra/.env` | `feature/CRC-10-typed-settings` |
| CRC-11 | `scripts/smoke_test.py` with per-service checks and a result table | `feature/CRC-11-smoke-test` |
| CRC-12 | Unit tests for settings; mark integration tests with `@pytest.mark.integration` | `test/CRC-12-settings-tests` |

---

## CRC-13 · Developer entrypoint

**Type:** Story · **Points:** 2

> As an engineer, I want documented one-word commands, so that I don't rediscover
> the right `docker compose` incantation every session.

**Acceptance criteria**

1. `Makefile` at the repo root with: `setup`, `up`, `down`, `ps`, `logs`, `smoke`, `fmt`, `lint`, `test`, `clean`.
2. `dev.ps1` exposes the same targets for PowerShell, since Windows has no `make`.
3. `make help` lists every target with a one-line description.
4. Project README documents the ports table and the startup sequence.

---

## CRC-14 · Airflow behind a compose profile — *stretch*

**Type:** Story · **Points:** 5

> As a data engineer, I want Airflow running against the same stack, so that Phase 1
> can schedule the backfill.

**Acceptance criteria**

1. `docker compose --profile airflow up -d` adds Airflow 3.3.1 without disturbing running services.
2. UI reachable at `http://localhost:8080`; login works.
3. `LocalExecutor` against a dedicated metadata Postgres — no Redis, no Celery worker.
4. Example DAGs disabled; `dags/` bind-mounted from the project.
5. The data plane still comes up standalone without the profile.

---

## Not in this sprint

Carried to Sprint 2, deliberately — pulling them in would put the sprint goal at risk.

| Story | Points |
|---|---|
| CI pipeline: lint, type check, tests, compose validation on PR | 3 |
| Secret scanning: detect-secrets baseline + gitleaks in CI | 2 |
| Terraform skeleton + AWS budget alarm + Snowflake resource monitor | 5 |

---

## Retro notes

*(fill in Sunday)*

**Went well:**

**Slowed me down:**

**One change for Sprint 2:**

**Actual points completed:** ___ of 10 committed

# CH-004 · A smoke test that had only ever been seen to pass

| | |
|---|---|
| **Phase** | 0 — Foundations |
| **Jira** | CRC-13 |
| **ADR** | none |
| **Category** | data-quality, ops |
| **Status** | Resolved |
| **Surfaced** | 2026-10-06 |
| **Closed** | 2026-10-09 |

## Context

Sprint 1 produced a local data plane — Redpanda, MinIO, Postgres — verified by
running six commands by hand and reading the output. That verification lived in a
chat transcript, not in the repository. Nobody could answer "does it still work?"
without repeating it from memory.

CRC-13 was the story that turned those manual checks into one command.

### What was delivered

**`src/chainrisk/settings.py`** — the only module in the codebase that knows a
hostname, a port or a credential. Typed with pydantic-settings, passwords as
`SecretStr`, derived properties for each endpoint, and a Postgres DSN that
percent-encodes user and password. Host and port defaults live here (overridable
by environment variable) rather than in `.env`: compose owns ports *for
containers*, and the host-side client is a different consumer with its own needs.
That decision is documented in `infra/.env.example`.

**`scripts/smoke_test.py`** — five independent checks, run concurrently:

| Check | What it actually proves |
|---|---|
| Kafka | produce to a topic and consume the message back |
| Schema Registry | register a schema, read it back, delete the subject |
| MinIO | head the bronze bucket; put/get/delete an object in artifacts |
| Postgres | connect and assert `wal_level = logical` |
| DuckDB | open the database file and run a query |

Every network operation carries a timeout; the whole run carries a deadline
derived from them (`MAX_SEQUENTIAL_OPS * TIMEOUT_S + 5`) rather than a magic
number. Each check catches its own exceptions and returns a result, so one
failure never suppresses the other four. Results print as they complete.

**`tests/test_settings.py`** — 15 unit tests that pass with the stack down,
including secret masking in `repr`, percent-encoding of awkward passwords, and an
autouse fixture that strips every settable variable so a developer's shell cannot
leak into a test run.

**Tooling** — mypy extended from `src` to `src`, `scripts` and `tests` with the
pydantic plugin; pytest's `python_files` narrowed so `smoke_test.py` is not
collected as a test module.

## The problem

The script passed. Five green lines, every time.

And that was the entire evidence base. Two of its seven acceptance criteria —
*exits non-zero on failure* and *failures name the service and the reason* — had
never been exercised even once. A tool whose only purpose is to tell you when
something is broken had never been shown a broken thing.

The first attempt to run it against a stopped stack was abandoned: librdkafka
wrote five lines of raw connection errors to stderr, the script printed nothing
at all until every check finished, and it looked wedged. It was killed with
Ctrl-C, which produced a traceback. So the run proved nothing, and left an open
question — had it actually hung, or had it merely looked like it?

## Why it was hard

**Green is not evidence.** A healthy-looking check only tells you the code path
that succeeds, works. Everything about failure — exit codes, message quality,
whether a check detects anything at all — is a separate, untested program.

**The failure path is where the design flaws live.** All three defects found in
this ticket were invisible on the happy path: output buffered until the end, a C
library writing to stderr, and a whole-run deadline (20s) set below the longest
legitimate single check (~31s), which would have reported a merely-slow broker as
hung and hard-exited a working stack.

**Verification kept being blocked by bad measurements.** Four times in this one
ticket, a signal that looked like evidence was not:

| Signal | What it claimed | What was true |
|---|---|---|
| `$sw.Elapsed` from a stopwatch started in an earlier shell command | 47 seconds | 12.8 seconds — the stopwatch predated the run |
| `awk length` over a CRLF file | line was 101 characters | 100 characters; `awk` was counting the carriage return |
| `mc mb --ignore-existing` printing "Bucket created successfully" | bucket was created | bucket already existed — the flag suppresses the error, not the message (see [CH-003](CH-003-minio-eol-registry-lockout.md)) |
| Docker Hub's tag API listing an image | image was pullable | the registry refused it (CH-003) |

The 47-second reading nearly caused a code change to fix a teardown hang that did
not exist.

## Options considered

| Option | Pros | Cons | Cost of being wrong |
|---|---|---|---|
| **A. Ship it green** | Fast; acceptance criteria look satisfied on paper | Two criteria rest on no evidence | A diagnostic tool that reports success when the stack is broken — worse than no tool, because it is believed |
| **B. Unit-test the checks with mocked clients** | Fast, deterministic, CI-friendly | Mocks assert your assumptions about the clients, not the clients' behaviour. None of the three real defects involved logic a mock would model | False confidence, with a green test suite to back it |
| **C. Run it against a deliberately broken stack** | Exercises the real failure path end to end, including output quality and exit code | Slow; must stop and restart the stack; not automatable in CI without compose | Costs minutes |

## Decision

**Option C**, run three times across three review rounds, because the first two
attempts produced no usable evidence.

The proof that closed the ticket:

```
docker compose down
uv run python scripts/smoke_test.py
  PASS  duckdb           opened existing chainrisk.duckdb; query OK (0.1s)
  FAIL  schema-registry  ConnectionError: ... actively refused it (4.1s)
  FAIL  minio            EndpointConnectionError: Could not connect ... (9.4s)
  FAIL  kafka            TimeoutError [librdkafka: Connect to ipv4#127.0.0.1:19092 failed] (10.0s)
  FAIL  postgres         ConnectionTimeout: ... '::1' ... '127.0.0.1' ... (10.0s)
  1/5 checks passed
exit=1   elapsed=12.76s
```

**What it cost:** three review cycles and roughly a third of the story's effort
spent on verification rather than on the code. In exchange, every claim the
ticket makes is now backed by an observation.

## How we validated it

All seven acceptance criteria, each against evidence rather than assertion:
`5/5` against a healthy stack and `exit=1` with five named failures against a
stopped one; `ruff`, `mypy` and 15 tests clean; wall time 12.76s measured in a
single statement rather than across two commands.

Two results were worth more than the pass itself.

The Postgres failure printed attempts against both `::1` and `127.0.0.1`, taking
10.0s — confirming a prediction made while writing the check, that `localhost`
resolves to two addresses and `connect_timeout` applies *per address*. The
timeout had been halved in advance for exactly that reason.

The Kafka failure read `TimeoutError [librdkafka: Connect to ipv4#127.0.0.1:19092
failed]`. `TimeoutError` alone says nothing. Capturing librdkafka's last log line
— after silencing its stderr output — turned a bare exception into a diagnosis.

## Outcome

CRC-13 merged with all seven criteria met. The stack is now verifiable in one
command, in 13 seconds, with failures that name what is broken and why.

Three defects were found and fixed that the passing path never revealed: buffered
output, unsilenced C-library logging, and a deadline shorter than the work it was
meant to bound.

## What I'd do differently

**Write the failing case first.** The whole ticket would have been one round
instead of three if the first run had been against a stopped stack. The failure
path is the product; the success path is the easy half.

**Measure in one statement.** Every bad measurement here came from a value that
outlived the thing it was measuring, or from counting the wrong unit. A stopwatch
started in a different command is not timing this run. `awk length` counts bytes,
`ruff` counts characters, and on Windows those differ by one per line.

**Distrust a tool's own report of its own success.** Across this ticket and
CH-003, every wrong turn came from believing a claim instead of checking the
underlying state: a log line, a tag listing, a stopwatch, a byte count. The
filesystem, the registry and the exit code were right every time.

---

## 90-second answer

> I'd written a smoke test for a local data platform — five services, round-trip
> checks, one command. It passed every time I ran it, and I was ready to call it
> done.
>
> Then I realised the only thing I'd ever observed was the success path. The
> whole point of the tool is to tell you when something is broken, and it had
> never been shown a broken thing. Two of its acceptance criteria — that it exits
> non-zero, and that failures name the service and the reason — had zero evidence
> behind them.
>
> So I stopped the stack and ran it. That one run found three defects the passing
> path couldn't: it buffered all its output until the end, so a slow failure
> looked like a hang; the Kafka C library was writing raw connection errors
> straight to stderr underneath my formatted output; and the whole-run deadline
> was twenty seconds while the longest legitimate check could take thirty-one —
> so a merely slow broker would have been reported as hung and the process hard-
> exited on a working stack.
>
> The other thing it taught me was about measurement. I nearly "fixed" a
> performance problem that didn't exist, because I read elapsed time off a
> stopwatch that had been started in an earlier command. The real number was
> 12.8 seconds, not 47. Same lesson as a registry that listed an image it refused
> to serve, and a CLI that printed "created successfully" for something that
> already existed.
>
> What I took from it: write the failing case first, measure in one statement,
> and never accept a tool's report of its own success as evidence.

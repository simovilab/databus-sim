# Databus FSM Test Harness

End-to-end test suite that drives all databus FSMs using their JSON spec files as the single source of truth.

---

## Prerequisites

- **Python 3.11+** and **uv**
- **databus stack** running (see below)

---

## Start the databus stack

```bash
cd ../databus
docker compose -f compose.dev.yml up
```

Wait until all services are healthy. Host ports:

| Service | Address |
|---|---|
| Backend REST | `http://localhost:8000` |
| MQTT (NanoMQ) | `localhost:1883` |
| RabbitMQ | `localhost:5672` (mgmt: `localhost:15672`, guest/guest) |
| Postgres | `localhost:5432` (db: `databus`, user: `postgres`) |
| Redis | `localhost:6379` |

---

## Install the harness

```bash
cd sim/
uv sync
```

---

## Run

```bash
# All scenarios
uv run python -m harness run

# Single tag
uv run python -m harness run -t register-run
uv run python -m harness run -t happy-path
uv run python -m harness run -t failure-path

# Single scenario file
uv run python -m harness run scenarios/register-run/happy.yaml

# Output JUnit XML for CI
uv run python -m harness run --junit results.xml
```

---

## Other commands

```bash
# Parse all FSM JSONs and show structure (also shows end-run spec bug warning)
uv run python -m harness list-fsms

# Validate scenario YAML files against FSMs without executing anything
uv run python -m harness validate

# Show which FSM edges have scenario coverage
uv run python -m harness coverage

# Generate scaffold stubs for all uncovered failure edges
uv run python -m harness scaffold

# Delete scenario files whose FSM references are stale (preview first)
uv run python -m harness prune --dry-run
uv run python -m harness prune
```

---

## Unit tests (no databus required)

```bash
uv run pytest -m unit -v
```

These 21 tests cover FSM parsing, graph navigation, and the 4 iterability guarantees — they run in < 1 second with no external services.

---

## Interpreting output

### `harness run`

| Status | Meaning |
|---|---|
| **PASS** | All assertions satisfied within timeout |
| **FAIL** | One or more assertions timed out or returned wrong value |
| **SKIP** | Scenario is a scaffold stub (fill in `inputs:` to enable) |

**Expect FAIL on day 1.** Most FSMs are unimplemented. A failing scenario tells you *exactly* which AMQP event or HTTP response is missing — that is the contract the databus must satisfy.

### `harness coverage`

Shows every FSM edge (state → event → state) and whether a scenario exercises it. Red ✗ on a failure edge means no scenario has been written for that branch yet. Run `harness scaffold` to auto-generate stubs.

---

## When FSM JSONs change

Run this sequence to keep scenarios in sync after editing a spec file:

```bash
# 1. Remove scenarios that reference states/events no longer in the FSM
uv run python -m harness prune

# 2. Generate stubs for any new failure edges
uv run python -m harness scaffold

# 3. Verify everything is clean
uv run python -m harness validate
```

---

## How to add a scenario

1. Create a new YAML file under `scenarios/<fsm-id>/`.
2. Follow the shape in existing scenarios (or copy `register-run/happy.yaml`).
3. The `expected_branch` must reference **exact** state and event names from the FSM JSON. Run `harness validate` — any typo is a hard error at load time.
4. Run `uv run python -m harness run scenarios/your-file.yaml` to exercise it.

Minimal scaffold:

```yaml
id: my-scenario
fsm: register-run          # must match the FSM "id" field exactly
description: What this tests

expected_branch:
  terminal_state: waiting
  via_states: [submitting, notifying]
  via_events:
    - backend.RUN_SUBMISSION_FAILED
    - backend.RUN_NOTIFICATION_SENT

inputs:
  - driver: http
    method: POST
    url: "{backend}/api/run/"
    json:
      vehicle_id: "BAD_VEHICLE"
    expect_status: [201, 202]
    capture:
      run_id: "$.id"

cleanup:
  - driver: db
    sql: "DELETE FROM run WHERE id = '{captured.run_id}'"

timeout_ms: 10000
tags: [register-run, failure-path]
```

**Drivers available:**

| Driver | Purpose |
|---|---|
| `http` | REST calls to backend |
| `mqtt` | Publish telemetry to NanoMQ |
| `amqp` | Publish events directly to RabbitMQ |
| `scheduler` | Fire a named scheduler trigger event |
| `db` | Run raw SQL (for cleanup only) |

---

## How to update mappings.yaml when databus changes

`mappings.yaml` maps abstract FSM event/action names to concrete transport details (exchange, routing key, endpoint URL). When databus renames a queue or adds an exchange:

1. Start databus and inspect the RabbitMQ management UI at `localhost:15672`.
2. Find the correct exchange and routing key.
3. Update the relevant entry in `mappings.yaml`.
4. Run `uv run python -m harness list-fsms` — it will warn about any unmapped events/actions.

---

## Known issues

- **`end-run.json` spec bug**: `end-run.json` is identical to `build-gtfs-realtime.json`. The harness skips `end-run` scenarios and prints a loud warning. Fix the spec file and re-run `harness scaffold` to generate stubs.
- **Databus not yet implemented**: Most FSMs produce FAIL on day 1. This is expected — the harness is a spec contract, not a regression suite. Failures tell the databus team what's missing.

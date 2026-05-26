# Databus FSM Test Harness — Implementation Handoff

**Audience:** Sonnet 4.6 (or any implementer) picking this up cold.
**Goal:** Build a Python test harness that drives all 6 databus FSMs end-to-end, using the FSM JSON files as the single source of truth so scenarios survive FSM edits.

---

## 1. Context you need

### The FSMs
Live at `../context/behavior/databus/system/json/`:
- `register-run.json` — run submission → validation → initialization → confirmation
- `ingest-telemetry.json` — MQTT frame → validation → processing → state update request
- `update-system-state.json` — FSM-driven vehicle state evolution
- `build-gtfs-realtime.json` — snapshot → build → publish (GTFS-RT feed)
- `save-gtfs-feed-messages.json` — periodic parquet archival
- `end-run.json` — **SPEC BUG**: file currently contains the same content as `build-gtfs-realtime.json`. Skip scenarios for this FSM until the spec is fixed; surface the bug via a loud warning on startup.

### The databus repo
Lives at `../databus/` and runs via `docker compose -f compose.dev.yml up` from that directory. Host ports when running:
- Backend REST: `http://localhost:8000`
- MQTT broker (NanoMQ): `localhost:1883`
- RabbitMQ AMQP: `localhost:5672` (mgmt UI `:15672`, default guest/guest)
- Postgres: `localhost:5432` (db `databus`, user `postgres`, trust auth)
- Redis: `localhost:6379`

### Architecture facts (from `databus/ARCHITECTURE.md`)
- **MQTT** = external telemetry only (vehicles → telemetry-broker → realtime-engine).
- **AMQP (RabbitMQ)** = internal messaging. FSM events like `backend.RUN_SUBMISSION_SUCCEEDED` and `realtime_engine.RUN_VALIDATION_SUCCEEDED` flow through RabbitMQ.
- Three message semantics: **commands** (from orchestrator), **observations** (from realtime-engine), **assertions** (from publisher/tasks).

### Current implementation status (as of 2026-04-23)
Most FSMs are unimplemented. This is by design — the harness is a spec contract, not a regression test. Expect failures on day 1, passing transitions as databus lands code. Specifically:
- `realtime_engine/tasks.py` is a stub.
- `publisher/publisher.py` reads Redis directly, emits no FSM events.
- `scheduler/scheduler.py` celery-beat triggers `publisher.build_vehicle_positions` every 15s — no `scheduler.BUILD_TRIGGERED` AMQP event.
- No MQTT subscriber in the databus backend.
- No run-lifecycle endpoints beyond generic DRF CRUD on `/api/run/`.
- No AsyncAPI specs on disk.

### The existing simulator
`sim/simulator.py` publishes telemetry to MQTT topics `transit/vehicle/<vehicle_id>/{position,progression,occupancy,data}`. Keep this running in parallel — the harness *reuses* it for `ingest-telemetry` scenarios rather than reinventing telemetry emission.

---

## 2. Core design principle: FSM JSON is the source of truth

**Nothing in the harness hardcodes state names, event names, or transition paths.** All of these come from parsing the FSM JSON at startup.

Concretely this means:

1. **`fsm_graph.py`** loads each JSON → builds a directed graph where nodes are states and edges are `(trigger_event, target_state, entry_actions)`. Expose methods:
   - `paths(from_state, to_state)` → all transition paths
   - `transitions_to(state)` → edges that end at a state (useful for "all failure paths")
   - `events_out(state)` → events that transition *out* of a state
   - `validate_reference(state_name, event_name)` → raises if name doesn't exist

2. **Scenarios reference FSM entities by name, not by value.** At scenario load time, every referenced state/event is checked against the parsed FSM; missing references = hard fail with exact scenario + field + FSM location.

3. **Scenarios describe the branch they want, not the transition sequence.** The harness walks the graph to compute the expected sequence.

4. **Skeleton scenarios auto-generated** for every `*_FAILED` edge in every FSM. A CLI subcommand `harness scaffold` creates stubs in `scenarios/_scaffold/` that you fill in with the triggering input. On each run the harness reports untested failure edges.

5. **Mapping layer** in a separate `mappings.yaml` translates abstract FSM actions (e.g. `backend.validate_payload`, `tasks.persist_parquet_file`) to concrete observables (AMQP exchange + routing key, HTTP endpoint, DB table, file path). When databus renames a topic, one file changes.

### What changes when an FSM changes
| Change | What breaks | How you fix it |
|---|---|---|
| State renamed | Scenarios referencing old name fail at load with clear error | Bulk-rename in YAMLs (grep) |
| Event renamed | Same | Same |
| New failure branch added | Harness reports "untested edge X → Y on event Z" | Run `harness scaffold`, fill in input |
| Transition removed | Scenarios exercising it fail at load | Delete obsolete scenarios |
| Action name changed | `mappings.yaml` mapping missing → clear error | Update one line in `mappings.yaml` |
| New FSM added | No scenarios yet | Add JSON; `harness scaffold`; write happy-path |

---

## 3. File layout to create

```
sim/
  harness/
    __init__.py
    __main__.py              # CLI: run, scaffold, validate, list-fsms, report
    fsm_graph.py             # FSM JSON → graph; path/edge/reference utilities
    scenario_schema.py       # pydantic models for scenario YAML
    scenario_loader.py       # load YAMLs, validate against FSMs, expand templates
    runner.py                # scenario executor: drive inputs, collect events, assert
    assertions.py            # "expect event within Nms", "expect branch matches graph"
    reporter.py              # console table + junit XML output
    config.py                # env-driven endpoints (backend, mqtt, amqp, pg, redis)
    mappings.py              # loads mappings.yaml, resolves action → observable
    drivers/
      __init__.py
      http.py                # httpx-based backend REST driver
      mqtt.py                # paho-mqtt publish (telemetry input)
      amqp.py                # aio-pika or pika publish (direct event injection when needed)
      scheduler.py           # fire scheduler.BUILD_TRIGGERED / tasks.SAVE_TRIGGERED
    observers/
      __init__.py
      amqp_sniffer.py        # subscribe to all broker exchanges, log every message
      mqtt_sniffer.py        # subscribe to telemetry topics (for round-trip checks)
      feed_poller.py         # GET /gtfs/realtime/{tu,vp}, decode protobuf
      db_poller.py           # psycopg for position/progression/occupancy/run tables
      lake_watcher.py        # watchdog on parquet output dir
  scenarios/
    register-run/
      happy.yaml
      bad_vehicle.yaml
      bad_trip.yaml
      bad_schedule.yaml
      operator_reject.yaml
      init_failure.yaml
    ingest-telemetry/
      happy.yaml
      malformed_json.yaml
      no_active_run.yaml
      bad_coordinates.yaml
      stale_timestamp.yaml
      processing_failure.yaml
    update-system-state/
      approach_stop.yaml
      at_stop.yaml
      depart_stop.yaml
      off_shape.yaml
      fetch_failure.yaml
      update_failure.yaml
    build-gtfs-realtime/
      tick_happy.yaml
      snapshot_failure.yaml
      build_failure.yaml
      publish_failure.yaml
    save-gtfs-feed-messages/
      happy.yaml
      serialization_failure.yaml
      persist_failure.yaml
    _scaffold/                # auto-generated stubs live here until filled in
  mappings.yaml               # single place to map FSM actions/events → concrete observables
  pyproject.toml              # uv-managed; pinned deps
  README_TESTING.md           # how to run, how to interpret output
  HANDOFF.md                  # this file
```

---

## 4. Scenario YAML shape (concrete proposal)

```yaml
id: register-run-bad-vehicle
fsm: register-run
description: Reject run submission when vehicle_id does not exist.

# The harness validates these against the FSM at load time.
# If any event/state no longer exists, this scenario fails to load with a clear error.
expected_branch:
  terminal_state: waiting
  via_states: [submitting, validating, notifying]   # harness verifies this path exists in FSM JSON
  via_events:
    - backend.RUN_SUBMISSION_SUCCEEDED
    - realtime_engine.RUN_VALIDATION_FAILED
    - backend.RUN_NOTIFICATION_SENT

# How to drive the FSM into the desired branch.
inputs:
  - driver: http
    method: POST
    url: "{backend}/api/run/"
    json:
      vehicle_id: "DOES_NOT_EXIST"
      trip_id: "{fixtures.trip_id}"
      scheduled_start: "{now_iso}"
    expect_status: [201, 202]
    capture:
      run_id: "$.id"   # jsonpath, stored in scenario context as {captured.run_id}

# Extra assertions beyond path-walking.
expect:
  context:
    rejection_reason: "vehicle_not_found"
  # Concrete observables — resolved through mappings.yaml so renames don't break scenarios.
  observations:
    - action: backend.log_errors
      within_ms: 3000

# Cleanup runs even on failure.
cleanup:
  - driver: db
    sql: "DELETE FROM run WHERE id = {captured.run_id}"

# How long to wait overall before declaring timeout.
timeout_ms: 10000

# Tags for selective runs: pytest -m happy, pytest -m register-run
tags: [register-run, failure-path, validation]
```

### Scenario template substitutions
- `{backend}`, `{mqtt}`, `{amqp}` — from `config.py`
- `{now_iso}`, `{now_epoch}` — wall clock at scenario start
- `{fixtures.KEY}` — from `fixtures.yaml` (shared seed IDs)
- `{captured.KEY}` — set by earlier `capture:` steps in the same scenario

---

## 5. mappings.yaml shape

Abstract FSM names → concrete observables. One file to update per databus release.

```yaml
# AMQP message locations — what routing_key / exchange an event actually lands on.
events:
  backend.RUN_SUBMISSION_SUCCEEDED:
    transport: amqp
    exchange: databus.commands
    routing_key: run.submission.succeeded
  backend.RUN_SUBMISSION_FAILED:
    transport: amqp
    exchange: databus.commands
    routing_key: run.submission.failed
  realtime_engine.RUN_VALIDATION_SUCCEEDED:
    transport: amqp
    exchange: databus.observations
    routing_key: run.validation.succeeded
  telemetry_broker.TELEMETRY_RECEIVED:
    transport: mqtt
    topic_pattern: "transit/vehicle/+/data"
  scheduler.BUILD_TRIGGERED:
    transport: amqp
    exchange: databus.commands
    routing_key: build.triggered
  # ... all events from all 6 FSMs

# Action locations — the concrete side-effects each FSM entry action produces.
actions:
  backend.validate_payload:
    kind: log_or_noop          # no observable side-effect, skip in assertions
  backend.send_run_validation_request:
    transport: amqp
    exchange: databus.commands
    routing_key: run.validate
  realtime_engine.write_run_metadata:
    transport: redis
    key_pattern: "run:{run_id}"
  tasks.persist_parquet_file:
    transport: file
    path_pattern: "{lake_dir}/gtfs_rt/{feed_type}/{snapshot_timestamp}.parquet"
  tasks.publish_feed_files:
    transport: http
    url_pattern: "{backend}/gtfs/realtime/{feed_type}.pb"
  # ... every action referenced in any FSM
```

**On startup** the harness cross-checks:
- Every event mentioned in any FSM JSON has a mapping (warn if missing).
- Every action in any FSM JSON has a mapping (warn if missing).

This means when a new FSM is added, you see exactly what mappings you're missing.

---

## 6. Dependencies (pyproject.toml)

```toml
[project]
name = "simovi-harness"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "httpx>=0.27",
  "paho-mqtt>=2.1",
  "aio-pika>=9.4",             # AMQP async client
  "pydantic>=2.8",
  "pyyaml>=6.0",
  "jsonpath-ng>=1.6",
  "watchdog>=5.0",
  "psycopg[binary]>=3.2",
  "redis>=5.0",
  "gtfs-realtime-bindings>=1.0",
  "pytest>=8.3",               # harness runs scenarios, but also exposes as pytest collectors
  "pytest-asyncio>=0.24",
  "rich>=13.7",                # pretty console reports
]

[tool.uv]
dev-dependencies = ["ruff", "mypy", "pytest-cov"]
```

Use **uv** (already the project's package manager — confirmed from `rules/feedback_python_package_manager.md` in memory).

---

## 7. CLI surface

```
python -m harness run                         # run all scenarios
python -m harness run -t register-run         # run by tag
python -m harness run scenarios/.../happy.yaml
python -m harness validate                    # load + validate only, no execution
python -m harness scaffold                    # generate stubs for untested FSM edges
python -m harness list-fsms                   # summary of each FSM's nodes/edges/coverage
python -m harness coverage                    # which FSM transitions are covered by scenarios
python -m harness report --junit out.xml      # CI-friendly output
```

---

## 8. Iterability guarantees (enforce with tests of the harness itself)

Write unit tests for the harness that verify:

1. **Renaming a state in an FSM JSON** causes scenarios referencing it to fail loading with a message naming the scenario file, field path, and the dead reference.
2. **Adding a new `*_FAILED` edge to an FSM** causes `harness coverage` to list it as untested.
3. **A scenario whose `expected_branch` does not exist in the FSM graph** fails loading even if all state/event names exist individually (path validity ≠ reference validity).
4. **A scenario with a path that goes through a renamed state** still loads if the scenario uses the new name — the "old" state is simply no longer reachable.

These four tests *are* the iterability guarantee. Add them early; they protect the property you care about.

---

## 9. Build order

1. `fsm_graph.py` + unit tests against all 6 JSONs. Must handle single-action `entry` (dict) vs list-of-actions `entry`, as the JSONs mix both forms.
2. `scenario_schema.py` + `scenario_loader.py` with FSM-reference validation.
3. `mappings.py` + scaffolding for `mappings.yaml` (partial, with clear TODOs for missing topics).
4. `config.py` + `drivers/http.py` + `drivers/mqtt.py`.
5. `observers/amqp_sniffer.py` (critical — this is how most assertions work).
6. `runner.py` + `assertions.py` + `reporter.py`.
7. First scenario end-to-end: `register-run/happy.yaml`. Expect it to fail because databus lacks the implementation — that's fine, the harness should report *what's missing* clearly.
8. Remaining happy paths (5 scenarios).
9. `scaffold` subcommand — auto-generate stubs for all `*_FAILED` edges.
10. Fill failure-path scenarios (~15 scenarios).
11. `coverage` subcommand.
12. `__main__.py` CLI wiring + `README_TESTING.md`.

---

## 10. Open questions to flag to the user if they block progress

- **Parquet output location.** `save-gtfs-feed-messages` persists parquet blobs; we don't know where yet. Check `backend/databus/settings.py` for a `LAKE_DIR`, `PARQUET_DIR`, or similar. If missing, ask the user or mount `/lake` based on the `lake_data:` volume declared in `compose.dev.yml`.
- **Backend run endpoints.** Today only generic DRF `RunViewSet` (CRUD). FSM implies richer endpoints (submit/confirm/reject/end). If still missing when you write scenarios, encode the *expected* endpoint in the scenario and let it fail loudly until databus lands it.
- **AMQP exchange topology.** Names in the `mappings.yaml` above are best guesses (`databus.commands`, `databus.observations`, `databus.assertions`). Confirm by reading RabbitMQ management UI once databus is running, or reading Celery/aio-pika config once it exists.
- **`end-run.json` spec bug.** The spec file duplicates `build-gtfs-realtime.json`. The harness should print a loud warning on startup and skip `end-run` scenarios until fixed.

---

## 11. Coding standards

Per `~/.claude/rules/python/*`:
- Python 3.11+, black + ruff + isort, type hints on all signatures.
- `@dataclass(frozen=True)` for DTOs.
- `Protocol` types for driver/observer interfaces.
- Use `logging`, not `print()`.
- pytest with `@pytest.mark.unit` / `@pytest.mark.integration`.
- Secrets via env (`os.environ["X"]`), never hardcoded.
- Small files (<400 lines), one responsibility each.

---

## 12. Definition of done

- [ ] `python -m harness validate` passes with zero scenario load errors.
- [ ] `python -m harness list-fsms` shows all 6 FSMs parsed (end-run spec bug surfaced loudly).
- [ ] `python -m harness coverage` reports which of the ~20 failure edges have scenarios.
- [ ] At least the 6 happy-path scenarios exist and attempt to run against a live databus (even if most fail because databus isn't implemented yet).
- [ ] The 4 iterability guarantee tests (§8) pass.
- [ ] `README_TESTING.md` explains: how to start databus, how to run the harness, how to read output, how to add a scenario, how to update `mappings.yaml` when databus changes.

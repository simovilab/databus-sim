# simulator

Self-contained development environment for the SIMOVI vehicle telemetry pipeline. Runs a synthetic fleet of 6 buses on UCR routes (`bUCR_L1`, `bUCR_L2`), publishes GTFS-Realtime MQTT telemetry, drives the databus run lifecycle, and exposes a live map + operator UI in the browser.

> The FSM test harness under `sim/harness/` is out of scope for this README. See `sim/README_TESTING.md`.

---

## Architecture

Three docker services in `compose.yml`:

| Service | Image | Role |
|---|---|---|
| `simulator` | local `sim/Dockerfile` | Publishes MQTT telemetry, exposes HTTP control on `SIM_HTTP_PORT`, talks to databus |
| `ws-bridge` | `eclipse-mosquitto:2.0` | MQTT-over-WebSockets bridge on `MQTT_WS_PORT` ↔ databus telemetry-broker on `DATABUS_MQTT_HOST:DATABUS_MQTT_PORT` |
| `web` | local `web/Dockerfile` (nginx) | Static UI on `WEB_PORT`, reverse-proxies `/sim/` → simulator and `/databus/` → orchestrator |
| `broker` (standalone only) | `emqx/nanomq:0.24.9-full` | Local MQTT broker, only when running without databus |

Wired mode (default) talks to the **databus** stack on `DATABUS_HOST` / `DATABUS_MQTT_HOST` (default `host.docker.internal`):
- `DATABUS_HTTP_PORT` (default `8000`) — orchestrator (HTTP: create/update run **and read run-lifecycle state**)
- `DATABUS_MQTT_PORT` (default `1883`) — telemetry-broker (MQTT)

The simulator reads run-lifecycle state over HTTP (`GET /api/run/{run_id}/`); it has **no Redis dependency** and never touches databus's Redis, keeping the two stacks independent.

**All ports and hosts are configured in a single `.env` file** so the stack can adapt to a databus deployment that uses different ports — see [Configuration](#configuration-ports--hosts) below.

---

## Configuration (ports & hosts)

Every port the stack publishes or connects to lives in a single `.env` file at the
repo root. Copy the template and edit only what differs from your databus setup:

```bash
cp .env.example .env
# edit .env, then:
docker compose up --build
```

Every value has a default baked into `docker-compose.yml`, so the stack still runs
with no `.env` for the standard layout. `.env` is git-ignored; commit changes to
`.env.example` instead.

| Variable | Default | What it controls |
|---|---|---|
| `WEB_PORT` | `8080` | Host port for the browser UI |
| `SIM_HTTP_PORT` | `8081` | Simulator FastAPI control API (host + in-container) |
| `MQTT_WS_PORT` | `8083` | MQTT-over-WebSockets port the **browser** connects to |
| `MQTT_TCP_PORT` | `1884` | Bridge plain-MQTT listener (simulator → bridge, in-network) |
| `DATABUS_HOST` | `host.docker.internal` | Host where the databus orchestrator runs |
| `DATABUS_HTTP_PORT` | `8000` | databus orchestrator REST port |
| `DATABUS_MQTT_HOST` | `host.docker.internal` | Host of the databus telemetry broker |
| `DATABUS_MQTT_PORT` | `1883` | databus telemetry-broker (MQTT) port |

These three files have no native env support and are rendered from `.env` at
container startup, so you never edit them by hand:

- **mosquitto bridge config** → rendered inline by the `ws-bridge` service `command` in `docker-compose.yml`.
- **`web/nginx.conf.template`** → rendered to `default.conf` by nginx's `envsubst` entrypoint.
- **`web/config.js.template`** → rendered to `config.js`, which tells the browser the `MQTT_WS_PORT`.

> The `sim/harness/` test harness reads its own env vars (`DATABUS_BACKEND_URL`,
> `MQTT_HOST`, `MQTT_PORT`, …) and is independent of this `.env`. See `sim/README_TESTING.md`.

---

## Prerequisites

1. Docker + Docker Compose
2. The databus stack running:
   ```bash
   cd ../databus && ./scripts/dev.sh
   ```
3. GTFS data loaded into databus (only needs to be done once):
   ```bash
   docker compose -f ../databus/compose.dev.yml exec orchestrator \
       uv run python manage.py loaddata gtfs.json
   ```

---

## Seed databus with the simulator's fleet

**This step is mandatory.** The simulator's roster is hardcoded (`sim/fleet.py`) as `unit-01`…`unit-06`, and the web UI's "Request Run" modal defaults to operator `op-001`. None of these exist in the stock databus fixtures (which ship with `SJB1234` / `SJB5678` and operators like `1-1234-5678`). Without this seed, every `create-run` call returns HTTP 400 `Vehicle not found` or `Operator not found`.

Run this one-liner once after the databus stack comes up:

```bash
docker compose -f ../databus/compose.dev.yml exec orchestrator \
  uv run python manage.py shell -c "
from django.contrib.auth.models import User
from operations.models import Operator, Vehicle, Company
company, _ = Company.objects.get_or_create(id='SIM', defaults={'name': 'Simulator'})
for vid in ['unit-01','unit-02','unit-03','unit-04','unit-05','unit-06']:
    Vehicle.objects.get_or_create(id=vid, defaults={'company': company, 'label': vid, 'license_plate': vid})
user, _ = User.objects.get_or_create(username='op-001', defaults={'first_name':'Sim','last_name':'Operator'})
Operator.objects.get_or_create(id='op-001', defaults={'user': user})
print('seed ok:', Vehicle.objects.filter(id__startswith='unit-').count(), 'vehicles,', Operator.objects.filter(id='op-001').count(), 'operator')
"
```

You only need to re-run this after a database wipe.

---

## Start the simulator

From this directory:

```bash
docker compose up -d simulator ws-bridge web
```

Then open **http://localhost:8080** for the live map and operator UI.

To follow logs:
```bash
docker compose logs -f simulator
```

---

## Using the UI

Four tabs at `http://localhost:8080`:

| Tab | What it does |
|---|---|
| **Fleet** | Live map of all 6 vehicles, their MQTT telemetry, bound run, and lifecycle state. |
| **Runs** | Active/recent runs with cancel / interrupt / short-turn buttons. Polls `GET /sim/run/{id}` every 2 s. |
| **Schedule** | Edit `sim/schedule.yaml` — schedule runs to fire automatically at `start_time`. |
| **Operator** | "Request Run" button → opens a modal that POSTs `/api/create-run/` to databus on demand. |

### Typical operator-driven flow

1. Open **Operator** → **Request Run** → pick a vehicle, trip (loaded from databus), and operator ID.
2. The run is created in databus (state: `Initialized`).
3. Click the run in the **Runs** tab → POST `run_confirmed_by_operator` via `/api/update-run/`.
4. Databus transitions `Initialized → Confirmed`. The simulator's `RunBinder` notices by polling `GET /api/run/{run_id}/` and:
   - binds the vehicle to the run,
   - resets `progress_m = 0`,
   - sets `transmitting = True`.
5. As MQTT pings arrive at the databus realtime-engine, the run advances `Confirmed → Tracking → InProgress`.
6. To end: click **Cancel** / **Interrupt** / **Short-turn** in the Runs tab. The terminal state is picked up on the next poll and the simulator unbinds the vehicle.

### Scheduled flow

Edit `sim/schedule.yaml` (bind-mounted into the container — host edits show up live):

```yaml
defaults:
  pre_run_idle_s: 30
  post_run_idle_s: 30
  auto_confirm_delay_s: 5
runs:
  - id: "sched-001"
    vehicle_id: "unit-01"
    operator_id: "op-001"
    route_id: "bUCR_L1"
    trip_id: "trip-bUCR_L1-0001"      # must exist in databus GTFS
    direction_id: 0
    shape_id: "hacia_artes"            # must exist in sim/shapes.json
    schedule_relationship: "SCHEDULED"
    start_time: "2026-05-19T16:00:00-06:00"
    auto_confirm: true
    auto_start_motion_after_s: 10
```

Then either restart the simulator or hit `POST /sim/schedule/reload` (also exposed as a button in the Schedule tab).

---

## Simulator CLI flags

Set in `compose.yml` under `services.simulator.command`, or pass when running locally with `uv`:

```bash
cd sim && uv sync && uv run python -m sim.simulator [flags]
```

| Flag | Default | Description |
|---|---|---|
| `--interval N` | `2.0` | Tick interval in seconds |
| `--per-route N` | `3` | Advisory only — fleet is fixed at 6 (3 per route) |
| `--stop-vehicle ID` | — | Silence a specific vehicle (repeatable) |
| `--only-vehicle ID` | — | Publish only this vehicle (repeatable) |
| `--random-drop-rate N` | `0` | Drop N % of frames (simulates packet loss) |
| `--stop-all-after N` | `0` | Freeze all vehicles after N ticks (simulates outage) |

### Environment variables

These are read by the simulator **process**. Inside the compose stack they are
wired from `.env` (see [Configuration](#configuration-ports--hosts)); the defaults
below apply when running the process bare (e.g. `python -m sim.simulator`).

| Variable | Default | Purpose |
|---|---|---|
| `MQTT_HOST` | `localhost` | MQTT broker host (compose: `ws-bridge`) |
| `MQTT_PORT` | `1883` | MQTT broker port (compose: `MQTT_TCP_PORT`, `1884`) |
| `MQTT_TOPIC_ROOT` | `transit/vehicle` | Topic prefix for telemetry |
| `DATABUS_BASE_URL` | `http://localhost:8000` | Orchestrator HTTP base — create/update run + read run state (compose: built from `DATABUS_HOST`/`DATABUS_HTTP_PORT`) |
| `SIM_HTTP_PORT` | `8081` | FastAPI control port |
| `SIM_CORS_ORIGINS` | `http://localhost:8080` | Comma-separated browser origins allowed by CORS (compose: `http://localhost:${WEB_PORT}`) |
| `SCHEDULE_PATH` | `/app/schedule.yaml` | Path to schedule file (bind-mounted) |
| `POST_RUN_IDLE_S` | `30` | Seconds a vehicle keeps transmitting after reaching the terminal stop |
| `RUN_POLL_INTERVAL_S` | `2.0` | How often `RunBinder` polls databus for run state |

---

## HTTP control API (`:8081`, proxied at `/sim/`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/healthz` | Liveness probe |
| `GET` | `/fleet` | Snapshot of all 6 vehicles + bindings (see `sim/CONTRACTS.md` §1.2) |
| `GET` | `/schedule` | Current `schedule.yaml` + per-entry status |
| `PUT` | `/schedule` | Overwrite `schedule.yaml` (atomic write + reload) |
| `POST` | `/schedule/reload` | Re-read `schedule.yaml` from disk |
| `GET` | `/run/{run_id}` | Pull-through read of databus run state (`GET /api/run/{run_id}/`) |

---

## MQTT topics

Published every tick (QoS 0) on `transit/vehicle/<vehicle_id>/…`:

| Leaf | Payload |
|---|---|
| `position` | `timestamp, latitude, longitude, bearing, speed, odometer` |
| `progression` | `timestamp, current_stop_sequence, stop_id, current_status, congestion_level, route_id, shape_id` |
| `occupancy` | `timestamp, occupancy_status, occupancy_percentage` |

Internal sim state topics on `sim/state/{fleet,schedule}` (read by the web UI).
Operator control topics on `sim/control/{transmit,move,speed,...}` (written by the web UI).

---

## Standalone mode (no databus)

For map visualization only — no run lifecycle, no databus, no Redis:

```bash
docker compose --profile standalone up broker simulator web
```

Brings up a local NanoMQ broker on `:1883` / `:8083`. The simulator will publish telemetry but `RunBinder` and `Scheduler` will log connection errors against databus, which can be ignored.

---

## Troubleshooting

### `create-run` returns 400 "Vehicle not found" / "Operator not found"
You skipped the **Seed databus** step above. Run the one-liner.

### A run is stuck in `InProgress` and won't cancel after a databus restart
Cause: databus restart lost the run's state. The simulator's `RunBinder` keeps the vehicle bound. The cancel button calls databus, but databus no longer has the run.

Fix (already in code as of the `RunBinder._force_unbind` change): wait ~2 s after a databus restart and the simulator will log `run_binder.lost … force-unbinding` and clear the binding automatically — a `404` from `GET /api/run/{run_id}/` is treated exactly as a lost run.

To clean up the browser side too:
```js
// DevTools console at http://localhost:8080
localStorage.removeItem('simovi_recentRuns')
```
Then reload.

### The vehicles don't appear on the map
- Check the simulator logs (`docker compose logs simulator`) for MQTT connection errors.
- Confirm the ws-bridge is running: `docker compose ps ws-bridge`.
- In standalone mode, make sure you started `broker` too.

### Schedule entries never fire
- `start_time` must include a timezone (e.g. `…-06:00`).
- The simulator only ticks the scheduler every `SCHEDULER_TICK_S` (default 1 s) — give it a moment after editing.

---

## Local debugging

Concrete commands for inspecting MQTT traffic, run state, HTTP endpoints, and logs while the stack is up.

### Watch MQTT traffic

Use a throwaway `eclipse-mosquitto` container — no install needed.

```bash
# All vehicle telemetry, decorated with topic name
docker run --rm -it --network=host eclipse-mosquitto:2.0 \
  mosquitto_sub -h localhost -p 1883 -t 'transit/vehicle/+/+' -v

# Single vehicle, single leaf
docker run --rm -it --network=host eclipse-mosquitto:2.0 \
  mosquitto_sub -h localhost -p 1883 -t 'transit/vehicle/unit-01/position' -v

# Everything the simulator publishes (telemetry + state + control echo)
docker run --rm -it --network=host eclipse-mosquitto:2.0 \
  mosquitto_sub -h localhost -p 1883 -t '#' -v

# Just internal sim state (fleet + schedule snapshots)
docker run --rm -it --network=host eclipse-mosquitto:2.0 \
  mosquitto_sub -h localhost -p 1883 -t 'sim/state/+' -v
```

If telemetry never appears, the simulator either isn't running, is pointed at the wrong broker, or no vehicle has `transmitting=True` yet (vehicles only transmit once their bound run hits `Confirmed`).

### Publish MQTT control commands

Same surface the web UI uses. Useful for testing without touching the browser.

```bash
# Turn unit-01 transmission on/off
docker run --rm --network=host eclipse-mosquitto:2.0 \
  mosquitto_pub -h localhost -p 1883 \
  -t 'sim/control/unit-01/transmit' -m '{"on": true}'

# Start motion (only meaningful after a run is bound)
docker run --rm --network=host eclipse-mosquitto:2.0 \
  mosquitto_pub -h localhost -p 1883 \
  -t 'sim/control/unit-01/moving' -m '{"on": true}'

# Override speed (m/s) — pass null to clear
docker run --rm --network=host eclipse-mosquitto:2.0 \
  mosquitto_pub -h localhost -p 1883 \
  -t 'sim/control/unit-01/speed' -m '{"value": 8.5}'

# Force a fault for N ticks (stale_ts | out_of_bounds | malformed)
docker run --rm --network=host eclipse-mosquitto:2.0 \
  mosquitto_pub -h localhost -p 1883 \
  -t 'sim/control/unit-01/inject_fault' \
  -m '{"kind": "stale_ts", "duration_ticks": 5}'

# Snap a vehicle to ~1 m before its terminal stop
docker run --rm --network=host eclipse-mosquitto:2.0 \
  mosquitto_pub -h localhost -p 1883 \
  -t 'sim/control/unit-01/jump_to_terminal' -m '{}'

# Tell the simulator to re-read schedule.yaml from disk
docker run --rm --network=host eclipse-mosquitto:2.0 \
  mosquitto_pub -h localhost -p 1883 \
  -t 'sim/control/global/reload_schedule' -m '{}'
```

Topic surface (full table in `sim/CONTRACTS.md` §1.1):
`sim/control/<vehicle_id>/{transmit,moving,speed,occupancy,dwell,jump_to_terminal,set_progress,inject_fault}` and
`sim/control/global/{start_run,reload_schedule}`.

### Inspect run state

The simulator's `RunBinder` polls `GET /api/run/{run_id}/` on databus; hitting
that endpoint directly tells you exactly what state the binder will see next
tick. No Redis access required.

```bash
# Read run state straight from databus (what RunBinder polls)
curl -s http://localhost:8000/api/run/<run_id>/ | jq

# Just the lifecycle state field
curl -s http://localhost:8000/api/run/<run_id>/ | jq -r '.run_lifecycle_state'

# Watch state changes in real time (re-prints every 1 s)
watch -n 1 "curl -s http://localhost:8000/api/run/<run_id>/ | jq -r '.run_lifecycle_state'"
```

A `404` from `/api/run/<run_id>/` while the UI shows a bound run is the
"stuck run" scenario — see Troubleshooting above.

### Hit the simulator HTTP control directly

```bash
# Liveness
curl -s http://localhost:8081/healthz

# Fleet snapshot (all 6 vehicles + bindings)
curl -s http://localhost:8081/fleet | jq

# Schedule (with per-entry pending/requested/initialized/failed status)
curl -s http://localhost:8081/schedule | jq

# Pull-through read of databus run state for one run
curl -s http://localhost:8081/run/<run_id> | jq

# Force a schedule reload from disk
curl -s -X POST http://localhost:8081/schedule/reload
```

### Hit the databus orchestrator directly

```bash
# List trips known to databus (also drives the Request Run modal dropdown)
curl -s http://localhost:8000/api/trips/ | jq '.[] | {trip_id, route_id, shape_id, direction_id}'

# Inspect a run's FSM transition history (audit log)
curl -s http://localhost:8000/api/runs/<run_id>/history/ | jq

# Manually transition a run (same call the UI makes for cancel/interrupt/short-turn)
curl -s -X POST http://localhost:8000/api/update-run/ \
  -H 'Content-Type: application/json' \
  -d '{"run_id": "<run_id>", "event": "cancel_run", "details": {"actor_role": "dispatcher"}}'
```

### Follow logs

```bash
# Simulator only
docker compose logs -f simulator

# Databus realtime-engine (consumes MQTT telemetry, drives the FSM)
docker compose -f ../databus/compose.dev.yml logs -f realtime-engine

# Databus orchestrator (handles create-run / update-run)
docker compose -f ../databus/compose.dev.yml logs -f orchestrator

# Everything from databus
docker compose -f ../databus/compose.dev.yml logs -f
```

Filter for the binder's state transitions:
```bash
docker compose logs simulator 2>&1 | grep run_binder
```

### Verify the end-to-end pipeline

After a run is `Confirmed`, this trio should all show activity within ~2 s:

```bash
# 1. Simulator says the vehicle is transmitting:
curl -s http://localhost:8081/fleet | jq '.vehicles[] | select(.vehicle_id=="unit-01")'

# 2. Telemetry is hitting the broker:
docker run --rm -it --network=host eclipse-mosquitto:2.0 \
  mosquitto_sub -h localhost -p 1883 -t 'transit/vehicle/unit-01/+' -v -C 3

# 3. Realtime-engine is advancing run state (what RunBinder polls):
curl -s http://localhost:8000/api/run/<run_id>/ | jq -r '.run_lifecycle_state'
```

If (1) is good but (2) is silent, the simulator can't reach the broker — check `MQTT_HOST` and the ws-bridge.
If (2) is good but (3) doesn't advance past `Confirmed`, the realtime-engine isn't consuming — check its logs.

---

## Repository layout

```
simulator/
├── docker-compose.yml         # ws-bridge + simulator + web
├── .env.example               # all ports & hosts (copy to .env)
├── web/                       # static UI (nginx)
│   ├── nginx.conf.template    # envsubst → reverse-proxies /sim/ and /databus/
│   ├── config.js.template     # envsubst → window.SIM_CONFIG (MQTT_WS_PORT)
│   ├── index.html, app.js, style.css
│   ├── lib/  modals/  tabs/
└── sim/
    ├── simulator.py           # tick loop + MQTT publisher
    ├── fleet.py               # FleetState + 6-vehicle roster
    ├── controller.py          # MQTT control subscriber
    ├── state_publisher.py     # MQTT state publisher
    ├── databus_client.py      # HTTP client: create-run, update-run, run state
    ├── run_binder.py          # polls databus run state → drives FleetState
    ├── scheduler.py           # reads schedule.yaml → fires create-run
    ├── http_control.py        # FastAPI on :8081
    ├── shapes.json            # GTFS polylines + stops
    ├── schedule.yaml          # bind-mounted schedule
    └── tests/                 # pytest unit tests
```

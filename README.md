# SIMOVI Simulator

Self-contained Django ASGI development tool that simulates a fleet of 6 buses on UCR routes (`bUCR_L1`, `bUCR_L2`), publishes GTFS-Realtime telemetry, drives the databus run lifecycle, and exposes a live map + operator UI in the browser. Everything runs in **one process**: background tasks (tick loop, run-binder, scheduler) start via the ASGI lifespan protocol alongside the web server.

> The FSM test harness under `sim/harness/` is out of scope for this README.

---

## Architecture

```
browser
  │
  ├─ WebSocket /ws/fleet/ ─────────────────────────────────┐
  │  (fleet / schedule / telemetry push)                    │
  │                                                         │
  ├─ HTTP /sim/* (DRF REST) ────────────────────────────────┤
  │  (fleet, schedule, control, run, healthz)               │
  │                                                         ▼
  └─ HTTP /databus/* (httpx proxy) ──► Django ASGI process (uvicorn, 1 worker)
                                          │
                                          ├── tick_loop()          ─┐
                                          ├── RunBinder.poll_loop() │ asyncio tasks
                                          ├── Scheduler.run_loop()  ┘
                                          │
                                          ├── paho MQTT/TCP ──► databus telemetry-broker
                                          │   transit/vehicle/<id>/{position,progression,occupancy}
                                          │
                                          └── httpx ──► databus REST
                                              GET  /api/runs/<id>/state/
                                              POST /api/create-run/
                                              POST /api/runs/<id>/update/
```

Single-process invariant: the in-memory `FleetState` and `InMemoryChannelLayer` require **exactly one worker**. See [Operational Notes](#operational-notes).

---

## Prerequisites

1. Docker + Docker Compose (for container mode) **or** Python 3.12 + `uv` (for local dev).
2. The databus stack running (optional — see [Standalone Mode](#standalone-mode-no-databus)):
   ```bash
   cd ../databus && ./scripts/dev.sh
   ```
3. GTFS data loaded into databus (only needed once):
   ```bash
   docker compose -f ../databus/compose.dev.yml exec orchestrator \
       uv run python manage.py loaddata gtfs.json
   ```

### Seed databus with the simulator fleet

**This step is mandatory before creating any runs.** The simulator's roster is hardcoded as `unit-01`…`unit-06` with operator `op-001`. These do not exist in the stock databus fixtures. Without the seed, every `create-run` call returns HTTP 400 `Vehicle not found` or `Operator not found`.

Run once after the databus stack comes up:

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

Re-run after a database wipe.

---

## Quickstart

### Local development

```bash
uv sync --dev
cp .env.example .env        # optional — edit ports/hosts to taste
./scripts/dev.sh            # reads WEB_PORT (and DATABUS_* etc.) from .env
```

`scripts/dev.sh` sources `.env` and launches a single uvicorn worker on `WEB_PORT`
(default `8080`). Equivalently, by hand:

```bash
uv run uvicorn --host 0.0.0.0 --port 8080 sim_project.asgi:application
```

Then open **http://localhost:8080** (or your `WEB_PORT`).

> **Do NOT use `manage.py runserver`** — Django's dev server does not emit the ASGI lifespan protocol, so the tick loop, run-binder, and scheduler will never start.

### Docker

```bash
cp .env.example .env        # optional — Docker reads .env automatically
docker compose up --build
```

Then open **http://localhost:8080** (or your `WEB_PORT`).

To follow logs:
```bash
docker compose logs -f simulator
```

---

## Configuration

All runtime knobs are environment variables, read from a **`.env` file at the repo root** in *both* run paths:

- **Docker** — `docker compose` substitutes `${VAR}` from `.env` into `docker-compose.yml`.
- **Local** — `sim_project/settings.py` loads `.env` via `python-dotenv`, and `scripts/dev.sh` binds uvicorn to `$WEB_PORT`.

A real environment variable always overrides `.env`, and every variable has a default that works without a `.env`. Start from `.env.example` (`cp .env.example .env`). To change the port the whole service runs on, set **`WEB_PORT`** — nothing else.

The ports/hosts you'll usually touch:

| Variable | Default | What it controls |
|---|---|---|
| `WEB_PORT` | `8080` | The one port for UI + REST API + WebSocket (Docker publishes `WEB_PORT:WEB_PORT`; local binds uvicorn to it) |
| `DATABUS_HOST` | `host.docker.internal` | Databus orchestrator host (use `127.0.0.1` for a local non-Docker run) |
| `DATABUS_HTTP_PORT` | `8000` | Databus orchestrator REST port |
| `DATABUS_MQTT_HOST` | `host.docker.internal` | Databus telemetry-broker host |
| `DATABUS_MQTT_PORT` | `1883` | Databus telemetry-broker (MQTT) port |

All other knobs:

| Variable | Default | What it controls |
|---|---|---|
| `MQTT_HOST` | ← `DATABUS_MQTT_HOST`, else `localhost` | Broker host the paho publisher connects to (rarely set directly) |
| `MQTT_PORT` | ← `DATABUS_MQTT_PORT`, else `1883` | Broker port (rarely set directly) |
| `MQTT_TOPIC_ROOT` | `transit/vehicle` | MQTT topic prefix for telemetry publishes |
| `DATABUS_BASE_URL` | ← `http://DATABUS_HOST:DATABUS_HTTP_PORT`, else `http://localhost:8000` | Databus orchestrator base URL for REST + the `/databus/` proxy (rarely set directly) |
| `SHAPES_PATH` | `simulator_app/data/shapes.json` | Absolute path to GTFS polylines + stops JSON |
| `SCHEDULE_PATH` | `simulator_app/data/schedule.yaml` | Path to schedule file (bind-mounted in Docker for live edits) |
| `SIM_TICK_INTERVAL` | `2.0` | Tick loop interval in seconds |
| `POST_RUN_IDLE_S` | `30` | Seconds a vehicle keeps transmitting after reaching the terminal stop |
| `RUN_POLL_INTERVAL_S` | `2.0` | How often RunBinder polls databus for run state |
| `SCHEDULER_TICK_S` | `1.0` | How often the scheduler checks for entries due to fire |
| `SIM_ONLY_VEHICLES` | `` (all) | Comma-separated vehicle IDs to simulate; others are skipped |
| `SIM_STOP_VEHICLES` | `` (none) | Comma-separated vehicle IDs to silence at startup |
| `SIM_RANDOM_DROP_RATE` | `0` | Integer 0–100: percentage of ticks to randomly drop (packet-loss simulation) |
| `SIM_STOP_ALL_AFTER` | `0` | Freeze all vehicles after N ticks (0 = never) |
| `DEBUG` | `true` | Django DEBUG mode |
| `LOG_LEVEL` | `INFO` | Root logger level (`DEBUG`, `INFO`, `WARNING`, …) |
| `ALLOWED_HOSTS` | `*` | Django ALLOWED_HOSTS (comma-separated) |
| `DJANGO_SECRET_KEY` | `dev-insecure-…` | Django secret key — change before any real deployment |

Both Docker and local runs derive `MQTT_HOST`/`MQTT_PORT` from `DATABUS_MQTT_HOST`/`DATABUS_MQTT_PORT` and build `DATABUS_BASE_URL` from `DATABUS_HOST` + `DATABUS_HTTP_PORT`, so the same `.env` works everywhere — you set the `DATABUS_*` vars, not the low-level ones.

---

## Using the UI

Four tabs at `http://localhost:8080`:

| Tab | What it does |
|---|---|
| **Fleet** | Live map of all 6 vehicles, their telemetry, bound run, and lifecycle state |
| **Schedule** | View and edit `schedule.yaml` — schedule runs to fire automatically at `start_time` |
| **Operator** | "Request Run" modal — create a run on demand via the databus REST API |
| **Runs** | Active/recent runs with cancel / interrupt / short-turn buttons |

### Typical operator-driven flow

1. Open **Operator** → **Request Run** → pick a vehicle, trip (loaded from databus), and operator ID.
2. The run is created in databus (state: `Initialized`).
3. Click the run in the **Runs** tab → POST `run_confirmed_by_operator` to databus via the `/databus/` proxy.
4. Databus transitions `Initialized → Confirmed`. The simulator's `RunBinder` notices by polling `GET /api/runs/{run_id}/state/` and:
   - binds the vehicle to the run,
   - resets `progress_m = 0`,
   - sets `transmitting = True`.
5. As MQTT pings arrive at the databus realtime-engine, the run advances `Confirmed → Tracking → In Progress`.
6. To end: click **Cancel** / **Interrupt** / **Short-turn** in the Runs tab. The terminal state is picked up on the next poll and the simulator unbinds the vehicle.

### Scheduled flow

Edit `simulator_app/data/schedule.yaml` (bind-mounted as `/app/schedule.yaml` in Docker). Then either restart the simulator or hit `POST /sim/schedule/reload` (also exposed as a button in the Schedule tab).

---

## Schedule Format

```yaml
defaults:
  pre_run_idle_s: 30          # fire create-run this many seconds before start_time
  post_run_idle_s: 30         # vehicle keeps transmitting this many s after terminal stop
  auto_confirm_delay_s: 5     # seconds after create-run to auto-POST run_confirmed (if auto_confirm)

runs:
  - id: "sched-001"                          # unique string; required
    vehicle_id: "unit-01"                    # must be in the fleet roster
    operator_id: "op-001"                    # must exist in databus
    route_id: "bUCR_L1"
    trip_id: "trip-bUCR_L1-0001"            # must exist in databus GTFS
    direction_id: 0                          # 0 or 1
    shape_id: "hacia_artes"                  # must exist in shapes.json
    schedule_relationship: "SCHEDULED"       # SCHEDULED | ADDED | UNSCHEDULED
    start_time: "2026-05-19T16:00:00-06:00"  # ISO 8601 WITH timezone — required
    auto_confirm: true                       # auto-POST run_confirmed_by_operator
    auto_start_motion_after_s: 10            # start moving N seconds after Confirmed (null to skip)
```

**Required fields per entry:** `id`, `vehicle_id`, `operator_id`, `route_id`, `trip_id`, `direction_id`, `shape_id`, `start_time`.

`start_time` **must include a timezone** (e.g. `-06:00`) — entries without a timezone are silently skipped.

---

## API Reference

### WebSocket — `/ws/fleet/`

Connect once; all realtime data flows over this socket. On connect the server immediately sends the current fleet and schedule snapshots (replicating MQTT retained-message semantics). Control is via HTTP POST — the socket is receive-only.

#### Message types

**`fleet`** — full fleet snapshot, sent on connect and after every tick:
```json
{
  "type": "fleet",
  "payload": {
    "vehicles": [
      {
        "vehicle_id": "unit-01",
        "route_id": "bUCR_L1",
        "transmitting": true,
        "moving": true,
        "bound_run_id": "abc123",
        "bound_trip_id": "trip-bUCR_L1-0001",
        "lifecycle_state": "In Progress",
        "progress_m": 412.5
      }
    ],
    "published_at": "2026-05-19T16:00:02+00:00"
  }
}
```

**`schedule`** — schedule snapshot, sent on connect and whenever the schedule changes:
```json
{
  "type": "schedule",
  "payload": {
    "defaults": {"pre_run_idle_s": 30, "post_run_idle_s": 30, "auto_confirm_delay_s": 5},
    "runs": [
      {
        "id": "sched-001",
        "vehicle_id": "unit-01",
        "status": "initialized",
        "bound_run_id": "abc123"
      }
    ],
    "published_at": "2026-05-19T16:00:01+00:00"
  }
}
```

Note: the schedule payload uses the key `runs` (not `entries`). The browser's `ws_client.js` aliases it internally.

**`telemetry`** — single-vehicle telemetry leaf, sent each tick:
```json
{
  "type": "telemetry",
  "vehicle_id": "unit-01",
  "leaf": "position",
  "payload": {
    "timestamp": 1747670402,
    "latitude": 9.935812,
    "longitude": -84.051234,
    "bearing": 287.3,
    "speed": 7.42,
    "odometer": 412.5
  }
}
```

Telemetry `leaf` values: `position`, `progression`, `occupancy`. Their payload shapes are byte-identical to the MQTT topics (`transit/vehicle/<id>/<leaf>`) that databus's realtime-engine consumes.

### HTTP REST — `/sim/*`

All endpoints return JSON. No authentication required (`AllowAny`).

| Method | Path | Description |
|---|---|---|
| `GET` | `/sim/healthz` | Liveness probe; returns `{"ok": true}` |
| `GET` | `/sim/fleet` | Full fleet snapshot |
| `GET` | `/sim/schedule` | Current schedule + per-entry status |
| `PUT` | `/sim/schedule` | Overwrite `schedule.yaml` (atomic write + reload); body: schedule document |
| `POST` | `/sim/schedule/reload` | Re-read `schedule.yaml` from disk |
| `GET` | `/sim/run/<run_id>` | Pull-through read of databus run state (`GET /api/runs/<id>/state/`) |
| `POST` | `/sim/runs/track` | Register a run with RunBinder; body: `{vehicle_id, trip_id, run_id, shape_id}` |
| `POST` | `/sim/control/<vehicle_id>/<knob>` | Per-vehicle control action (see table below) |
| `POST` | `/sim/control/global/<knob>` | Global control action (see table below) |

#### Per-vehicle control knobs (`POST /sim/control/<vehicle_id>/<knob>`)

| Knob | Body | Effect |
|---|---|---|
| `transmit` | `{"on": true\|false}` | Enable/disable telemetry publishing for this vehicle |
| `moving` | `{"on": true\|false}` | Start/stop vehicle motion |
| `speed` | `{"value": 8.5}` or `{"value": null}` | Override speed in m/s; `null` clears the override |
| `occupancy` | `{"value": 42}` or `{"value": null}` | Override occupancy percentage (0–100); `null` clears |
| `dwell` | `{"stop_id": "stop-X", "ticks": 3}` | Force a dwell at stop for N ticks |
| `jump_to_terminal` | `{}` | Snap vehicle to ~1 m before its terminal stop |
| `set_progress` | `{"stop_id": "stop-X"}` | Jump vehicle to the shape point nearest to `stop_id` |
| `inject_fault` | `{"kind": "stale_ts"\|"out_of_bounds"\|"malformed", "duration_ticks": 5}` | Inject a telemetry fault for N ticks |

#### Global control knobs (`POST /sim/control/global/<knob>`)

| Knob | Body | Effect |
|---|---|---|
| `start_run` | `{"vehicle_id": "unit-01"}` | Set the named vehicle to `moving=True` |
| `reload_schedule` | `{}` | Reload `schedule.yaml` from disk |

### Databus proxy — `/databus/<path>`

All methods (GET, POST, PUT, PATCH, DELETE) are forwarded to `DATABUS_BASE_URL/<path>`, preserving query string, body, and status code. This avoids CORS issues because the browser hits the same origin.

**Security note:** Do not expose `WEB_PORT` to an untrusted network. The proxy is unauthenticated — it would otherwise be an open gateway to the databus write API.

---

## Operational Notes

### Single-worker invariant

The simulator uses `InMemoryChannelLayer` and an in-memory `FleetState`. Both require **exactly one process**:

- Run `uvicorn` without `--workers` (the default is 1).
- Do NOT run `docker compose up --scale simulator=N` with N > 1.
- Do NOT front uvicorn with gunicorn multi-worker or any other multi-process setup.

Multiple workers would split-brain the fleet state and deafen the channel layer — the map would freeze and control POSTs would be silently ignored.

If you ever need to scale, switch to a Redis channel layer and external fleet state.

### ASGI server: uvicorn, not daphne

The `CMD` in `Dockerfile` uses `uvicorn`. `daphne 4.x` does not emit the ASGI lifespan protocol, so background tasks never start under daphne. `daphne` is kept as a dev dependency only because `channels.testing.WebsocketCommunicator` imports it at module load.

### Auth: AllowAny

All DRF endpoints use `AllowAny`. This is intentional for a local/dev tool. Keep `WEB_PORT` on a trusted network.

---

## Standalone Mode (no databus)

The UI, map, and WebSocket work without databus. Paho MQTT publishes fail harmlessly with a warning log. RunBinder and Scheduler log connection errors but do not crash.

```bash
uv run uvicorn --host 0.0.0.0 --port 8080 sim_project.asgi:application
# or:
docker compose up --build
```

No local MQTT broker is needed — the old `broker` (NanoMQ) service is gone.

---

## Testing

Run the full suite (94 tests):
```bash
uv run pytest
```

With coverage:
```bash
uv run pytest --cov=simulator_app --cov-report=term-missing
```

Tests live in:
- `simulator_app/tests/` — unit and integration tests for all app modules
- `tests/` — boot-level smoke tests (`/sim/healthz`, ASGI import)

---

## E2E Verification Checklist (manual, requires live databus)

After starting the stack with a seeded databus:

- [ ] Open `http://localhost:8080` — map renders, Fleet tab shows 6 vehicles.
- [ ] Open Operator tab → Request Run → select a vehicle, trip, operator → Submit.
- [ ] Run appears in the Runs tab with state `Initialized`.
- [ ] Click **Confirm** — run transitions to `Confirmed`.
- [ ] Within 2–4 s, the vehicle on the map starts moving.
- [ ] Telemetry appears at the databus broker:
  ```bash
  docker run --rm -it --network=host eclipse-mosquitto:2.0 \
    mosquitto_sub -h localhost -p 1883 -t 'transit/vehicle/unit-01/+' -v -C 3
  ```
- [ ] Click **Cancel** (or **Interrupt** / **Short-turn**) — vehicle stops transmitting and the run disappears from active bindings.
- [ ] Edit `schedule.yaml`, add an entry, click **Reload** in the Schedule tab — new entry appears with status `pending`, fires at `start_time`.

---

## Troubleshooting

### `create-run` returns 400 "Vehicle not found" / "Operator not found"
You skipped the **Seed databus** step. Run the one-liner under [Prerequisites](#prerequisites).

### A run is stuck in `In Progress` and won't cancel after a databus restart
Cause: databus restart lost the run's state. The `RunBinder` will automatically force-unbind after it receives a `404` on `GET /api/runs/{run_id}/state/` — wait ~2–4 s and look for `run_binder.lost … force-unbinding` in the logs.

To also clear the browser side:
```js
// Browser DevTools console at http://localhost:8080
localStorage.removeItem('simovi_recentRuns')
```
Then reload.

### Vehicles don't appear on the map
- Check logs for paho MQTT connection errors: `docker compose logs simulator | grep paho`
- Vehicles only transmit once their bound run reaches `Confirmed` — check the Runs tab.
- In standalone mode (no databus), vehicles never receive a `Confirmed` transition and never start transmitting. Use `POST /sim/control/unit-01/transmit` with `{"on": true}` to force transmission.

### Schedule entries never fire
- `start_time` must include a timezone offset (e.g. `"2026-05-19T16:00:00-06:00"`). Entries without a timezone are silently skipped.
- The scheduler ticks every `SCHEDULER_TICK_S` seconds (default 1 s). Give it a moment after editing.

### WebSocket not connecting
- Confirm uvicorn is running (not `runserver`): `uvicorn` logs appear at startup.
- Check that `WEB_PORT` matches what you're connecting to.
- The WebSocket URL is derived from `location` in the browser — no config needed for same-origin.

### Run state polling fails / `databus client not ready`
Databus is not reachable at `DATABUS_BASE_URL`. The simulator degrades gracefully — HTTP control endpoints still work but run/track operations return 503.

### Inspecting state directly

```bash
# Fleet snapshot
curl -s http://localhost:8080/sim/fleet | jq

# Schedule with status
curl -s http://localhost:8080/sim/schedule | jq

# Run state (proxied from databus)
curl -s http://localhost:8080/sim/run/<run_id> | jq

# Liveness
curl -s http://localhost:8080/sim/healthz

# Databus run state directly (what RunBinder polls)
curl -s http://localhost:8000/api/runs/<run_id>/state/ | jq -r '.run_lifecycle_state'

# Force schedule reload
curl -s -X POST http://localhost:8080/sim/schedule/reload
```

---

## Repository Layout

```
databus-sim/
├── manage.py
├── pyproject.toml               # Django, channels, uvicorn, drf, httpx, paho-mqtt, …
├── Dockerfile                   # single image; CMD: uvicorn sim_project.asgi:application
├── docker-compose.yml           # ONE service (simulator); no ws-bridge, no nginx, no mosquitto
├── sim_project/                 # Django project (settings, urls, asgi)
│   ├── settings.py              # all env vars + defaults; CHANNEL_LAYERS; AllowAny
│   ├── urls.py                  # /sim/* → api.urls; /databus/* → proxy; / → index
│   └── asgi.py                  # ProtocolTypeRouter{http, websocket, lifespan}
├── simulator_app/
│   ├── apps.py                  # AppConfig (SimulatorAppConfig)
│   ├── runtime.py               # Runtime singleton; start/stop_runtime(); tick_loop()
│   ├── domain/
│   │   ├── fleet.py             # FleetState, Vehicle dataclass, 6-vehicle roster
│   │   ├── kinematics.py        # Shape loading, step_vehicle, build_vehicle_payloads (pure)
│   │   └── control.py           # apply_control / apply_global_control (transport-agnostic)
│   ├── services/
│   │   ├── databus_client.py    # async httpx: create-run, update-run, get-run-state
│   │   ├── run_binder.py        # polls databus run state → drives FleetState
│   │   └── scheduler.py        # reads schedule.yaml → fires create-run at start_time
│   ├── realtime/
│   │   ├── consumers.py         # FleetConsumer (AsyncJsonWebsocketConsumer)
│   │   ├── broadcast.py         # group_send helpers (200 ms throttle on fleet)
│   │   └── routing.py           # websocket_urlpatterns: /ws/fleet/
│   ├── api/
│   │   ├── views.py             # DRF function-based views for all /sim/* endpoints
│   │   ├── serializers.py       # ScheduleDocumentSerializer, TrackRunRequestSerializer
│   │   ├── urls.py              # URL patterns (healthz, fleet, schedule, run, control)
│   │   └── proxy.py             # async Django view: /databus/* → httpx → databus REST
│   ├── data/
│   │   ├── shapes.json          # GTFS polylines + stops (source of truth for kinematics)
│   │   └── schedule.yaml        # default empty schedule (bind-mounted in Docker)
│   ├── templates/index.html     # single-page app shell (served by TemplateView)
│   ├── static/                  # app.js, style.css, lib/, tabs/, modals/
│   │   ├── ws_client.js         # WebSocket wrapper (replaces MQTT.js)
│   │   ├── sim_api.js           # fetch wrappers for /sim/* endpoints
│   │   ├── databus_api.js       # fetch wrappers for /databus/* endpoints
│   │   ├── tabs/                # fleet.js, schedule.js, operator.js, runs.js
│   │   └── modals/              # run_request.js, dwell.js, inject_fault.js, …
│   └── tests/                   # pytest-django unit + integration tests
├── tests/                       # boot-level smoke tests
└── sim/                         # LEGACY — superseded by simulator_app/
    ├── harness/                 # STILL ACTIVE — FSM test harness (out of scope here)
    └── *.py                     # old FastAPI/MQTT code kept for reference only
```

The old `sim/*.py` files (`simulator.py`, `controller.py`, `state_publisher.py`, `http_control.py`, etc.) and `web/` (nginx static) are **superseded legacy** kept only for reference. They are not imported or executed by the Django service. `sim/harness/` is the still-active FSM test harness — see `sim/harness/AGENT_RUNBOOK.md`.

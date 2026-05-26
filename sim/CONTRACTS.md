# SIMOVI Simulator — Cross-Agent Contracts (P0)

**Status**: locked at P0. Any change here must be agreed by all three downstream agents (A, B, C).
**Verified against**: databus commit at HEAD on 2026-05-19. Endpoints checked: `POST /api/create-run/`, `POST /api/update-run/`, MQTT subscriptions in `realtime-engine` Celery bootstep, Redis key `run:{run_id}`.

This document is the single source of truth for every interface that crosses an agent boundary in the simulator restructure (see `docs/simulator/PLAN.md`).

---

## 1. MQTT topic protocol

Broker: `host.docker.internal:1883` from the simulator container; `:8083` (WebSocket) from the web UI.
Encoding: JSON (UTF-8). Numeric values use plain JSON numbers (no NaN/Inf).

### 1.1 Control topics — UI → simulator

| Topic | Direction | Retained | QoS | Payload schema | Example |
|---|---|---|---|---|---|
| `sim/control/<vehicle_id>/transmit` | UI → sim | no | 0 | `{"on": bool}` | `{"on": false}` |
| `sim/control/<vehicle_id>/moving` | UI → sim | no | 0 | `{"on": bool}` | `{"on": true}` |
| `sim/control/<vehicle_id>/speed` | UI → sim | no | 0 | `{"value": float \| null}` (m/s; null releases override) | `{"value": 7.5}` |
| `sim/control/<vehicle_id>/occupancy` | UI → sim | no | 0 | `{"value": int \| null}` (0–100; null releases override) | `{"value": 60}` |
| `sim/control/<vehicle_id>/dwell` | UI → sim | no | 0 | `{"stop_id": str \| null, "ticks": int}` | `{"stop_id": "bUCR_LA", "ticks": 5}` |
| `sim/control/<vehicle_id>/jump_to_terminal` | UI → sim | no | 0 | `{}` | `{}` |
| `sim/control/<vehicle_id>/set_progress` | UI → sim | no | 0 | `{"stop_id": str}` | `{"stop_id": "bUCR_LA"}` |
| `sim/control/<vehicle_id>/inject_fault` | UI → sim | no | 0 | `{"kind": "stale_ts"\|"out_of_bounds"\|"malformed", "duration_ticks": int}` | `{"kind":"stale_ts","duration_ticks":3}` |
| `sim/control/global/start_run` | UI → sim | no | 0 | `{"vehicle_id": str}` | `{"vehicle_id":"unit-01"}` |
| `sim/control/global/reload_schedule` | UI → sim | no | 0 | `{}` | `{}` |

Notes:
- `<vehicle_id>` must be one of the fleet roster IDs (§4).
- `start_run` flips `vehicle.moving=true` for the named vehicle; it does **not** create a databus run. Use the HTTP / Operator path for that.
- Override fields (`speed_override`, `occupancy_override`, `dwell_override`) are cleared by publishing `{"value": null}` (or for dwell, `{"stop_id": null, "ticks": 0}`).

### 1.2 State topics — simulator → UI

| Topic | Direction | Retained | QoS | Payload schema |
|---|---|---|---|---|
| `sim/state/fleet` | sim → UI | yes | 0 | Fleet snapshot (see below) |
| `sim/state/schedule` | sim → UI | yes | 0 | Schedule snapshot (see below) |

State publisher throttles to ≤ 1 update / 200 ms / topic (coalesce).

`sim/state/fleet` example:
```json
{
  "vehicles": [
    {
      "vehicle_id": "unit-01",
      "route_id": "bUCR_L1",
      "transmitting": true,
      "moving": false,
      "speed_override": null,
      "occupancy_override": null,
      "bound_run_id": "5f1d2c3a-...",
      "bound_trip_id": "trip-bUCR_L1-0001",
      "bound_shape_id": "hacia_artes",
      "lifecycle_state": "Confirmed",
      "progress_m": 1234.5,
      "last_state_change_at": "2026-05-19T15:00:00-06:00"
    }
  ],
  "published_at": "2026-05-19T15:00:00-06:00"
}
```

Lifecycle state values: `Requested`, `Validated`, `Initialized`, `Confirmed`, `Tracking`, `InProgress`, `NoSignal`, `Completed`, `Cancelled`, `Interrupted`, `ShortTurned`, or `null` (no bound run). Matches `RunLifecycleStates` enum on databus.

`sim/state/schedule` example:
```json
{
  "entries": [
    {
      "id": "sched-001",
      "vehicle_id": "unit-01",
      "route_id": "bUCR_L1",
      "trip_id": "trip-bUCR_L1-0001",
      "shape_id": "hacia_artes",
      "start_time": "2026-05-19T16:00:00-06:00",
      "status": "pending",
      "bound_run_id": null
    }
  ],
  "published_at": "2026-05-19T15:00:00-06:00"
}
```

Status values: `pending` | `requested` | `confirmed` | `tracking` | `in_progress` | `completed` | `failed`.

### 1.3 Telemetry topics — simulator → databus (read-only for UI)

Telemetry payload is also visible to the UI for map rendering. Schemas live in §7.

---

## 2. HTTP control API (simulator on `:8081`)

FastAPI app. CORS allows `http://localhost:8080`. JSON in / JSON out.

| Method | Path | Purpose | Body | Response |
|---|---|---|---|---|
| GET | `/schedule` | Parsed `schedule.yaml` + computed status per entry | — | `ScheduleSnapshot` |
| PUT | `/schedule` | Overwrite `schedule.yaml` (validated) | `ScheduleDocument` | `{"ok": true}` |
| POST | `/schedule/reload` | Re-read `schedule.yaml` from disk | — | `{"ok": true}` |
| GET | `/fleet` | Current fleet state (mirror of MQTT `sim/state/fleet`) | — | Fleet snapshot |
| GET | `/run/{run_id}` | Proxy a Redis read of `run:{run_id}` | — | `{"run_id": str, "run_lifecycle_state": str \| null, "fields": {...}}` |
| GET | `/healthz` | Liveness | — | `{"ok": true}` |

`ScheduleSnapshot`:
```json
{
  "defaults": {"pre_run_idle_s": 30, "post_run_idle_s": 30, "auto_confirm_delay_s": 5},
  "runs": [
    {
      "id": "sched-001",
      "vehicle_id": "unit-01",
      "operator_id": "op-001",
      "route_id": "bUCR_L1",
      "trip_id": "trip-bUCR_L1-0001",
      "direction_id": 0,
      "shape_id": "hacia_artes",
      "schedule_relationship": "SCHEDULED",
      "start_time": "2026-05-19T16:00:00-06:00",
      "auto_confirm": false,
      "auto_start_motion_after_s": null,
      "status": "pending",
      "bound_run_id": null
    }
  ]
}
```

`PUT /schedule` accepts the same shape minus `status` / `bound_run_id` (those are computed).

---

## 3. `schedule.yaml` schema

```yaml
defaults:
  pre_run_idle_s: 30       # seconds before start_time the vehicle starts idling/transmitting
  post_run_idle_s: 30      # seconds the vehicle keeps idling/transmitting after terminal stop
  auto_confirm_delay_s: 5  # seconds after create-run to POST run_confirmed_by_operator (if auto_confirm)

runs:
  - id: "sched-001"                       # unique within file
    vehicle_id: "unit-01"                 # must be in fleet roster (§4)
    operator_id: "op-001"                 # must exist in databus PostgreSQL (see AGENT_RUNBOOK)
    route_id: "bUCR_L1"
    trip_id: "trip-bUCR_L1-0001"          # must exist in databus GTFS fixture
    direction_id: 0
    shape_id: "hacia_artes"               # must exist in sim/shapes.json
    schedule_relationship: "SCHEDULED"    # constant for v1
    start_time: "2026-05-19T16:00:00-06:00"
    auto_confirm: false                   # if true, scheduler auto-POSTs run_confirmed_by_operator
    auto_start_motion_after_s: null       # if set, scheduler flips vehicle.moving=true N s after confirmation
```

Validation rules:
- `id` unique within file.
- `vehicle_id` in fleet roster.
- `start_time` parseable as ISO-8601 with timezone.
- `direction_id` ∈ {0, 1}.
- `schedule_relationship` ∈ {"SCHEDULED", "ADDED", "UNSCHEDULED"} (databus enum; v1 only writes "SCHEDULED").

---

## 4. Fleet roster (fixed)

6 vehicles, 3 per route. Defined as a constant `FLEET` in `sim/fleet.py`.

| vehicle_id | route_id | default shape_id |
|---|---|---|
| unit-01 | bUCR_L1 | hacia_artes |
| unit-02 | bUCR_L1 | hacia_educacion |
| unit-03 | bUCR_L1 | desde_artes_sin_milla |
| unit-04 | bUCR_L2 | desde_artes_con_milla |
| unit-05 | bUCR_L2 | desde_educacion_con_milla |
| unit-06 | bUCR_L2 | desde_artes_con_milla |

Shape IDs are taken from `sim/shapes.json` (`bUCR_L1` shapes: `desde_artes_sin_milla`, `desde_educacion_sin_milla`, `hacia_artes`, `hacia_educacion`; `bUCR_L2` shapes: `desde_artes_con_milla`, `desde_educacion_con_milla`). Agent A may revise per-vehicle defaults when wiring kinematics; the route assignment and ID set must not change.

---

## 5. Databus API contracts

Base URL: `http://localhost:8000` (host) or `http://host.docker.internal:8000` (from simulator container).

### 5.1 Create run
`POST /api/create-run/`
```json
{
  "vehicle_id": "unit-01",
  "operator_id": "op-001",
  "route_id": "bUCR_L1",
  "trip_id": "trip-bUCR_L1-0001",
  "direction_id": 0,
  "shape_id": "hacia_artes",
  "schedule_relationship": "SCHEDULED"
}
```
Success response (HTTP 200):
```json
{"status": "success", "run_id": "5f1d2c3a-...", "run_lifecycle_state": "Initialized"}
```
Synchronously drives `Requested → Validated → Initialized`.

### 5.2 Update run
`POST /api/update-run/`
```json
{
  "run_id": "5f1d2c3a-...",
  "event": "run_confirmed_by_operator",
  "details": {}
}
```
Success response (HTTP 200):
```json
{"status": "success", "run_lifecycle_state": "Confirmed"}
```
Valid `event` values (lower_snake_case, from `backend/runs/domain/events.py`):
`run_confirmed_by_operator`, `cancel_run`, `interrupt_run`, `short_turn_run`, plus the internal events fired by the bootstep on telemetry (`first_ping_received`, `motion_started`, `terminal_stop_reached`, `signal_lost`, `signal_restored`). The simulator only needs the operator-driven set.

---

## 6. Redis key for run state polling

Key: `run:{run_id}` (Redis hash).
Field of interest: `run_lifecycle_state` (string).
Values: same enum as §1.2.

Connection:
- From host: `redis://localhost:6379/0`.
- From simulator container (different compose project than databus): `redis://host.docker.internal:6379/0`. The databus Redis service binds `:6379` to the host, so this works without a shared network.
- From within the databus compose network (not used by simulator): `redis://state:6379/0`.

Polling cadence: `RUN_POLL_INTERVAL_S` default 2 s. Stop polling on terminal state.

---

## 7. MQTT telemetry payload contract (read by databus consumer)

Subscribed topics on `realtime-engine` Celery bootstep (`MQTT_CONSUMER_ENABLED=true`):
- `transit/vehicle/+/position`
- `transit/vehicle/+/progression`
- `transit/vehicle/+/occupancy`

The `data` leaf is **not** subscribed — simulator must not publish it.

### 7.1 Position (QoS 0)
Topic: `transit/vehicle/<vehicle_id>/position`
```json
{
  "timestamp": 1716148800,
  "latitude": 9.937,
  "longitude": -84.051,
  "bearing": 178.0,
  "speed": 7.2,
  "odometer": 1234.5
}
```
`speed` is in m/s. Threshold > 0.5 m/s fires `Tracking → InProgress` on databus.

### 7.2 Progression (QoS 0)
Topic: `transit/vehicle/<vehicle_id>/progression`
```json
{
  "timestamp": 1716148800,
  "current_stop_sequence": 5,
  "stop_id": "bUCR_LA",
  "current_status": "IN_TRANSIT_TO",
  "congestion_level": "RUNNING_SMOOTHLY",
  "route_id": "bUCR_L1",
  "shape_id": "hacia_artes"
}
```
`current_status` ∈ {`IN_TRANSIT_TO`, `STOPPED_AT`, `INCOMING_AT`}. Terminal-stop guard matches `stop_id` against the bound trip's terminal stop while `current_status == "STOPPED_AT"`.

### 7.3 Occupancy (QoS 0)
Topic: `transit/vehicle/<vehicle_id>/occupancy`
```json
{
  "timestamp": 1716148800,
  "occupancy_status": "FEW_SEATS_AVAILABLE",
  "occupancy_percentage": 40
}
```
Opaque to FSM. Persisted for GTFS-RT builders.

### 7.4 Gating
The consumer drops a ping whose `vehicle:{id}:current_run` Redis key is unset (no active run). Simulator must always pair `transmitting=True` with a confirmed bound run, or expect those pings to be dropped silently.

---

## 8. Agent ownership map (for reference)

| Area | Owner | Files |
|---|---|---|
| Telemetry shape, fleet state, FSM-driving controls | Agent A | `sim/fleet.py`, `sim/simulator.py`, `sim/controller.py`, `sim/state_publisher.py`, `sim/redis_client.py` (interface) |
| HTTP control, scheduler, databus integration, run binder | Agent B | `sim/http_control.py`, `sim/scheduler.py`, `sim/databus_client.py`, `sim/run_binder.py`, `sim/redis_client.py` (impl), `sim/schedule.yaml` |
| Web UI (Option B) | Agent C | `web/index.html`, `web/style.css`, `web/app.js`, `web/lib/*.js`, `web/tabs/*.js`, `web/modals/*.js` |

Shared files touched only in P0: `compose.yml`, `pyproject.toml`, `CONTRACTS.md`, `AGENT_RUNBOOK.md`.

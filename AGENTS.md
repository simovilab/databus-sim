# AGENTS — Codemap and Working Guide

This file is the authoritative quick-reference for an AI agent or new engineer modifying the SIMOVI simulator. Read it before touching any code.

---

## Architecture Summary

```
uvicorn (1 worker, no --workers)
│
├── Django ASGI (HTTP)
│   ├── /sim/*       → simulator_app.api.views (DRF function-based views)
│   ├── /databus/*   → simulator_app.api.proxy (async httpx passthrough)
│   └── /            → TemplateView (index.html)
│
├── Django Channels (WebSocket)
│   └── /ws/fleet/   → simulator_app.realtime.consumers.FleetConsumer
│
└── ASGI Lifespan → simulator_app.runtime.start_runtime()
    ├── asyncio.Task: tick_loop()              (runtime.py)
    ├── asyncio.Task: RunBinder.poll_loop()    (services/run_binder.py)
    └── asyncio.Task: Scheduler.run_loop()     (services/scheduler.py)
         │
         ├── paho MQTT/TCP → databus telemetry-broker
         │   transit/vehicle/<id>/{position,occupancy}
         └── httpx → databus REST
             GET  /api/runs/<id>/state/
             POST /api/create-run/
             POST /api/runs/<id>/update/
```

### Hard invariants

| Invariant | Why it must not be broken |
|---|---|
| **Exactly one uvicorn worker** | `InMemoryChannelLayer` and `FleetState` are in-process memory — two workers = split-brain fleet, deaf channel layer |
| **Lifespan only fires under uvicorn** | `manage.py runserver` and daphne 4.x do not emit ASGI lifespan — background tasks never start |
| **`daphne` is test-only** | `channels.testing.WebsocketCommunicator` imports daphne at module load; it is NOT the ASGI server |
| **`AllowAny` is deliberate** | No auth/DB/session machinery; acceptable for a local dev tool; don't expose `WEB_PORT` publicly |
| **`DJANGO_SETTINGS_MODULE` set in `asgi.py`** | Set via `os.environ.setdefault` before `get_asgi_application()` — `uvicorn sim_project.asgi:application` works without external env |
| **paho failures are non-fatal** | `start_runtime()` catches all paho connect errors and logs a warning; `mqtt = None` means telemetry publishes are skipped |
| **Runtime singleton never replaced** | `_runtime: Runtime = Runtime()` is module-level; `get_runtime()` always returns the same object |
| **Independent of databus internals** | No shared DB, Redis, broker config, or code; integration only via databus public REST + MQTT broker |

---

## Module Map

### `sim_project/` — Django project layer

| File | Responsibility | Depends on |
|---|---|---|
| `settings.py` | All env vars + defaults; `CHANNEL_LAYERS`; `REST_FRAMEWORK`; `INSTALLED_APPS` | nothing |
| `urls.py` | URL routing: `/sim/` → api.urls; `/databus/` → proxy; `/` → index | `api.proxy`, `api.urls` |
| `asgi.py` | `ProtocolTypeRouter`; `_LifespanHandler` calls `start_runtime()`/`stop_runtime()`; sets `DJANGO_SETTINGS_MODULE` | `runtime`, `realtime.routing` |

### `simulator_app/` — Application layer

#### `runtime.py` — The central seam

The module-level `_runtime: Runtime` singleton holds every live object. **Background tasks are created here and nowhere else.**

| Symbol | Role |
|---|---|
| `Runtime` (dataclass) | Container: `fleet`, `scheduler`, `binder`, `databus`, `mqtt`, `channel_layer`, `_tasks`, `_shapes`, `_stops` |
| `get_runtime()` | The one way views/consumers/broadcast access shared state |
| `start_runtime()` | Wires all services; creates 3 asyncio Tasks; safe when databus/MQTT absent |
| `stop_runtime()` | Cancels tasks; stops paho; closes httpx |
| `tick_loop()` | Steps vehicles, publishes MQTT telemetry, calls `broadcast_fleet` + `broadcast_telemetry` each cycle |

#### `domain/fleet.py`

| Symbol | Role |
|---|---|
| `Vehicle` | Dataclass: per-vehicle state (`transmitting`, `moving`, `progress_m`, `bound_run_id`, `_kin`, …) |
| `FleetState` | Mutable container; all mutations flow through methods (`set_transmitting`, `set_moving`, `bind_run`, `unbind_run`, …) which call `_notify()` → `on_change` callbacks |
| `on_change: list[Callable]` | Subscribers called on every fleet mutation; `runtime.py` wires `broadcast_fleet` here |
| `FLEET` / `_make_fleet()` | Hardcoded 6-vehicle roster (unit-01…unit-06) |

#### `domain/kinematics.py` — Pure payload builder

The most databus-critical module. **Telemetry payload shapes must stay byte-identical** — databus's realtime-engine consumes them.

| Symbol | Role |
|---|---|
| `load_shapes(path)` | Parses `shapes.json` → `(shapes_by_id, stops_list, routes_list)` |
| `_init_kin(v)` | Populates `v._kin` with initial scratch values (idempotent) |
| `_get_shape(v, shapes)` | Resolves `v.bound_shape_id` or `v.default_shape_id` |
| `step_vehicle(v, dt, shape, stops)` | Advances kinematics: dwell, idle, speed, progress, terminal clamp, auto-dwell |
| `build_vehicle_payloads(v, shape, stops)` | **The telemetry contract.** Returns `{"position": {…}, "occupancy": {…}}` or `None` if not transmitting. Payload fields: see [telemetry payload fields](#telemetry-payload-fields) |
| `Shape` | Dataclass: `shape_id`, `points: list[(lat, lon, dist_km)]`, `total_dist_m` |

#### `domain/control.py` — Add a control knob here

Transport-agnostic. DRF views call `apply_control(runtime, vehicle_id, knob, payload)` and `apply_global_control(runtime, knob, payload)`.

| Symbol | Role |
|---|---|
| `apply_control(runtime, vehicle_id, knob, payload)` | Dispatches per-vehicle knobs; logs and discards unknown knobs/malformed payloads |
| `apply_global_control(runtime, knob, payload)` | Dispatches global knobs |
| `_PER_VEHICLE_KNOBS` | `frozenset`: `transmit, moving, speed, occupancy, dwell, jump_to_terminal, set_progress, inject_fault` |
| `_GLOBAL_KNOBS` | `frozenset`: `start_run, reload_schedule` |
| `_PER_VEHICLE_DISPATCH` | Dict: knob → handler function |
| `_GLOBAL_DISPATCH` | Dict: knob → handler function |

#### `realtime/broadcast.py` — The only place that pushes to the browser

All WebSocket pushes go through here. Do not call `channel_layer.group_send` anywhere else.

| Symbol | Role |
|---|---|
| `broadcast_fleet(snapshot)` | Throttled (≤ 1/200 ms): sends `{"type":"fleet","payload":{…}}` to the `"fleet"` group |
| `broadcast_schedule(snapshot)` | Unthrottled: sends `{"type":"schedule","payload":{…}}` |
| `broadcast_telemetry(vehicle_id, leaf, data)` | Sends `{"type":"telemetry","vehicle_id":…,"leaf":…,"payload":{…}}` |
| `GROUP = "fleet"` | The single Channels group name |
| `THROTTLE_S = 0.200` | Fleet broadcast throttle window |

#### `realtime/consumers.py`

| Symbol | Role |
|---|---|
| `FleetConsumer` | `AsyncJsonWebsocketConsumer`; joins group `"fleet"` on connect; immediately sends fleet + schedule snapshots; relays group messages via `fleet_update` handler; receive is a no-op |

#### `realtime/routing.py`

```python
websocket_urlpatterns = [path("ws/fleet/", FleetConsumer.as_asgi())]
```

#### `api/views.py`

DRF `@api_view` functions, one per endpoint. All use `get_runtime()` for state access. Async httpx calls use `async_to_sync`.

| View | Route |
|---|---|
| `healthz_view` | `GET /sim/healthz` |
| `fleet_view` | `GET /sim/fleet` |
| `schedule_view` | `GET /sim/schedule`, `PUT /sim/schedule` |
| `schedule_reload_view` | `POST /sim/schedule/reload` |
| `run_detail_view` | `GET /sim/run/<run_id>` |
| `track_run_view` | `POST /sim/runs/track` |
| `control_vehicle_view` | `POST /sim/control/<vehicle_id>/<knob>` |
| `control_global_view` | `POST /sim/control/global/<knob>` |

#### `api/proxy.py`

`databus_proxy_view(request, path)` — async Django view. Forwards all methods (GET/POST/PUT/PATCH/DELETE) to `settings.DATABUS_BASE_URL/<path>`. Strips hop-by-hop headers. Returns 502 on connection error, 504 on timeout.

#### `api/serializers.py`

| Serializer | Used by |
|---|---|
| `ScheduleDocumentSerializer` | `PUT /sim/schedule` |
| `TrackRunRequestSerializer` | `POST /sim/runs/track` (fields: `vehicle_id`, `trip_id`, `run_id`, `shape_id`) |

#### `services/databus_client.py`

Async httpx client. Long-lived (opened at startup, closed at shutdown via `open()`/`close()`).

| Method | Calls |
|---|---|
| `create_run(req)` | `POST /api/create-run/` |
| `update_run(req)` | `POST /api/runs/<id>/update/` |
| `get_run_state(run_id)` | `GET /api/runs/<id>/state/` → `run_lifecycle_state` string or `None` |
| `get_run_hash(run_id)` | Same endpoint → full fields dict (for `GET /sim/run/<id>`) |
| `get_trip_terminal_stop(trip_id)` | `GET /api/stop-times/?trip=<id>` → highest-sequence stop_id |

#### `services/run_binder.py`

Background asyncio task. Polls `get_run_state()` every `RUN_POLL_INTERVAL_S` seconds for each tracked run. On state change calls `_apply_state(binding, new_state)` which drives `FleetState` mutations (`bind_run`, `unbind_run`, `set_transmitting`, `set_moving`).

Terminal states: `"Completed"`, `"Cancelled"`, `"Interrupted"`, `"Short Turned"`.
Run lost (404 after 30 s grace): `_force_unbind`.

#### `services/scheduler.py`

Background asyncio task. Reads `schedule.yaml` on `load()` / `reload()`. Fires `create_run` when `now >= entry.start_time - pre_run_idle_s`. On success calls `binder.track()`. Publishes schedule snapshot via `broadcast_schedule()`. Supports `auto_confirm` and `auto_start_motion_after_s`.

---

## "How Do I…" Recipes

### Add a per-vehicle control knob

1. Add the knob name to `_PER_VEHICLE_KNOBS` in `simulator_app/domain/control.py`.
2. Write a handler function: `def _handle_mything(runtime, vehicle_id, payload): …`
3. Register it in `_PER_VEHICLE_DISPATCH`.
4. Add a test in `simulator_app/tests/test_control.py`.
5. The browser can now call `POST /sim/control/unit-01/mything` with a JSON body — no other changes needed.

### Add a global control knob

Same as above but add to `_GLOBAL_KNOBS` and `_GLOBAL_DISPATCH`. URL: `POST /sim/control/global/<knob>`.

### Add a WebSocket message type

1. Add a new `broadcast_X(payload)` helper in `realtime/broadcast.py` that calls `_group_send({"type": "fleet.update", "kind": "X", ...})`.
2. Add a handler in `FleetConsumer.fleet_update()` matching `kind == "X"`.
3. Call `broadcast_X()` from wherever the event originates (typically `tick_loop()` or a service callback).
4. Add a test in `simulator_app/tests/test_consumer.py`.

### Change the tick rate

Set `SIM_TICK_INTERVAL` env var (float, seconds). The `tick_loop()` reads it once at startup from `settings.SIM_TICK_INTERVAL`. To change at runtime there is no hot-reload — restart the process.

### Add a REST endpoint

1. Write a DRF `@api_view` function in `simulator_app/api/views.py`.
2. Add a `path(...)` entry in `simulator_app/api/urls.py`.
3. Add an `APIClient` test in `simulator_app/tests/test_api.py`.

### Change the telemetry payload shape

**Stop and think first.** The payload fields from `build_vehicle_payloads()` are consumed byte-for-byte by databus's realtime-engine over MQTT. Any field rename, removal, or type change breaks that contract. If you must change the shape:

1. Coordinate with the databus team.
2. Update `simulator_app/domain/kinematics.py` → `build_vehicle_payloads()`.
3. Update `simulator_app/tests/test_kinematics.py` — the telemetry shape tests assert the exact output fields (these are your regression guard).
4. The same payloads are also pushed to the browser via `broadcast_telemetry()` — update browser consumers if needed.

Current payload fields:
- `position`: `timestamp, latitude, longitude, bearing, speed, odometer`
- `occupancy`: `timestamp, occupancy_percentage`

> The sim is a *dumb sensor emitter*: it publishes only what a real vehicle can
> sense by itself. The `progression` leaf (map-matched stop status) and the
> `occupancy_status` enum are server-owned — databus recomputes them and ignores
> anything the edge sends — so neither goes on the wire.

### Add a background task

1. Write an `async def my_task() -> None:` (infinite loop with `await asyncio.sleep(...)`).
2. In `runtime.py` → `start_runtime()`, add `asyncio.create_task(my_task(), name="my_task")` and append it to `_runtime._tasks`.
3. `stop_runtime()` already cancels all tasks in `_runtime._tasks` — no changes needed there.

### Access the runtime from a view or consumer

```python
from simulator_app.runtime import get_runtime
rt = get_runtime()
# rt.fleet, rt.scheduler, rt.binder, rt.databus, rt.mqtt, rt.channel_layer
```

---

## Gotchas

| Gotcha | Detail |
|---|---|
| **`manage.py runserver` doesn't start tasks** | Django's dev server does not emit ASGI lifespan. Always use `uvicorn sim_project.asgi:application`. |
| **`daphne` is in dev deps for testing only** | `channels.testing.WebsocketCommunicator` imports daphne at module load. daphne is NOT used as the server. Adding it to `INSTALLED_APPS` would change behavior — don't. |
| **`InMemoryChannelLayer` = one process** | Never run `--workers N` with N > 1. See invariants table. |
| **`DJANGO_SETTINGS_MODULE` is set in `asgi.py`** | The `os.environ.setdefault(...)` call is at the top of `asgi.py`, before `get_asgi_application()`. Running `uvicorn sim_project.asgi:application` works without setting it externally. |
| **Paho runs in its own thread** | `mqtt_client.loop_start()` creates a background thread. Never call paho's blocking APIs (`loop_forever`, `connect_async`) on the asyncio event loop. `publish()` is thread-safe. |
| **Paho failures are non-fatal by design** | If the broker is absent, `_runtime.mqtt = None` and `tick_loop()` skips all `mqtt.publish()` calls with no error. This is the standalone mode. |
| **Schedule payload key is `runs`** | `scheduler.snapshot()` returns `{"defaults": {…}, "runs": […]}`. The browser's `ws_client.js` aliases `runs` to `entries` internally — but the wire format and the Python code use `runs`. |
| **`start_time` needs a timezone** | `scheduler.py` raises `ValueError` on naive datetimes. Entries without a timezone are skipped silently during the `run_loop`. Always include an offset (e.g. `-06:00` or `+00:00`). |
| **`FleetState.on_change` is a plain list** | It holds sync callables. The `broadcast_fleet` callback wired in `start_runtime()` wraps the async call with `asyncio.ensure_future()`. Don't put blocking or non-awaitable code here. |
| **`jump_to_terminal` / `set_progress` reload shapes from disk** | These control handlers call `load_shapes(settings.SHAPES_PATH)` on every invocation — they are not performance-critical but do disk I/O. |
| **`databus_proxy_view` is an async Django view** | It uses `httpx.AsyncClient` and is mounted with `re_path` (not `path`). Django 4.1+ supports async function-based views natively. |
| **No DB at runtime** | The simulator has a throwaway sqlite DB (so `manage.py check` works) but never reads or writes it during normal operation. Don't add models unless strictly necessary. |

---

## Testing and Verification

### Run the test suite

```bash
uv run pytest                         # 94 tests
uv run pytest -v                      # verbose
uv run pytest --cov=simulator_app --cov-report=term-missing
```

Test paths (from `pyproject.toml`): `tests/` and `simulator_app/tests/`.

### Where tests live

| File | What it covers |
|---|---|
| `tests/test_smoke.py` | Boot-level: `/sim/healthz` 200, ASGI import |
| `simulator_app/tests/test_api.py` | All `/sim/*` DRF endpoints (mocked runtime) |
| `simulator_app/tests/test_consumer.py` | `FleetConsumer` connect/disconnect/snapshot via `WebsocketCommunicator` |
| `simulator_app/tests/test_control.py` | `apply_control` / `apply_global_control` dispatch and validation |
| `simulator_app/tests/test_fleet.py` | `FleetState` mutators and `on_change` callbacks |
| `simulator_app/tests/test_kinematics.py` | `step_vehicle`, `build_vehicle_payloads` (telemetry shape assertions) |
| `simulator_app/tests/test_tick_loop.py` | `tick_loop()` integration (mocked fleet/mqtt/broadcast) |
| `simulator_app/tests/test_run_binder.py` | `RunBinder` track/untrack/poll/state-change |
| `simulator_app/tests/test_scheduler.py` | `Scheduler` load/reload/write/dispatch |
| `simulator_app/tests/test_databus_client.py` | `DatabusClient` HTTP calls (mocked with `respx`) |

### Live boot smoke commands

```bash
# Start the server
uv run uvicorn --host 0.0.0.0 --port 8080 sim_project.asgi:application

# In another terminal:
curl -s http://localhost:8080/sim/healthz      # → {"ok": true}
curl -s http://localhost:8080/sim/fleet | jq   # → {vehicles: [...]}
curl -s http://localhost:8080/sim/schedule | jq

# WebSocket smoke (requires websocat or similar)
websocat ws://localhost:8080/ws/fleet/
# → immediately receives {"type":"fleet","payload":{…}} and {"type":"schedule","payload":{…}}
```

### E2E against databus

Full E2E is manual — it requires a live databus stack. See the [E2E Verification Checklist](README.md#e2e-verification-checklist-manual-requires-live-databus) in `README.md`.

---

## Telemetry Payload Fields

Reference for the three MQTT leaves published to `transit/vehicle/<id>/<leaf>` and simultaneously pushed to the browser via `broadcast_telemetry`:

### `position`
```json
{
  "timestamp": 1747670402,      // Unix UTC integer seconds
  "latitude": 9.935812,         // float, 6 decimal places
  "longitude": -84.051234,      // float, 6 decimal places
  "bearing": 287.3,             // float degrees, 1 decimal place
  "speed": 7.42,                // float m/s, 2 decimal places (0.0 when stopped/dwelling)
  "odometer": 412.5             // float meters along shape, 1 decimal place
}
```

### `occupancy`
```json
{
  "timestamp": 1747670402,
  "occupancy_percentage": 42
}
```

`occupancy_status` is **not** sent: databus recomputes the GTFS-RT enum from
`occupancy_percentage` server-side and discards any value the edge supplies.

> **Removed: `progression`.** databus no longer subscribes to
> `transit/vehicle/+/progression`; stop status (`current_status`/`stop_id`/
> `current_stop_sequence`) is now computed server-side. The sim does not publish
> this leaf.

These shapes are asserted in `simulator_app/tests/test_kinematics.py`. Do not change them without updating both the test and coordinating with the databus team.

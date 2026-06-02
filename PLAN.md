# PLAN — Translate the MQTT simulator into a self-contained Django app

> Scope: the **MQTT telemetry simulator** under `sim/` plus the `web/` UI and the
> `ws-bridge`. The FSM **test harness** (`sim/harness/`) is explicitly out of scope
> and is left untouched.

---

## 1. Goal

Collapse the three runtime services (`web` nginx, `simulator` FastAPI+tick-loop,
`ws-bridge` mosquitto) into **one self-contained Django ASGI service**, while
keeping observable behavior identical.

### Why
1. databus is already a Django app, so a Django simulator is idiomatic for the team.
2. Three containers → one container removes the nginx reverse proxy, the mosquitto
   WS bridge, and the browser's MQTT.js dependency.

### Hard constraint (from the user)
The simulator stays **completely independent of databus**. It keeps its **own**
Django project, settings, `manage.py`, Dockerfile, and container. It integrates
with databus **only** through databus's public surface:
- **HTTP REST** — poll run state (`GET /api/runs/{id}/state/`), create/update runs
  (`POST /api/create-run/`, `POST /api/runs/{id}/update/`), list trips/stop-times.
- **MQTT telemetry** — publish `transit/vehicle/<id>/{position,progression,occupancy}`
  to databus's telemetry-broker.

It must **not** share databus's database, Redis, Channels layer, broker config, or code.

---

## 2. Locked decisions

| Decision | Choice | Consequence |
|---|---|---|
| **Location** | New standalone Django project in **this repo** (`databus-sim`) | No merge into `databus/backend`. |
| **Browser transport** | **Django Channels** (WebSockets) | Browser drops MQTT.js + the ws-bridge. |
| **Process model** | **Single ASGI container** (daphne): tick loop + run-binder + scheduler as background asyncio tasks; Channels `InMemoryChannelLayer` | No Redis, no internal broker, one container. Cannot scale daphne to >1 worker (acceptable for a sim). |
| **MQTT footprint** | Thin **paho publisher to databus's broker only** (`transit/vehicle/+/+`). `sim/state/*` → Channels push; `sim/control/*` → DRF POST | ws-bridge and the internal-broker dependency are removed entirely. |
| **Worker count** | **Exactly one daphne worker** (no `--workers`, no gunicorn/uvicorn multi-worker fronting) | Required by the in-memory `FleetState` + `InMemoryChannelLayer`. The simplest config that works; documented in compose + README so nobody scales it out and silently breaks realtime/control. |
| **Auth** | **`AllowAny` on all endpoints** (no token/login) | Preserves today's no-auth FastAPI behavior; no DB/user/token machinery needed. Safe because the service is a local/dev tool; the single caveat is **don't publish `WEB_PORT` to an untrusted network** (the `/databus/` proxy would otherwise become an unauthenticated gateway to databus's write API). |

---

## 3. Current architecture (recap)

```
browser ──MQTT/WS──► ws-bridge(mosquitto) ──MQTT/TCP──► databus telemetry-broker
   │  subscribes: sim/state/{fleet,schedule}, transit/vehicle/+/+
   │  publishes:  sim/control/+/+
   ├──HTTP /sim/*──► nginx ──► simulator FastAPI :8081  (fleet, schedule, run, runs/track)
   └──HTTP /databus/*► nginx ──► databus orchestrator REST

simulator process:
  - sync tick loop (thread): step_vehicle() + publish_vehicle() → paho MQTT
  - asyncio thread: uvicorn(FastAPI http_control) + RunBinder.poll_loop + Scheduler.run_loop
  - Controller (paho sub sim/control/+/+) → FleetState
  - StatePublisher (paho pub sim/state/{fleet,schedule}, retained)
  - DatabusClient (httpx) → databus REST
```

Key insight that makes this tractable: the **domain logic is already
transport-agnostic**. `FleetState`, kinematics (`step_vehicle`/`publish_vehicle`),
`RunBinder`, `Scheduler`, and `DatabusClient` don't care about MQTT or FastAPI.
Only the **edges** (MQTT pub/sub, FastAPI routes, the asyncio/thread bootstrap)
change. The bulk of `sim/` is ported nearly verbatim.

---

## 4. Target architecture

```
                       ┌────────────────────────── single daphne process ──────────────────────────┐
browser ──WS /ws/fleet─┤ Channels consumer (group "fleet")  ◄── group_send() each tick              │
   │                   │ DRF /api/sim/*  (control, schedule, fleet, run, runs/track)                 │
   │  static + index ──┤ Django staticfiles / TemplateView  (same-origin; no nginx)                  │
   └──HTTP /databus/*──┤ DRF proxy view  ──httpx──► databus orchestrator REST (avoids CORS)          │
                       │                                                                              │
                       │ ASGI lifespan startup → asyncio tasks:                                       │
                       │   • tick_loop()    step+publish; group_send fleet+telemetry to browser       │
                       │                    AND paho.publish transit/vehicle/* to databus broker      │
                       │   • RunBinder.poll_loop()  httpx poll databus run state → FleetState         │
                       │   • Scheduler.run_loop()   schedule.yaml → create/update run                 │
                       │ module-global Runtime{ fleet, scheduler, binder, databus, mqtt, layer }      │
                       └──────────────────────────────────────────────────────────────────────────────┘
                                              │ paho MQTT/TCP
                                              ▼
                                  databus telemetry-broker (transit/vehicle/*)
```

One event loop owns the tick loop, binder, and scheduler. Because DRF views and
the Channels consumer run in the **same process**, they reach the in-memory
`FleetState` through a module-level singleton — no Redis/IPC needed.

---

## 5. Module-by-module translation map

| Current (`sim/`) | Target (`simulator_app/`) | Change |
|---|---|---|
| `fleet.py` (`FleetState`, `Vehicle`, roster) | `domain/fleet.py` | **Verbatim.** Keep `on_change` hook. |
| `simulator.py` kinematics (`Shape`, `haversine_m`, `interpolate`, `step_vehicle`, `publish_vehicle`, `load_shapes`, …) | `domain/kinematics.py` | **Port verbatim**, minus the `__main__`/threading bootstrap. `publish_vehicle` is split: payload-building (pure) vs. the sink (MQTT publish + Channels push). |
| `simulator.py` `run()` / `_async_services_main()` / threading | `runtime.py` + ASGI lifespan | **Rewritten.** Becomes async `tick_loop()` + lifespan task wiring (see §6.1). |
| `controller.py` (MQTT `sim/control/+/+` → fleet) | `api/control.py` (DRF) + `domain/control.py` | **Repurposed.** The `_handle_*` validation/dispatch logic moves into a transport-agnostic `apply_control(fleet, scheduler, binder, vehicle_id, knob, payload)`; DRF views call it. (see §6.4) |
| `state_publisher.py` (MQTT `sim/state/*` retained) | `realtime/broadcast.py` | **Replaced.** `FleetState.on_change` → `group_send("fleet", snapshot)` over Channels. Throttle (≤1/200 ms) preserved. |
| `databus_client.py` (httpx) | `services/databus_client.py` | **Verbatim.** Async httpx client unchanged. |
| `run_binder.py` (`RunBinder.poll_loop`) | `services/run_binder.py` | **Verbatim.** Background asyncio task. |
| `scheduler.py` (`Scheduler.run_loop`, schedule.yaml) | `services/scheduler.py` | **Verbatim.** Background asyncio task; `state_publisher` callback swapped for a Channels broadcaster. |
| `http_control.py` (FastAPI) | `api/views.py` + `api/serializers.py` + `api/urls.py` (DRF) | **Rewritten** as DRF. Route surface preserved (see §6.3). |
| `shapes.json`, `schedule.yaml`, `mappings.yaml` | `simulator_app/data/` (or kept at repo paths) | Moved/bind-mounted; `schedule.yaml` stays host-editable. |
| `web/` (nginx static + MQTT.js) | `simulator_app/static/` + `templates/` | UI ported; MQTT.js removed, WS client added (see §7). |
| `web/nginx.conf.template` | Django URLconf + `/databus/` proxy view | nginx removed; same-origin via Django. |
| `ws-bridge` (mosquitto) | **deleted** | Replaced by Channels. |
| `sim/harness/**` | **untouched** | Out of scope. |

---

## 6. New / rewritten components

### 6.1 ASGI lifespan + background tasks (`asgi.py`, `runtime.py`)
- `runtime.py` exposes a module-level `Runtime` holder built once at startup:
  `fleet`, `scheduler`, `binder`, `databus` (httpx client), `mqtt` (paho client),
  `channel_layer`. Accessors (`get_runtime()`) used by views/consumers.
- ASGI app = Channels `ProtocolTypeRouter` with a custom **`"lifespan"`** handler
  (Django's bare ASGI app ignores lifespan; Channels lets us add one):
  - **startup**: load shapes, build `FleetState`, connect paho (`loop_start()`),
    open `DatabusClient`, wire `Controller`/scheduler callbacks, then
    `asyncio.create_task()` for `tick_loop`, `binder.poll_loop`, `scheduler.run_loop`.
  - **shutdown**: cancel tasks, `paho.loop_stop()/disconnect()`, close httpx.
- `tick_loop()` is the async rewrite of the current sync loop: `await asyncio.sleep(interval)`,
  iterate fleet, `step_vehicle`, then for each vehicle **(a)** paho-publish telemetry
  to the databus broker (unchanged topics/payloads) and **(b)** accumulate a browser
  snapshot. After the loop, `group_send("fleet", {fleet, telemetry})`.
  - CLI flags (`--interval`, `--per-route`, `--stop-vehicle`, `--only-vehicle`,
    `--random-drop-rate`, `--stop-all-after`) become **settings/env** read at startup.

### 6.2 Channels consumer (`realtime/consumers.py`, `realtime/routing.py`)
- `FleetConsumer(AsyncJsonWebsocketConsumer)`: on connect, join group `"fleet"`,
  immediately send the current snapshot (replaces MQTT "retained" semantics), then
  stream subsequent `group_send` payloads. Read-only (control is via DRF).
- `routing.py`: `path("ws/fleet/", FleetConsumer.as_asgi())`.
- `realtime/broadcast.py`: `broadcast_fleet(snapshot)` / `broadcast_schedule(snapshot)`
  wrapping `channel_layer.group_send`, with the 200 ms throttle/coalesce ported from
  `StatePublisher`.

### 6.3 DRF control/HTTP API (`api/`)
Preserve the route surface the browser already calls (rebased under `/api/sim/`,
or kept at `/sim/` to minimize UI churn — see §7):

| Method | Path (today `/sim/…`) | Replaces | Backed by |
|---|---|---|---|
| GET | `/healthz` | FastAPI healthz | trivial view |
| GET | `/fleet` | `/fleet` | `runtime.fleet.snapshot()` |
| GET | `/schedule` | `/schedule` | `scheduler.snapshot()` |
| PUT | `/schedule` | `/schedule` | `scheduler.write()` + `reload()` |
| POST | `/schedule/reload` | `/schedule/reload` | `scheduler.reload()` |
| GET | `/run/{run_id}` | `/run/{run_id}` | `databus.get_run_hash()` |
| POST | `/runs/track` | `/runs/track` | `binder.track()` (+ terminal-stop lookup) |
| **POST** | `/control/{vehicle_id}/{knob}` | **MQTT `sim/control/<id>/<knob>`** | `apply_control()` |
| **POST** | `/control/global/{knob}` | **MQTT `sim/control/global/<knob>`** | `apply_control()` |

- Serializers (`ScheduleEntry`, `ScheduleDocument`, `RunStateResponse`,
  `TrackRunRequest`, control payloads) port the pydantic models 1:1 to DRF
  serializers for validation at the boundary.
- `/databus/` reverse proxy replaced by a small DRF passthrough view (httpx) OR
  the browser calls databus directly with CORS — **recommend the proxy** to keep
  same-origin and avoid touching databus.

### 6.4 Control dispatch (`domain/control.py`)
- Port `Controller._handle_*` bodies into pure functions taking `(runtime, vehicle_id, knob, payload)`.
- Keep the same validation + safe-discard semantics (log & ignore malformed).
- `jump_to_terminal` / `set_progress` reuse `domain/kinematics.py` (no more
  cross-imports from a `simulator.py` god-module).

---

## 7. Web / frontend changes

- **Served by Django**: `index.html` → a `TemplateView` (or plain static + staticfiles);
  `app.js`, `style.css`, `lib/`, `modals/`, `tabs/`, `shapes.json` → `static/`.
- **Remove** `<script src="…mqtt.min.js">` and `lib/mqtt_client.js`.
- **Add** `lib/ws_client.js`: a tiny WebSocket wrapper exposing the same
  `subscribe(topic, cb)` shape `app.js` expects, but driven by one `/ws/fleet/`
  socket. The server pushes `{type:"fleet"|"schedule"|"telemetry", …}`; the wrapper
  fans out to the existing store handlers so `app.js`/tabs barely change.
- **`controls.publish(topic, payload)`** (used by tabs/modals) is reimplemented to
  translate `sim/control/<id>/<knob>` topic strings into `POST /control/<id>/<knob>`
  fetch calls — **no tab/modal rewrites needed** if we keep that one shim.
- `config.js.template` (MQTT_WS_PORT) → dropped; WS URL derived from `location`.
- `sim_api.js` / `databus_api.js` keep working against `/sim/*` and `/databus/*`
  (now Django-served). Decision: **keep the `/sim/` and `/databus/` path prefixes**
  to minimize frontend diff; map them in the Django URLconf.

---

## 8. Proposed project layout

```
databus-sim/
├── manage.py
├── pyproject.toml                # Django, channels, daphne, djangorestframework,
│                                 # httpx, paho-mqtt, orjson, pyyaml  (drop fastapi/uvicorn)
├── Dockerfile                    # one image; CMD: daphne sim_project.asgi:application
├── docker-compose.yml            # ONE service (simulator); ws-bridge & web removed
├── sim_project/                  # Django project (settings, urls, asgi)
│   ├── settings.py               # minimal INSTALLED_APPS; InMemoryChannelLayer; no DB or sqlite-only
│   ├── urls.py                   # /, /sim/*, /databus/* , (ws routing in asgi)
│   └── asgi.py                   # ProtocolTypeRouter{http, websocket, lifespan}
├── simulator_app/
│   ├── apps.py
│   ├── runtime.py                # Runtime singleton + lifespan startup/shutdown
│   ├── domain/  fleet.py  kinematics.py  control.py
│   ├── services/  databus_client.py  run_binder.py  scheduler.py
│   ├── realtime/  consumers.py  routing.py  broadcast.py
│   ├── api/  views.py  serializers.py  urls.py
│   ├── data/  shapes.json  schedule.yaml  mappings.yaml
│   ├── templates/  index.html
│   ├── static/  app.js  style.css  lib/  modals/  tabs/  ws_client.js
│   └── tests/    (pytest-django; ports sim/tests/*)
└── sim/harness/                  # UNCHANGED (out of scope)
```

---

## 9. Settings & config

- **INSTALLED_APPS** (minimal): `daphne`, `channels`, `rest_framework`,
  `django.contrib.staticfiles`, `simulator_app`. Avoid `admin`/`auth`/`sessions`
  so the service can run **DB-free** (or a throwaway sqlite if migrations insist).
- `ASGI_APPLICATION = "sim_project.asgi.application"`.
- `CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}`.
- Config via env (`decouple`/`os.environ`), preserving existing knobs:
  `MQTT_HOST/PORT/TOPIC_ROOT`, `DATABUS_BASE_URL`, `SIM_CORS_ORIGINS`,
  `SCHEDULE_PATH`, `POST_RUN_IDLE_S`, `RUN_POLL_INTERVAL_S`, `SCHEDULER_TICK_S`,
  tick `INTERVAL`, plus the former CLI flags.
- **CORS**: with same-origin serving, CORS largely goes away; keep
  `django-cors-headers` only if the browser still hits databus cross-origin
  (the `/databus/` proxy makes it unnecessary).
- **Auth/permissions**: **decided — `AllowAny` on all endpoints.** This is an
  internal dev simulator with no auth today; `AllowAny` preserves that exactly and
  needs no DB/user/token store. Deliberate deviation from the "permissions on all
  endpoints" guideline, accepted because the service is local/dev-only. **Operational
  rule:** do not expose `WEB_PORT` to an untrusted network (the `/databus/` proxy
  would otherwise be an open gateway to databus's write API). If exposure ever
  becomes possible, the minimal hardening is a shared-secret `X-Sim-Token` header
  gating write methods — not in scope now.

---

## 10. Docker / compose

- **Delete** `ws-bridge` and `web` services and the nginx/mosquitto config files.
- **`simulator` service**: build the Django image; `CMD daphne -b 0.0.0.0 -p ${WEB_PORT} sim_project.asgi:application`.
- Ports: expose a single `WEB_PORT` (UI + API + WS all same-origin). Drop
  `MQTT_WS_PORT`, `MQTT_TCP_PORT`, `SIM_HTTP_PORT`.
- Keep `extra_hosts: host.docker.internal` for reaching databus's broker/REST.
- `schedule.yaml` stays bind-mounted for live host edits.
- `.env`/`.env.example` trimmed to: `WEB_PORT`, `DATABUS_HOST`, `DATABUS_HTTP_PORT`,
  `DATABUS_MQTT_HOST`, `DATABUS_MQTT_PORT`, tick/poll knobs.
- **Standalone mode** (no databus): UI works purely over Channels; telemetry
  paho-publish simply fails/logs harmlessly. No local broker needed anymore.

---

## 11. Testing strategy (TDD, target 80%+)

Port `sim/tests/*` to `pytest-django`; most are transport-agnostic and move with
minimal edits:
- **Unit (verbatim-ish)**: `test_controller` → control-dispatch tests against
  `apply_control`; `test_scheduler`, `test_run_binder`, `test_databus_client`,
  `test_telemetry_shape`, `test_fsm_graph` (harness-related — confirm still in scope=no).
- **New unit**: `tick_loop` builds correct snapshot; `broadcast` throttle/coalesce.
- **Integration (DRF `APITestCase`)**: each `/sim/*` and `/control/*` endpoint —
  status codes + effect on `FleetState`; `/databus/` proxy passthrough (mock httpx).
- **Channels**: `ChannelsLiveServerTestCase`/`WebsocketCommunicator` — connect to
  `/ws/fleet/`, assert initial snapshot + a pushed update after a fleet mutation.
- **E2E smoke** (manual, ports `web/MANUAL_TEST.md`): `docker compose up`; open UI;
  confirm map renders, schedule edit reloads, operator create-run drives a vehicle,
  cancel/interrupt/short-turn unbinds — identical to today.

---

## 12. Phased implementation

1. **Scaffold** Django project + app, settings, deps; empty ASGI with lifespan no-op. Boots under daphne.
2. **Port domain** (`fleet.py`, `kinematics.py`, `databus_client.py`, `run_binder.py`,
   `scheduler.py`) unchanged; unit tests green.
3. **Runtime + tick loop**: lifespan starts tasks; paho publishes telemetry to broker
   (verify with `mosquitto_sub` against databus broker — parity with today).
4. **Channels**: consumer + broadcast; wire `FleetState.on_change` and tick snapshot.
5. **DRF API**: port `http_control` routes + add `/control/*`; `APITestCase` green.
6. **Frontend**: `ws_client.js` + `controls.publish` shim; drop MQTT.js/config.js;
   serve via Django; verify tabs/modals work unchanged.
7. **`/databus/` proxy** view; confirm operator flow + trips dropdown.
8. **Compose/docs**: one service; delete ws-bridge/web/nginx/mosquitto; update README.
9. **Full E2E** smoke parity pass; coverage check.

---

## 13. Risks & watch-items

- **Lifespan support**: must mount a `"lifespan"` handler in `ProtocolTypeRouter`
  (bare Django ASGI ignores it). Verify daphne fires startup/shutdown.
- **Single-process invariant** *(decided: one worker)*: in-memory fleet +
  `InMemoryChannelLayer` require **exactly one** daphne worker. Ship daphne with no
  `--workers`, never front it with multi-worker gunicorn/uvicorn, and note it in the
  compose file + README. Multiple workers would split-brain the fleet and deafen the
  channel layer (frozen map, silently-ignored control POSTs).
- **paho in async process**: keep `loop_start()` (own thread); `publish()` is
  non-blocking — don't call paho's blocking APIs on the event loop.
- **Retained-snapshot semantics**: MQTT retained messages gave late-joining browsers
  instant state; replicate by having the consumer **send the current snapshot on
  connect**.
- **Throttle parity**: port `StatePublisher`'s 200 ms coalescing to `broadcast.py`
  so high-frequency fleet mutations don't flood the socket.
- **Run-state contract unchanged**: still `GET /api/runs/{id}/state/` on databus
  (per `prompts/databus-http-option.md`) — no databus changes required.
- **Telemetry payloads must stay byte-identical** (databus realtime-engine consumes
  them): reuse `publish_vehicle` payload builders unchanged; assert via
  `test_telemetry_shape`.
- **`AllowAny`** *(decided)* deviates from the global "permissions on all endpoints"
  rule — intentional (internal dev sim, no auth today). Operational guardrail: keep
  `WEB_PORT` on a trusted/local network only.

---

## 14. Definition of done

- `docker compose up` starts **one** service; ws-bridge, web, nginx, mosquitto gone.
- Browser UI (map, fleet, schedule, operator, runs tabs) behaves identically, now
  over `/ws/fleet/` + `/sim/*` + `/databus/*`, no MQTT.js.
- Telemetry still reaches databus's broker with identical topics/payloads; run
  lifecycle still drives vehicles via REST polling.
- `pytest` green, 80%+ coverage; `grep -r "fastapi\|uvicorn\|mosquitto\|mqtt_client.js"`
  returns nothing in the runtime path.
- README + `.env.example` updated to the single-service topology.
- `sim/harness/` untouched.
```

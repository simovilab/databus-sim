# SIMOVI Simulator — Agent Runbook

Operator one-pager for starting the full stack, seeding the databus, and
verifying every module in isolation. Pairs with `sim/CONTRACTS.md` (locked
interfaces) and `docs/simulator/PLAN.md` (the restructure plan).

---

## 0. Prerequisites

- Docker Desktop running.
- `uv` installed (Astral) for local Python.
- `mosquitto-clients` (`mosquitto_pub`, `mosquitto_sub`) and `redis-cli` for poking the stack.
- The databus repo cloned at `../databus/` (sibling to `simulator/`).

---

## 1. Start the full stack (3 terminals)

**Terminal 1 — databus** (orchestrator + realtime-engine + RabbitMQ + NanoMQ + Redis + Postgres):
```bash
cd ../databus
docker compose -f compose.dev.yml up -d
# Wait ~20 s for migrations; tail logs:
docker compose -f compose.dev.yml logs -f orchestrator realtime-engine
```

**Terminal 2 — simulator** (Python sim + nginx web):
```bash
cd simulator
docker compose up --build           # or: --profile standalone for a local broker
```
The simulator container exposes:
- MQTT publisher → `host.docker.internal:1883` (databus telemetry-broker).
- HTTP control → `localhost:8081`.
- Reads schedule from bind-mounted `sim/schedule.yaml`.

**Terminal 3 — UI**:
Open `http://localhost:8080`. You should see the Option B shell:
top bar with broker/sim status badges, tab strip (Fleet | Schedule | Operator | Runs),
and the map pane on the right. Each tab shows a placeholder until C1/C2 land.

---

## 2. Seed databus so create-run succeeds

`POST /api/create-run/` validates `vehicle_id`, `operator_id`, `route_id`,
`trip_id`, `shape_id` against PostgreSQL. With a fresh databus DB, these
records do not exist — every create-run will 400.

Seeding (run inside the databus orchestrator container):
```bash
cd ../databus
docker compose -f compose.dev.yml exec orchestrator bash

# GTFS fixture (routes, trips, stops, shapes):
uv run python manage.py loaddata feed/files/gtfs.json

# Operators + Vehicles — verify the exact fixture name in databus/backend/operators/:
uv run python manage.py loaddata operators/fixtures/operators.json
uv run python manage.py loaddata operators/fixtures/vehicles.json
```
If those fixture files don't exist yet, create operator/vehicle rows from the Django shell:
```bash
uv run python manage.py shell <<'PY'
from operators.models import Operator
from vehicles.models import Vehicle
Operator.objects.get_or_create(operator_id="op-001", defaults={"name": "Sim Op"})
for vid, route in [("unit-01","bUCR_L1"),("unit-02","bUCR_L1"),("unit-03","bUCR_L1"),
                   ("unit-04","bUCR_L2"),("unit-05","bUCR_L2"),("unit-06","bUCR_L2")]:
    Vehicle.objects.get_or_create(vehicle_id=vid, defaults={"route_id": route})
PY
```
Adjust the model paths / field names if the schema differs — `git grep operator_id`
in databus to confirm.

---

## 3. Verify each module in isolation

### 3.1 Python imports (P0 smoke test)
```bash
cd simulator
PYTHONPATH=. uv run --project sim python -c \
  "import sim.fleet, sim.controller, sim.state_publisher, sim.run_binder, \
          sim.scheduler, sim.http_control, sim.databus_client, sim.redis_client; \
   print('imports ok')"
```
Expected: `imports ok`.

### 3.2 Telemetry shape (Agent A1)
```bash
mosquitto_sub -h localhost -t 'transit/vehicle/+/+' -v
```
Expected leaves: `position`, `progression`, `occupancy`. No `data` topic.

### 3.3 Control subscriber (Agent A2)
```bash
mosquitto_pub -h localhost -t 'sim/control/unit-01/transmit' -m '{"on": false}'
mosquitto_sub -h localhost -t 'transit/vehicle/unit-01/+'  # should go silent within a tick
```

### 3.4 State publisher (Agent A2)
```bash
mosquitto_sub -h localhost -t 'sim/state/+' -v
```
Expected: retained snapshots on `sim/state/fleet` and `sim/state/schedule`.

### 3.5 HTTP control (Agent B1)
```bash
curl -s http://localhost:8081/healthz
curl -s http://localhost:8081/schedule | jq .
curl -s http://localhost:8081/fleet    | jq .
```

### 3.6 Run creation against databus
```bash
curl -s -X POST http://localhost:8000/api/create-run/ \
  -H 'Content-Type: application/json' \
  -d '{
    "vehicle_id":"unit-01","operator_id":"op-001","route_id":"bUCR_L1",
    "trip_id":"<a real trip_id from your GTFS fixture>",
    "direction_id":0,"shape_id":"hacia_artes",
    "schedule_relationship":"SCHEDULED"
  }' | jq .
```
Expected: `{"status":"success","run_id":"...","run_lifecycle_state":"Initialized"}`.

Confirm the run:
```bash
RUN_ID=<from above>
curl -s -X POST http://localhost:8000/api/update-run/ \
  -H 'Content-Type: application/json' \
  -d "{\"run_id\":\"$RUN_ID\",\"event\":\"run_confirmed_by_operator\",\"details\":{}}" | jq .
```

### 3.7 Read run state from Redis
```bash
redis-cli HGETALL run:$RUN_ID
# Look at the run_lifecycle_state field.
```

---

## 4. Agent ownership reference

See `sim/CONTRACTS.md` §8 for the full table. TL;DR:

- **Agent A** owns `sim/fleet.py`, `sim/simulator.py`, `sim/controller.py`,
  `sim/state_publisher.py`, `sim/redis_client.py` (interface).
- **Agent B** owns `sim/http_control.py`, `sim/scheduler.py`,
  `sim/databus_client.py`, `sim/run_binder.py`, `sim/redis_client.py` (impl),
  `sim/schedule.yaml`.
- **Agent C** owns `web/index.html`, `web/style.css`, `web/app.js`,
  `web/lib/*.js`, `web/tabs/*.js`, `web/modals/*.js`.

Shared files (touched only in P0): `compose.yml`, `sim/pyproject.toml`,
`sim/CONTRACTS.md`, `sim/AGENT_RUNBOOK.md`.

---

## 5. Common pitfalls

- **`POST /api/create-run/` returns 400 "vehicle does not exist"** — seeding
  step (§2) was skipped.
- **No telemetry visible to databus** — confirm `MQTT_CONSUMER_ENABLED=true`
  on `realtime-engine`, and that the simulator publishes to
  `host.docker.internal:1883`, not the standalone-profile local broker.
- **`current_run` Redis key unset** — consumer drops the ping silently. Run
  binder must mark the vehicle as bound *before* the simulator starts
  publishing motion (CONTRACTS.md §7.4).
- **Browser CORS error on `:8081`** — confirm `http://localhost:8080` is in
  the FastAPI `allow_origins` list (CONTRACTS.md §2).

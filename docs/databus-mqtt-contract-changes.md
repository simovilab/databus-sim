# databus MQTT contract — what the simulator must publish

**Date:** 2026-06-08
**Source plan:** `databus` repo → `plans/redis-gtfs-rt-entity-division.md`
**databus status:** ✅ **IMPLEMENTED** on branch `feat/run-lifecycle-mqtt`
(commits `549de89`..`77937ec`). The consumer side is live; the simulator is
**still on the old contract** and needs migrating (see §4).

> This file supersedes the earlier "heads-up / not yet implemented" version. The
> contract below is what databus's MQTT consumer (`backend/realtime_engine/mqtt.py`)
> actually does now. The typed field contracts are owned by
> `backend/runs/domain/telemetry/{position,occupancy,keys}.py` — treat those as the
> source of truth if anything here drifts.

## 1. The boundary databus enforces (observation vs. interpretation)

MQTT carries **only what a real vehicle can sense by itself**. Anything needing
the GTFS schedule, shape geometry, stop locations, run→trip assignment, or the
fleet view is computed **server-side** in databus. The simulator emulates the
edge device, so it must be a *dumb sensor emitter*.

## 2. What databus subscribes to (and ignores)

`mqtt._on_connect` subscribes to **exactly two leaves**:

- `transit/vehicle/+/position`
- `transit/vehicle/+/occupancy`

It **no longer subscribes to `transit/vehicle/+/progression`**. The sim may keep
publishing `progression` — it is silently ignored (dropped at debug as an
"unknown leaf"). There is **no longer any value** in publishing it.

## 3. The wire contract databus expects

### `transit/vehicle/<id>/position`  (edge — kept as-is)
JSON object:

| field | type | required | notes |
|---|---|---|---|
| `latitude` | float | **yes** | payload dropped if missing/non-numeric |
| `longitude` | float | **yes** | payload dropped if missing/non-numeric |
| `bearing` | float | no | |
| `speed` | float | no | |
| `odometer` | float | no | |
| `timestamp` | int | no | Unix epoch seconds. databus lifts this to the GTFS-RT top-level `VehiclePosition.timestamp` (the `Position` sub-message has no timestamp). |

The sim's current `position` payload (`kinematics.build_vehicle_payloads`) already
matches this **exactly** — no change needed.

### `transit/vehicle/<id>/occupancy`  (edge — drop the enum)
JSON object:

| field | type | required | notes |
|---|---|---|---|
| `occupancy_percentage` | int | no | raw measurement (meter bars / CV camera) |
| `occupancy_count` | int | no | raw measurement; databus stores it but does **not** emit it in the GTFS-RT VehiclePosition (not a field there) |
| `occupancy_status` | — | **DO NOT SEND** | **Server policy.** databus recomputes the enum from `occupancy_percentage` and **discards any value the edge sends.** |

**Occupancy bucketing policy** (databus `occupancy.classify_status`, so tests can
assert the server-computed enum):

| `occupancy_percentage` | server `occupancy_status` |
|---|---|
| `None` / absent | `NO_DATA_AVAILABLE` |
| `< 20` | `MANY_SEATS_AVAILABLE` |
| `< 50` | `FEW_SEATS_AVAILABLE` |
| `< 80` | `STANDING_ROOM_ONLY` |
| `>= 80` | `FULL` |

(These thresholds were adopted from the sim's own `occupancy_status()`, so the
mapping is unchanged — it just now lives server-side. Only 4 of 9 GTFS enum
values are covered; the rest await product input.)

## 4. Simulator migration checklist

Target: publish **only** `position` and `occupancy`, with no server-owned fields.

- [ ] **Stop publishing the `progression` leaf** entirely. In
  `kinematics.build_vehicle_payloads` (`simulator_app/domain/kinematics.py:302-327`),
  drop the `progression` dict from the returned payloads and stop emitting it in
  `runtime.py:288-291`. The map-matching that produces it
  (`current_status`/`stop_id`/`current_stop_sequence`, `STOP_RADIUS_M` /
  `INCOMING_AT_RADIUS_M`) is no longer consumed by databus.
- [ ] **Drop `occupancy_status`** from the `occupancy` payload
  (`kinematics.py:318-322`). Keep `occupancy_percentage`; optionally add
  `occupancy_count`. You can keep the `occupancy_status()` helper for the
  browser UI if useful, but don't put it on the wire.
- [ ] **Leave `position` unchanged.**
- [ ] Update sim tests that assert 3 leaves / `occupancy_status` on the wire
  (`tests/test_kinematics.py:76,140,153`, `tests/test_tick_loop.py:82,86`).

### Sequencing note (this is now safe)
The earlier version of this doc said "keep publishing `progression` until databus
has real server-side map-matching." **That caveat is moot:** databus already
stopped subscribing to `progression` and now computes stop status server-side.
That server step is currently a **seam** — `compute_stop_status` always returns
`current_status = IN_TRANSIT_TO` (real map-matching, the §11 port of this sim's
`kinematics.py`, is the deferred follow-up). So during this interim, end-to-end
`current_status` is seam-quality regardless of what the sim publishes. Removing
the sim's `progression` leaf changes nothing downstream.

## 5. How databus ingests (preconditions for any end-to-end test)

`mqtt._handle_telemetry` does, in order:

1. Parse JSON; **non-JSON payloads are dropped** (warning).
2. `run_id = GET vehicle:<id>:current_run`. **If the vehicle has no active run,
   the telemetry is dropped** (debug). ← *the #1 gotcha for tests.*
3. Route by leaf through the typed contract; **invalid payloads are dropped**
   (e.g. `position` missing lat/lon) without touching `last_seen` or detection.
4. On success: write the typed hash, `SET runs:last_seen:<run_id>`, and call the
   detection dispatcher.

Resulting Redis state written by ingestion:
- `vehicle:<id>:position` (hash) — typed position
- `vehicle:<id>:occupancy` (hash) — `occupancy_percentage`/`occupancy_count` +
  server-computed `occupancy_status`

Server-side (written elsewhere, not by MQTT):
- `run:<id>:trip` — by the lifecycle action on run start
- `run:<id>:vehicle_stop_status` — by the progression seam after each position
  (`IN_TRANSIT_TO`)
- `vehicle:<id>:metadata` — by the lifecycle action

## 6. Writing a test on the sim side

Two useful levels:

**A. Contract/unit test (no databus running).** Assert the sim's published
payloads conform to §3: `position` has `latitude`+`longitude` (+ optional
bearing/speed/odometer/timestamp), `occupancy` has `occupancy_percentage` and
**no** `occupancy_status`, and the `progression` leaf is **not** emitted. This is
the cheapest guard against drift and is fully in the sim repo.

**B. End-to-end against databus.** To get databus to accept telemetry and emit a
feed, the preconditions in §5 must hold — a run must be **assigned and in
progress** for the vehicle. Either:
- drive databus's run lifecycle so it sets `vehicle:<id>:current_run`,
  `run:<id>` (with a `vehicle` field), and adds the run to `runs:in_progress`; or
- seed those Redis keys directly in the test harness before publishing.

Then publish a `position` (and `occupancy`) message on
`transit/vehicle/<id>/{position,occupancy}` and assert:
- `vehicle:<id>:position` hash appears with the typed values;
- `vehicle:<id>:occupancy` hash has the **server-recomputed** `occupancy_status`
  per the §3 table (e.g. `occupancy_percentage=40` → `FEW_SEATS_AVAILABLE`);
- after running databus's `build_vehicle_positions` task, the emitted
  `vehicle_positions.json`/`.pb` parses as a GTFS-RT `FeedMessage` with the
  vehicle's entity (timestamp at the VehiclePosition level, not inside
  `position`; `current_status = IN_TRANSIT_TO` from the seam).

The sim's `kinematics.py` remains the **reference oracle** for the eventual
databus map-matching port (§11 of the databus plan): when that port lands, it is
"good enough" when, fed the sim's own position stream, it reproduces the sim's
`current_status` / `current_stop_sequence` within tolerance.

## 7. Also note

- The `control` plane occupancy override (`simulator_app/domain/control.py`) is
  unaffected — it still drives `occupancy_percentage`, which is exactly the raw
  field databus wants.
- Not in scope: stop-time-updates; congestion (`run:<id>:congestion_level` is
  reserved but databus has no producer yet — don't send congestion on the wire).

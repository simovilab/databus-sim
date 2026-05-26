# SIMOVI Simulator UI — Manual Test Plan

Target browsers: Chrome 124+, Safari 17+. Firefox is best-effort.

## Prerequisites

- Full stack running per `sim/AGENT_RUNBOOK.md` (databus + simulator + web).
- `mosquitto-clients` available on the host for CLI verification steps.
- Databus seeded with GTFS fixture, operator `op-001`, and vehicles `unit-01`..`unit-06`.

---

## 1. Boot & status badges

| Step | Expected |
|---|---|
| Open `http://localhost:8080` | Page loads; no console errors |
| Observe topbar | "SIMOVI Simulator" heading, two badges visible |
| broker badge | Turns green "broker: connected" within 2 s |
| sim badge    | Turns green "sim: online" within 5 s |
| Disconnect broker (stop nanomq) | badge goes gray "broker: disconnected" |
| Reconnect broker | badge goes green again within 3 s |

## 2. Layout

| Step | Expected |
|---|---|
| Desktop (>900 px) | Control panel on left (~65%), map on right (~35%) |
| Narrow viewport (<900 px) | Map on top (~40 vh), control panel below, scrollable |
| Tab strip visible | Fleet (active), Schedule, Operator, Runs |

## 3. Fleet tab (C1)

### 3.1 Initial render

```bash
# Publish a fleet snapshot
mosquitto_pub -h localhost -t 'sim/state/fleet' -r -m '{
  "vehicles": [
    {"vehicle_id":"unit-01","route_id":"bUCR_L1","transmitting":true,"moving":false,
     "speed_override":null,"occupancy_override":null,"bound_run_id":null,
     "bound_trip_id":null,"bound_shape_id":"hacia_artes","lifecycle_state":null,
     "progress_m":0,"last_state_change_at":"2026-05-19T16:00:00-06:00"}
  ],
  "published_at":"2026-05-19T16:00:00-06:00"
}'
```

| Step | Expected |
|---|---|
| Publish above payload | Card for unit-01 appears |
| Lifecycle chip | "idle" in gray |
| Transmitting toggle | Checked (on) |
| Moving toggle | Unchecked (off) |

### 3.2 Transmit toggle

```bash
mosquitto_sub -h localhost -t 'sim/control/+/+' -v &
```

| Step | Expected |
|---|---|
| Uncheck "Transmit" on unit-01 | `sim/control/unit-01/transmit {"on":false}` published |
| Check "Transmit" again | `sim/control/unit-01/transmit {"on":true}` published |

### 3.3 Moving toggle

| Step | Expected |
|---|---|
| Check "Moving" | `sim/control/unit-01/moving {"on":true}` published |

### 3.4 Speed slider

| Step | Expected |
|---|---|
| Drag speed slider to ~10.0 | Slider value label updates live |
| Release slider | `sim/control/unit-01/speed {"value":10.0}` published |
| Click "reset" next to speed | `sim/control/unit-01/speed {"value":null}` published |

### 3.5 Occupancy slider

Same as speed slider but topic is `sim/control/unit-01/occupancy`.

### 3.6 Start motion button

| Step | Expected |
|---|---|
| Click "Start motion" | `sim/control/global/start_run {"vehicle_id":"unit-01"}` published |

### 3.7 Jump to terminal

| Step | Expected |
|---|---|
| Click "Jump to terminal" | `sim/control/unit-01/jump_to_terminal {}` published |

### 3.8 Inject fault modal

| Step | Expected |
|---|---|
| Click "Inject fault" | Modal opens with 3 radio options + ticks field |
| Select "out_of_bounds", ticks=5, Submit | `sim/control/unit-01/inject_fault {"kind":"out_of_bounds","duration_ticks":5}` published |
| Open modal, press Escape | Modal closes, nothing published |
| Open modal, click backdrop | Modal closes, nothing published |

### 3.9 Dwell modal

| Step | Expected |
|---|---|
| Click "Dwell" | Modal opens with stop_id + ticks fields |
| Enter stop_id "bUCR_LA", ticks=3, Submit | `sim/control/unit-01/dwell {"stop_id":"bUCR_LA","ticks":3}` published |
| Leave stop_id blank, Submit | Publishes `{"stop_id":null,"ticks":...}` |

### 3.10 State chip updates

```bash
mosquitto_pub -h localhost -t 'sim/state/fleet' -r -m '{
  "vehicles":[{"vehicle_id":"unit-01","route_id":"bUCR_L1","transmitting":true,
   "moving":true,"speed_override":null,"occupancy_override":null,
   "bound_run_id":"abc-123","bound_trip_id":"trip-bUCR_L1-0001",
   "bound_shape_id":"hacia_artes","lifecycle_state":"InProgress",
   "progress_m":500,"last_state_change_at":"2026-05-19T16:05:00-06:00"}],
  "published_at":"2026-05-19T16:05:00-06:00"
}'
```

| Step | Expected |
|---|---|
| Publish above | Chip updates to "InProgress" (green) within 1 s |
| run-id chip | Shows "abc-123..." truncated |

## 4. Map pane (C1)

### 4.1 Vehicle marker

```bash
mosquitto_pub -h localhost -t 'transit/vehicle/unit-01/position' -m \
  '{"timestamp":1716148800,"latitude":9.937,"longitude":-84.051,"bearing":90,"speed":3.5,"odometer":100}'
```

| Step | Expected |
|---|---|
| Publish position (unit-01 transmitting=true in fleet) | Blue/gray dot appears at correct lat/lon |
| Toggle transmitting off (fleet update) | Marker disappears from map |

### 4.2 Route polyline

| Step | Expected |
|---|---|
| Fleet snapshot: unit-01 bound_run_id=non-null, lifecycle_state="InProgress" | bUCR_L1 route polylines appear on map |
| Update fleet: bound_run_id=null | Polylines disappear |

## 5. Schedule tab (C2)

| Step | Expected |
|---|---|
| Click "Schedule" tab | Table loads (GET /schedule call to :8081) |
| Table empty (no entries) | "No schedule entries" message shown |
| Click "Add run" | Schedule entry modal opens |
| Fill all fields, Submit | Row appears in table |
| Click "Edit" on a row | Modal opens pre-populated |
| Click "Remove" | Confirmation prompt; row removed on confirm |
| Click "Save" | PUT /schedule called; success alert |
| Click "Reload from disk" | Schedule reloads from disk |

### 5.1 Live status column

```bash
mosquitto_pub -h localhost -t 'sim/state/schedule' -r -m \
  '{"entries":[{"id":"sched-001","status":"confirmed","bound_run_id":"abc-123"}],"published_at":"..."}'
```

| Step | Expected |
|---|---|
| Publish above (entry id must match table row) | Status cell updates to "confirmed" in teal |

## 6. Operator tab (C2)

| Step | Expected |
|---|---|
| Click "Operator" tab | "Request Run" button + empty pending list |
| Click "Request Run" | Run request modal opens |
| Select vehicle, fill trip/operator, Submit | POST /api/create-run/ fires; run appears in pending list |
| Pending item shows | vehicle_id, trip_id, truncated run_id, lifecycle chip |
| Wait 2 s | Lifecycle chip polls /run/{id} and updates |
| Click "Confirm" on pending item | POST /api/update-run/ with event=run_confirmed_by_operator; chip → Confirmed |
| Click "Reject" on another item | Prompt for reason; POST /api/update-run/ with event=run_rejected |

## 7. Runs tab (C2)

| Step | Expected |
|---|---|
| Click "Runs" tab | Runs from localStorage + fleet bound_run_ids listed |
| Click a run row | Expands with Cancel / Interrupt / Short-turn buttons |
| Click "Cancel" | Confirm prompt; POST /api/update-run/ event=cancel_run |
| Click "Interrupt" | Confirm prompt; POST /api/update-run/ event=interrupt_run |
| Click "Short-turn" | Short-turn modal opens; enter stop_id; POST with event=short_turn_run |
| State transitions to Completed | Run item shows "Completed" chip; action buttons hidden |
| Click "Clear completed" | Completed runs removed from list |

## 8. Keyboard accessibility

| Step | Expected |
|---|---|
| Tab through fleet card controls | All controls reachable by keyboard |
| Open any modal, Tab through fields | All fields reachable |
| Modal open, press Escape | Modal closes cleanly |
| Modal open, Tab to Cancel, Enter | Modal closes |

## 9. Console hygiene

| Step | Expected |
|---|---|
| Open browser DevTools, Console tab | Zero errors, zero warnings at steady state |
| Publish malformed MQTT payload | Caught and logged as warning, no crash |

---

## Known limitations

- `GET /api/trips/` endpoint: if databus returns a non-standard shape for trip objects, the datalist autocomplete in run-request modal may be empty. User can type trip_id manually.
- `dwell` modal stop_id is free-text (no autocomplete from trip stops — no endpoint for that in v1).
- `short_turn` stop_id is free-text for the same reason.
- Firefox: `<dialog>` and `::backdrop` supported since FF 98; styling may differ slightly.

"""Sim-side progression oracle: derive stop status from true along-track progress.

Translated from databus/backend/runs/domain/progression/compute.py.
Uses the simulator's true ``progress_m`` (not GPS-projected) as the primary
progress anchor, so no polyline projection step is needed here.

Public surface
--------------
compute_stop_status(progress_m, observed_lat, observed_lon, shape_stops, *, speed, prev_state)
    → dict with ``current_status``, ``current_stop_sequence``, ``stop_id``.

Never raises — all exceptions produce the IN_TRANSIT_TO fallback.
Result is broadcast over Channels (internal transport) ONLY; never published to MQTT.
"""

from __future__ import annotations

from .geo import haversine_m


# ---------------------------------------------------------------------------
# Tunable constants (ported verbatim from databus)
# ---------------------------------------------------------------------------

# Vehicle is considered *at* a stop when within this radius.
STOP_RADIUS_M = 20.0

# Vehicle is *incoming* when within this radius and still approaching.
INCOMING_AT_RADIUS_M = 50.0

# Speed at or below this threshold (m/s) qualifies as stationary.
# 0.5 m/s ≈ 1.8 km/h — slow enough to be considered dwell.
STATIONARY_SPEED_MPS = 0.5


# ---------------------------------------------------------------------------
# Keys (constants matching databus vehicle_stop_status contract)
# ---------------------------------------------------------------------------

CURRENT_STATUS = "current_status"
CURRENT_STOP_SEQUENCE = "current_stop_sequence"
STOP_ID = "stop_id"


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


def compute_stop_status(
    progress_m: float,
    observed_lat: float,
    observed_lon: float,
    shape_stops: list[dict],
    *,
    speed: float | None = None,
    prev_state: dict | None = None,
) -> dict:
    """Derive a stop-status dict from the vehicle's true along-track progress.

    Parameters
    ----------
    progress_m:
        The vehicle's true along-track distance (metres) from the shape start,
        as maintained by the simulator (``v.progress_m``).
    observed_lat, observed_lon:
        The interpolated GPS coordinates for the current tick.
    shape_stops:
        Ordered list (ascending stop_sequence / progress_m) of stop dicts,
        each containing: ``stop_id``, ``stop_sequence``, ``lat``, ``lon``,
        ``progress_m``.  As produced by Phase 1's ``build_route_geometry``
        per shape.
    speed:
        Current vehicle speed in m/s (optional).  ``None`` is treated as
        unknown, which allows the STOPPED_AT rule to trigger.
    prev_state:
        Optional previous result dict.  Used to enforce a monotonic
        sequence floor so stop_sequence never regresses between ticks.

    Returns
    -------
    dict
        Always contains ``current_status`` (str).  Contains
        ``current_stop_sequence`` (int) and ``stop_id`` (str) when a stop
        candidate is resolved.

    Never raises.
    """
    try:
        return _compute_core(progress_m, observed_lat, observed_lon, shape_stops, speed, prev_state)
    except Exception:
        return _fallback(prev_state)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fallback(prev_state: dict | None) -> dict:
    """IN_TRANSIT_TO + carry-forward of prev_state seq/stop_id."""
    result: dict = {CURRENT_STATUS: "IN_TRANSIT_TO"}
    if prev_state is not None:
        seq = prev_state.get(CURRENT_STOP_SEQUENCE)
        if seq is not None:
            result[CURRENT_STOP_SEQUENCE] = seq
        stop_id = prev_state.get(STOP_ID)
        if stop_id is not None:
            result[STOP_ID] = stop_id
    return result


def _compute_core(
    progress_m: float,
    observed_lat: float,
    observed_lon: float,
    shape_stops: list[dict],
    speed: float | None,
    prev_state: dict | None,
) -> dict:
    """Core logic — may raise; callers wrap in try/except."""
    if not shape_stops:
        return _fallback(prev_state)

    # Step 1: pick the upcoming stop (smallest progress_m >= vehicle progress).
    candidate = _pick_upcoming_stop(shape_stops, progress_m)
    if candidate is None:
        return _fallback(prev_state)

    # Step 2: haversine distance from observed position to candidate stop.
    dist_m = haversine_m(
        observed_lat, observed_lon,
        float(candidate["lat"]), float(candidate["lon"]),
    )

    # Step 3: three-state radius rules (ported verbatim from databus).
    if dist_m <= STOP_RADIUS_M and (speed is None or speed <= STATIONARY_SPEED_MPS):
        status = "STOPPED_AT"
    elif dist_m <= INCOMING_AT_RADIUS_M and progress_m < candidate["progress_m"]:
        status = "INCOMING_AT"
    else:
        status = "IN_TRANSIT_TO"

    chosen_seq: int = int(candidate["stop_sequence"])
    chosen_stop_id: str = str(candidate["stop_id"])

    # Step 4: monotonic guard — never regress sequence.
    if prev_state is not None:
        prev_seq = prev_state.get(CURRENT_STOP_SEQUENCE)
        if prev_seq is not None:
            prev_seq = int(prev_seq)
            if chosen_seq < prev_seq:
                chosen_seq = prev_seq
                chosen_stop_id = str(prev_state.get(STOP_ID, chosen_stop_id))

    return {
        CURRENT_STATUS: status,
        CURRENT_STOP_SEQUENCE: chosen_seq,
        STOP_ID: chosen_stop_id,
    }


def _pick_upcoming_stop(
    stops: list[dict],
    progress_m: float,
) -> dict | None:
    """Return the next stop ahead of ``progress_m``, or the last stop.

    Stops must be ordered by stop_sequence / progress_m ascending.
    Returns the stop with the smallest ``progress_m`` that is >= ``progress_m``.
    If the vehicle has passed all stops, the last (terminal) stop is returned.
    Returns ``None`` only when ``stops`` is empty.
    """
    if not stops:
        return None

    for stop in stops:
        if float(stop["progress_m"]) >= progress_m:
            return stop

    # Vehicle has passed all stops — return the terminal stop.
    return stops[-1]

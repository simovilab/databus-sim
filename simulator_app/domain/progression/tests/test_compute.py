"""Pure unit tests for progression/compute.py — no Django/DB required.

Covers:
- STOPPED_AT: vehicle within STOP_RADIUS_M and speed <= STATIONARY_SPEED_MPS.
- INCOMING_AT: vehicle within INCOMING_AT_RADIUS_M and still approaching.
- IN_TRANSIT_TO: vehicle far from any stop.
- Monotonic guard: prev_state prevents sequence regression.
- Empty shape_stops: returns IN_TRANSIT_TO fallback with prev_state carry-forward.
- Never-raises contract: exception inside core returns fallback.
- _pick_upcoming_stop: selects correct candidate.
"""

from __future__ import annotations

import pytest

from simulator_app.domain.progression.compute import (
    CURRENT_STATUS,
    CURRENT_STOP_SEQUENCE,
    STOP_ID,
    compute_stop_status,
    _pick_upcoming_stop,
    STOP_RADIUS_M,
    INCOMING_AT_RADIUS_M,
    STATIONARY_SPEED_MPS,
)
from simulator_app.domain.progression.geo import haversine_m


# ---------------------------------------------------------------------------
# Test fixtures / helpers
# ---------------------------------------------------------------------------

# A simple straight-line shape with 3 stops.
# All stops placed at known coordinates near the equator for easy distance math.
# stop_sequence: 1-based, progress_m: monotonically increasing.

_STOP_LAT = 9.9350   # rough UCR area latitude
_STOP_LON = -84.0530  # rough UCR area longitude

# Each stop is ~30 m apart (south to north) so we can test radius rules.
# 30 m north ≈ 0.00027°  (1° lat ≈ 111 195 m → 30 / 111195 ≈ 0.000270°)
_DELTA_DEG = 30.0 / 111_195.0  # ~0.000270°


def _make_stops() -> list[dict]:
    """Three stops at ~0 m, ~30 m, ~60 m along the shape."""
    return [
        {
            "stop_id": "s1",
            "stop_sequence": 1,
            "lat": _STOP_LAT,
            "lon": _STOP_LON,
            "progress_m": 0.0,
        },
        {
            "stop_id": "s2",
            "stop_sequence": 2,
            "lat": _STOP_LAT + _DELTA_DEG,
            "lon": _STOP_LON,
            "progress_m": 30.0,
        },
        {
            "stop_id": "s3",
            "stop_sequence": 3,
            "lat": _STOP_LAT + 2 * _DELTA_DEG,
            "lon": _STOP_LON,
            "progress_m": 60.0,
        },
    ]


# ---------------------------------------------------------------------------
# STOPPED_AT
# ---------------------------------------------------------------------------


class TestStoppedAt:
    """Vehicle is within STOP_RADIUS_M and speed is at/below stationary threshold."""

    def test_near_stop_slow_speed(self):
        stops = _make_stops()
        # Vehicle at stop s2 location, progress just before s2.
        result = compute_stop_status(
            progress_m=28.0,
            observed_lat=stops[1]["lat"],
            observed_lon=stops[1]["lon"],
            shape_stops=stops,
            speed=STATIONARY_SPEED_MPS,
        )
        assert result[CURRENT_STATUS] == "STOPPED_AT"
        assert result[STOP_ID] == "s2"
        assert result[CURRENT_STOP_SEQUENCE] == 2

    def test_near_stop_zero_speed(self):
        stops = _make_stops()
        result = compute_stop_status(
            progress_m=28.0,
            observed_lat=stops[1]["lat"],
            observed_lon=stops[1]["lon"],
            shape_stops=stops,
            speed=0.0,
        )
        assert result[CURRENT_STATUS] == "STOPPED_AT"

    def test_near_stop_unknown_speed(self):
        """speed=None (unknown) should allow STOPPED_AT to trigger."""
        stops = _make_stops()
        result = compute_stop_status(
            progress_m=28.0,
            observed_lat=stops[1]["lat"],
            observed_lon=stops[1]["lon"],
            shape_stops=stops,
            speed=None,
        )
        assert result[CURRENT_STATUS] == "STOPPED_AT"

    def test_near_stop_fast_speed_not_stopped(self):
        """Fast vehicle within STOP_RADIUS_M should NOT get STOPPED_AT."""
        stops = _make_stops()
        result = compute_stop_status(
            progress_m=28.0,
            observed_lat=stops[1]["lat"],
            observed_lon=stops[1]["lon"],
            shape_stops=stops,
            speed=5.0,  # fast — above STATIONARY_SPEED_MPS
        )
        # Should be INCOMING_AT (within INCOMING_AT_RADIUS_M and approaching)
        # or IN_TRANSIT_TO — but NOT STOPPED_AT.
        assert result[CURRENT_STATUS] != "STOPPED_AT"


# ---------------------------------------------------------------------------
# INCOMING_AT
# ---------------------------------------------------------------------------


class TestIncomingAt:
    """Vehicle is within INCOMING_AT_RADIUS_M and its progress < stop's progress_m."""

    def test_approaching_within_50m(self):
        stops = _make_stops()
        # Place vehicle ~40 m south (behind) of s2. progress also before s2.
        offset_40m_deg = 40.0 / 111_195.0
        obs_lat = stops[1]["lat"] - offset_40m_deg
        obs_lon = stops[1]["lon"]
        # Verify actual distance is within INCOMING_AT_RADIUS_M.
        dist = haversine_m(obs_lat, obs_lon, stops[1]["lat"], stops[1]["lon"])
        assert dist < INCOMING_AT_RADIUS_M

        result = compute_stop_status(
            progress_m=15.0,  # before stop s2 at 30.0
            observed_lat=obs_lat,
            observed_lon=obs_lon,
            shape_stops=stops,
            speed=5.0,  # moving fast, not stationary
        )
        assert result[CURRENT_STATUS] == "INCOMING_AT"
        assert result[STOP_ID] == "s2"

    def test_incoming_at_not_triggered_when_past_all_stops(self):
        """When progress_m >= all stops' progress_m, last stop is the candidate.
        If already past the terminal stop's progress, and INCOMING_AT condition
        requires progress_m < stop.progress_m, the condition must fail."""
        stops = _make_stops()
        # Vehicle is past s3 (the last stop at 60.0 m) — candidate falls back to s3.
        # But since progress_m (70.0) >= s3.progress_m (60.0), INCOMING_AT is blocked.
        result = compute_stop_status(
            progress_m=70.0,  # past all stops
            observed_lat=stops[2]["lat"],
            observed_lon=stops[2]["lon"],
            shape_stops=stops,
            speed=5.0,
        )
        # INCOMING_AT requires progress_m < stop.progress_m, so it must not fire.
        assert result[CURRENT_STATUS] != "INCOMING_AT"


# ---------------------------------------------------------------------------
# IN_TRANSIT_TO
# ---------------------------------------------------------------------------


class TestInTransitTo:
    """Vehicle is far from the upcoming stop — normal cruising."""

    def test_far_from_stop(self):
        stops = _make_stops()
        # Vehicle at a lat far from any stop.
        far_lat = _STOP_LAT - 0.01  # ~1.1 km south
        far_lon = _STOP_LON
        result = compute_stop_status(
            progress_m=0.0,
            observed_lat=far_lat,
            observed_lon=far_lon,
            shape_stops=stops,
            speed=8.0,
        )
        assert result[CURRENT_STATUS] == "IN_TRANSIT_TO"

    def test_returns_required_keys(self):
        stops = _make_stops()
        result = compute_stop_status(
            progress_m=5.0,
            observed_lat=_STOP_LAT - 0.005,
            observed_lon=_STOP_LON,
            shape_stops=stops,
        )
        assert CURRENT_STATUS in result
        # Should also have stop_id and stop_sequence for a resolvable candidate.
        assert STOP_ID in result
        assert CURRENT_STOP_SEQUENCE in result


# ---------------------------------------------------------------------------
# Monotonic guard
# ---------------------------------------------------------------------------


class TestMonotonicGuard:
    """prev_state enforces monotonic floor — sequence must not regress."""

    def test_guard_prevents_regression(self):
        stops = _make_stops()
        # prev_state has sequence 3 (s3).
        prev = {CURRENT_STOP_SEQUENCE: 3, STOP_ID: "s3"}

        # Now vehicle is near s2 (seq=2) — lower than prev.
        result = compute_stop_status(
            progress_m=28.0,
            observed_lat=stops[1]["lat"],
            observed_lon=stops[1]["lon"],
            shape_stops=stops,
            speed=0.0,
            prev_state=prev,
        )
        # Even if naturally STOPPED_AT s2, monotonic guard holds sequence at 3.
        assert result[CURRENT_STOP_SEQUENCE] == 3
        assert result[STOP_ID] == "s3"

    def test_guard_allows_advance(self):
        stops = _make_stops()
        # prev_state has sequence 1 — vehicle advances to s2.
        prev = {CURRENT_STOP_SEQUENCE: 1, STOP_ID: "s1"}

        result = compute_stop_status(
            progress_m=28.0,
            observed_lat=stops[1]["lat"],
            observed_lon=stops[1]["lon"],
            shape_stops=stops,
            speed=0.0,
            prev_state=prev,
        )
        # Should advance to seq=2.
        assert result[CURRENT_STOP_SEQUENCE] == 2
        assert result[STOP_ID] == "s2"

    def test_guard_no_prev_state(self):
        """Without prev_state the guard is not applied — fresh sequence is used."""
        stops = _make_stops()
        result = compute_stop_status(
            progress_m=28.0,
            observed_lat=stops[1]["lat"],
            observed_lon=stops[1]["lon"],
            shape_stops=stops,
            speed=0.0,
            prev_state=None,
        )
        assert result[CURRENT_STOP_SEQUENCE] == 2


# ---------------------------------------------------------------------------
# Empty shape_stops → IN_TRANSIT_TO fallback
# ---------------------------------------------------------------------------


class TestEmptyShapeStops:
    def test_empty_stops_returns_in_transit_to(self):
        result = compute_stop_status(
            progress_m=100.0,
            observed_lat=_STOP_LAT,
            observed_lon=_STOP_LON,
            shape_stops=[],
        )
        assert result[CURRENT_STATUS] == "IN_TRANSIT_TO"
        assert STOP_ID not in result
        assert CURRENT_STOP_SEQUENCE not in result

    def test_empty_stops_carries_forward_prev_seq(self):
        prev = {CURRENT_STOP_SEQUENCE: 5, STOP_ID: "stop-X"}
        result = compute_stop_status(
            progress_m=100.0,
            observed_lat=_STOP_LAT,
            observed_lon=_STOP_LON,
            shape_stops=[],
            prev_state=prev,
        )
        assert result[CURRENT_STATUS] == "IN_TRANSIT_TO"
        assert result[CURRENT_STOP_SEQUENCE] == 5
        assert result[STOP_ID] == "stop-X"


# ---------------------------------------------------------------------------
# Never-raises contract
# ---------------------------------------------------------------------------


class TestNeverRaises:
    def test_bad_stop_data_returns_fallback(self):
        """Corrupt stop data must not propagate exceptions — fallback is returned."""
        bad_stops = [{"stop_id": "bad", "stop_sequence": 1}]  # missing lat/lon/progress_m
        result = compute_stop_status(
            progress_m=0.0,
            observed_lat=_STOP_LAT,
            observed_lon=_STOP_LON,
            shape_stops=bad_stops,
        )
        assert result[CURRENT_STATUS] == "IN_TRANSIT_TO"

    def test_nan_progress_returns_fallback(self):
        import math
        result = compute_stop_status(
            progress_m=math.nan,
            observed_lat=_STOP_LAT,
            observed_lon=_STOP_LON,
            shape_stops=_make_stops(),
        )
        # nan comparisons may return unexpected results — but we must not raise.
        assert CURRENT_STATUS in result

    def test_never_raises_with_none_prev_state(self):
        result = compute_stop_status(
            progress_m=0.0,
            observed_lat=_STOP_LAT,
            observed_lon=_STOP_LON,
            shape_stops=[],
            prev_state=None,
        )
        assert result[CURRENT_STATUS] == "IN_TRANSIT_TO"


# ---------------------------------------------------------------------------
# _pick_upcoming_stop
# ---------------------------------------------------------------------------


class TestPickUpcomingStop:
    def test_returns_none_for_empty(self):
        assert _pick_upcoming_stop([], 0.0) is None

    def test_returns_first_stop_when_at_start(self):
        stops = _make_stops()
        result = _pick_upcoming_stop(stops, 0.0)
        assert result is not None
        assert result["stop_id"] == "s1"

    def test_returns_correct_upcoming_stop(self):
        stops = _make_stops()
        # Progress between s1 (0) and s2 (30) → upcoming is s2.
        result = _pick_upcoming_stop(stops, 15.0)
        assert result is not None
        assert result["stop_id"] == "s2"

    def test_returns_last_stop_when_past_all(self):
        stops = _make_stops()
        result = _pick_upcoming_stop(stops, 9999.0)
        assert result is not None
        assert result["stop_id"] == "s3"

    def test_returns_stop_at_exact_progress(self):
        stops = _make_stops()
        # Exactly at s2's progress_m.
        result = _pick_upcoming_stop(stops, 30.0)
        assert result is not None
        assert result["stop_id"] == "s2"

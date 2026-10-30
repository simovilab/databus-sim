"""Pure unit tests for progression/shapes.py — no Django/DB required.

Ported and adapted from databus/backend/runs/domain/progression/tests/test_shapes.py,
with additional sim-specific tests for build_route_geometry using the real
shapes.json data file.

Tests cover:
- build_polyline: cumulative distances, empty input, single point, km→m
  conversion, fallback behaviour, _validate_dists_m.
- assign_stops_monotonic: empty, single stop, straight shape, loop-back shape.
- build_stops: progress_m + cross_track_m present for all stops.
- build_route_geometry: integration tests against real shapes.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simulator_app.domain.progression.geo import haversine_m
from simulator_app.domain.progression.shapes import (
    _validate_dists_m,
    assign_stops_monotonic,
    build_polyline,
    build_route_geometry,
    build_stops,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SHAPES_JSON = (
    Path(__file__).resolve().parents[4] / "simulator_app" / "static" / "shapes.json"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _straight_shape_points(n: int = 5) -> list[tuple[float, float, float]]:
    """n points along the prime meridian from (0,0) to (n-1 deg, 0).

    All cum_dist_km set to 0.0 → forces haversine fallback in build_polyline.
    """
    return [(float(i), 0.0, 0.0) for i in range(n)]


def _straight_stops(n: int = 3) -> list[dict]:
    """n stops distributed along the prime meridian."""
    return [
        {"stop_id": f"S{i}", "stop_sequence": i + 1, "lat": float(i), "lon": 0.0}
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# build_polyline
# ---------------------------------------------------------------------------


class TestBuildPolyline:
    def test_empty_input(self):
        assert build_polyline([]) == []

    def test_single_point_cum_zero(self):
        result = build_polyline([(10.0, 20.0, 0.0)])
        assert len(result) == 1
        lat, lon, cum = result[0]
        assert lat == pytest.approx(10.0)
        assert lon == pytest.approx(20.0)
        assert cum == pytest.approx(0.0)

    def test_two_points_cumulative_haversine_fallback(self):
        """All-zero cum_dist_km → haversine fallback."""
        raw = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]
        result = build_polyline(raw)
        assert result[0][2] == pytest.approx(0.0)
        expected_d = haversine_m(0.0, 0.0, 1.0, 0.0)
        assert result[1][2] == pytest.approx(expected_d, rel=1e-9)

    def test_three_points_matches_manual_haversine(self):
        raw = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]
        result = build_polyline(raw)
        d01 = haversine_m(0.0, 0.0, 1.0, 0.0)
        d12 = haversine_m(1.0, 0.0, 2.0, 0.0)
        assert result[0][2] == pytest.approx(0.0)
        assert result[1][2] == pytest.approx(d01, rel=1e-9)
        assert result[2][2] == pytest.approx(d01 + d12, rel=1e-9)

    def test_cumulative_is_monotonically_increasing(self):
        raw = _straight_shape_points(5)
        result = build_polyline(raw)
        cums = [r[2] for r in result]
        for i in range(1, len(cums)):
            assert cums[i] > cums[i - 1]

    def test_preserves_lat_lon(self):
        raw = [(51.5, -0.1, 0.0), (51.6, -0.1, 0.0)]
        result = build_polyline(raw)
        assert result[0][0] == pytest.approx(51.5)
        assert result[0][1] == pytest.approx(-0.1)
        assert result[1][0] == pytest.approx(51.6)

    def test_uses_provided_km_dists_when_valid(self):
        """When cum_dist_km values form a valid (non-decreasing, within band)
        sequence, build_polyline uses them (converted to metres) rather than
        falling back to haversine."""
        # Build a reference haversine polyline first.
        raw_zero = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]
        hav_poly = build_polyline(raw_zero)
        hav_total_m = hav_poly[-1][2]
        # Provide valid km distances that are ~0.9× haversine total (within band).
        scale = 0.9
        km0 = 0.0
        km1 = hav_total_m * scale * 0.5 / 1000.0
        km2 = hav_total_m * scale / 1000.0
        raw_valid = [(0.0, 0.0, km0), (1.0, 0.0, km1), (2.0, 0.0, km2)]
        result = build_polyline(raw_valid)
        # First point normalised to 0.
        assert result[0][2] == pytest.approx(0.0, abs=1e-9)
        # Last point: provided distance (km→m, offset-normalised).
        expected_last = (km2 - km0) * 1000.0
        assert result[-1][2] == pytest.approx(expected_last, rel=1e-9)
        # Must differ from haversine total.
        assert result[-1][2] != pytest.approx(hav_total_m, rel=0.05)

    def test_none_cum_dist_triggers_fallback(self):
        """Any None in cum_dist_km triggers haversine fallback."""
        hav_poly = build_polyline([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])
        raw_none = [(0.0, 0.0, None), (1.0, 0.0, None)]
        result = build_polyline(raw_none)
        for a, b in zip(hav_poly, result):
            assert a[2] == pytest.approx(b[2], rel=1e-12)

    def test_first_point_always_zero(self):
        """Normalisation: even if first cum_dist_km > 0, result[0][2] == 0."""
        raw_zero = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]
        hav_poly = build_polyline(raw_zero)
        hav_total_km = hav_poly[-1][2] / 1000.0
        # Offset start by 0.5 km; scale within band.
        offset = 0.5
        km0 = offset
        km1 = offset + hav_total_km * 0.45
        km2 = offset + hav_total_km * 0.9
        raw_offset = [(0.0, 0.0, km0), (1.0, 0.0, km1), (2.0, 0.0, km2)]
        result = build_polyline(raw_offset)
        assert result[0][2] == pytest.approx(0.0, abs=1e-9)

    def test_accepts_list_triples(self):
        """Input as lists (as in shapes.json) rather than tuples."""
        raw = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
        result = build_polyline(raw)
        assert len(result) == 2
        assert result[0][2] == pytest.approx(0.0)
        assert result[1][2] > 0.0


# ---------------------------------------------------------------------------
# _validate_dists_m
# ---------------------------------------------------------------------------


class TestValidateDistsM:
    def test_too_short(self):
        assert _validate_dists_m([0.0, 100.0], 3, 200_000.0) is False

    def test_passes(self):
        assert _validate_dists_m([0.0, 100_000.0, 200_000.0], 3, 200_000.0) is True

    def test_ratio_below_band(self):
        # total = 50 000, haversine = 200 000 → ratio = 0.25 < 0.5.
        assert _validate_dists_m([0.0, 25_000.0, 50_000.0], 3, 200_000.0) is False

    def test_ratio_above_band(self):
        # total = 600 000, haversine = 200 000 → ratio = 3.0 > 2.0.
        assert _validate_dists_m([0.0, 300_000.0, 600_000.0], 3, 200_000.0) is False

    def test_non_monotonic(self):
        total = 200_000.0
        bad = [0.0, total * 0.8, total * 0.6]
        assert _validate_dists_m(bad, 3, total) is False

    def test_single_point_haversine_zero(self):
        # haversine_total == 0 → sanity band is skipped → passes if otherwise OK.
        assert _validate_dists_m([0.0], 1, 0.0) is True

    def test_none_entry_returns_false(self):
        assert _validate_dists_m([0.0, None, 200_000.0], 3, 200_000.0) is False  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# assign_stops_monotonic
# ---------------------------------------------------------------------------


class TestAssignStopsMonotonic:
    def test_empty_stops_returns_empty(self):
        poly = build_polyline([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)])
        assert assign_stops_monotonic([], poly) == []

    def test_single_stop(self):
        poly = build_polyline([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])
        stops = [{"stop_id": "A", "stop_sequence": 1, "lat": 0.0, "lon": 0.0}]
        result = assign_stops_monotonic(stops, poly)
        assert len(result) == 1
        assert result[0] == pytest.approx(0.0, abs=10.0)

    def test_monotonic_straight_shape(self):
        """Stops on a straight shape must get monotonically non-decreasing progress_m."""
        raw = [(float(i), 0.0, 0.0) for i in range(5)]
        poly = build_polyline(raw)
        stops = [
            {"stop_id": f"S{i}", "stop_sequence": i, "lat": float(i), "lon": 0.0}
            for i in range(5)
        ]
        result = assign_stops_monotonic(stops, poly)
        assert len(result) == 5
        for i in range(1, len(result)):
            assert result[i] >= result[i - 1], f"Not monotonic at index {i}: {result}"

    def test_no_segments_edge_case(self):
        """Single-point polyline (no segments) — all stops get the same progress."""
        poly = build_polyline([(5.0, 10.0, 0.0)])
        stops = [
            {"stop_id": "A", "stop_sequence": 1, "lat": 5.0, "lon": 10.0},
            {"stop_id": "B", "stop_sequence": 2, "lat": 5.001, "lon": 10.001},
        ]
        result = assign_stops_monotonic(stops, poly)
        assert len(result) == 2
        assert result[0] == pytest.approx(0.0, abs=1e-9)
        assert result[1] == pytest.approx(0.0, abs=1e-9)

    def test_loop_back_shape_dp_assigns_correct_pass(self):
        """Key regression: a shape that doubles back.

        Shape geometry (all on the equator, east-west):

            A(0,0) ---> B(0,1) ---> C(0,2) ---> D(0,1.5) ---> E(0,0.5)

        The route goes east to lon=2, then turns around heading west.
        Stop S3 at (0, 0.6) is physically close to the EARLY part (A→B) but
        is actually on the RETURN leg (D→E covers lon 1.5 → 0.5).

        DP monotonic assignment must keep S3 on the return leg, yielding a
        progress_m larger than S2's progress_m.
        """
        from simulator_app.domain.progression.geo import project_point_to_polyline

        raw = [
            (0.0, 0.0, 0.0),   # A — start
            (0.0, 1.0, 0.0),   # B — 1° east
            (0.0, 2.0, 0.0),   # C — 2° east (turn-around)
            (0.0, 1.5, 0.0),   # D — heading back west
            (0.0, 0.5, 0.0),   # E — end (close to start side)
        ]
        poly = build_polyline(raw)

        stops = [
            {"stop_id": "S1", "stop_sequence": 1, "lat": 0.0, "lon": 0.0},
            {"stop_id": "S2", "stop_sequence": 2, "lat": 0.0, "lon": 2.0},
            {"stop_id": "S3", "stop_sequence": 3, "lat": 0.0, "lon": 0.6},
        ]

        dp_result = assign_stops_monotonic(stops, poly)

        assert len(dp_result) == 3
        # Monotonically non-decreasing.
        for i in range(1, len(dp_result)):
            assert dp_result[i] >= dp_result[i - 1], (
                f"DP result not monotonic: {dp_result}"
            )
        # S1 is near the start — progress_m should be small (< 50 km).
        assert dp_result[0] < 50_000.0, f"S1 progress too large: {dp_result[0]}"
        # S2 is at the turn-around — progress_m ≈ haversine A→B→C ≈ 222 km.
        assert dp_result[1] > 150_000.0, f"S2 progress too small: {dp_result[1]}"
        # S3 must be assigned the RETURN leg, not the outbound.
        assert dp_result[2] > dp_result[1], (
            f"S3 ({dp_result[2]:.1f} m) should be > S2 ({dp_result[1]:.1f} m)"
        )
        # Contrast: naive global nearest-segment gives a WRONG small value.
        naive_proj = project_point_to_polyline(0.0, 0.6, poly)
        naive_s3_progress = naive_proj["progress_m"]
        assert naive_s3_progress < dp_result[2], (
            f"Expected naive ({naive_s3_progress:.1f} m) < DP ({dp_result[2]:.1f} m)"
        )


# ---------------------------------------------------------------------------
# build_stops
# ---------------------------------------------------------------------------


class TestBuildStops:
    def test_empty_input(self):
        poly = build_polyline(_straight_shape_points(3))
        assert build_stops([], poly) == []

    def test_single_stop_on_first_vertex(self):
        poly = build_polyline(_straight_shape_points(3))
        stops = [{"stop_id": "X", "stop_sequence": 1, "lat": 0.0, "lon": 0.0}]
        result = build_stops(stops, poly)
        assert len(result) == 1
        assert result[0]["progress_m"] == pytest.approx(0.0, abs=1.0)
        assert result[0]["stop_id"] == "X"
        assert result[0]["stop_sequence"] == 1

    def test_cross_track_m_present(self):
        """build_stops must include cross_track_m for each stop."""
        poly = build_polyline(_straight_shape_points(5))
        stops = _straight_stops(3)
        result = build_stops(stops, poly)
        assert all("cross_track_m" in s for s in result)

    def test_cross_track_m_non_negative(self):
        poly = build_polyline(_straight_shape_points(5))
        stops = _straight_stops(3)
        result = build_stops(stops, poly)
        assert all(s["cross_track_m"] >= 0.0 for s in result)

    def test_progress_m_present_for_all_stops(self):
        poly = build_polyline(_straight_shape_points(5))
        stops = _straight_stops(3)
        result = build_stops(stops, poly)
        assert all("progress_m" in s for s in result)

    def test_progress_m_monotonic_along_straight_shape(self):
        """Stops at equally-spaced latitudes along the prime meridian must
        have strictly increasing progress_m."""
        raw_pts = [(float(i), 0.0, 0.0) for i in range(5)]
        poly = build_polyline(raw_pts)
        stop_rows = [
            {"stop_id": f"S{i}", "stop_sequence": i, "lat": float(i), "lon": 0.0}
            for i in range(5)
        ]
        result = build_stops(stop_rows, poly)
        progs = [r["progress_m"] for r in result]
        for i in range(1, len(progs)):
            assert progs[i] > progs[i - 1], (
                f"progress not monotonic at index {i}: {progs}"
            )

    def test_stop_on_first_vertex_has_zero_cross_track(self):
        """A stop at the exact first vertex should have cross_track ≈ 0."""
        poly = build_polyline([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])
        stops = [{"stop_id": "A", "stop_sequence": 1, "lat": 0.0, "lon": 0.0}]
        result = build_stops(stops, poly)
        assert result[0]["cross_track_m"] == pytest.approx(0.0, abs=1.0)

    def test_output_contains_all_required_keys(self):
        poly = build_polyline(_straight_shape_points(3))
        stops = _straight_stops(2)
        result = build_stops(stops, poly)
        expected_keys = {"stop_id", "stop_sequence", "lat", "lon", "progress_m", "cross_track_m"}
        for s in result:
            assert set(s.keys()) == expected_keys


# ---------------------------------------------------------------------------
# build_route_geometry — integration tests using real shapes.json
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def shapes_data() -> dict:
    """Load the real shapes.json once for all integration tests."""
    assert _SHAPES_JSON.exists(), f"shapes.json not found at {_SHAPES_JSON}"
    return json.loads(_SHAPES_JSON.read_text())


class TestBuildRouteGeometry:
    """Integration tests using real UCR bus shapes.json data."""

    def _get_route(self, shapes_data: dict, route_id: str) -> dict:
        for r in shapes_data["routes"]:
            if r["route_id"] == route_id:
                return r
        raise KeyError(f"route_id {route_id!r} not in shapes.json")

    def test_bUCR_L1_produces_non_empty_shapes(self, shapes_data):
        route = self._get_route(shapes_data, "bUCR_L1")
        result = build_route_geometry(route, shapes_data["shapes"], shapes_data["stops"])
        assert len(result["shapes"]) > 0, "bUCR_L1 should have at least one shape entry"

    def test_bUCR_L1_produces_non_empty_merged_stops(self, shapes_data):
        route = self._get_route(shapes_data, "bUCR_L1")
        result = build_route_geometry(route, shapes_data["shapes"], shapes_data["stops"])
        assert len(result["stops"]) > 0, "bUCR_L1 should have a non-empty merged stops list"

    def test_bUCR_L2_produces_non_empty_shapes(self, shapes_data):
        route = self._get_route(shapes_data, "bUCR_L2")
        result = build_route_geometry(route, shapes_data["shapes"], shapes_data["stops"])
        assert len(result["shapes"]) > 0, "bUCR_L2 should have at least one shape entry"

    def test_bUCR_L2_produces_non_empty_merged_stops(self, shapes_data):
        route = self._get_route(shapes_data, "bUCR_L2")
        result = build_route_geometry(route, shapes_data["shapes"], shapes_data["stops"])
        assert len(result["stops"]) > 0, "bUCR_L2 should have a non-empty merged stops list"

    def test_shape_stops_ordered_by_non_decreasing_progress_m(self, shapes_data):
        """Each shape's stops list must have non-decreasing progress_m."""
        for route in shapes_data["routes"]:
            result = build_route_geometry(route, shapes_data["shapes"], shapes_data["stops"])
            for shape_entry in result["shapes"]:
                stops = shape_entry["stops"]
                progs = [s["progress_m"] for s in stops]
                for i in range(1, len(progs)):
                    assert progs[i] >= progs[i - 1], (
                        f"progress_m not non-decreasing in shape {shape_entry['shape_id']}: "
                        f"{progs}"
                    )

    def test_shape_stops_have_sequential_stop_sequence_starting_at_1(self, shapes_data):
        """stop_sequence must be 1, 2, 3, ... for each shape."""
        for route in shapes_data["routes"]:
            result = build_route_geometry(route, shapes_data["shapes"], shapes_data["stops"])
            for shape_entry in result["shapes"]:
                stops = shape_entry["stops"]
                if not stops:
                    continue
                sequences = [s["stop_sequence"] for s in stops]
                assert sequences[0] == 1, (
                    f"first stop_sequence should be 1 in shape {shape_entry['shape_id']}"
                )
                for i in range(1, len(sequences)):
                    assert sequences[i] == sequences[i - 1] + 1, (
                        f"stop_sequence not sequential in shape {shape_entry['shape_id']}: "
                        f"{sequences}"
                    )

    def test_every_returned_stop_id_exists_in_global_stops(self, shapes_data):
        """Every stop_id in a shape's stops list must exist in the global stops."""
        global_stop_ids = {s["stop_id"] for s in shapes_data["stops"]}
        for route in shapes_data["routes"]:
            result = build_route_geometry(route, shapes_data["shapes"], shapes_data["stops"])
            for shape_entry in result["shapes"]:
                for stop in shape_entry["stops"]:
                    assert stop["stop_id"] in global_stop_ids, (
                        f"stop_id {stop['stop_id']!r} not in global stops"
                    )

    def test_merged_stops_are_deduplicated(self, shapes_data):
        """The top-level merged stops list must have no duplicate stop_ids."""
        for route in shapes_data["routes"]:
            result = build_route_geometry(route, shapes_data["shapes"], shapes_data["stops"])
            stop_ids = [s["stop_id"] for s in result["stops"]]
            assert len(stop_ids) == len(set(stop_ids)), (
                f"Duplicate stop_ids in merged stops for route {route['route_id']}"
            )

    def test_result_has_required_top_level_keys(self, shapes_data):
        route = self._get_route(shapes_data, "bUCR_L1")
        result = build_route_geometry(route, shapes_data["shapes"], shapes_data["stops"])
        assert set(result.keys()) == {
            "route_id", "short_name", "color", "text_color", "shapes", "stops"
        }
        assert result["route_id"] == "bUCR_L1"
        assert result["short_name"] == route["short_name"]

    def test_shape_entries_have_required_keys(self, shapes_data):
        route = self._get_route(shapes_data, "bUCR_L1")
        result = build_route_geometry(route, shapes_data["shapes"], shapes_data["stops"])
        for shape_entry in result["shapes"]:
            assert set(shape_entry.keys()) == {"shape_id", "latlngs", "stops"}

    def test_latlngs_are_lat_lon_pairs(self, shapes_data):
        route = self._get_route(shapes_data, "bUCR_L1")
        result = build_route_geometry(route, shapes_data["shapes"], shapes_data["stops"])
        for shape_entry in result["shapes"]:
            for pair in shape_entry["latlngs"]:
                assert len(pair) == 2, "latlngs entries must be [lat, lon] pairs"
                lat, lon = pair
                assert -90 <= lat <= 90
                assert -180 <= lon <= 180

    def test_synthetic_far_stop_excluded_by_threshold(self, shapes_data):
        """A stop placed >1 km from all shapes should NOT appear in any shape's stops."""
        # Place a synthetic stop in the middle of the Pacific (far from UCR campus).
        far_stop = {
            "stop_id": "SYNTHETIC_FAR",
            "name": "Far Away",
            "lat": 0.0,
            "lon": -150.0,
        }
        extended_stops = shapes_data["stops"] + [far_stop]
        for route in shapes_data["routes"]:
            result = build_route_geometry(
                route,
                shapes_data["shapes"],
                extended_stops,
                cross_track_threshold_m=50.0,
            )
            for shape_entry in result["shapes"]:
                ids = [s["stop_id"] for s in shape_entry["stops"]]
                assert "SYNTHETIC_FAR" not in ids, (
                    f"Far synthetic stop should not appear in shape {shape_entry['shape_id']}"
                )

    def test_missing_shape_ids_are_skipped_gracefully(self, shapes_data):
        """shape_ids referencing missing keys in shapes dict are silently skipped."""
        route = dict(self._get_route(shapes_data, "bUCR_L1"))
        route = dict(route)
        route["shape_ids"] = ["NONEXISTENT_SHAPE_ID"] + route["shape_ids"]
        # Should not raise; the missing id is just skipped.
        result = build_route_geometry(route, shapes_data["shapes"], shapes_data["stops"])
        # Real shapes still processed.
        assert len(result["shapes"]) > 0

    def test_cross_track_threshold_zero_excludes_all_stops(self, shapes_data):
        """With threshold=0, no stop is close enough → every shape has empty stops."""
        for route in shapes_data["routes"]:
            result = build_route_geometry(
                route,
                shapes_data["shapes"],
                shapes_data["stops"],
                cross_track_threshold_m=0.0,
            )
            for shape_entry in result["shapes"]:
                assert shape_entry["stops"] == [], (
                    f"With threshold=0, stops should be empty for shape "
                    f"{shape_entry['shape_id']}"
                )

"""Per-shape geometry: build polylines and project stops for the simulator.

Translated from databus/backend/runs/domain/progression/shapes.py, adapted
to the simulator's shapes.json data format (cumulative distances already
provided in kilometres as the 3rd element of each shape point).

Public surface
--------------
build_polyline(points)               — sim shape points → cumulative-m polyline.
assign_stops_monotonic(stop_rows, polyline) — DP monotonic stop projection.
build_stops(stop_rows, polyline)     — stops with progress_m + cross_track_m.
build_route_geometry(route, shapes, stops)  — full route geometry dict.
"""

from __future__ import annotations

from .geo import (
    haversine_m,
    project_point_to_polyline,
    project_point_to_segment,
)


# ---------------------------------------------------------------------------
# Pure builders
# ---------------------------------------------------------------------------


def build_polyline(
    points: list,
) -> list[tuple[float, float, float]]:
    """Convert sim shape points to a cumulative-distance polyline in metres.

    Parameters
    ----------
    points:
        List of ``(lat, lon, cum_dist_km)`` triples (or lists) as stored in
        the simulator's ``shapes.json``.  The 3rd element is the cumulative
        distance in **kilometres** (monotonic non-decreasing, pre-computed).

    Returns
    -------
    List of ``(lat, lon, cum_dist_m)`` tuples.  The first point is always
    normalised to ``cum_dist_m = 0.0``.  Returns ``[]`` for empty input.

    Fallback
    --------
    If the provided cum_dist_km values are unusable (None entries, or the
    sequence is not non-decreasing) the function falls back to computing
    cumulative haversine distances from scratch.
    """
    if not points:
        return []

    # Extract coordinates.
    lats: list[float] = []
    lons: list[float] = []
    raw_kms: list[float | None] = []
    for pt in points:
        lats.append(float(pt[0]))
        lons.append(float(pt[1]))
        raw_kms.append(None if pt[2] is None else float(pt[2]))

    n = len(lats)

    # Always compute haversine cumulative as fallback.
    hav_cum: list[float] = [0.0]
    for i in range(1, n):
        hav_cum.append(hav_cum[-1] + haversine_m(lats[i - 1], lons[i - 1], lats[i], lons[i]))

    # Try to use provided km distances.
    provided_dists_m: list[float] | None = None
    if all(d is not None for d in raw_kms):
        dists_m = [d * 1000.0 for d in raw_kms]  # type: ignore[operator]
        haversine_total = hav_cum[-1]
        if _validate_dists_m(dists_m, n, haversine_total):
            provided_dists_m = dists_m

    if provided_dists_m is not None:
        offset = provided_dists_m[0]
        return [(lats[i], lons[i], provided_dists_m[i] - offset) for i in range(n)]

    # Fallback: use haversine cumulative.
    return [(lats[i], lons[i], hav_cum[i]) for i in range(n)]


def _validate_dists_m(
    dists_m: list[float],
    n_points: int,
    haversine_total_m: float,
) -> bool:
    """Return True iff *dists_m* passes all usability checks.

    Translated verbatim from databus shapes.py._validate_dists_m.
    """
    # 1. Same length.
    if len(dists_m) != n_points:
        return False

    # 2. No None / missing entries (caller guarantees this, but guard anyway).
    if any(d is None for d in dists_m):
        return False

    # 3. Non-decreasing.
    for i in range(1, len(dists_m)):
        if float(dists_m[i]) < float(dists_m[i - 1]):
            return False

    # 4. Sanity band: total must be within [0.5, 2.0] × haversine total.
    # Guard against single-point polyline (total == 0).
    total = float(dists_m[-1]) - float(dists_m[0])
    if haversine_total_m > 0.0:
        ratio = total / haversine_total_m
        if not (0.5 <= ratio <= 2.0):
            return False

    return True


def assign_stops_monotonic(
    stop_rows: list[dict],
    polyline: list[tuple[float, float, float]],
) -> list[float]:
    """Assign a ``progress_m`` to each stop using DP monotonic projection.

    Places stops in order along the polyline with a hard forward-monotonic
    constraint (segment index of stop k must be >= segment index of stop k-1).
    This correctly handles loop-back / doubling-back shapes where a late stop
    is physically close to an early segment — independent per-stop
    nearest-segment projection would snap it to the wrong pass.

    Algorithm — Viterbi / prefix-min recurrence (ported verbatim from databus):

    For K stops and M segments (polyline has M+1 points):

        cost[k][j]  = cross_track_m of stop k onto segment j
        pos[k][j]   = progress_m of stop k onto segment j

        DP[0][j]    = cost[0][j]
        DP[k][j]    = cost[k][j]  +  min_{j' <= j}  DP[k-1][j']
        back[k][j]  = argmin_{j' <= j} DP[k-1][j']

    Final clamp enforces non-decreasing progress_m within the same segment
    (guards numerical noise when two consecutive stops land on the same segment).

    Parameters
    ----------
    stop_rows:
        Stops ordered by sequence ascending.  Each dict must have ``lat`` and
        ``lon`` keys.
    polyline:
        Cumulative polyline as returned by ``build_polyline``.

    Returns
    -------
    List of ``float`` — one ``progress_m`` per stop, same order as *stop_rows*.
    Empty list when *stop_rows* is empty.
    """
    K = len(stop_rows)
    if K == 0:
        return []

    M = len(polyline) - 1  # number of segments
    if M <= 0:
        # No segments — every stop snaps to the single (or absent) point.
        fallback = polyline[0][2] if polyline else 0.0
        return [fallback] * K

    # Pre-compute cost and pos matrices: shape (K, M).
    cost: list[list[float]] = []
    pos: list[list[float]] = []

    for row in stop_rows:
        lat, lon = row["lat"], row["lon"]
        c_row: list[float] = []
        p_row: list[float] = []
        for j in range(M):
            pm, ct = project_point_to_segment(lat, lon, polyline[j], polyline[j + 1])
            c_row.append(ct)
            p_row.append(pm)
        cost.append(c_row)
        pos.append(p_row)

    # DP — O(K * M) using a running prefix minimum to avoid inner O(M) loop.
    INF = float("inf")
    dp: list[list[float]] = [[INF] * M for _ in range(K)]
    back: list[list[int]] = [[-1] * M for _ in range(K)]

    # Initialise first stop.
    for j in range(M):
        dp[0][j] = cost[0][j]
        back[0][j] = j

    # Fill rows k = 1..K-1.
    for k in range(1, K):
        # Running prefix min of dp[k-1] up to and including j.
        prefix_min = INF
        prefix_arg = 0
        for j in range(M):
            if dp[k - 1][j] < prefix_min:
                prefix_min = dp[k - 1][j]
                prefix_arg = j
            dp[k][j] = cost[k][j] + prefix_min
            back[k][j] = prefix_arg

    # Backtrack: find best final segment for the last stop.
    best_j = int(min(range(M), key=lambda j: dp[K - 1][j]))

    # Recover segment assignments via backtrack table.
    segments: list[int] = [0] * K
    segments[K - 1] = best_j
    for k in range(K - 2, -1, -1):
        segments[k] = back[k + 1][segments[k + 1]]

    # Read off progress_m for each stop from its assigned segment.
    progress: list[float] = [pos[k][segments[k]] for k in range(K)]

    # Final clamp: enforce non-decreasing (handles same-segment inversions).
    for k in range(1, K):
        if progress[k] < progress[k - 1]:
            progress[k] = progress[k - 1]

    return progress


def build_stops(
    stop_rows: list[dict],
    polyline: list[tuple[float, float, float]],
) -> list[dict]:
    """Project each stop onto the polyline and return enriched stop dicts.

    Uses DP-based monotonic assignment (``assign_stops_monotonic``) to
    correctly handle loop-back / doubling-back shapes.

    Parameters
    ----------
    stop_rows:
        List of dicts, each with keys ``stop_id`` (str), ``stop_sequence``
        (int), ``lat`` (float), ``lon`` (float).  Must be ordered by sequence
        ascending.
    polyline:
        Cumulative polyline as returned by ``build_polyline``.

    Returns
    -------
    List of dicts (same order as input) with the additional keys
    ``progress_m`` (float) and ``cross_track_m`` (float).
    """
    progress_list = assign_stops_monotonic(stop_rows, polyline)
    result = []
    for row, pm in zip(stop_rows, progress_list):
        proj = project_point_to_polyline(row["lat"], row["lon"], polyline)
        result.append(
            {
                "stop_id": row["stop_id"],
                "stop_sequence": row["stop_sequence"],
                "lat": row["lat"],
                "lon": row["lon"],
                "progress_m": pm,
                "cross_track_m": proj["cross_track_m"],
            }
        )
    return result


# ---------------------------------------------------------------------------
# Route geometry builder (sim-specific)
# ---------------------------------------------------------------------------


def build_route_geometry(
    route: dict,
    shapes: dict,
    stops: list[dict],
    *,
    cross_track_threshold_m: float = 50.0,
) -> dict:
    """Build a complete route geometry dict from shapes.json data.

    Parameters
    ----------
    route:
        A route entry from ``shapes.json``; must have keys ``route_id``,
        ``short_name``, ``color``, ``text_color``, and ``shape_ids``.
    shapes:
        The ``shapes`` dict from ``shapes.json``: shape_id → list of
        ``[lat, lon, cum_dist_km]`` points.
    stops:
        The flat global stops list from ``shapes.json``: each entry has
        ``stop_id``, ``name``, ``lat``, ``lon``.
    cross_track_threshold_m:
        Maximum perpendicular distance (metres) for a stop to be considered
        a member of a shape.  Default: 50 m.

    Returns
    -------
    dict with keys:
        ``route_id``   (str)
        ``short_name`` (str)
        ``color``      (str)
        ``text_color`` (str)
        ``shapes``     list of shape dicts:
            ``shape_id``  (str)
            ``latlngs``   list of ``[lat, lon]`` pairs
            ``stops``     list of stop dicts:
                ``stop_id``, ``name``, ``lat``, ``lon``,
                ``stop_sequence`` (1-based), ``progress_m``
        ``stops``      merged unique stop list across all shapes
                       (dedup by stop_id, stable insertion order):
            ``stop_id``, ``name``, ``lat``, ``lon``
    """
    shape_entries: list[dict] = []
    merged_stops_by_id: dict[str, dict] = {}

    for shape_id in route.get("shape_ids", []):
        if shape_id not in shapes:
            continue

        raw_points = shapes[shape_id]
        polyline = build_polyline(raw_points)

        latlngs = [[pt[0], pt[1]] for pt in polyline]

        # Find member stops: project each global stop and keep those within threshold.
        candidates: list[tuple[float, dict]] = []
        for stop in stops:
            proj = project_point_to_polyline(stop["lat"], stop["lon"], polyline)
            if proj["cross_track_m"] <= cross_track_threshold_m:
                candidates.append((proj["progress_m"], stop))

        # Order by progress_m for monotonic assignment.
        candidates.sort(key=lambda x: x[0])

        # Build stop_rows for build_stops (assign stop_sequence 1..n).
        stop_rows = [
            {
                "stop_id": s["stop_id"],
                "stop_sequence": idx + 1,
                "lat": s["lat"],
                "lon": s["lon"],
            }
            for idx, (_, s) in enumerate(candidates)
        ]

        built = build_stops(stop_rows, polyline)

        # Enrich with name from the global stops dict for output.
        stop_name_map = {s["stop_id"]: s.get("name", "") for s in stops}
        shape_stop_list: list[dict] = []
        for bs in built:
            shape_stop_list.append(
                {
                    "stop_id": bs["stop_id"],
                    "name": stop_name_map.get(bs["stop_id"], ""),
                    "lat": bs["lat"],
                    "lon": bs["lon"],
                    "stop_sequence": bs["stop_sequence"],
                    "progress_m": bs["progress_m"],
                }
            )

        shape_entries.append(
            {
                "shape_id": shape_id,
                "latlngs": latlngs,
                "stops": shape_stop_list,
            }
        )

        # Merge unique stops (stable insertion order, dedup by stop_id).
        for entry in shape_stop_list:
            sid = entry["stop_id"]
            if sid not in merged_stops_by_id:
                merged_stops_by_id[sid] = {
                    "stop_id": sid,
                    "name": entry["name"],
                    "lat": entry["lat"],
                    "lon": entry["lon"],
                }

    return {
        "route_id": route["route_id"],
        "short_name": route.get("short_name", ""),
        "color": route.get("color", ""),
        "text_color": route.get("text_color", ""),
        "shapes": shape_entries,
        "stops": list(merged_stops_by_id.values()),
    }

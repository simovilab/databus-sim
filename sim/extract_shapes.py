#!/usr/bin/env python
"""One-shot extractor: gtfs.json (Django fixture) -> shapes.json.

Keeps only what the simulator + webui need: UCR routes (bUCR_L1, bUCR_L2),
their shapes as ordered polylines, and the stops.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

TARGET_ROUTES = {"bUCR_L1", "bUCR_L2"}


def extract(fixture_path: Path, out_path: Path) -> None:
    raw = json.loads(fixture_path.read_text())

    routes: dict[str, dict] = {}
    stops: list[dict] = []
    trips_by_route: dict[str, set[str]] = defaultdict(set)
    shape_points: dict[str, list[tuple[int, float, float, float]]] = defaultdict(list)

    for record in raw:
        model = record["model"]
        fields = record["fields"]

        if model == "feed.route" and fields["route_id"] in TARGET_ROUTES:
            routes[fields["route_id"]] = {
                "route_id": fields["route_id"],
                "short_name": fields["route_short_name"],
                "long_name": fields["route_long_name"],
                "color": fields["route_color"],
                "text_color": fields["route_text_color"],
            }
        elif model == "feed.trip" and fields["route_id"] in TARGET_ROUTES:
            trips_by_route[fields["route_id"]].add(fields["shape_id"])
        elif model == "feed.stop":
            stops.append({
                "stop_id": fields["stop_id"],
                "name": fields["stop_name"],
                "lat": fields["stop_lat"],
                "lon": fields["stop_lon"],
            })
        elif model == "feed.shape":
            shape_points[fields["shape_id"]].append((
                fields["shape_pt_sequence"],
                fields["shape_pt_lat"],
                fields["shape_pt_lon"],
                fields["shape_dist_traveled"],
            ))

    relevant_shape_ids = {s for ids in trips_by_route.values() for s in ids}

    shapes: dict[str, list[list[float]]] = {}
    for shape_id in relevant_shape_ids:
        pts = sorted(shape_points[shape_id])
        shapes[shape_id] = [[lat, lon, dist] for _, lat, lon, dist in pts]

    for route_id, route in routes.items():
        route["shape_ids"] = sorted(trips_by_route[route_id])

    out = {
        "routes": list(routes.values()),
        "shapes": shapes,
        "stops": stops,
    }
    out_path.write_text(json.dumps(out, indent=2))
    print(f"Wrote {out_path}: {len(routes)} routes, {len(shapes)} shapes, {len(stops)} stops")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        default="../../databus/backend/feed/fixtures/gtfs.json",
        help="Path to databus gtfs.json fixture",
    )
    parser.add_argument(
        "--out",
        default="shapes.json",
        help="Output path",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    fixture = (script_dir / args.fixture).resolve()
    out = (script_dir / args.out).resolve()
    extract(fixture, out)


if __name__ == "__main__":
    main()

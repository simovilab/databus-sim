"""Background poll loop for NavSat vehicle positions.

Mirrors the resilience shape of runtime.py's other background tasks (tick_loop,
binder.poll_loop): sleep → do the thing → never let one bad iteration kill the
task. Only started by runtime.start_runtime() when settings.NAVSAT_URL is set;
otherwise the feature is a no-op (no vendor token required to run the sim).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from simulator_app.services.navsat_client import NavSatClient, NavSatClientError

log = logging.getLogger(__name__)


def _to_snapshot(records: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "plate_number": r.plate_number,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "estado": r.estado,
        }
        for r in records
    ]


async def navsat_poll_loop(client: NavSatClient, interval_s: float) -> None:
    """Poll NavSat every ``interval_s`` seconds; broadcast the snapshot to the browser."""
    import simulator_app.realtime.broadcast as _broadcast_mod
    from simulator_app.runtime import get_runtime

    log.info("navsat_poll_loop: started (interval=%.1fs)", interval_s)

    while True:
        await asyncio.sleep(interval_s)

        try:
            records = await client.fetch()
        except NavSatClientError as exc:
            log.warning("navsat_poll_loop: fetch failed: %s", exc)
            continue
        except Exception as exc:  # noqa: BLE001 — never let a bad poll kill the task
            log.exception("navsat_poll_loop: unexpected error: %s", exc)
            continue

        snapshot = _to_snapshot(records)
        get_runtime()._navsat_snapshot = snapshot
        await _broadcast_mod.broadcast_navsat(snapshot)

"""FastAPI HTTP control API on :8081.

Owned by Agent B (B1). P0 defines the route surface (see ``CONTRACTS.md`` §2)
as stubs that raise :class:`NotImplementedError`. B1 fills in the bodies.

The :class:`HttpControl` object is the wiring seam: ``simulator.py`` will
construct it with the fleet/scheduler/redis instances and pass ``app`` to
uvicorn.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .databus_client import DatabusClient
from .fleet import FleetState
from .redis_client import RedisClient
from .run_binder import RunBinder
from .scheduler import Scheduler


# ---------------------------------------------------------------------------
# Request / response models — locked at P0; see CONTRACTS.md §2 / §3.
# ---------------------------------------------------------------------------


class ScheduleEntry(BaseModel):
    id: str
    vehicle_id: str
    operator_id: str
    route_id: str
    trip_id: str
    direction_id: int
    shape_id: str
    schedule_relationship: str = "SCHEDULED"
    start_time: str
    auto_confirm: bool = False
    auto_start_motion_after_s: int | None = None


class ScheduleDefaults(BaseModel):
    pre_run_idle_s: int = 30
    post_run_idle_s: int = 30
    auto_confirm_delay_s: int = 5


class ScheduleDocument(BaseModel):
    defaults: ScheduleDefaults = ScheduleDefaults()
    runs: list[ScheduleEntry] = []


class RunStateResponse(BaseModel):
    run_id: str
    run_lifecycle_state: str | None
    fields: dict[str, Any] = {}


class TrackRunRequest(BaseModel):
    """Register an externally-created run (e.g. operator-driven) with RunBinder.

    The scheduler path does this internally on dispatch. For runs created via
    the databus REST API (`/api/create-run/`) the UI must call this endpoint so
    the simulator can resolve `terminal_stop_id` and bind the vehicle when the
    run reaches Confirmed.
    """

    run_id: str
    vehicle_id: str
    trip_id: str
    shape_id: str


class TrackRunResponse(BaseModel):
    status: str = "ok"
    run_id: str
    terminal_stop_id: str | None = None


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


class HttpControl:
    """Bundle of dependencies + the FastAPI app instance."""

    def __init__(
        self,
        fleet: FleetState,
        scheduler: Scheduler,
        redis_client: RedisClient,
        binder: RunBinder,
        databus: DatabusClient,
    ) -> None:
        self.fleet = fleet
        self.scheduler = scheduler
        self.redis_client = redis_client
        self.binder = binder
        self.databus = databus
        self.app = _build_app(self)


def _build_app(ctrl: HttpControl) -> FastAPI:
    app = FastAPI(title="SIMOVI simulator control", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:8080"],
        allow_methods=["GET", "PUT", "POST"],
        allow_headers=["*"],
    )

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/schedule")
    def get_schedule() -> dict[str, Any]:
        return ctrl.scheduler.snapshot()

    @app.put("/schedule")
    def put_schedule(doc: ScheduleDocument) -> dict[str, bool]:
        ctrl.scheduler.write(doc.model_dump())
        ctrl.scheduler.reload()
        return {"ok": True}

    @app.post("/schedule/reload")
    def reload_schedule() -> dict[str, bool]:
        ctrl.scheduler.reload()
        return {"ok": True}

    @app.get("/fleet")
    def get_fleet() -> dict[str, Any]:
        return ctrl.fleet.snapshot()

    @app.get("/run/{run_id}")
    async def get_run(run_id: str) -> RunStateResponse:
        fields = await ctrl.redis_client.get_run_hash(run_id)
        if not fields:
            raise HTTPException(status_code=404, detail=f"run {run_id!r} not found in Redis")
        lifecycle_state = fields.get("run_lifecycle_state")
        return RunStateResponse(run_id=run_id, run_lifecycle_state=lifecycle_state, fields=fields)

    @app.post("/runs/track")
    async def track_run(req: TrackRunRequest) -> TrackRunResponse:
        if ctrl.fleet.get(req.vehicle_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown vehicle {req.vehicle_id!r}")
        terminal_stop_id: str | None = None
        try:
            terminal_stop_id = await ctrl.databus.get_trip_terminal_stop(req.trip_id)
        except Exception as exc:
            # Not fatal — bind without it; is_at_terminal_stop guard will fail
            # later but the run will at least be tracked.
            import logging
            logging.getLogger(__name__).warning(
                "track_run: get_trip_terminal_stop failed for trip %s: %s", req.trip_id, exc
            )
        ctrl.binder.track(
            run_id=req.run_id,
            vehicle_id=req.vehicle_id,
            trip_id=req.trip_id,
            shape_id=req.shape_id,
            terminal_stop_id=terminal_stop_id,
        )
        return TrackRunResponse(run_id=req.run_id, terminal_stop_id=terminal_stop_id)

    return app

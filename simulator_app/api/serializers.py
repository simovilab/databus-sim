"""DRF serializers for the SIMOVI simulator API.

Phase 5 backend agent: port the pydantic models from sim/http_control.py 1:1
to DRF serializers. The contracts are locked at Phase 0 (see sim/CONTRACTS.md §2–3).

Locked shapes (do not rename fields):
  ScheduleEntry, ScheduleDefaults, ScheduleDocument — schedule.yaml body
  RunStateResponse       — GET /sim/run/<run_id> response
  TrackRunRequest        — POST /sim/runs/track request
  TrackRunResponse       — POST /sim/runs/track response
  ControlPayload         — POST /sim/control/* request (varies by knob)
"""

# Phase 5 implementation goes here.

"""DRF serializers for the SIMOVI simulator API.

Ports the pydantic models from sim/http_control.py 1:1 to DRF serializers.
Field names are locked per CONTRACTS.md §2–3.

Locked shapes (do not rename fields):
  ScheduleEntry, ScheduleDefaults, ScheduleDocument — schedule.yaml body
  RunStateResponse       — GET /sim/run/<run_id> response
  TrackRunRequest        — POST /sim/runs/track request
  TrackRunResponse       — POST /sim/runs/track response
"""

from __future__ import annotations

from rest_framework import serializers


# ---------------------------------------------------------------------------
# Schedule serializers
# ---------------------------------------------------------------------------


class ScheduleDefaultsSerializer(serializers.Serializer):
    pre_run_idle_s = serializers.IntegerField(default=30, min_value=0)
    post_run_idle_s = serializers.IntegerField(default=30, min_value=0)
    auto_confirm_delay_s = serializers.IntegerField(default=5, min_value=0)


class ScheduleEntrySerializer(serializers.Serializer):
    id = serializers.CharField()
    vehicle_id = serializers.CharField()
    operator_id = serializers.CharField()
    route_id = serializers.CharField()
    trip_id = serializers.CharField()
    direction_id = serializers.IntegerField()
    shape_id = serializers.CharField()
    schedule_relationship = serializers.ChoiceField(
        choices=["SCHEDULED", "ADDED", "UNSCHEDULED"],
        default="SCHEDULED",
    )
    start_time = serializers.CharField()
    auto_confirm = serializers.BooleanField(default=False)
    auto_start_motion_after_s = serializers.IntegerField(
        allow_null=True, required=False, default=None
    )

    def validate_direction_id(self, value: int) -> int:
        if value not in (0, 1):
            raise serializers.ValidationError("direction_id must be 0 or 1")
        return value


class ScheduleDocumentSerializer(serializers.Serializer):
    defaults = ScheduleDefaultsSerializer(required=False, default={})
    runs = serializers.ListField(
        child=ScheduleEntrySerializer(),
        required=False,
        default=list,
    )

    def validate(self, data: dict) -> dict:
        runs = data.get("runs") or []
        ids_seen: set[str] = set()
        for entry in runs:
            eid = entry.get("id", "")
            if not eid:
                raise serializers.ValidationError(
                    "Each schedule entry must have an 'id' field"
                )
            if eid in ids_seen:
                raise serializers.ValidationError(
                    f"Duplicate schedule entry id: {eid!r}"
                )
            ids_seen.add(eid)
        return data


# ---------------------------------------------------------------------------
# Run / track serializers
# ---------------------------------------------------------------------------


class RunStateResponseSerializer(serializers.Serializer):
    run_id = serializers.CharField()
    run_lifecycle_state = serializers.CharField(allow_null=True)
    fields = serializers.DictField(child=serializers.CharField(), default=dict)


class TrackRunRequestSerializer(serializers.Serializer):
    run_id = serializers.CharField()
    vehicle_id = serializers.CharField()
    trip_id = serializers.CharField()
    shape_id = serializers.CharField()


class TrackRunResponseSerializer(serializers.Serializer):
    status = serializers.CharField(default="ok")
    run_id = serializers.CharField()
    terminal_stop_id = serializers.CharField(allow_null=True, required=False)

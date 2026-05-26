"""SIMOVI simulator package.

Public submodules (see ``CONTRACTS.md`` for the contract each owns):

- :mod:`sim.fleet` — fleet roster + state container (Agent A).
- :mod:`sim.controller` — MQTT control subscriber (Agent A).
- :mod:`sim.state_publisher` — MQTT state publisher (Agent A).
- :mod:`sim.run_binder` — Redis poller / vehicle↔run binder (Agent B).
- :mod:`sim.scheduler` — schedule.yaml runner (Agent B).
- :mod:`sim.http_control` — FastAPI control API on :8081 (Agent B).
- :mod:`sim.databus_client` — HTTP client for /api/create-run /api/update-run (Agent B).
- :mod:`sim.redis_client` — Redis reader for run:{run_id} (Agent A interface, Agent B impl).
"""

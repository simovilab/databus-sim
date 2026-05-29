"""SIMOVI simulator package.

Public submodules (see ``CONTRACTS.md`` for the contract each owns):

- :mod:`sim.fleet` — fleet roster + state container (Agent A).
- :mod:`sim.controller` — MQTT control subscriber (Agent A).
- :mod:`sim.state_publisher` — MQTT state publisher (Agent A).
- :mod:`sim.run_binder` — vehicle↔run binder; polls databus for run state (Agent B).
- :mod:`sim.scheduler` — schedule.yaml runner (Agent B).
- :mod:`sim.http_control` — FastAPI control API on :8081 (Agent B).
- :mod:`sim.databus_client` — HTTP client for /api/create-run, /api/update-run, /api/run (Agent B).
"""

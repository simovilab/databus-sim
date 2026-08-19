"""Domain layer — pure business logic, no framework dependencies.

Submodules (ported from sim/ in Phase 2):
  fleet.py       — FleetState, Vehicle, roster (verbatim from sim/fleet.py)
  kinematics.py  — Shape, step_vehicle, publish_vehicle, load_shapes (from sim/simulator.py)
  control.py     — apply_control() dispatch (from sim/controller.py _handle_* bodies)
"""

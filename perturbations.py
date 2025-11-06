# perturbations.py
import random

class PerturbationEngine:
    def __init__(self, config, seed=None):
        self.config = config.get("perturbations", {})
        self.random = random.Random(seed)

    def apply_delay(self, vehicle):
        conf = self.config.get("delay", {})
        if not conf.get("enabled", False):
            return
        mean = conf.get("mean", 0)
        std = conf.get("stddev", 0)
        delay = max(0, self.random.gauss(mean, std))
        vehicle.total_time += delay  # Afecta el tiempo total del vehículo
        vehicle.delay_s = getattr(vehicle, "delay_s", 0) + delay

    def apply_detour(self, vehicle):
        conf = self.config.get("detour", {})
        if not conf.get("enabled", False):
            return
        if vehicle.route in conf.get("routes", []):
            extra_distance = conf.get("extra_distance_m", 0)
            vehicle.total_distance += extra_distance
            vehicle.extra_distance_m = getattr(vehicle, "extra_distance_m", 0) + extra_distance

    def apply_holding(self, vehicle):
        conf = self.config.get("holding", {})
        if not conf.get("enabled", False):
            return
        if vehicle.is_dwelling:
            hold = conf.get("hold_time_s", 0)
            vehicle.dwell_time_remaining += hold
            vehicle.hold_time_s = getattr(vehicle, "hold_time_s", 0) + hold

    def apply_all(self, vehicle):
        self.apply_delay(vehicle)
        self.apply_detour(vehicle)
        self.apply_holding(vehicle)
    


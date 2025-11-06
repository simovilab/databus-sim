# metrics/collectors.py  (sustituir las funciones relevantes)

from dataclasses import dataclass
from typing import Dict, List, Any
import time
import statistics
import pandas as pd
import os

@dataclass
class StopEvent:
    vehicle_id: str
    stop_id: str
    arrival_time: float

class MetricsCollector:
    def __init__(self, schedule_headways: Dict[str, float], schedule_df: pd.DataFrame = None, otp_threshold=120):
        self.stop_events = {}
        self.schedule_headways = schedule_headways
        self.otp_threshold = otp_threshold
        self.otp_records = []
        self.headway_records = []
        self.gaps = []
        self.schedule_df = schedule_df

        # ✅ unified rows list (system + vehicle)
        self.rows: List[Dict[str, Any]] = []

    # === vehicle stop ===
    def record_stop(self, vehicle, stop_id, actual_time_sec):
        # guardar evento
        now = actual_time_sec
        if stop_id not in self.stop_events:
            self.stop_events[stop_id] = []
        self.stop_events[stop_id].append(StopEvent(vehicle.vehicle_id, stop_id, now))

        # OTP: buscar schedule row por trip_id y stop_id
        if self.schedule_df is not None and getattr(vehicle, "trip_id", None) is not None:
            sched = self.schedule_df[
                (self.schedule_df["trip_id"] == vehicle.trip_id) &
                (self.schedule_df["stop_id"] == stop_id)
            ]
            if not sched.empty and "arrival_time_sec" in sched.columns:
                planned = sched.iloc[0]["arrival_time_sec"]
                diff = abs(actual_time_sec - planned)
                self.otp_records.append(diff <= self.otp_threshold)
                # opcional: guarda el valor numérico
                self.rows.append({
                    "type": "otp",
                    "tick": None,
                    "vehicle_id": vehicle.vehicle_id,
                    "trip_id": vehicle.trip_id,
                    "stop_id": stop_id,
                    "otp_diff_sec": diff,
                    "on_time": diff <= self.otp_threshold
                })
        # headway calculation (existing logic)
        events = self.stop_events[stop_id]
        if len(events) >= 2:
            actual_headway = events[-1].arrival_time - events[-2].arrival_time
            planned = self.schedule_headways.get(vehicle.route, 300)
            self.headway_records.append(actual_headway - planned)
            if actual_headway > planned * 1.8:
                self.gaps.append((stop_id, actual_headway))

    # === record vehicle metrics (acepta position y cualquier extra) ===
    def record_vehicle(self, vehicle_id: str, tick: int, metrics: dict, position: tuple = None, **extra):
        """
        Guarda una fila tipo 'vehicle' con:
        - tick, vehicle_id
        - columnas desde metrics (total_distance, current_speed, ...)
        - si position=(lon,lat) => añade position_lon, position_lat
        - cualquier extra adicional será añadido como columna
        """
        row = {"type": "vehicle", "tick": tick, "vehicle_id": vehicle_id}
        # metrics dict => columnas
        row.update(metrics or {})
        # position tuple (lon, lat) -> columnas separadas
        if position is not None:
            try:
                lon, lat = position
                row["position_lon"] = float(lon)
                row["position_lat"] = float(lat)
            except Exception:
                # si viene otra forma, lo guardamos como str
                row["position"] = str(position)
        # extras
        row.update(extra)
        self.rows.append(row)

    # === record system metrics ===
    def record_system(self, tick: int, metrics: dict):
        row = {"type": "system", "tick": tick}
        row.update(metrics or {})
        self.rows.append(row)

    # === Export unified CSV (todo en un solo archivo) ===
    def export(self, filename: str):
        """
        Guarda todas las filas (system + vehicle) en un único CSV.
        """
        # Asegurar carpeta destino
        os.makedirs(os.path.dirname(os.path.abspath(filename)) or ".", exist_ok=True)

        df = pd.DataFrame(self.rows)
        df.to_csv(filename, index=False)
        print(f"✅ Unified metrics saved to {os.path.abspath(filename)}")

    def export_headways(self, filename="headways_loaded.csv"):
        pd.DataFrame.from_dict(
            self.schedule_headways, orient="index", columns=["headway_seconds"]
        ).to_csv(filename)

    def summary(self):
        return {
            "otp_rate": sum(self.otp_records) / len(self.otp_records) if self.otp_records else 0,
            "avg_headway_error": statistics.mean(self.headway_records) if self.headway_records else 0,
            "service_gaps": len(self.gaps)
        }



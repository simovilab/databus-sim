import pandas as pd
import yaml
import json

# ============================================================
# 1. Cargar datos GTFS desde un archivo ZIP
# ============================================================

import zipfile

def load_data(file_path):
    """
    Carga archivos GTFS (en formato ZIP) y devuelve un diccionario
    de DataFrames con las tablas disponibles (routes, trips, stops, etc.).
    """
    tables = {}
    with zipfile.ZipFile(file_path, "r") as z:
        for filename in z.namelist():
            if filename.endswith(".txt") and not filename.startswith("__MACOS"):
                with z.open(filename) as f:
                    df_name = filename.split(".")[0]
                    tables[df_name] = pd.read_csv(f)
    return tables


# ============================================================
# 2. Cargar archivo de configuración YAML o JSON
# ============================================================

def load_config(config_path):
    """
    Carga un archivo de configuración en formato YAML o JSON.
    Devuelve un diccionario con los parámetros de configuración.
    """
    if config_path.endswith(".yaml") or config_path.endswith(".yml"):
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    elif config_path.endswith(".json"):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        raise ValueError("El archivo de configuración debe ser YAML o JSON.")


# ============================================================
# 3. Validación de las tablas GTFS
# ============================================================

def validate_gtfs(gtfs_data):
    """
    Verifica que las tablas esenciales del GTFS estén presentes.
    """
    required_tables = ["routes", "trips"]
    for table in required_tables:
        if table not in gtfs_data:
            raise ValueError(f"Falta la tabla requerida: {table}")
    return True


# ============================================================
# 4. Clase Scenario
# ============================================================

class Scenario:
    """
    Representa un escenario de simulación generado a partir de datos GTFS
    y parámetros definidos en un archivo de configuración.
    """

    def __init__(self, routes, trips, frequencies=None):
        self.routes = routes
        self.trips = trips
        self.frequencies = frequencies

    @classmethod
    def from_gtfs(cls, gtfs_data, config):
        """
        Construye un escenario usando datos GTFS y un diccionario de configuración.
        """
        routes = gtfs_data["routes"].copy()
        trips = gtfs_data["trips"].copy()
        frequencies = gtfs_data.get("frequencies", None)

        # -------------------------
        # Aplicar filtros del config
        # -------------------------

        # Filtrar por rutas específicas
        if "routes" in config:
            allowed_routes = config["routes"]
            routes = routes[routes["route_id"].isin(allowed_routes)]
            trips = trips[trips["route_id"].isin(allowed_routes)]

        # Filtrar por service_id (ej. "weekday", "saturday", etc.)
        if "service_id" in config:
            allowed_services = config["service_id"]
            trips = trips[trips["service_id"].isin(allowed_services)]

        # Filtrar frecuencias si se solicita
        if config.get("frequencies_enabled", False) and frequencies is not None:
            frequencies = frequencies[
                frequencies["trip_id"].isin(trips["trip_id"])
            ]
        else:
            frequencies = None

        return cls(routes, trips, frequencies)

    def summary(self):
        """
        Devuelve un resumen del escenario (cantidad de rutas, viajes, etc.)
        """
        return {
            "num_routes": len(self.routes),
            "num_trips": len(self.trips),
            "has_frequencies": self.frequencies is not None
        }


# ============================================================
# 5. Ejemplo de uso
# ============================================================

if __name__ == "__main__":
    # Rutas de prueba
    gtfs_zip_path = "./assets/GTFS_bUCR.zip"
    config_path = "./config/scenario.yaml"

    print("Cargando configuración y datos GTFS...")
    config = load_config(config_path)
    gtfs_data = load_data(gtfs_zip_path)
    validate_gtfs(gtfs_data)

    scenario = Scenario.from_gtfs(gtfs_data, config)
    print("✅ Escenario creado con éxito")
    print(scenario.summary())

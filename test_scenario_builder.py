import sys
import os
from scenario_builder import load_data, load_config, Scenario, validate_gtfs
import pytest

def test_scenario_creation():
    gtfs_path = "./assets/GTFS_bUCR.zip"
    config_path = "./config/scenario.yaml"

    gtfs_data = load_data(gtfs_path)
    config = load_config(config_path)
    validate_gtfs(gtfs_data)

    scenario = Scenario.from_gtfs(gtfs_data, config)
    summary = scenario.summary()

    assert summary["num_routes"] > 0
    assert "num_trips" in summary
    assert isinstance(summary["has_frequencies"], bool)
    
def test_empty_routes():
    gtfs_path = "./assets/GTFS_bUCR.zip"
    config_path = "./config/scenario.yaml"
    
    gtfs_data = load_data(gtfs_path)
    config = load_config(config_path)
    
    # Sobrescribir las rutas para simular rutas vacías
    config["routes"] = []
    
    scenario = Scenario.from_gtfs(gtfs_data, config)
    summary = scenario.summary()
    
    assert summary["num_routes"] == 0
    assert summary["num_trips"] == 0
    
def test_nonexistent_service():
    gtfs_data = load_data("./assets/GTFS_bUCR.zip")
    config = load_config("./config/scenario.yaml")
    
    # Usar un service_id que no exista
    config["service_id"] = ["fin_de_semana"]
    
    scenario = Scenario.from_gtfs(gtfs_data, config)
    summary = scenario.summary()
    
    assert summary["num_trips"] == 0

def test_frequencies_enabled():
    gtfs_data = load_data("./assets/GTFS_bUCR.zip")
    config = load_config("./config/scenario.yaml")
    
    config["frequencies_enabled"] = True
    
    scenario = Scenario.from_gtfs(gtfs_data, config)
    summary = scenario.summary()
    
    assert isinstance(summary["has_frequencies"], bool)

def test_shapes_match_trips():
    gtfs_data = load_data("./assets/GTFS_bUCR.zip")
    config = load_config("./config/scenario.yaml")
    
    scenario = Scenario.from_gtfs(gtfs_data, config)
    
    # Shape_ids incluidos en los trips filtrados por configuración
    included_trips = gtfs_data["trips"][
        gtfs_data["trips"]["route_id"].isin(config.get("routes", []))
    ]
    included_shape_ids = included_trips["shape_id"].dropna().unique()
    
    # Shapes del GTFS
    scenario_shapes = gtfs_data["shapes"]["shape_id"].unique()
    
    # Comprobar que todos los shapes de los trips estén en shapes
    for sid in included_shape_ids:
        assert sid in scenario_shapes



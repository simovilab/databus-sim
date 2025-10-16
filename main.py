import pandas as pd
import zipfile


def load_data(file_path):
    """From a ZIP file, unzip and load the CSV files (with .txt extension) into Pandas DataFrames, following a specific GTFS naming convention: agency, stops, routes, trips, shapes, etc."""
    tables = {}
    with zipfile.ZipFile(file_path, "r") as z:
        for filename in z.namelist():
            if filename.endswith(".txt") and not filename.startswith("__MACOS"):
                with z.open(filename) as f:
                    df_name = filename.split(".")[0]
                    tables[df_name] = pd.read_csv(f)
    return tables

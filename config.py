import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = Path(os.environ.get("P1_RAW_DIR", ROOT / "data" / "raw"))
DB_PATH = Path(os.environ.get("P1_DB_PATH", ROOT / "data" / "flights.duckdb"))
OUT_DIR = Path(os.environ.get("P1_OUT_DIR", ROOT / "outputs"))
SEED = 42
MIN_CARRIER_FLIGHTS = 1000      # carriers with fewer flights are left out of the adjusted ranking

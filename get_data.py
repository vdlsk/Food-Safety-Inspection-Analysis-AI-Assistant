"""Fetch the real data: US BTS on-time records for every flight departing NYC (JFK, LGA, EWR) in 2013,
with hourly airport weather and FAA aircraft registry fields, as packaged in the `nycflights13`
dataset (Hadley Wickham; sources: BTS TranStats, NOAA ISD, FAA). Pulled from PyPI.

Usage:  python src/get_data.py
"""
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from config import RAW_DIR


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    subprocess.run([sys.executable, "-m", "pip", "download", "--no-deps", "--no-binary", ":all:",
                    "-d", str(tmp), "nycflights13==0.0.3"], check=True)
    tgz = next(tmp.glob("nycflights13-*.tar.gz"))
    with tarfile.open(tgz) as t:
        for m in t.getmembers():
            if "/data/" in m.name and m.isfile():
                m.name = Path(m.name).name
                t.extract(m, RAW_DIR, filter="data")
    with zipfile.ZipFile(RAW_DIR / "flights.csv.zip") as z:
        z.extractall(RAW_DIR)
    print(sorted(p.name for p in RAW_DIR.glob("*.csv")))


if __name__ == "__main__":
    main()

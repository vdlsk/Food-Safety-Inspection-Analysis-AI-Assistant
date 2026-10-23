"""Build the DuckDB database.  Usage:  python src/build_db.py"""
import json

import duckdb

from config import DB_PATH, OUT_DIR, RAW_DIR, ROOT


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute((ROOT / "sql" / "01_build.sql").read_text().format(raw=RAW_DIR.as_posix()))
    one = lambda s: con.execute(s).fetchone()  # noqa: E731
    n, canc, div, late, unrel = one("""SELECT count(*), avg(cancelled::INT), avg(diverted::INT), avg(late15),
                                       avg(unreliable) FROM flights""")
    stats = {
        "flights": n, "cancel_rate": canc, "diverted_rate": div, "late15_rate_of_completed": late,
        "unreliable_rate": unrel,
        "carriers": one("SELECT count(DISTINCT carrier) FROM flights")[0],
        "routes": one("SELECT count(DISTINCT route) FROM flights")[0],
        "competitive_routes": one("SELECT count(*) FROM competitive_routes")[0],
        "flights_on_competitive_routes": one(
            "SELECT count(*) FROM flights JOIN competitive_routes USING (route)")[0],
        "weather_match_rate": one("SELECT avg((temp IS NOT NULL)::INT) FROM flights")[0],
        "plane_match_rate": one("SELECT avg((plane_year IS NOT NULL)::INT) FROM flights")[0],
        "date_range": [str(x) for x in one("SELECT min(flight_date), max(flight_date) FROM flights")],
    }
    (OUT_DIR / "build_stats.json").write_text(json.dumps(stats, indent=2, default=float))
    print(json.dumps(stats, indent=2, default=float))


if __name__ == "__main__":
    main()

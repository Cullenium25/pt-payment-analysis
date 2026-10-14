#!/usr/bin/env python3
"""
Load the PT service-line extract into a SQLite database so the analysis can be
done in SQL rather than only in pandas.

Produces: data/pt_market.db  (table: pt_geo_2024)

Usage:
    python scripts/build_db.py
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "pt_geo_2024.csv"
DB = ROOT / "data" / "pt_market.db"
TABLE = "pt_geo_2024"

INDEXES = [
    f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_hcpcs ON {TABLE}(HCPCS_Cd)",
    f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_geo ON {TABLE}(Rndrng_Prvdr_Geo_Desc)",
    f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_pos ON {TABLE}(Place_Of_Srvc)",
]

# Territories and non-geographic catch-alls. Kept in the table for completeness
# but excluded from per-state comparisons so the 50 states + DC aren't skewed by
# tiny-population outliers or military/foreign postings.
#
# CMS renamed the Americas military region between data years ("Armed Forces
# Americas" in some releases, "Armed Forces Central/South America" in this 2024
# file). Both spellings are listed so the exclusion holds across years; relying
# on only one silently leaks a military region into per-state comparisons.
NON_STATES = (
    "Guam", "Puerto Rico", "Virgin Islands", "Northern Mariana Islands",
    "American Samoa", "Unknown", "Foreign Country",
    "Armed Forces Europe", "Armed Forces Pacific",
    "Armed Forces Americas",              # name used in some CMS releases
    "Armed Forces Central/South America",  # name used in the 2024 release
)


def main() -> int:
    if not SRC.exists():
        print(f"ERROR: {SRC} not found. Run: python scripts/fetch_data.py", file=sys.stderr)
        return 1

    # Keep identifier columns as text on the way back in as well: a CSV
    # round-trip would otherwise re-infer HCPCS_Cd as an integer.
    df = pd.read_csv(SRC, dtype={"HCPCS_Cd": "string", "Rndrng_Prvdr_Geo_Cd": "string",
                                 "Rndrng_Prvdr_Geo_Lvl": "string",
                                 "Place_Of_Srvc": "string", "HCPCS_Drug_Ind": "string"})
    db = sqlite3.connect(DB)
    df.to_sql(TABLE, db, if_exists="replace", index=False)
    for stmt in INDEXES:
        db.execute(stmt)
    db.commit()

    n = pd.read_sql_query(f"SELECT COUNT(*) n FROM {TABLE}", db).iloc[0, 0]
    print(f"[ok  ] {DB} (table {TABLE}): {n:,} rows, {DB.stat().st_size / 1e6:.2f} MB")
    print(f"       indexes: {len(INDEXES)}")

    view_sql = f"""
    CREATE VIEW IF NOT EXISTS v_state_office AS
    SELECT * FROM {TABLE}
    WHERE Rndrng_Prvdr_Geo_Lvl = 'State'
      AND Place_Of_Srvc = 'O'
      AND Rndrng_Prvdr_Geo_Desc NOT IN ({", ".join("'" + s + "'" for s in NON_STATES)});
    """
    db.execute("DROP VIEW IF EXISTS v_state_office")
    db.execute(view_sql)
    db.commit()
    nv = pd.read_sql_query("SELECT COUNT(*) n FROM v_state_office", db).iloc[0, 0]
    geos = pd.read_sql_query(
        "SELECT DISTINCT Rndrng_Prvdr_Geo_Desc g FROM v_state_office", db)
    leaked = sorted(set(geos.g) & set(NON_STATES))
    # Assert rather than trust: a renamed CMS geography silently leaks a
    # territory or military region into every per-state comparison downstream.
    assert not leaked, f"non-state geographies leaked into v_state_office: {leaked}"
    print(f"       view v_state_office: {nv:,} rows, {len(geos)} geographies "
          f"(50 states + DC, office setting)")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

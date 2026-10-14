#!/usr/bin/env python3
"""
Download the CMS Medicare Physician & Other Practitioners, by Geography and
Service public use file (PT service-line extract).

Source : Centers for Medicare & Medicaid Services (CMS)
Dataset: https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners/medicare-physician-other-practitioners-by-geography-and-service
License: Public Use File, free, no registration required.

The full file is ~42 MB and ~268,000 rows covering 9,403 HCPCS codes across
61 geographies. We download the whole file and then subset to physical therapy
service-line codes (CPT 97000-97799), which keeps the committed artifact small
without hiding the filtering decision.

Usage:
    python scripts/fetch_data.py            # download + subset
    python scripts/fetch_data.py --force    # re-download even if cached
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import urllib.request

DATA_URL = (
    "https://data.cms.gov/sites/default/files/2026-05/"
    "e534c74b-79b8-4892-8a95-5a17e2dfec9f/MUP_PHY_R26_P05_V10_D24_Geo.csv"
)
DATA_YEAR = 2024

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
RAW_CSV = RAW_DIR / f"cms_physician_geography_service_{DATA_YEAR}.csv"
PT_CSV = ROOT / "data" / f"pt_geo_{DATA_YEAR}.csv"

# Physical therapy / physical medicine & rehabilitation service-line codes.
# CPT 97000-97799 covers therapeutic procedures, evaluations, and modalities.
PT_CODES = {
    # Therapeutic procedures
    "97110": "Therapeutic exercise",
    "97112": "Neuromuscular re-education",
    "97113": "Aquatic therapy",
    "97116": "Gait training",
    "97124": "Massage",
    "97140": "Manual therapy",
    "97150": "Group therapy",
    "97530": "Therapeutic activities",
    "97535": "Self-care / home management training",
    # Evaluations
    "97161": "PT evaluation (low complexity)",
    "97162": "PT evaluation (moderate complexity)",
    "97163": "PT evaluation (high complexity)",
    "97164": "PT re-evaluation",
    # Modalities / supervised physical medicine
    "97012": "Mechanical traction",
    "97016": "Vasopneumatic compression",
    "97018": "Hot wax bath",
    "97022": "Whirlpool therapy",
    "97032": "Electrical stimulation",
    "97035": "Ultrasound",
    # Other PT
    "97542": "Wheelchair assessment",
    "97750": "Functional capacity test",
}


def download(url: str, dest: pathlib.Path, force: bool = False) -> pathlib.Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        print(f"[skip] cached: {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
        return dest

    print(f"[get ] {url}")
    tmp = dest.with_suffix(".part")
    with urllib.request.urlopen(url, timeout=600) as resp, tmp.open("wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        seen = 0
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
            seen += len(chunk)
            if total:
                pct = 100 * seen / total
                print(f"\r       {seen / 1e6:6.1f} / {total / 1e6:.1f} MB ({pct:4.1f}%)", end="")
    print()
    tmp.replace(dest)
    print(f"[ok  ] {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def subset_to_pt(src: pathlib.Path, dest: pathlib.Path) -> pathlib.Path:
    import pandas as pd

    print(f"[read] {src.name}")
    # HCPCS_Cd and Rndrng_Prvdr_Geo_Cd are IDENTIFIERS, not numbers. Read them
    # as strings: numeric inference turns "97110" into 97110 and silently drops
    # leading zeros (e.g. code "0001U"), which breaks all later joins/filters.
    df = pd.read_csv(
        src,
        low_memory=False,
        dtype={"HCPCS_Cd": "string", "Rndrng_Prvdr_Geo_Cd": "string",
               "Rndrng_Prvdr_Geo_Lvl": "string", "Place_Of_Srvc": "string",
               "HCPCS_Drug_Ind": "string"},
    )
    print(f"       full file: {len(df):,} rows, {df.HCPCS_Cd.nunique():,} HCPCS codes")

    pt = df[df.HCPCS_Cd.isin(PT_CODES)].copy()
    pt["service_line"] = pt.HCPCS_Cd.map(PT_CODES).astype("string")
    pt["data_year"] = DATA_YEAR
    assert pt.HCPCS_Cd.map(type).eq(str).all(), "HCPCS_Cd must remain a string"

    dest.parent.mkdir(parents=True, exist_ok=True)
    pt.to_csv(dest, index=False)
    print(f"[ok  ] {dest.name}: {len(pt):,} rows, {pt.HCPCS_Cd.nunique()} PT codes, "
          f"{dest.stat().st_size / 1e6:.2f} MB")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-download even if cached")
    args = ap.parse_args()

    raw = download(DATA_URL, RAW_CSV, force=args.force)
    subset_to_pt(raw, PT_CSV)
    print("\nNext: python scripts/build_db.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())

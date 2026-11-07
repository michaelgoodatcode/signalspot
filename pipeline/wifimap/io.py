"""Reading raw collector exports and the spot list."""
from __future__ import annotations

import glob
import os
from pathlib import Path

import pandas as pd

from .config import RAW_COLUMNS, SPOT_COLUMNS


def find_raw_files(raw_dir: str | Path) -> list[str]:
    files = sorted(glob.glob(os.path.join(str(raw_dir), "**", "*.csv"), recursive=True))
    return [f for f in files if not os.path.basename(f).startswith(".")]


def read_raw(files: list[str]) -> pd.DataFrame:
    """Read one or more collector CSV exports. Everything is read as text; cleaning parses types."""
    if not files:
        raise FileNotFoundError(
            "No raw CSV files found. Export readings from the collector page and put the CSVs in "
            "data/raw/ (local) or upload them to the raw volume (Databricks)."
        )
    frames = []
    for f in files:
        df = pd.read_csv(f, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        df.columns = [c.strip() for c in df.columns]
        if "reading_id" not in df.columns:
            raise ValueError(f"{f} does not look like a SignalSpot collector export (no reading_id column).")
        for col in RAW_COLUMNS:
            if col not in df.columns:
                df[col] = ""
        df = df[RAW_COLUMNS].copy()
        df["source_file"] = os.path.basename(f)
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    for col in out.columns:
        out[col] = out[col].astype(object).where(out[col].notna(), "").astype(str).str.strip()
    return out


def read_spots(path: str | Path) -> pd.DataFrame:
    spots = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    spots.columns = [c.strip() for c in spots.columns]
    missing = [c for c in SPOT_COLUMNS if c not in spots.columns]
    if missing:
        raise ValueError(f"spots.csv is missing columns: {missing}")
    spots = spots[SPOT_COLUMNS].copy()
    for col in SPOT_COLUMNS:
        spots[col] = spots[col].astype(str).str.strip()
    problems = []
    if (spots["spot_id"] == "").any():
        problems.append("some rows have an empty spot_id")
    dupes = spots.loc[spots["spot_id"].duplicated(), "spot_id"].tolist()
    if dupes:
        problems.append(f"duplicate spot_id values: {dupes}")
    if (spots["building_id"] == "").any():
        problems.append("some rows have an empty building_id")
    bad_indoor = spots.loc[~spots["indoor"].isin(["0", "1"]), "spot_id"].tolist()
    if bad_indoor:
        problems.append(f"indoor must be 0 or 1 for: {bad_indoor}")
    lat = pd.to_numeric(spots["lat"], errors="coerce")
    lng = pd.to_numeric(spots["lng"], errors="coerce")
    bad_coords = spots.loc[(spots["lat"] != "") & lat.isna() | (spots["lng"] != "") & lng.isna(), "spot_id"].tolist()
    if bad_coords:
        problems.append(f"lat/lng not numeric for: {bad_coords}")
    if problems:
        raise ValueError("spots.csv problems: " + "; ".join(problems))
    spots["indoor"] = spots["indoor"].astype(int)
    spots["lat"] = lat
    spots["lng"] = lng
    spots["coord_source"] = spots["coord_source"].where(spots["coord_source"] != "", "approx")
    return spots.reset_index(drop=True)

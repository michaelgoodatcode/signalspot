"""Validation and cleaning of raw speed-test readings.

Every row that is removed is written to the QA log with a reason, so the
methodology can report exactly what was dropped and why.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import NUMERIC_RAW_COLUMNS, VALID_NETWORK_TYPES, VALID_STATUSES, window_for_hour

CARRIER_ALIASES = {
    "at&t": "AT&T", "att": "AT&T", "at and t": "AT&T", "at t": "AT&T",
    "verizon": "Verizon", "vzw": "Verizon",
    "t-mobile": "T-Mobile", "tmobile": "T-Mobile", "t mobile": "T-Mobile",
    "other": "Other",
}

BROWSER_TYPE_MAP = {"wifi": "wifi", "cellular": "cell"}


def _blank(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip() == ""


def normalize_carrier(name: str) -> str:
    key = " ".join(str(name).strip().lower().split())
    return CARRIER_ALIASES.get(key, str(name).strip())


def clean(raw: pd.DataFrame, spots: pd.DataFrame, config: dict,
          event_start: str | None = None, event_end: str | None = None,
          allow_synthetic: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (clean_readings, qa_log)."""
    df = raw.copy().reset_index(drop=True)
    qa_rows: list[dict] = []

    def drop(mask: pd.Series, reason: str, detail_col: str | None = None):
        nonlocal df
        mask = mask.fillna(False).astype(bool)
        if mask.any():
            for _, r in df[mask].iterrows():
                qa_rows.append({
                    "reading_id": r.get("reading_id", ""),
                    "source_file": r.get("source_file", ""),
                    "reason": reason,
                    "detail": str(r.get(detail_col, "")) if detail_col else "",
                })
            df = df[~mask].reset_index(drop=True)

    for col in ("network_type", "status", "browser_conn_type", "crowd_level"):
        df[col] = df[col].astype(str).str.strip().str.lower()

    drop(_blank(df["reading_id"]), "missing_reading_id")

    # Duplicates come from exporting the same phone more than once. If any copy was
    # flagged as bad by the collector, the reading stays flagged.
    flagged_ids = set(df.loc[df["exclude"].isin(["1", "true", "True", "yes"]), "reading_id"])
    df.loc[df["reading_id"].isin(flagged_ids), "exclude"] = "1"
    drop(df["reading_id"].duplicated(keep="first"), "duplicate_reading_id", "reading_id")

    if not allow_synthetic:
        drop(df["app_version"].str.lower().str.startswith("synthetic"), "synthetic_row", "app_version")

    drop(df["exclude"] == "1", "flagged_by_collector", "exclude_reason")

    ts = pd.to_datetime(df["ts_utc"], utc=True, errors="coerce")
    drop(ts.isna(), "bad_timestamp", "ts_utc")
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True, errors="coerce")

    if event_start:
        start = pd.Timestamp(event_start)
        start = start.tz_localize(config["timezone"]) if start.tzinfo is None else start
        drop(df["ts_utc"] < start.tz_convert("UTC"), "outside_event_window", "ts_utc")
    if event_end:
        end = pd.Timestamp(event_end)
        end = end.tz_localize(config["timezone"]) if end.tzinfo is None else end
        drop(df["ts_utc"] > end.tz_convert("UTC"), "outside_event_window", "ts_utc")

    known_spots = set(spots["spot_id"])
    drop(~df["spot_id"].isin(known_spots), "unknown_spot", "spot_id")
    drop(~df["network_type"].isin(VALID_NETWORK_TYPES), "bad_network_type", "network_type")
    drop(_blank(df["network_name"]), "missing_network_name")
    drop(~df["status"].isin(VALID_STATUSES), "bad_status", "status")

    # Android Chrome reports the connection type; if it contradicts what the collector
    # selected (e.g. "cell" chosen but the phone was still on Wi-Fi) the reading is wrong.
    browser = df["browser_conn_type"].map(BROWSER_TYPE_MAP)
    drop(browser.notna() & (browser != df["network_type"]), "network_mismatch", "browser_conn_type")

    for col in NUMERIC_RAW_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)

    max_mbps = config["plausibility"]["max_mbps"]
    max_lat = config["plausibility"]["max_latency_ms"]
    implausible = (
        (df["download_mbps"] < 0) | (df["download_mbps"] > max_mbps)
        | (df["upload_mbps"] < 0) | (df["upload_mbps"] > max_mbps)
        | (df["latency_ms"] < 0) | (df["latency_ms"] > max_lat)
        | (df["jitter_ms"] < 0) | (df["jitter_ms"] > max_lat)
    )
    drop(implausible, "implausible_value")

    # ---- derived fields ----
    spot_info = spots.set_index("spot_id")
    df["building_id"] = df["spot_id"].map(spot_info["building_id"])
    df["floor"] = df["spot_id"].map(spot_info["floor"]).astype(str)
    df["indoor"] = df["spot_id"].map(spot_info["indoor"]).astype(int)

    is_cell = df["network_type"] == "cell"
    df.loc[is_cell, "network_name"] = df.loc[is_cell, "network_name"].map(normalize_carrier)
    df["network_name"] = df["network_name"].astype(str).str.strip()
    df["network_key"] = df["network_type"] + ":" + df["network_name"]

    # The speed test engine reports 0 (not "missing") when every request fails. A latency
    # of 0 ms is physically impossible, so non-positive latencies are treated as missing,
    # and a reading only "has data" if something was actually measured.
    for col in ("latency_ms", "down_loaded_latency_ms", "up_loaded_latency_ms"):
        df.loc[df[col] <= 0, col] = np.nan
    df.loc[df["latency_ms"].isna(), "jitter_ms"] = np.nan
    df["has_data"] = (df["download_mbps"] > 0) | (df["upload_mbps"] > 0) | df["latency_ms"].notna()
    # A test reported as ok/partial but with no numbers at all is a failure.
    df.loc[~df["has_data"], "status"] = "failed"
    # Failed tests and missing directions count as 0 Mbps: a test that could not
    # move data is evidence of a dead zone, not a missing value.
    df["eff_down_mbps"] = df["download_mbps"].fillna(0.0)
    df["eff_up_mbps"] = df["upload_mbps"].fillna(0.0)

    local = df["ts_utc"].dt.tz_convert(config["timezone"])
    df["ts_local"] = local.dt.strftime("%Y-%m-%dT%H:%M:%S")
    df["local_date"] = local.dt.strftime("%Y-%m-%d")
    df["local_hour"] = local.dt.hour.astype(int)
    df["day_of_week"] = local.dt.strftime("%A")
    df["window_id"] = df["local_hour"].map(lambda h: window_for_hour(int(h), config["windows"]))

    decimals = config["gps"]["round_decimals"]
    df["gps_lat"] = df["gps_lat"].round(decimals)
    df["gps_lng"] = df["gps_lng"].round(decimals)

    # Store UTC as a naive timestamp (Spark/Delta treat it as UTC with session tz = UTC).
    df["ts_utc"] = df["ts_utc"].dt.tz_convert("UTC").dt.tz_localize(None)

    ordered = [
        "reading_id", "ts_utc", "ts_local", "local_date", "local_hour", "day_of_week", "window_id",
        "collector_id", "device_model", "spot_id", "building_id", "floor", "indoor",
        "network_type", "network_name", "network_key", "cell_gen", "crowd_level", "retest_group",
        "status", "has_data", "download_mbps", "upload_mbps", "eff_down_mbps", "eff_up_mbps",
        "latency_ms", "jitter_ms", "down_loaded_latency_ms", "up_loaded_latency_ms",
        "cf_rtc_class", "duration_s", "gps_lat", "gps_lng", "gps_acc_m",
        "browser_conn_type", "notes", "error", "app_version", "source_file",
    ]
    clean_df = df[ordered].sort_values(["ts_utc", "reading_id"]).reset_index(drop=True)
    qa = pd.DataFrame(qa_rows, columns=["reading_id", "source_file", "reason", "detail"])
    return clean_df, qa


def retest_variability(clean_df: pd.DataFrame) -> dict:
    """Median absolute % difference between back-to-back repeat tests at the same spot."""
    out = {}
    rt = clean_df[(clean_df["retest_group"].astype(str) != "") & clean_df["has_data"]]
    for net_type in ["all"] + sorted(rt["network_type"].unique().tolist()):
        sub = rt if net_type == "all" else rt[rt["network_type"] == net_type]
        diffs = {"up": [], "down": [], "latency": []}
        n_pairs = 0
        for _, g in sub.groupby("retest_group"):
            if len(g) < 2:
                continue
            g = g.sort_values("ts_utc").iloc[:2]
            n_pairs += 1
            for key, col in (("up", "eff_up_mbps"), ("down", "eff_down_mbps"), ("latency", "latency_ms")):
                a, b = float(g[col].iloc[0]), float(g[col].iloc[1])
                if np.isnan(a) or np.isnan(b):
                    continue
                mean = (a + b) / 2
                diffs[key].append(0.0 if mean == 0 else abs(a - b) / mean * 100)
        out[net_type] = {
            "n_pairs": n_pairs,
            **{f"median_abs_pct_diff_{k}": (float(np.median(v)) if v else None) for k, v in diffs.items()},
        }
    return out

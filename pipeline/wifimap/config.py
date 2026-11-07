"""Shared configuration and schema definitions.

Thresholds and time windows live in docs/shared/config.json so the Python
pipeline, the phone collector and the web app all use the same numbers.
"""
from __future__ import annotations

import json
from pathlib import Path

PIPELINE_VERSION = "1.0.0"

PKG_DIR = Path(__file__).resolve().parent
REPO_ROOT = PKG_DIR.parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "docs" / "shared" / "config.json"
DEFAULT_SPOTS_PATH = REPO_ROOT / "docs" / "data" / "spots.csv"
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "raw"
DEFAULT_PROCESSED_DIR = REPO_ROOT / "data" / "processed"
DEFAULT_APP_JSON = REPO_ROOT / "docs" / "data" / "spots_scored.json"

# Columns written by docs/collector/collector.js (keep in sync with RAW_COLUMNS there).
RAW_COLUMNS = [
    "reading_id", "ts_utc", "collector_id", "device_model", "spot_id",
    "network_type", "network_name", "cell_gen", "crowd_level", "retest_group",
    "status", "download_mbps", "upload_mbps", "latency_ms", "jitter_ms",
    "down_loaded_latency_ms", "up_loaded_latency_ms", "cf_rtc_class", "duration_s",
    "gps_lat", "gps_lng", "gps_acc_m", "browser_conn_type",
    "exclude", "exclude_reason", "error", "notes", "app_version",
]

NUMERIC_RAW_COLUMNS = [
    "download_mbps", "upload_mbps", "latency_ms", "jitter_ms",
    "down_loaded_latency_ms", "up_loaded_latency_ms", "duration_s",
    "gps_lat", "gps_lng", "gps_acc_m",
]

SPOT_COLUMNS = [
    "spot_id", "building_id", "building_name", "floor", "zone", "label",
    "indoor", "lat", "lng", "coord_source",
]

VALID_NETWORK_TYPES = {"wifi", "cell"}
VALID_STATUSES = {"ok", "partial", "failed"}

# Column types for the Delta tables written by the Databricks notebook.
CLEAN_TYPES = {
    "reading_id": "string", "ts_utc": "timestamp", "ts_local": "string",
    "local_date": "string", "local_hour": "long", "day_of_week": "string",
    "window_id": "string", "collector_id": "string", "device_model": "string",
    "spot_id": "string", "building_id": "string", "floor": "string", "indoor": "long",
    "network_type": "string", "network_name": "string", "network_key": "string",
    "cell_gen": "string", "crowd_level": "string", "retest_group": "string",
    "status": "string", "has_data": "boolean",
    "download_mbps": "double", "upload_mbps": "double",
    "eff_down_mbps": "double", "eff_up_mbps": "double",
    "latency_ms": "double", "jitter_ms": "double",
    "down_loaded_latency_ms": "double", "up_loaded_latency_ms": "double",
    "cf_rtc_class": "string", "duration_s": "double",
    "gps_lat": "double", "gps_lng": "double", "gps_acc_m": "double",
    "browser_conn_type": "string", "notes": "string", "error": "string",
    "app_version": "string", "source_file": "string",
}

QA_TYPES = {"reading_id": "string", "source_file": "string", "reason": "string", "detail": "string"}

STATS_TYPES = {
    "spot_id": "string", "network_key": "string", "network_type": "string",
    "window_id": "string", "n": "long", "n_with_data": "long", "fail_rate": "double",
    "down_med": "double", "down_p25": "double", "down_p75": "double",
    "up_med": "double", "up_p25": "double", "up_p75": "double",
    "lat_med": "double", "jit_med": "double", "share_5g": "double",
    "tier": "string", "zoom_score": "double", "low_confidence": "boolean",
    "first_ts_local": "string", "last_ts_local": "string",
}

SPOTS_TYPES = {
    "spot_id": "string", "building_id": "string", "building_name": "string",
    "floor": "string", "zone": "string", "label": "string", "indoor": "long",
    "lat": "double", "lng": "double", "coord_source": "string",
}


def load_config(path: str | Path | None = None) -> dict:
    """Load docs/shared/config.json (or an explicit path)."""
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(p, encoding="utf-8") as fh:
        cfg = json.load(fh)
    window_ids = [w["id"] for w in cfg["windows"]]
    if len(set(window_ids)) != len(window_ids) or "all" in window_ids:
        raise ValueError("Window ids must be unique and must not be 'all'.")
    covered = set()
    for w in cfg["windows"]:
        covered.update(_hours(w["start_hour"], w["end_hour"]))
    if covered != set(range(24)):
        missing = sorted(set(range(24)) - covered)
        raise ValueError(f"Time windows must cover all 24 hours; missing hours: {missing}")
    return cfg


def _hours(start: int, end: int) -> list[int]:
    if start == end:
        return list(range(24))
    if start < end:
        return list(range(start, end))
    return list(range(start, 24)) + list(range(0, end))


def window_for_hour(hour: int, windows: list[dict]) -> str:
    """Return the id of the window containing a local hour (handles windows that wrap past midnight)."""
    for w in windows:
        if hour in _hours(w["start_hour"], w["end_hour"]):
            return w["id"]
    raise ValueError(f"No window covers hour {hour}")

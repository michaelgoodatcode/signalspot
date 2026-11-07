"""Per spot x network x time-window statistics and Zoom readiness tiers."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TIER_ORDER = {"good": 0, "ok": 1, "poor": 2, "dead": 3}


def _q(values: pd.Series, q: float) -> float:
    v = values.dropna().astype(float)
    return float(np.quantile(v, q)) if len(v) else float("nan")


def zoom_tier(s: dict, tiers: dict) -> str:
    """Classify a stats row as good / ok / poor / dead for a video call."""
    if s["n_with_data"] == 0 or s["fail_rate"] >= 0.5 or (s["up_med"] < 0.1 and s["down_med"] < 0.1):
        return "dead"
    for name in ("good", "ok"):
        t = tiers[name]
        lat_ok = not math.isnan(s["lat_med"]) and s["lat_med"] <= t["latency_ms"]
        jit_ok = math.isnan(s["jit_med"]) or s["jit_med"] <= t["jitter_ms"]
        if (s["up_med"] >= t["up_mbps"] and s["down_med"] >= t["down_mbps"] and lat_ok and jit_ok
                and s["fail_rate"] <= t["max_fail_rate"]):
            return name
    return "poor"


def zoom_score(s: dict, w: dict) -> float:
    """0-100 score used to rank spots within a tier. Weights come from config.json."""
    if s["n_with_data"] == 0:
        return 0.0
    clip = lambda x: max(0.0, min(1.0, x))
    s_up = clip(s["up_med"] / w["up_cap_mbps"])
    s_down = clip(s["down_med"] / w["down_cap_mbps"])
    lat = s["lat_med"]
    s_lat = 0.0 if math.isnan(lat) else clip((w["latency_ceiling_ms"] - lat) / (w["latency_ceiling_ms"] - w["latency_floor_ms"]))
    jit = s["jit_med"]
    s_jit = 0.5 if math.isnan(jit) else clip((w["jitter_ceiling_ms"] - jit) / w["jitter_ceiling_ms"])
    raw = w["up"] * s_up + w["down"] * s_down + w["latency"] * s_lat + w["jitter"] * s_jit
    return round(100 * raw * (1 - s["fail_rate"]), 1)


def _stats_for_group(g: pd.DataFrame, config: dict) -> dict:
    n = int(len(g))
    n_data = int(g["has_data"].sum())
    s = {
        "n": n,
        "n_with_data": n_data,
        "fail_rate": round(1 - n_data / n, 3) if n else 1.0,
        "down_med": _q(g["eff_down_mbps"], 0.5),
        "down_p25": _q(g["eff_down_mbps"], 0.25),
        "down_p75": _q(g["eff_down_mbps"], 0.75),
        "up_med": _q(g["eff_up_mbps"], 0.5),
        "up_p25": _q(g["eff_up_mbps"], 0.25),
        "up_p75": _q(g["eff_up_mbps"], 0.75),
        "lat_med": _q(g["latency_ms"], 0.5),
        "jit_med": _q(g["jitter_ms"], 0.5),
        "share_5g": float("nan"),
        "first_ts_local": str(g["ts_local"].min()),
        "last_ts_local": str(g["ts_local"].max()),
    }
    if (g["network_type"] == "cell").all():
        gens = g["cell_gen"].astype(str)
        known = gens[gens != ""]
        if len(known):
            s["share_5g"] = round(float((known == "5G").mean()), 3)
    s["tier"] = zoom_tier(s, config["zoom_tiers"])
    s["zoom_score"] = zoom_score(s, config["zoom_score_weights"])
    s["low_confidence"] = n < config["low_confidence_n"]
    return s


STATS_COLUMNS = [
    "spot_id", "network_key", "network_type", "window_id", "n", "n_with_data", "fail_rate",
    "down_med", "down_p25", "down_p75", "up_med", "up_p25", "up_p75", "lat_med", "jit_med",
    "share_5g", "tier", "zoom_score", "low_confidence", "first_ts_local", "last_ts_local",
]


def aggregate(clean_df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Long table: one row per (spot, network, window) plus window_id='all'."""
    rows = []
    if len(clean_df):
        for (spot_id, net_key), g in clean_df.groupby(["spot_id", "network_key"], sort=True):
            base = {"spot_id": spot_id, "network_key": net_key, "network_type": g["network_type"].iloc[0]}
            rows.append({**base, "window_id": "all", **_stats_for_group(g, config)})
            for win_id, gw in g.groupby("window_id", sort=True):
                rows.append({**base, "window_id": win_id, **_stats_for_group(gw, config)})
    stats = pd.DataFrame(rows, columns=STATS_COLUMNS)
    if len(stats):
        for col in ("down_med", "down_p25", "down_p75", "up_med", "up_p25", "up_p75", "lat_med", "jit_med"):
            stats[col] = stats[col].astype(float).round(2)
        stats["n"] = stats["n"].astype(int)
        stats["n_with_data"] = stats["n_with_data"].astype(int)
        stats["low_confidence"] = stats["low_confidence"].astype(bool)
    return stats

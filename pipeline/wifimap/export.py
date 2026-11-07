"""Build the JSON the web app reads, plus a human-readable collection summary."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .aggregate import TIER_ORDER
from .clean import retest_variability
from .config import PIPELINE_VERSION


def _py(v, nd: int = 2):
    """Convert numpy / NaN values into JSON-safe Python values (floats rounded to `nd` decimals)."""
    if v is None:
        return None
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, nd)
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return v


def resolve_spot_coords(spots: pd.DataFrame, clean_df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Pick coordinates for each spot.

    Priority: coordinates typed into spots.csv with coord_source='manual' > median of
    accurate GPS fixes recorded at the spot > approximate coordinates in spots.csv >
    the building's centroid.
    """
    out = spots.copy()
    gps_cfg = config["gps"]
    for i, sp in out.iterrows():
        if sp["coord_source"] == "manual" and not (pd.isna(sp["lat"]) or pd.isna(sp["lng"])):
            continue
        max_acc = gps_cfg["indoor_max_accuracy_m"] if sp["indoor"] == 1 else gps_cfg["outdoor_max_accuracy_m"]
        fixes = clean_df[(clean_df["spot_id"] == sp["spot_id"]) & clean_df["gps_lat"].notna()
                         & clean_df["gps_lng"].notna() & (clean_df["gps_acc_m"] <= max_acc)]
        if len(fixes):
            out.at[i, "lat"] = round(float(fixes["gps_lat"].median()), gps_cfg["round_decimals"])
            out.at[i, "lng"] = round(float(fixes["gps_lng"].median()), gps_cfg["round_decimals"])
            out.at[i, "coord_source"] = "gps_median"
        elif pd.isna(sp["lat"]) or pd.isna(sp["lng"]):
            out.at[i, "coord_source"] = "unplaced"
    # Fill anything still unplaced with its building's centroid.
    centroids = building_centroids(out)
    for i, sp in out.iterrows():
        if sp["coord_source"] == "unplaced" and sp["building_id"] in centroids:
            c = centroids[sp["building_id"]]
            out.at[i, "lat"], out.at[i, "lng"] = c["lat"], c["lng"]
            out.at[i, "coord_source"] = "building_fallback"
    return out


def building_centroids(spots: pd.DataFrame) -> dict:
    placed = spots[spots["lat"].notna() & spots["lng"].notna() & (spots["coord_source"] != "unplaced")]
    cents = {}
    for bid, g in placed.groupby("building_id"):
        indoor = g[g["indoor"] == 1]
        use = indoor if len(indoor) else g
        cents[bid] = {"lat": round(float(use["lat"].mean()), 5), "lng": round(float(use["lng"].mean()), 5)}
    return cents


def build_app_json(spots: pd.DataFrame, stats: pd.DataFrame, clean_df: pd.DataFrame,
                   qa: pd.DataFrame, n_raw: int, config: dict) -> dict:
    spots_r = resolve_spot_coords(spots, clean_df, config)
    cents = building_centroids(spots_r)

    synthetic = bool(len(clean_df)) and bool(clean_df["app_version"].str.lower().str.startswith("synthetic").any())

    networks = []
    if len(clean_df):
        for key, g in clean_df.groupby("network_key", sort=True):
            networks.append({
                "key": key, "type": g["network_type"].iloc[0], "name": g["network_name"].iloc[0],
                "n_readings": int(len(g)), "n_spots": int(g["spot_id"].nunique()),
            })
        # Wi-Fi first, then by number of readings.
        networks.sort(key=lambda n: (n["type"] != "wifi", -n["n_readings"]))

    stat_fields = ["n", "n_with_data", "fail_rate", "down_med", "down_p25", "down_p75",
                   "up_med", "up_p25", "up_p75", "lat_med", "jit_med", "share_5g",
                   "tier", "zoom_score", "low_confidence", "first_ts_local", "last_ts_local"]
    by_spot: dict = {}
    for _, r in stats.iterrows():
        by_spot.setdefault(r["spot_id"], {}).setdefault(r["network_key"], {})[r["window_id"]] = {
            f: _py(r[f]) for f in stat_fields
        }

    spot_list = []
    for _, sp in spots_r.iterrows():
        spot_list.append({
            "spot_id": sp["spot_id"], "building_id": sp["building_id"], "building_name": sp["building_name"],
            "floor": sp["floor"], "zone": sp["zone"], "label": sp["label"], "indoor": int(sp["indoor"]),
            "lat": _py(sp["lat"], 6), "lng": _py(sp["lng"], 6), "coord_source": sp["coord_source"],
            "stats": by_spot.get(sp["spot_id"], {}),
        })

    buildings = []
    for bid, g in spots_r.groupby("building_id", sort=True):
        c = cents.get(bid, {"lat": None, "lng": None})
        buildings.append({"building_id": bid, "building_name": g["building_name"].iloc[0],
                          "lat": c["lat"], "lng": c["lng"], "n_spots": int(len(g))})

    dropped = qa[qa["reason"] != "duplicate_reading_id"] if len(qa) else qa
    meta = {
        "project": config.get("project", "SignalSpot"),
        "repo_url": config.get("repo_url", ""),
        "pipeline_version": PIPELINE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "synthetic": synthetic,
        "timezone": config["timezone"],
        "n_raw_rows": int(n_raw),
        "n_readings": int(len(clean_df)),
        "n_dropped": int(len(dropped)),
        "dropped_by_reason": {k: int(v) for k, v in (qa["reason"].value_counts().items() if len(qa) else [])},
        "n_spots_total": int(len(spots_r)),
        "n_spots_covered": int(clean_df["spot_id"].nunique()) if len(clean_df) else 0,
        "n_collectors": int(clean_df["collector_id"].nunique()) if len(clean_df) else 0,
        "collection_start_local": str(clean_df["ts_local"].min()) if len(clean_df) else None,
        "collection_end_local": str(clean_df["ts_local"].max()) if len(clean_df) else None,
        "days_of_week": sorted(clean_df["day_of_week"].unique().tolist()) if len(clean_df) else [],
        "windows": config["windows"],
        "zoom_tiers": config["zoom_tiers"],
        "zoom_score_weights": config["zoom_score_weights"],
        "low_confidence_n": config["low_confidence_n"],
        "walk_speed_m_per_min": config["walk_speed_m_per_min"],
        "retest": retest_variability(clean_df) if len(clean_df) else {},
        "tier_order": TIER_ORDER,
    }
    return {"meta": meta, "networks": networks, "buildings": buildings, "spots": spot_list}


def write_json(obj: dict, path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False, allow_nan=False)
        fh.write("\n")


def collection_summary_md(app: dict, clean_df: pd.DataFrame, qa: pd.DataFrame, stats: pd.DataFrame) -> str:
    m = app["meta"]
    L = []
    L.append("# Collection summary (auto-generated)\n")
    L.append(f"_Generated {m['generated_at_utc']} by pipeline v{m['pipeline_version']}. Do not edit by hand; "
             "re-run the pipeline instead._\n")
    if m["synthetic"]:
        L.append("> **WARNING: this summary was built from SYNTHETIC test data, not real measurements.**\n")
    L.append("## At a glance\n")
    L.append("| Item | Value |\n|---|---|")
    L.append(f"| Collection window (local time, {m['timezone']}) | {m['collection_start_local']} → {m['collection_end_local']} |")
    L.append(f"| Days of week covered | {', '.join(m['days_of_week']) or '—'} |")
    L.append(f"| Raw rows exported | {m['n_raw_rows']} |")
    L.append(f"| Readings kept after cleaning | {m['n_readings']} |")
    L.append(f"| Rows removed (excluding duplicate copies) | {m['n_dropped']} |")
    L.append(f"| Spots covered / defined | {m['n_spots_covered']} / {m['n_spots_total']} |")
    L.append(f"| Collectors | {m['n_collectors']} |")
    L.append("")
    if len(clean_df):
        L.append("## Readings by building and network\n")
        pv = pd.crosstab(clean_df["building_id"], clean_df["network_key"], margins=True, margins_name="Total")
        L.append(_md_table(pv.reset_index().rename(columns={"building_id": "Building"})))
        L.append("\n## Readings by time window\n")
        order = [w["id"] for w in m["windows"]]
        pv2 = pd.crosstab(clean_df["window_id"], clean_df["network_type"], margins=True, margins_name="Total")
        pv2 = pv2.reindex([o for o in order if o in pv2.index] + ["Total"])
        L.append(_md_table(pv2.reset_index().rename(columns={"window_id": "Window"})))
        L.append("\n## Test outcomes\n")
        L.append(_md_table(clean_df["status"].value_counts().rename_axis("Status").reset_index(name="Readings")))
        allw = stats[stats["window_id"] == "all"]
        if len(allw):
            L.append("\n## Zoom readiness of spots (all times combined)\n")
            pv3 = pd.crosstab(allw["network_key"], allw["tier"])
            pv3 = pv3.reindex(columns=[t for t in ["good", "ok", "poor", "dead"] if t in pv3.columns])
            L.append(_md_table(pv3.reset_index().rename(columns={"network_key": "Network"})))
    L.append("\n## Repeatability (back-to-back retests)\n")
    rt = m.get("retest", {})
    if rt and rt.get("all", {}).get("n_pairs"):
        L.append("| Network type | Pairs | Median abs. % diff (upload) | (download) | (latency) |\n|---|---|---|---|---|")
        for k, v in rt.items():
            f = lambda x: "—" if x is None else f"{x:.1f}%"
            L.append(f"| {k} | {v['n_pairs']} | {f(v['median_abs_pct_diff_up'])} | {f(v['median_abs_pct_diff_down'])} | {f(v['median_abs_pct_diff_latency'])} |")
    else:
        L.append("No retest pairs recorded yet. Use the collector's “run twice” option at ~10% of spots.")
    L.append("\n## Rows removed during cleaning\n")
    if len(qa):
        L.append(_md_table(qa["reason"].value_counts().rename_axis("Reason").reset_index(name="Rows")))
        L.append("\n`duplicate_reading_id` rows are extra copies from exporting the same phone twice; no data is lost.")
    else:
        L.append("None.")
    L.append("")
    return "\n".join(L)


def _md_table(df: pd.DataFrame) -> str:
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in r.tolist()) + " |")
    return "\n".join(lines)

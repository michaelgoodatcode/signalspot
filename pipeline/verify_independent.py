"""Independent check of the SignalSpot pipeline, using only the Python standard library.

Recomputes every spot x time-window result straight from the raw phone exports
(data/raw/*.csv) without importing pandas, numpy or the wifimap package, then
compares them with what the pipeline wrote to data/processed/spot_stats.csv.

Usage (from the repo root):
    python3 pipeline/verify_independent.py
"""
import csv
import glob
import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = json.load(open(os.path.join(ROOT, "docs", "shared", "config.json"), encoding="utf-8"))
TZ = ZoneInfo(CONFIG["timezone"])
MISMATCH_M = CONFIG.get("gps", {}).get("building_mismatch_m", 400)


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)]


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def quantile(values, q):
    """Linear-interpolation quantile (same definition as numpy's default)."""
    v = sorted(values)
    if not v:
        return None
    pos = (len(v) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def metres(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(a))


def window_for(hour):
    for w in CONFIG["windows"]:
        s, e = w["start_hour"], w["end_hour"]
        if (s < e and s <= hour < e) or (s > e and (hour >= s or hour < e)) or s == e:
            return w["id"]
    raise ValueError(hour)


# ---- spots and building centres (mean of indoor spots) ----
spots = {s["spot_id"]: s for s in read_csv(os.path.join(ROOT, "docs", "data", "spots.csv"))}
by_building = {}
for s in spots.values():
    if num(s["lat"]) is not None and num(s["lng"]) is not None:
        by_building.setdefault(s["building_id"], []).append(s)
centre = {}
for b, ss in by_building.items():
    use = [s for s in ss if s["indoor"] == "1"] or ss
    centre[b] = (statistics.fmean(num(s["lat"]) for s in use), statistics.fmean(num(s["lng"]) for s in use))

# ---- raw rows ----
raw = []
for f in sorted(glob.glob(os.path.join(ROOT, "data", "raw", "**", "*.csv"), recursive=True)):
    if not os.path.basename(f).startswith("."):
        raw += read_csv(f)

removed, kept, seen = {}, [], set()
flagged = {r["reading_id"] for r in raw if r.get("exclude") in ("1", "true", "True", "yes")}


def remove(reason):
    removed[reason] = removed.get(reason, 0) + 1


for r in raw:
    rid = r["reading_id"]
    if not rid:
        remove("missing_reading_id"); continue
    if rid in seen:
        remove("duplicate_reading_id"); continue
    seen.add(rid)
    if r.get("app_version", "").lower().startswith("synthetic"):
        remove("synthetic_row"); continue
    if rid in flagged:
        remove("flagged_by_collector"); continue
    try:
        ts = datetime.fromisoformat(r["ts_utc"].replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        remove("bad_timestamp"); continue
    if r["spot_id"] not in spots:
        remove("unknown_spot"); continue
    b = spots[r["spot_id"]]["building_id"]
    lat, lng, acc = num(r["gps_lat"]), num(r["gps_lng"]), num(r["gps_acc_m"])
    if None not in (lat, lng, acc) and b in centre and acc <= 100 \
            and metres(lat, lng, *centre[b]) > MISMATCH_M:
        remove("gps_building_mismatch"); continue
    net = r["network_type"].lower()
    if net not in ("wifi", "cell"):
        remove("bad_network_type"); continue
    if not r["network_name"]:
        remove("missing_network_name"); continue
    if r["status"].lower() not in ("ok", "partial", "failed"):
        remove("bad_status"); continue
    browser = {"wifi": "wifi", "cellular": "cell"}.get(r["browser_conn_type"].lower())
    if browser and browser != net:
        remove("network_mismatch"); continue
    down, up, lat_ms, jit = num(r["download_mbps"]), num(r["upload_mbps"]), num(r["latency_ms"]), num(r["jitter_ms"])
    if lat_ms is not None and lat_ms <= 0:
        lat_ms = None
    if lat_ms is None:
        jit = None
    name = r["network_name"]
    if net == "cell" and name.lower() in ("at&t", "att", "at and t", "at t"):
        name = "AT&T"
    kept.append({
        "spot": r["spot_id"], "net": f"{net}:{name}", "window": window_for(ts.astimezone(TZ).hour),
        "has_data": (down or 0) > 0 or (up or 0) > 0 or lat_ms is not None,
        "down": down or 0.0, "up": up or 0.0, "lat": lat_ms, "jit": jit,
    })


def tier(n_data, fail, down, up, lat, jit):
    if n_data == 0 or fail >= 0.5 or (up < 0.1 and down < 0.1):
        return "dead"
    for name in ("good", "ok"):
        t = CONFIG["zoom_tiers"][name]
        if (up >= t["up_mbps"] and down >= t["down_mbps"] and lat is not None and lat <= t["latency_ms"]
                and (jit is None or jit <= t["jitter_ms"]) and fail <= t["max_fail_rate"]):
            return name
    return "poor"


groups = {}
for k in kept:
    for w in ("all", k["window"]):
        groups.setdefault((k["spot"], k["net"], w), []).append(k)

mine = {}
for key, g in groups.items():
    n, n_data = len(g), sum(x["has_data"] for x in g)
    fail = round(1 - n_data / n, 3)
    d = [x["down"] for x in g]; u = [x["up"] for x in g]
    lats = [x["lat"] for x in g if x["lat"] is not None]; jits = [x["jit"] for x in g if x["jit"] is not None]
    med = lambda v: quantile(v, 0.5) if v else None
    mine[key] = {
        "n": n, "fail_rate": fail,
        "down_med": quantile(d, .5), "down_p25": quantile(d, .25), "down_p75": quantile(d, .75),
        "up_med": quantile(u, .5), "up_p25": quantile(u, .25), "up_p75": quantile(u, .75),
        "lat_med": med(lats), "jit_med": med(jits),
        "tier": tier(n_data, fail, quantile(d, .5), quantile(u, .5), med(lats), med(jits)),
    }

# ---- compare with the pipeline's output ----
theirs = {(r["spot_id"], r["network_key"], r["window_id"]): r
          for r in read_csv(os.path.join(ROOT, "data", "processed", "spot_stats.csv"))}

mismatches = []
for key in sorted(set(mine) | set(theirs)):
    a, b = mine.get(key), theirs.get(key)
    if a is None or b is None:
        mismatches.append(f"{key}: only in {'pipeline' if a is None else 'this check'}")
        continue
    for field, mv in a.items():
        pv = b[field]
        if field == "tier":
            ok = mv == pv
        elif mv is None:
            ok = pv == ""
        else:
            # The pipeline stores 2 decimals, and numpy and Python break half-cent ties
            # differently (3.705 -> 3.70 vs 3.71), so allow half a cent.
            ok = pv != "" and abs(mv - float(pv)) <= 0.005 + 1e-9
        if not ok:
            mismatches.append(f"{key} {field}: this check={mv} pipeline={pv}")

print("SignalSpot independent check (Python standard library only)")
print(f"Raw rows: {len(raw)} | kept: {len(kept)} | removed: "
      + ", ".join(f"{k}={v}" for k, v in sorted(removed.items())))
print(f"Compared {len(mine)} spot x time-window results with data/processed/spot_stats.csv")
print("Checked per result: tests, failure rate, upload and download (median, p25, p75),")
print("                    median latency, median jitter, Zoom tier")
for m in mismatches:
    print("  MISMATCH", m)
print(f"Mismatches: {len(mismatches)} / {len(set(mine) | set(theirs))}")
sys.exit(1 if mismatches else 0)

"""Generate SYNTHETIC collector exports for testing the pipeline and app.

These rows are fake. Every row has app_version='synthetic-test', which the
pipeline drops unless --allow-synthetic is passed, and the app shows a red
"synthetic data" banner if any such row reaches it. Never put this output in
data/raw/.

Usage:
    python tests/make_synthetic.py OUT_DIR [--seed 7]
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "pipeline"))
from wifimap.config import RAW_COLUMNS  # noqa: E402

# Rough "truth" per zone, used only to make fake numbers look plausible.
ZONE_PROFILE = {
    "study area": (180, 90, 18), "lobby / main hall": (120, 60, 22),
    "far hallway / corner": (40, 15, 35), "stairwell": (3, 0.8, 120), "outdoor": (25, 8, 45),
}
CARRIERS = {"AT&T": 0.9, "Verizon": 1.1, "T-Mobile": 1.3}


def fake_reading(rng, spot, net_type, net_name, ts, collector, retest_group=""):
    zone = spot["zone"]
    down0, up0, lat0 = ZONE_PROFILE.get(zone, (60, 20, 30))
    if net_type == "cell":
        f = CARRIERS.get(net_name, 1.0)
        down0, up0, lat0 = (60 * f, 12 * f, 40) if zone != "stairwell" else (6 * f, 1.5 * f, 90)
    evening_load = 0.7 if ts.hour in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10) else 1.0
    fail_p = 0.45 if zone == "stairwell" else 0.03
    status = "failed" if rng.random() < fail_p else "ok"
    row = {c: "" for c in RAW_COLUMNS}
    row.update({
        "reading_id": str(uuid.UUID(int=rng.getrandbits(128))),
        "ts_utc": ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z",
        "collector_id": collector, "device_model": "Synthetic Phone",
        "spot_id": spot["spot_id"], "network_type": net_type, "network_name": net_name,
        "cell_gen": (rng.choice(["5G", "5G", "LTE"]) if net_type == "cell" else ""),
        "crowd_level": rng.choice(["low", "medium", "high"]),
        "retest_group": retest_group, "status": status, "app_version": "synthetic-test",
        "duration_s": f"{rng.uniform(15, 40):.1f}",
    })
    if status == "ok":
        row["download_mbps"] = f"{max(0.1, rng.gauss(down0, down0 * 0.25)) * evening_load:.2f}"
        row["upload_mbps"] = f"{max(0.05, rng.gauss(up0, up0 * 0.25)) * evening_load:.2f}"
        row["latency_ms"] = f"{max(5, rng.gauss(lat0, lat0 * 0.2)):.1f}"
        row["jitter_ms"] = f"{max(0.5, rng.gauss(lat0 * 0.15, 2)):.1f}"
        row["down_loaded_latency_ms"] = f"{float(row['latency_ms']) * 2.1:.1f}"
        row["up_loaded_latency_ms"] = f"{float(row['latency_ms']) * 2.6:.1f}"
        row["cf_rtc_class"] = rng.choice(["good", "great", "average"])
    else:
        row["error"] = "Failed to fetch"
    if spot["indoor"] == "0":
        row["gps_lat"] = f"{spot['true_lat'] + rng.gauss(0, 0.00005):.4f}"
        row["gps_lng"] = f"{spot['true_lng'] + rng.gauss(0, 0.00005):.4f}"
        row["gps_acc_m"] = f"{rng.uniform(4, 15):.0f}"
    return row


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)
    rng = random.Random(args.seed)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    with open(REPO / "docs" / "data" / "spots.csv", newline="", encoding="utf-8") as fh:
        spots = list(csv.DictReader(fh))
    bcoords = {s["building_id"]: (float(s["lat"]), float(s["lng"])) for s in spots if s["lat"]}
    offsets = {"N": (0.0006, 0), "S": (-0.0006, 0), "E": (0, 0.0007), "W": (0, -0.0007), "PLAZA": (0.0003, 0.0004)}
    for s in spots:
        if s["indoor"] == "0":
            b = bcoords[s["building_id"]]
            o = offsets[s["spot_id"].split("-")[-1]]
            s["true_lat"], s["true_lng"] = b[0] + o[0], b[1] + o[1]

    # Two collection rounds: Saturday afternoon and Saturday evening (local time = UTC-6).
    rounds = [datetime(2026, 11, 7, 20, 0, tzinfo=timezone.utc), datetime(2026, 11, 8, 1, 30, tzinfo=timezone.utc)]
    collectors = {"C1": "AT&T", "C2": "Verizon", "C3": "T-Mobile"}
    rows_by_collector = {c: [] for c in collectors}
    for r_i, start in enumerate(rounds):
        t = {c: start + timedelta(minutes=rng.randint(0, 5)) for c in collectors}
        for i, spot in enumerate(spots):
            if r_i == 1 and spot["building_id"] == "MSC" and spot["indoor"] == "1" and spot["floor"] == "2":
                continue  # leave some gaps so coverage is uneven, like real life
            c = list(collectors)[i % len(collectors)]
            t[c] += timedelta(seconds=rng.randint(60, 150))
            wifi = "TAMU_WiFi" if spot["indoor"] == "1" or rng.random() < 0.6 else "eduroam"
            group = f"rt-{uuid.UUID(int=rng.getrandbits(128)).hex[:8]}" if rng.random() < 0.1 else ""
            rows_by_collector[c].append(fake_reading(rng, spot, "wifi", wifi, t[c], c, group))
            if group:
                t[c] += timedelta(seconds=45)
                rows_by_collector[c].append(fake_reading(rng, spot, "wifi", wifi, t[c], c, group))
            t[c] += timedelta(seconds=rng.randint(40, 70))
            rows_by_collector[c].append(fake_reading(rng, spot, "cell", collectors[c], t[c], c))

    # Edge cases the pipeline must handle.
    c1 = rows_by_collector["C1"]
    c1[3]["exclude"], c1[3]["exclude_reason"] = "1", "walked away mid-test"
    c1[5]["browser_conn_type"] = "wifi" if c1[5]["network_type"] == "cell" else "cellular"
    bad = dict(c1[0]); bad["reading_id"] = "bad-ts"; bad["ts_utc"] = "not-a-time"; c1.append(bad)
    unk = dict(c1[1]); unk["reading_id"] = "unknown-spot"; unk["spot_id"] = "NOPE-9-X"; c1.append(unk)
    imp = dict(c1[2]); imp["reading_id"] = "implausible"; imp["download_mbps"] = "99999"; c1.append(imp)
    c1[7]["notes"] = 'notes with, a comma and "quotes"'

    written = []
    for c, rows in rows_by_collector.items():
        p = out / f"signalspot_{c}_synthetic.csv"
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=RAW_COLUMNS)
            w.writeheader()
            w.writerows(rows)
        written.append(p)
    # Second export from C2 that overlaps the first (duplicates).
    p = out / "signalspot_C2_synthetic_again.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=RAW_COLUMNS)
        w.writeheader()
        w.writerows(rows_by_collector["C2"][:10])
    written.append(p)
    print(f"Wrote {sum(len(r) for r in rows_by_collector.values()) + 10} synthetic rows to {out}")
    return written


if __name__ == "__main__":
    main()

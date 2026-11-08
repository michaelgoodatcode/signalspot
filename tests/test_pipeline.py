"""Tests for the SignalSpot pipeline. Run from the repo root:  python -m pytest -q"""
from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "pipeline"))

from wifimap.aggregate import aggregate, zoom_score, zoom_tier  # noqa: E402
from wifimap.clean import clean, normalize_carrier  # noqa: E402
from wifimap.config import RAW_COLUMNS, load_config, window_for_hour  # noqa: E402
from wifimap.export import build_app_json, resolve_spot_coords  # noqa: E402
from wifimap.io import read_raw, read_spots  # noqa: E402
from wifimap.pipeline import run, write_outputs  # noqa: E402

CFG = load_config()
SPOTS = read_spots(REPO / "docs" / "data" / "spots.csv")


def raw_row(**kw) -> dict:
    r = {c: "" for c in RAW_COLUMNS}
    r.update({"reading_id": kw.pop("reading_id", "r1"), "ts_utc": "2026-11-07T20:00:00.000Z",
              "collector_id": "C1", "spot_id": "ZACH-1-STUDY", "network_type": "wifi",
              "network_name": "TAMU_WiFi", "status": "ok", "download_mbps": "100",
              "upload_mbps": "50", "latency_ms": "20", "jitter_ms": "3", "app_version": "1.0.0",
              "source_file": "t.csv"})
    r.update({k: str(v) for k, v in kw.items()})
    return r


def raw_df(rows) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ---------- config ----------

def test_windows_cover_every_hour_and_wrap_midnight():
    ids = [window_for_hour(h, CFG["windows"]) for h in range(24)]
    assert ids[23] == "night" and ids[0] == "night" and ids[5] == "night"
    assert ids[6] == "morning" and ids[14] == "afternoon" and ids[21] == "evening"


def test_load_config_rejects_gaps(tmp_path):
    bad = json.loads((REPO / "docs/shared/config.json").read_text())
    bad["windows"] = bad["windows"][:2]
    p = tmp_path / "c.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="cover all 24 hours"):
        load_config(p)


# ---------- tiers ----------

def stats(**kw):
    s = {"n": 3, "n_with_data": 3, "fail_rate": 0.0, "up_med": 10.0, "down_med": 10.0,
         "lat_med": 30.0, "jit_med": 5.0}
    s.update(kw)
    return s


@pytest.mark.parametrize("kw,expected", [
    ({}, "good"),
    ({"up_med": 2.6, "down_med": 1.8, "lat_med": 100, "jit_med": 30}, "good"),   # exactly at threshold
    ({"up_med": 2.59}, "ok"),
    ({"up_med": 1.2, "down_med": 1.2, "lat_med": 150, "jit_med": 50}, "ok"),
    ({"up_med": 1.19}, "poor"),
    ({"lat_med": 151}, "poor"),
    ({"lat_med": float("nan")}, "poor"),
    ({"jit_med": float("nan")}, "good"),                                        # unknown jitter doesn't block
    ({"fail_rate": 0.3}, "ok"),
    ({"fail_rate": 0.5}, "dead"),
    ({"n_with_data": 0}, "dead"),
    ({"up_med": 0.05, "down_med": 0.05}, "dead"),
])
def test_zoom_tier(kw, expected):
    assert zoom_tier(stats(**kw), CFG["zoom_tiers"]) == expected


def test_zoom_score_is_bounded_and_monotonic():
    w = CFG["zoom_score_weights"]
    best = zoom_score(stats(up_med=50, down_med=50, lat_med=10, jit_med=0), w)
    worse = zoom_score(stats(up_med=1, down_med=1, lat_med=180, jit_med=50), w)
    assert best == 100.0
    assert 0 <= worse < best
    assert zoom_score(stats(n_with_data=0), w) == 0.0
    assert zoom_score(stats(fail_rate=0.5), w) == pytest.approx(zoom_score(stats(), w) * 0.5, abs=0.1)


# ---------- cleaning ----------

def test_clean_drops_with_reasons_and_keeps_good_rows():
    rows = [
        raw_row(reading_id="ok1"),
        raw_row(reading_id=""),                                              # missing id
        raw_row(reading_id="dup"), raw_row(reading_id="dup"),                # duplicate
        raw_row(reading_id="flag", exclude="1", exclude_reason="bad"),       # flagged
        raw_row(reading_id="ts", ts_utc="garbage"),                          # bad timestamp
        raw_row(reading_id="spot", spot_id="NOPE"),                          # unknown spot
        raw_row(reading_id="net", network_type="bluetooth"),                 # bad network type
        raw_row(reading_id="name", network_name=""),                         # missing network name
        raw_row(reading_id="st", status="weird"),                            # bad status
        raw_row(reading_id="mm", network_type="cell", network_name="Verizon", browser_conn_type="wifi"),
        raw_row(reading_id="imp", upload_mbps="-3"),
        raw_row(reading_id="syn", app_version="synthetic-test"),
    ]
    c, qa = clean(raw_df(rows), SPOTS, CFG)
    assert sorted(c["reading_id"]) == ["dup", "ok1"]
    reasons = dict(zip(qa["reading_id"], qa["reason"]))
    assert reasons["flag"] == "flagged_by_collector"
    assert reasons["ts"] == "bad_timestamp"
    assert reasons["spot"] == "unknown_spot"
    assert reasons["net"] == "bad_network_type"
    assert reasons["name"] == "missing_network_name"
    assert reasons["st"] == "bad_status"
    assert reasons["mm"] == "network_mismatch"
    assert reasons["imp"] == "implausible_value"
    assert reasons["syn"] == "synthetic_row"
    assert (qa["reason"] == "duplicate_reading_id").sum() == 1
    assert (qa["reason"] == "missing_reading_id").sum() == 1


def test_flag_on_later_export_excludes_all_copies():
    rows = [raw_row(reading_id="x"), raw_row(reading_id="x", exclude="1", exclude_reason="redo")]
    c, qa = clean(raw_df(rows), SPOTS, CFG)
    assert len(c) == 0
    assert set(qa["reason"]) == {"duplicate_reading_id", "flagged_by_collector"}


def test_failed_tests_count_as_zero_and_status_is_corrected():
    rows = [
        raw_row(reading_id="f", status="failed", download_mbps="", upload_mbps="", latency_ms="", jitter_ms=""),
        raw_row(reading_id="liar", status="ok", download_mbps="", upload_mbps="", latency_ms="", jitter_ms=""),
        raw_row(reading_id="part", status="partial", upload_mbps=""),
    ]
    c, _ = clean(raw_df(rows), SPOTS, CFG)
    c = c.set_index("reading_id")
    assert c.loc["f", "eff_down_mbps"] == 0 and c.loc["f", "eff_up_mbps"] == 0
    assert c.loc["liar", "status"] == "failed" and not c.loc["liar", "has_data"]
    assert c.loc["part", "eff_up_mbps"] == 0 and c.loc["part", "eff_down_mbps"] == 100
    assert bool(c.loc["part", "has_data"])


def test_all_zero_result_from_engine_is_a_failure():
    # What @cloudflare/speedtest reports when every request fails (seen in a real run).
    rows = [raw_row(reading_id="z", status="ok", download_mbps="0", upload_mbps="0", latency_ms="0",
                    jitter_ms="0", down_loaded_latency_ms="0")]
    c, _ = clean(raw_df(rows), SPOTS, CFG)
    r = c.iloc[0]
    assert r["status"] == "failed" and not r["has_data"]
    assert math.isnan(r["latency_ms"]) and math.isnan(r["jitter_ms"]) and math.isnan(r["down_loaded_latency_ms"])
    st = aggregate(c, CFG)
    assert st.iloc[0]["tier"] == "dead" and math.isnan(st.iloc[0]["lat_med"])


def test_gps_far_from_labeled_building_is_dropped():
    # The first real export: labeled Zachry, but the phone was at the MSC (~960 m away).
    rows = [
        raw_row(reading_id="at-msc", spot_id="ZACH-1-STUDY", gps_lat="30.6126", gps_lng="-96.3414", gps_acc_m="12"),
        raw_row(reading_id="at-zach", spot_id="ZACH-1-LOBBY", gps_lat="30.6214", gps_lng="-96.3401", gps_acc_m="20"),
        raw_row(reading_id="rough-gps", spot_id="ZACH-2-STUDY", gps_lat="30.6126", gps_lng="-96.3414", gps_acc_m="500"),
        raw_row(reading_id="no-gps", spot_id="ZACH-2-LOBBY"),
        raw_row(reading_id="outdoor-ok", spot_id="MSC-OUT-N", gps_lat="30.6128", gps_lng="-96.3416", gps_acc_m="8"),
    ]
    c, qa = clean(raw_df(rows), SPOTS, CFG)
    assert sorted(c["reading_id"]) == ["at-zach", "no-gps", "outdoor-ok", "rough-gps"]
    r = qa[qa["reading_id"] == "at-msc"].iloc[0]
    assert r["reason"] == "gps_building_mismatch" and r["detail"].endswith("m from ZACH")
    assert 900 < float(r["detail"].split()[0]) < 1000
    assert "_gps_detail" not in c.columns


def test_spots_description_column_is_optional(tmp_path):
    assert (SPOTS["description"] == "").all()          # template has no description column
    p = tmp_path / "s.csv"
    p.write_text("spot_id,building_id,building_name,floor,zone,label,description,indoor,lat,lng,coord_source\n"
                 "A-1,A,Bldg A,1,study area,Floor 1 – booths,\"By the east windows, past the cafe\",1,30.6,-96.3,approx\n")
    s = read_spots(p)
    assert s.loc[0, "description"] == "By the east windows, past the cafe"


def test_carrier_normalization_and_network_key():
    assert normalize_carrier("at&t") == "AT&T" and normalize_carrier(" TMobile ") == "T-Mobile"
    rows = [raw_row(reading_id="a", network_type="CELL", network_name="verizon")]
    c, _ = clean(raw_df(rows), SPOTS, CFG)
    assert c.loc[0, "network_key"] == "cell:Verizon"


def test_local_time_window_and_event_filter():
    rows = [raw_row(reading_id="early", ts_utc="2026-11-07T12:00:00.000Z"),   # 6:00 AM CST
            raw_row(reading_id="late", ts_utc="2026-11-08T04:30:00.000Z")]    # 10:30 PM CST Nov 7
    c, _ = clean(raw_df(rows), SPOTS, CFG)
    c = c.set_index("reading_id")
    assert c.loc["early", "window_id"] == "morning" and c.loc["early", "local_hour"] == 6
    assert c.loc["late", "window_id"] == "night" and c.loc["late", "local_date"] == "2026-11-07"
    c2, qa = clean(raw_df(rows), SPOTS, CFG, event_start="2026-11-07T09:00")
    assert list(c2["reading_id"]) == ["late"]
    assert qa.loc[0, "reason"] == "outside_event_window"


# ---------- aggregation ----------

def test_aggregate_medians_and_all_window():
    rows = [raw_row(reading_id=f"r{i}", upload_mbps=str(u), download_mbps=str(d), latency_ms=str(l))
            for i, (u, d, l) in enumerate([(1, 10, 10), (3, 20, 20), (5, 30, 90)])]
    rows.append(raw_row(reading_id="f", status="failed", upload_mbps="", download_mbps="", latency_ms="",
                        jitter_ms=""))
    c, _ = clean(raw_df(rows), SPOTS, CFG)
    st = aggregate(c, CFG)
    allw = st[st["window_id"] == "all"].iloc[0]
    assert allw["n"] == 4 and allw["n_with_data"] == 3 and allw["fail_rate"] == 0.25
    assert allw["up_med"] == 2.0          # median of [0,1,3,5]
    assert allw["down_med"] == 15.0       # median of [0,10,20,30]
    assert allw["lat_med"] == 20.0        # failed test has no latency
    # up 2.0 < 2.6 rules out "good"; up >= 1.2 and fail_rate 0.25 <= 0.4 gives "ok"
    assert allw["tier"] == "ok"
    assert set(st["window_id"]) == {"all", "afternoon"}


def test_aggregate_tier_poor_case():
    rows = [raw_row(reading_id=f"r{i}", upload_mbps="0.8", download_mbps="5") for i in range(3)]
    c, _ = clean(raw_df(rows), SPOTS, CFG)
    st = aggregate(c, CFG)
    assert st[st["window_id"] == "all"].iloc[0]["tier"] == "poor"


def test_aggregate_empty():
    c, _ = clean(raw_df([raw_row(reading_id="x", spot_id="NOPE")]), SPOTS, CFG)
    st = aggregate(c, CFG)
    assert len(st) == 0
    app = build_app_json(SPOTS, st, c, pd.DataFrame(columns=["reading_id", "source_file", "reason", "detail"]), 1, CFG)
    assert app["meta"]["n_readings"] == 0 and len(app["spots"]) == len(SPOTS)


# ---------- coordinates ----------

def test_coordinates_gps_median_manual_and_fallback():
    spots = SPOTS.copy()
    spots.loc[spots["spot_id"] == "ZACH-1-LOBBY", ["lat", "lng", "coord_source"]] = [30.6215, -96.3399, "manual"]
    rows = [raw_row(reading_id=f"g{i}", spot_id="ZACH-OUT-N", gps_lat=f"{30.6220 + i * 0.0001:.4f}",
                    gps_lng="-96.3400", gps_acc_m="8") for i in range(3)]
    rows.append(raw_row(reading_id="inaccurate", spot_id="ZACH-OUT-N", gps_lat="31", gps_lng="-97", gps_acc_m="500"))
    rows.append(raw_row(reading_id="manualspot", spot_id="ZACH-1-LOBBY", gps_lat="30.6230", gps_lng="-96.3410", gps_acc_m="5"))
    # Indoor GPS fixes at two different MSC spots (like the real data): they move the building, not the spots.
    rows.append(raw_row(reading_id="in1", spot_id="MSC-1-STUDY", gps_lat="30.6118", gps_lng="-96.3414", gps_acc_m="26"))
    rows.append(raw_row(reading_id="in2", spot_id="MSC-2-LOBBY", gps_lat="30.6127", gps_lng="-96.3413", gps_acc_m="19"))
    rows.append(raw_row(reading_id="in3", spot_id="MSC-2-HALL", gps_lat="30.6127", gps_lng="-96.3414", gps_acc_m="80"))  # too rough
    c, _ = clean(raw_df(rows), spots, CFG)
    r = resolve_spot_coords(spots, c, CFG).set_index("spot_id")
    assert r.loc["ZACH-OUT-N", "coord_source"] == "gps_median"
    assert r.loc["ZACH-OUT-N", "lat"] == pytest.approx(30.6221)
    assert r.loc["ZACH-1-LOBBY", "lat"] == 30.6215                  # manual wins over GPS
    assert r.loc["ZACH-OUT-S", "coord_source"] == "building_fallback"
    # Every MSC indoor spot sits at the GPS-derived building position (median of the 2 accurate fixes).
    msc_in = r[(r["building_id"] == "MSC") & (r["indoor"] == 1)]
    assert set(msc_in["coord_source"]) == {"building_gps"}
    assert msc_in["lat"].nunique() == 1 and msc_in["lat"].iloc[0] == pytest.approx(30.61225)
    # Other indoor spots without GPS use the typed (approx) building coordinates.
    assert r.loc["EVAN-2-STUDY", "coord_source"] == "building" and r.loc["EVAN-2-STUDY", "lat"] == pytest.approx(30.6169)
    assert not r["lat"].isna().any()


# ---------- io ----------

def test_read_spots_validation(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("spot_id,building_id,building_name,floor,zone,label,indoor,lat,lng,coord_source\n"
                 "A,B,Bldg,1,z,l,1,30,-96,approx\nA,B,Bldg,1,z,l,2,x,-96,approx\n")
    with pytest.raises(ValueError) as e:
        read_spots(p)
    msg = str(e.value)
    assert "duplicate spot_id" in msg and "indoor must be 0 or 1" in msg and "not numeric" in msg


def test_read_raw_tolerates_missing_columns_and_bom(tmp_path):
    p = tmp_path / "old.csv"
    p.write_text("﻿reading_id,ts_utc,spot_id,network_type,network_name,status,download_mbps\n"
                 "a,2026-11-07T20:00:00Z,ZACH-1-STUDY,wifi,TAMU_WiFi,ok,12\n", encoding="utf-8")
    df = read_raw([str(p)])
    assert list(df.columns[:len(RAW_COLUMNS)]) == RAW_COLUMNS and df.loc[0, "upload_mbps"] == ""
    with pytest.raises(FileNotFoundError):
        read_raw([])


# ---------- end to end ----------

def test_end_to_end_synthetic(tmp_path):
    raw_dir = tmp_path / "raw"
    subprocess.run([sys.executable, str(REPO / "tests" / "make_synthetic.py"), str(raw_dir)], check=True,
                   capture_output=True)
    files = sorted(str(p) for p in raw_dir.glob("*.csv"))

    # Without --allow-synthetic every row is rejected.
    res0 = run(files, REPO / "docs/data/spots.csv")
    assert res0.app_json["meta"]["n_readings"] == 0

    res = run(files, REPO / "docs/data/spots.csv", allow_synthetic=True)
    paths = write_outputs(res, tmp_path / "app.json", tmp_path / "processed")
    data = json.loads(Path(paths["app_json"]).read_text())      # strict JSON, no NaN
    m = data["meta"]
    assert m["synthetic"] is True
    assert m["n_readings"] > 150 and m["n_spots_covered"] == 55
    assert m["retest"]["all"]["n_pairs"] > 0
    assert {"flagged_by_collector", "bad_timestamp", "unknown_spot", "network_mismatch",
            "implausible_value", "duplicate_reading_id"} <= set(m["dropped_by_reason"])
    assert any(n["key"] == "wifi:TAMU_WiFi" for n in data["networks"])
    for sp in data["spots"]:
        assert sp["lat"] is not None and sp["lng"] is not None
        for net in sp["stats"].values():
            assert "all" in net
            for s in net.values():
                assert s["tier"] in {"good", "ok", "poor", "dead"}
    # Coordinates keep full GPS precision (regression: they were once rounded to 2 decimals = ~1 km).
    out_n = next(sp for sp in data["spots"] if sp["spot_id"] == "ZACH-OUT-N")
    zach = next(b for b in data["buildings"] if b["building_id"] == "ZACH")
    assert out_n["coord_source"] == "gps_median" and 30.6214 < out_n["lat"] < 30.6222
    assert 40 < abs(out_n["lat"] - zach["lat"]) * 111320 < 100   # ~66 m north of the building
    stair = [sp for sp in data["spots"] if sp["zone"] == "stairwell" and "wifi:TAMU_WiFi" in sp["stats"]]
    assert any(sp["stats"]["wifi:TAMU_WiFi"]["all"]["tier"] in {"dead", "poor"} for sp in stair)
    text = Path(paths["readings_clean"]).read_text()
    assert 'notes with, a comma and ""quotes""' in text
    assert "SYNTHETIC" in Path(paths["summary"]).read_text()


def test_build_local_cli(tmp_path):
    raw_dir = tmp_path / "raw"
    subprocess.run([sys.executable, str(REPO / "tests" / "make_synthetic.py"), str(raw_dir)], check=True,
                   capture_output=True)
    out = subprocess.run([sys.executable, str(REPO / "pipeline" / "build_local.py"), "--raw-dir", str(raw_dir),
                          "--allow-synthetic", "--app-json", str(tmp_path / "a.json"),
                          "--processed-dir", str(tmp_path / "p")], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "SYNTHETIC" in out.stdout
    assert (tmp_path / "p" / "readings_clean.csv").exists()

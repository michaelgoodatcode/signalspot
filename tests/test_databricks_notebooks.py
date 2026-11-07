"""Execute the three Databricks notebooks end to end against a local Spark session.

The notebooks are Databricks "source format" .py files, which are plain Python
(markdown cells are comments). This test provides stand-ins for the Databricks
globals (dbutils.widgets, display) and points the volume root at a temp folder.
It runs in a copy of the repo so the real docs/data is never touched.

Skipped automatically when pyspark is not installed:
    pip install pyspark==3.5.3 && python -m pytest -q tests/test_databricks_notebooks.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pyspark = pytest.importorskip("pyspark")

REPO = Path(__file__).resolve().parents[1]


class FakeWidgets:
    def __init__(self, overrides):
        self.values, self.overrides = {}, overrides

    def text(self, name, default, label=None):
        self.values[name] = self.overrides.get(name, default)

    def dropdown(self, name, default, choices, label=None):
        v = self.overrides.get(name, default)
        assert v in choices, f"{v!r} not in {choices}"
        self.values[name] = v

    def get(self, name):
        return self.values[name]


class FakeDbutils:
    def __init__(self, overrides):
        self.widgets = FakeWidgets(overrides)


def run_notebook(path: Path, spark, overrides: dict, cwd: Path, shown: list):
    def display(df):
        rows = df.collect() if hasattr(df, "collect") else df
        shown.append((path.name, rows))

    g = {"__name__": "__main__", "spark": spark, "dbutils": FakeDbutils(overrides),
         "display": display, "displayHTML": print}
    src = path.read_text()
    assert src.startswith("# Databricks notebook source"), f"{path.name} is not in Databricks source format"
    old = os.getcwd()
    os.chdir(cwd)  # Databricks sets the working directory to the notebook's folder
    try:
        exec(compile(src, str(path), "exec"), g)
    finally:
        os.chdir(old)
    return g


@pytest.fixture(scope="module")
def spark(tmp_path_factory):
    from pyspark.sql import SparkSession
    wh = tmp_path_factory.mktemp("warehouse")
    s = (SparkSession.builder.master("local[2]").appName("signalspot-notebook-test")
         .config("spark.ui.enabled", "false").config("spark.sql.warehouse.dir", str(wh))
         .config("spark.sql.shuffle.partitions", "4").getOrCreate())
    s.sparkContext.setLogLevel("ERROR")
    yield s
    s.stop()


def test_notebooks_end_to_end(tmp_path, spark):
    repo = tmp_path / "repo"
    shutil.copytree(REPO, repo, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "node_modules"))
    for p in (repo / "data" / "raw").glob("*.csv"):
        p.unlink()
    vol_root = tmp_path / "Volumes"
    nb_dir = repo / "databricks"
    base = {"catalog": "", "schema": "signalspot", "volume_root": str(vol_root)}
    shown: list = []

    # 00 setup creates the volume folders.
    run_notebook(nb_dir / "00_setup.py", spark, base, nb_dir, shown)
    raw_vol = vol_root / "signalspot" / "raw"
    assert raw_vol.is_dir() and (vol_root / "signalspot" / "exports").is_dir()

    # Upload: one synthetic file set + one "real-looking" file (non-synthetic rows).
    subprocess.run([sys.executable, str(repo / "tests" / "make_synthetic.py"), str(raw_vol)], check=True,
                   capture_output=True)
    import csv
    with open(raw_vol / "signalspot_C1_synthetic.csv", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames
        rows = [r for r in reader if not r["exclude"] and not r["browser_conn_type"]][:5]
    for i, r in enumerate(rows):
        r["reading_id"], r["app_version"], r["collector_id"] = f"real-{i}", "1.0.0", "C9"
    with open(raw_vol / "signalspot_C9_real.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # 01 pipeline, keeping synthetic rows so the tables have content.
    g = run_notebook(nb_dir / "01_pipeline.py", spark, {**base, "allow_synthetic": "yes", "sync_repo": "yes"},
                     nb_dir, shown)
    meta = g["result"].app_json["meta"]
    assert meta["n_readings"] > 150 and meta["synthetic"] is True

    readings = spark.table("signalspot.readings")
    assert readings.count() == meta["n_readings"]
    types = dict(readings.dtypes)
    assert types["ts_utc"] == "timestamp" and types["upload_mbps"] == "double" and types["has_data"] == "boolean"
    # NaN must be stored as NULL (failed tests have no upload value).
    assert readings.filter("status = 'failed' AND upload_mbps IS NULL").count() > 0
    assert readings.filter("isnan(upload_mbps)").count() == 0
    # Timestamps survive the round trip as UTC.
    # (collect() converts to the machine's local zone, so compare inside Spark with session tz = UTC.)
    first = readings.selectExpr("date_format(ts_utc, 'HH') AS utc_hour", "ts_local").orderBy("ts_local").first()
    assert (int(first["utc_hour"]) - int(first["ts_local"][11:13])) % 24 == 6  # CST = UTC-6
    assert spark.table("signalspot.spot_stats").filter("window_id = 'all'").count() > 0
    assert spark.table("signalspot.spots").count() == 55
    assert spark.table("signalspot.qa_log").count() == sum(meta["dropped_by_reason"].values())

    # Outputs in the exports volume and synced into the repo copy.
    exports = vol_root / "signalspot" / "exports"
    for name in ("spots_scored.json", "readings_clean.csv", "spot_stats.csv", "qa_log.csv", "collection_summary.md"):
        assert (exports / name).exists(), name
    app = json.loads((repo / "docs" / "data" / "spots_scored.json").read_text())
    assert app["meta"]["n_readings"] == meta["n_readings"]
    copied = sorted(p.name for p in (repo / "data" / "raw").glob("*.csv"))
    assert copied == ["signalspot_C9_real.csv"], copied   # synthetic files are never copied into the dataset

    # Default run (allow_synthetic = no) drops synthetic rows and keeps the real ones.
    g2 = run_notebook(nb_dir / "01_pipeline.py", spark, {**base, "sync_repo": "no"}, nb_dir, shown)
    assert g2["result"].app_json["meta"]["n_readings"] == 5
    assert g2["result"].app_json["meta"]["synthetic"] is False

    # Re-run with synthetic so the coverage notebook has data, then run 02.
    run_notebook(nb_dir / "01_pipeline.py", spark, {**base, "allow_synthetic": "yes", "sync_repo": "no"},
                 nb_dir, shown)
    run_notebook(nb_dir / "02_coverage.py", spark, {"catalog": "", "schema": "signalspot"}, nb_dir, shown)
    for v in ("v_coverage", "v_todo", "v_dead_zones", "v_network_summary"):
        assert spark.table(f"signalspot.{v}").count() >= 0
    cov = spark.table("signalspot.v_coverage")
    assert cov.count() == 55
    summary = {r["network_key"]: r for r in spark.table("signalspot.v_network_summary").collect()}
    assert "wifi:TAMU_WiFi" in summary and summary["wifi:TAMU_WiFi"]["spots_tested"] > 0
    assert len([s for s in shown if s[0] == "02_coverage.py"]) == 4

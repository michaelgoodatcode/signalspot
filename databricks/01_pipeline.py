# Databricks notebook source
# MAGIC %md
# MAGIC # SignalSpot · 01 Pipeline
# MAGIC
# MAGIC **Raw collector CSVs (volume) → clean + validate → score every spot → Delta tables + app JSON.**
# MAGIC
# MAGIC 1. Reads every CSV in the `raw` volume.
# MAGIC 2. Cleans them with the shared `pipeline/wifimap` package (the same code `pipeline/build_local.py`
# MAGIC    runs locally, so results are reproducible without Databricks). Every removed row is logged with a reason.
# MAGIC 3. Aggregates per spot × network × time window and assigns a Zoom tier (good / ok / poor / dead).
# MAGIC 4. Writes Delta tables: `readings`, `spot_stats`, `spots`, `qa_log`.
# MAGIC 5. Writes `spots_scored.json` (what the web app reads) to the `exports` volume and, if this notebook runs in a
# MAGIC    Git folder, into the repo so you can commit & push it.
# MAGIC
# MAGIC Re-run any time new CSVs are uploaded. It overwrites its outputs, so it is safe to run repeatedly.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schema", "signalspot", "Schema")
dbutils.widgets.text("event_start", "", "Event start, local time (e.g. 2026-11-07T09:00)")
dbutils.widgets.text("event_end", "", "Event end, local time (blank = no limit)")
dbutils.widgets.dropdown("sync_repo", "yes", ["yes", "no"], "Write outputs into the Git folder")
dbutils.widgets.dropdown("allow_synthetic", "no", ["no", "yes"], "Keep SYNTHETIC test rows (testing only)")
dbutils.widgets.text("volume_root", "/Volumes", "Volume root (leave as /Volumes)")

catalog = dbutils.widgets.get("catalog").strip()
schema = dbutils.widgets.get("schema").strip()
event_start = dbutils.widgets.get("event_start").strip() or None
event_end = dbutils.widgets.get("event_end").strip() or None
sync_repo = dbutils.widgets.get("sync_repo") == "yes"
allow_synthetic = dbutils.widgets.get("allow_synthetic") == "yes"
volume_root = dbutils.widgets.get("volume_root").strip() or "/Volumes"


def q(name: str) -> str:
    return f"`{name}`"


ns = f"{q(catalog)}.{q(schema)}" if catalog else q(schema)


def table(name: str) -> str:
    return f"{ns}.{q(name)}"


# COMMAND ----------

# Import the shared pipeline package from this repo (works in a Git folder or an imported copy).
import importlib
import os
import sys


def find_repo_root(start: str) -> str:
    p = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(p, "pipeline", "wifimap")) and os.path.isfile(
                os.path.join(p, "docs", "shared", "config.json")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            raise RuntimeError(
                "Could not find the SignalSpot repo (pipeline/wifimap). Run this notebook from the repo's "
                "Git folder: Workspace → Create → Git folder → paste your GitHub repo URL.")
        p = parent


REPO_ROOT = find_repo_root(os.getcwd())
PIPELINE_DIR = os.path.join(REPO_ROOT, "pipeline")
if PIPELINE_DIR not in sys.path:
    sys.path.insert(0, PIPELINE_DIR)
for mod in [m for m in list(sys.modules) if m == "wifimap" or m.startswith("wifimap.")]:
    del sys.modules[mod]  # pick up edits without restarting Python

from wifimap.config import CLEAN_TYPES, PIPELINE_VERSION, QA_TYPES, SPOTS_TYPES, STATS_TYPES
from wifimap.io import find_raw_files
from wifimap.pipeline import run, write_outputs
from wifimap.spark_io import write_table

print(f"Repo root: {REPO_ROOT}  ·  wifimap v{PIPELINE_VERSION}")

# COMMAND ----------

RAW_DIR = os.path.join(volume_root, *[p for p in (catalog, schema, "raw") if p])
EXPORTS_DIR = os.path.join(volume_root, *[p for p in (catalog, schema, "exports") if p])
raw_files = find_raw_files(RAW_DIR)
print(f"{len(raw_files)} raw file(s) in {RAW_DIR}")
for f in raw_files:
    print("  ", os.path.basename(f))
if not raw_files:
    raise FileNotFoundError(f"No CSVs in {RAW_DIR}. Upload collector exports there (see 00_setup).")

# COMMAND ----------

result = run(
    raw_files,
    os.path.join(REPO_ROOT, "docs", "data", "spots.csv"),
    config_path=os.path.join(REPO_ROOT, "docs", "shared", "config.json"),
    event_start=event_start,
    event_end=event_end,
    allow_synthetic=allow_synthetic,
)
meta = result.app_json["meta"]
print(f"Kept {meta['n_readings']} of {meta['n_raw_rows']} rows · {meta['n_spots_covered']}/{meta['n_spots_total']} spots covered")
print("Removed by reason:", meta["dropped_by_reason"] or "none")
if meta["synthetic"]:
    print("WARNING: SYNTHETIC rows are included. The app will show a 'synthetic data' banner.")

# COMMAND ----------

# Delta tables (managed tables default to Delta on Databricks).
# Timestamps are passed to Spark as timezone-aware UTC values, so they are stored correctly
# whatever the session time zone is; setting it to UTC just makes them display as UTC.
try:
    spark.conf.set("spark.sql.session.timeZone", "UTC")
except Exception as e:
    print(f"(Could not set session time zone: {e})")
counts = {
    "readings": write_table(spark, result.clean, CLEAN_TYPES, table("readings"),
                            "Cleaned SignalSpot speed-test readings, one row per test"),
    "spot_stats": write_table(spark, result.stats, STATS_TYPES, table("spot_stats"),
                              "Per spot x network x time-window statistics and Zoom tier"),
    "spots": write_table(spark, result.spots_resolved, SPOTS_TYPES, table("spots"),
                         "Test spots with resolved coordinates"),
    "qa_log": write_table(spark, result.qa, QA_TYPES, table("qa_log"),
                          "Rows removed during cleaning and why"),
}
for name, n in counts.items():
    print(f"{table(name)}: {n} rows")

# COMMAND ----------

# Files for the app and the GitHub repo.
import shutil

os.makedirs(EXPORTS_DIR, exist_ok=True)
written = write_outputs(result, os.path.join(EXPORTS_DIR, "spots_scored.json"), EXPORTS_DIR)
print("Wrote to exports volume:")
for k, p in written.items():
    print(f"  {k}: {p}")


def is_synthetic_file(path: str) -> bool:
    import pandas as pd
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if len(df) == 0 or "app_version" not in df.columns:
        return False
    return bool(df["app_version"].str.lower().str.startswith("synthetic").all())


if sync_repo:
    try:
        repo_written = write_outputs(result, os.path.join(REPO_ROOT, "docs", "data", "spots_scored.json"),
                                     os.path.join(REPO_ROOT, "data", "processed"))
        raw_dest = os.path.join(REPO_ROOT, "data", "raw")
        os.makedirs(raw_dest, exist_ok=True)
        copied, skipped = 0, 0
        for f in raw_files:
            if is_synthetic_file(f):
                skipped += 1  # never publish synthetic test files as part of the dataset
                continue
            shutil.copyfile(f, os.path.join(raw_dest, os.path.basename(f)))
            copied += 1
        print(f"Synced into Git folder: app JSON, processed files, {copied} raw CSV(s)"
              + (f" ({skipped} synthetic file(s) skipped)" if skipped else ""))
        print("Next: open the Git folder → Commit & push. GitHub Pages will redeploy the app.")
    except OSError as e:
        print(f"Could not write into the Git folder ({e}).")
        print(f"Download the files from {EXPORTS_DIR} instead and commit them to the repo.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Quick look

# COMMAND ----------

display(spark.sql(f"""
  SELECT s.building_id, st.network_key, st.tier, COUNT(*) AS spots
  FROM {table('spot_stats')} st JOIN {table('spots')} s USING (spot_id)
  WHERE st.window_id = 'all'
  GROUP BY s.building_id, st.network_key, st.tier
  ORDER BY s.building_id, st.network_key, st.tier
"""))

# COMMAND ----------

print(result.summary_md)

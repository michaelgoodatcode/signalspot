# Databricks notebook source
# MAGIC %md
# MAGIC # SignalSpot · 00 Setup (run once)
# MAGIC
# MAGIC Creates the schema and two Unity Catalog volumes:
# MAGIC
# MAGIC | Volume | What goes in it |
# MAGIC |---|---|
# MAGIC | `raw` | CSV files exported from the phone collector. Upload them here after each collection round. |
# MAGIC | `exports` | Outputs written by `01_pipeline` (app JSON, cleaned CSVs, summary). |
# MAGIC
# MAGIC Works on Databricks Free Edition (default catalog `workspace`). Run this notebook from inside the
# MAGIC repo's Git folder.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schema", "signalspot", "Schema")
dbutils.widgets.text("volume_root", "/Volumes", "Volume root (leave as /Volumes)")

catalog = dbutils.widgets.get("catalog").strip()
schema = dbutils.widgets.get("schema").strip()
volume_root = dbutils.widgets.get("volume_root").strip() or "/Volumes"


def q(name: str) -> str:
    return f"`{name}`"


ns = f"{q(catalog)}.{q(schema)}" if catalog else q(schema)

# COMMAND ----------

import os

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {ns} COMMENT 'SignalSpot: campus Wi-Fi and cell speed tests'")

if volume_root == "/Volumes":
    for vol in ("raw", "exports"):
        spark.sql(f"CREATE VOLUME IF NOT EXISTS {ns}.{q(vol)}")
else:
    # Local test harness: plain folders stand in for volumes.
    for vol in ("raw", "exports"):
        os.makedirs(os.path.join(volume_root, *[p for p in (catalog, schema, vol) if p]), exist_ok=True)

raw_path = os.path.join(volume_root, *[p for p in (catalog, schema, "raw") if p])
exports_path = os.path.join(volume_root, *[p for p in (catalog, schema, "exports") if p])
print("Schema ready:", ns)
print("Upload collector CSVs to:", raw_path)
print("Pipeline outputs will go to:", exports_path)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next
# MAGIC 1. In **Catalog Explorer → workspace → signalspot → Volumes → raw**, click **Upload to this volume** and add
# MAGIC    the CSVs exported from each phone (it's fine to upload overlapping exports; duplicates are removed).
# MAGIC 2. Run **01_pipeline**.
# MAGIC 3. During collection, keep **02_coverage** open to see which spots still need readings.

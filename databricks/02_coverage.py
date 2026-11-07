# Databricks notebook source
# MAGIC %md
# MAGIC # SignalSpot · 02 Coverage tracker
# MAGIC
# MAGIC Keep this open during collection. After each round: upload the new CSVs → run `01_pipeline` → re-run this.
# MAGIC It answers **"where do we still need to go?"** and creates SQL views you can drop into a Databricks
# MAGIC dashboard (Dashboards → Create → add the `v_*` views as datasets).

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schema", "signalspot", "Schema")
dbutils.widgets.text("min_per_network_type", "2", "Target readings per spot per network type")
dbutils.widgets.text("min_windows", "2", "Target distinct time windows per spot")

catalog = dbutils.widgets.get("catalog").strip()
schema = dbutils.widgets.get("schema").strip()
min_n = int(dbutils.widgets.get("min_per_network_type") or 2)
min_windows = int(dbutils.widgets.get("min_windows") or 2)


def q(name: str) -> str:
    return f"`{name}`"


ns = f"{q(catalog)}.{q(schema)}" if catalog else q(schema)


def table(name: str) -> str:
    return f"{ns}.{q(name)}"


# COMMAND ----------

# Coverage per spot: how many Wi-Fi / cell readings and how many time windows so far.
spark.sql(f"""
CREATE OR REPLACE VIEW {table('v_coverage')} AS
SELECT
  s.spot_id, s.building_id, s.floor, s.label, s.indoor,
  COALESCE(SUM(CASE WHEN r.network_type = 'wifi' THEN 1 ELSE 0 END), 0) AS wifi_readings,
  COALESCE(SUM(CASE WHEN r.network_type = 'cell' THEN 1 ELSE 0 END), 0) AS cell_readings,
  COUNT(DISTINCT r.window_id) AS windows_covered,
  COUNT(DISTINCT r.collector_id) AS collectors,
  MAX(r.ts_local) AS last_reading_local
FROM {table('spots')} s
LEFT JOIN {table('readings')} r ON r.spot_id = s.spot_id
GROUP BY s.spot_id, s.building_id, s.floor, s.label, s.indoor
""")

# To-do list: spots below target, with what is missing.
spark.sql(f"""
CREATE OR REPLACE VIEW {table('v_todo')} AS
SELECT spot_id, building_id, floor, label, wifi_readings, cell_readings, windows_covered,
  CONCAT_WS(', ',
    CASE WHEN wifi_readings < {min_n} THEN CONCAT('wifi x', CAST({min_n} - wifi_readings AS STRING)) END,
    CASE WHEN cell_readings < {min_n} THEN CONCAT('cell x', CAST({min_n} - cell_readings AS STRING)) END,
    CASE WHEN windows_covered < {min_windows} THEN 'another time window' END
  ) AS still_needed
FROM {table('v_coverage')}
WHERE wifi_readings < {min_n} OR cell_readings < {min_n} OR windows_covered < {min_windows}
""")

# Dead zones and weak spots found so far (all times combined).
spark.sql(f"""
CREATE OR REPLACE VIEW {table('v_dead_zones')} AS
SELECT s.building_id, s.floor, s.label, st.network_key, st.tier, st.n, st.fail_rate,
       st.down_med, st.up_med, st.lat_med
FROM {table('spot_stats')} st JOIN {table('spots')} s ON s.spot_id = st.spot_id
WHERE st.window_id = 'all' AND st.tier IN ('dead', 'poor')
""")

# Network comparison: share of spots that are Zoom-ready on each network.
spark.sql(f"""
CREATE OR REPLACE VIEW {table('v_network_summary')} AS
SELECT network_key,
       COUNT(*) AS spots_tested,
       SUM(CASE WHEN tier IN ('good', 'ok') THEN 1 ELSE 0 END) AS zoom_ready_spots,
       ROUND(100.0 * SUM(CASE WHEN tier IN ('good', 'ok') THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_zoom_ready,
       ROUND(PERCENTILE_APPROX(up_med, 0.5), 2) AS median_spot_upload_mbps,
       SUM(CASE WHEN tier = 'dead' THEN 1 ELSE 0 END) AS dead_spots
FROM {table('spot_stats')}
WHERE window_id = 'all'
GROUP BY network_key
""")
print("Views ready:", ", ".join(table(v) for v in ("v_coverage", "v_todo", "v_dead_zones", "v_network_summary")))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Progress

# COMMAND ----------

progress = spark.sql(f"""
SELECT COUNT(*) AS spots_total,
       SUM(CASE WHEN wifi_readings >= {min_n} AND cell_readings >= {min_n} AND windows_covered >= {min_windows}
                THEN 1 ELSE 0 END) AS spots_done,
       SUM(wifi_readings + cell_readings) AS readings
FROM {table('v_coverage')}
""").collect()[0]
print(f"{progress['spots_done']} / {progress['spots_total']} spots at target · {progress['readings']} readings so far")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Where to go next (grouped by building and floor)

# COMMAND ----------

display(spark.sql(f"SELECT * FROM {table('v_todo')} ORDER BY building_id, floor, spot_id"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Dead zones found so far

# COMMAND ----------

display(spark.sql(f"SELECT * FROM {table('v_dead_zones')} ORDER BY tier, building_id, floor"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Network comparison

# COMMAND ----------

display(spark.sql(f"SELECT * FROM {table('v_network_summary')} ORDER BY pct_zoom_ready DESC"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Last 20 readings (sanity check)

# COMMAND ----------

display(spark.sql(f"""
SELECT ts_local, collector_id, spot_id, network_key, status,
       ROUND(download_mbps, 1) AS down, ROUND(upload_mbps, 1) AS up, ROUND(latency_ms) AS latency_ms
FROM {table('readings')} ORDER BY ts_utc DESC LIMIT 20
"""))

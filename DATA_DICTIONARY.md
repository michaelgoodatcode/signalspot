# Data dictionary

## `data/raw/*.csv` — collector exports (the primary dataset)

One row per speed test, exactly as recorded on the phone. Empty cell = not recorded / not measured.

| Field | Type | Description |
|---|---|---|
| `reading_id` | UUID | Unique id generated on the phone. Used to remove duplicates across exports |
| `ts_utc` | ISO 8601 UTC | Time the test started |
| `collector_id` | text | Pseudonymous team member id (C1…C9) |
| `device_model` | text | Phone model typed in setup (e.g. "iPhone 15") |
| `spot_id` | text | Spot tested; joins to `docs/data/spots.csv` |
| `network_type` | `wifi` / `cell` | Which radio was used |
| `network_name` | text | Wi-Fi SSID (e.g. `TAMU_WiFi`) or carrier (`AT&T`, `Verizon`, `T-Mobile`, `Other`) |
| `cell_gen` | `5G` / `LTE` / `3G/other` | Generation shown in the status bar (cell tests only) |
| `crowd_level` | `low` / `medium` / `high` | Collector's judgement of how busy the area was |
| `retest_group` | text | Same value on two back-to-back tests at the same spot (repeatability check) |
| `status` | `ok` / `partial` / `failed` | `failed` = no data could be measured (dead zone). `partial` = test ended early or one direction missing |
| `download_mbps` | number | Download throughput, Mbps (Cloudflare engine, 90th percentile of requests) |
| `upload_mbps` | number | Upload throughput, Mbps |
| `latency_ms` | number | Median unloaded round-trip time, ms |
| `jitter_ms` | number | Unloaded jitter, ms |
| `down_loaded_latency_ms` | number | Latency while downloading (bufferbloat indicator) |
| `up_loaded_latency_ms` | number | Latency while uploading |
| `cf_rtc_class` | text | Cloudflare's own real-time-communication rating (bad…great), for reference only |
| `duration_s` | number | How long the test took |
| `gps_lat`, `gps_lng` | number | Phone GPS, rounded to 4 decimals (~10 m). Optional |
| `gps_acc_m` | number | GPS accuracy radius reported by the phone, metres |
| `browser_conn_type` | text | `wifi` / `cellular` as reported by Android Chrome; empty on iPhone |
| `exclude` | `1` / empty | `1` = flagged as bad on the phone |
| `exclude_reason` | text | Why it was flagged |
| `error` | text | Error messages from the engine (truncated) |
| `notes` | text | Optional free-text note (no personal information) |
| `app_version` | text | Collector version. `synthetic-test` marks generated test data, which is never part of the dataset |

## `docs/data/spots.csv` — test spots

| Field | Description |
|---|---|
| `spot_id` | Unique id, e.g. `ZACH-2-STUDY`, `EVAN-OUT-N` |
| `building_id`, `building_name` | Building code and name |
| `floor` | Floor number (empty for outdoor) |
| `zone` | Type of place: study area, lobby / main hall, far hallway / corner, stairwell, outdoor |
| `label` | What collectors and app users see |
| `description` | *Optional.* Exactly where to stand (e.g. "Booths by the east windows"). If the column is missing or empty, the collector and app write a generic instruction from `zone` and `floor` |
| `indoor` | `1` indoor, `0` outdoor |
| `lat`, `lng` | Coordinates (may be empty for outdoor spots; filled from GPS) |
| `coord_source` | `manual` (typed in, wins), `approx` (building centroid), `gps` (to be filled from readings) |

## `data/processed/spots_resolved.csv` (Delta table `spots`)

spots.csv plus the coordinates the app uses. `coord_source`: `manual` (typed in), `building_gps` (indoor spot
placed at its building, whose position is the median of accurate indoor GPS fixes), `building` (same, from
spots.csv coordinates), `gps_median` (outdoor spot, median of GPS fixes with ≤30 m accuracy),
`building_fallback` (outdoor spot with no GPS yet, placed at its building).

## `data/processed/readings_clean.csv` (Delta table `readings`)

All raw fields that survive cleaning, plus:

| Field | Description |
|---|---|
| `ts_local`, `local_date`, `local_hour`, `day_of_week` | Campus time (America/Chicago) |
| `window_id` | `morning` (6–11), `midday` (11–14), `afternoon` (14–18), `evening` (18–22), `night` (22–6) |
| `building_id`, `floor`, `indoor` | Copied from spots.csv |
| `network_key` | `wifi:<SSID>` or `cell:<carrier>` |
| `has_data` | True if anything was measured |
| `eff_down_mbps`, `eff_up_mbps` | Throughput used for scoring: missing or failed counts as 0 |
| `source_file` | Which export the row came from |

## `data/processed/qa_log.csv` (Delta table `qa_log`)

Every removed row: `reading_id`, `source_file`, `reason`, `detail`. Reasons: `duplicate_reading_id`
(extra copy, no data lost), `flagged_by_collector`, `synthetic_row`, `bad_timestamp`, `outside_event_window`,
`unknown_spot`, `gps_building_mismatch` (accurate GPS fix more than 400 m from the labeled building), `bad_network_type`, `missing_network_name`, `bad_status`, `network_mismatch`,
`implausible_value`, `missing_reading_id`.

## `data/processed/spot_stats.csv` (Delta table `spot_stats`)

One row per spot × network × window, plus `window_id = all`.

| Field | Description |
|---|---|
| `n`, `n_with_data`, `fail_rate` | Tests, tests that measured something, share that failed |
| `down_med`, `down_p25`, `down_p75` | Median and quartiles of `eff_down_mbps` |
| `up_med`, `up_p25`, `up_p75` | Same for upload |
| `lat_med`, `jit_med` | Median latency / jitter of tests that measured them |
| `share_5g` | Share of cell tests on 5G |
| `tier` | `good` (Zoom-ready), `ok` (usable), `poor` (weak), `dead` (dead zone). Rules in METHODOLOGY.md |
| `zoom_score` | 0–100 ranking score within a tier (weights in `docs/shared/config.json`) |
| `low_confidence` | Fewer than 2 tests |
| `first_ts_local`, `last_ts_local` | Time span of the readings |

## `docs/data/spots_scored.json` — what the app reads

`meta` (counts, dates, thresholds, windows, repeatability, `synthetic` flag), `networks`
(key, type, name, readings, spots), `buildings` (centroids), and `spots` (each spot with resolved
coordinates and `stats[network_key][window_id]` holding the `spot_stats` fields above).

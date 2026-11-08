# SignalSpot

**Where on campus can I take a Zoom call or upload a big file on my phone's data?**

SignalSpot maps cellular quality inside and around the Memorial Student Center and Evans Library, built entirely
from speed tests we ran on foot during TAMU Datathon. Pick *Zoom call* or *Big upload* and where you are. The app
ranks the spots that will work, estimates how long an upload takes including the walk there, and shows the weak
spots to avoid.

- **App:** https://michaelgoodatcode.github.io/signalspot/
- **Collector (phone page used to gather the data):** https://michaelgoodatcode.github.io/signalspot/collector/
- **Dataset:** [data/raw/](data/raw/) (as exported from the phone) and [data/processed/](data/processed/) (cleaned)
- **Methodology, findings and limitations:** [METHODOLOGY.md](METHODOLOGY.md) · **Fields:** [DATA_DICTIONARY.md](DATA_DICTIONARY.md)

**The dataset:** 25 AT&T speed tests at 24 spots (MSC and Evans, indoors and out), Saturday Nov 7, 2026,
5:36–6:28 PM. Each test records download, upload, latency, jitter, latency under load, 5G/LTE, crowd level and GPS.
**Headline:** 11 of 12 Evans spots can handle a video call, but only 5 of 12 MSC spots. Median upload inside the
MSC was 0.48 Mbps; the test on the MSC plaza outside measured 6.2 Mbps. Everything is AT&T cellular only; see the limitations.

```
 phones (collector page)          Databricks (Free Edition)                      GitHub Pages
 ┌───────────────────────┐  CSV   ┌────────────────────────────────────┐  JSON  ┌──────────────────┐
 │ speed test per spot   │ ─────► │ raw volume → 01_pipeline:          │ ─────► │ map app          │
 │ Wi-Fi + cell, GPS,    │ upload │ clean + QA log → score per spot ×  │ commit │ Zoom / upload    │
 │ crowd level, retests  │        │ network × time window → Delta      │ & push │ ranking, dead    │
 └───────────────────────┘        │ tables + spots_scored.json         │        │ zones, popups    │
                                  │ 02_coverage: where to go next      │        └──────────────────┘
                                  └────────────────────────────────────┘
```

The same Python package (`pipeline/wifimap`) runs in the Databricks notebook and locally
(`pipeline/build_local.py`), so anyone can rebuild the app data from `data/raw/` without Databricks.

---

## Setup (about 20 minutes, do this first)

1. **Put the repo on GitHub** and turn on Pages: *Settings → Pages → Deploy from a branch → `main` / `/docs`*.
2. **Edit `docs/data/spots.csv`** so the labels match real landmarks (e.g. `Floor 2 – study area` →
   `Floor 2 – booths by the east windows`). Add or remove spots freely; keep `spot_id` unique.
   Optionally add a `description` column with exactly where to stand ("booths by the east windows");
   otherwise the collector writes a generic instruction from the zone and floor.
   The building coordinates are approximate (`coord_source=approx`); outdoor spots get their position
   from the phones' GPS automatically. If you know an exact coordinate, type it in and set `coord_source=manual`.
3. **Set `repo_url`** in `docs/shared/config.json` to your GitHub URL (the app links to this README).
4. **Smoke-test the collector on a real phone (important).** Open `/collector/` on each phone over HTTPS,
   do phone setup, and run one test on Wi-Fi and one on cellular. You should see realistic numbers
   (tens of Mbps, latency 10–80 ms). Then tap *Delete all readings* so practice runs never enter the dataset.
   The test logic was verified with a simulated engine and with the real engine offline; the live
   Cloudflare connection can only be checked from a real network.
5. **Databricks:** *Workspace → Create → Git folder* → paste the GitHub URL. Open `databricks/00_setup`
   and *Run all* (creates schema `workspace.signalspot` with volumes `raw` and `exports`).

## On the day: collection loop

1. Split up by building. Each phone gets a collector ID (C1, C2…) in *Phone setup*. Different carriers
   on the team = more networks covered.
2. Check the **where to stand** card: building code, floor, instructions and a mini map with your position.
   If it says GPS puts you at a different building, tap **Switch to …**. (The pipeline also drops readings
   whose GPS is more than 400 m from the labeled building.)
3. At each spot: run **Wi-Fi**, then turn Wi-Fi off and run **Cellular** (the collector switches for you
   and then moves you to the next spot that still needs data). Tick *Run twice* at about 1 in 10 spots.
4. A test that can't move any data is saved as `failed`: **that's a dead-zone measurement, keep it.**
   If something went wrong (wrong network, you walked away), tap *Flag bad*.
5. After each building: **Download CSV** (or *Share CSV* → AirDrop to a laptop) and upload it in
   *Catalog → workspace → signalspot → Volumes → raw → Upload to this volume*. Re-uploading overlapping
   exports is fine.
6. Run `databricks/01_pipeline` (set `event_start` to the hackathon start time), then `02_coverage` to see
   which spots still need readings. Aim for every spot to have ≥2 Wi-Fi and ≥2 cell tests in ≥2 time windows.
7. In the Git folder: **Commit & push**. Pages redeploys the app with the new data in about a minute.

## Run it without Databricks

```bash
pip install -r requirements.txt
python pipeline/build_local.py --event-start 2026-11-07T09:00   # reads data/raw/*.csv
python -m http.server -d docs 8000                               # open http://localhost:8000
```

## Tests

```bash
pip install -r requirements-dev.txt && playwright install chromium
python -m pytest -q          # pipeline, Databricks notebooks (local Spark), collector + app in a headless browser
node --test tests/js/*.test.mjs
```

The tests use **synthetic** data from `tests/make_synthetic.py`. Synthetic rows are tagged
`app_version=synthetic-test`, dropped by the pipeline unless `--allow-synthetic` is passed, never copied into
`data/raw/` by the notebook, and trigger a red banner in the app if they ever reach it.

## Repository layout

| Path | What |
|---|---|
| `docs/` | Everything GitHub Pages serves: the app (`index.html`), the collector (`collector/`), shared config and data |
| `docs/shared/config.json` | Thresholds and time windows used by pipeline, collector and app (single source of truth) |
| `docs/data/spots.csv` | The test spots |
| `docs/data/spots_scored.json` | Generated by the pipeline; what the app reads |
| `data/raw/` | **The dataset**: collector exports, one CSV per phone export |
| `data/processed/` | Cleaned readings, per-spot stats, QA log of removed rows, collection summary |
| `pipeline/wifimap/` | Cleaning, scoring and export code (pandas) |
| `databricks/` | `00_setup`, `01_pipeline`, `02_coverage` notebooks |
| `tests/` | pytest + Playwright + node tests, synthetic data generator |

## Credits

Speed tests use Cloudflare's open-source [`@cloudflare/speedtest`](https://github.com/cloudflare/speedtest)
engine (MIT, vendored in `docs/vendor/speedtest`). Map: [Leaflet](https://leafletjs.com) (BSD-2) with
© OpenStreetMap contributors tiles. Zoom bandwidth guidance: Zoom's published system requirements.

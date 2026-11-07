# 2-minute demo video: shot list

Replace every number in brackets with the real values from `data/processed/collection_summary.md` and the app. Never show synthetic data in the video.

The rubric wants: (a) how and where we collected, (b) what the dataset contains, (c) the app using it.

| Time | Shot | Say |
|---|---|---|
| 0:00–0:12 | Someone's Zoom freezing in a stairwell (staged) | "Ever had a call drop between classes? Nobody maps where campus internet actually works, so we measured it." |
| 0:12–0:40 | Walking into Zachry, phone on the collector, tapping *Run*; live numbers climbing; switching Wi-Fi off for the cell test; a stairwell test failing → **Dead zone** | "We picked 55 spots in Zachry, Evans and the MSC. At every spot we ran a Wi-Fi test and a cellular test with Cloudflare's open-source speed test engine. A test that can't move data is a dead-zone measurement, so we keep it." |
| 0:40–0:55 | Databricks: CSVs in the `raw` volume, `01_pipeline` output (kept / removed by reason), `02_coverage` to-do list | "Every export goes into Databricks. The pipeline de-duplicates, logs every removed row with a reason, and scores each spot per network and time of day. The coverage tracker told us where to go next." |
| 0:55–1:05 | `collection_summary.md`: readings by building/network/window, retest variability | "[N] tests, [M] spots, [K] collectors, with repeat tests to show how noisy a single test is." |
| 1:05–1:45 | App: *Right now* + Zoom call → ranked list → tap → popup comparing Wi-Fi vs carriers. Switch to *Big upload*, 10 GB, "I'm near MSC" → total time with walk. Dead-zone count in the summary bar | "Ask where to take a Zoom call right now. Each answer comes from our medians for this time window. For a 10 GB upload it adds the walk: [X] minutes to [spot] beats a [Y]-minute upload here." |
| 1:45–2:00 | Methodology page / limitations section | "It's one weekend of data, measured to Cloudflare not Zoom, and weekday crowds may be worse. Everything's in the repo." |

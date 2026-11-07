# Methodology

> Fill in the `[brackets]` before submitting. The counts, dates and test outcomes come from
> [data/processed/collection_summary.md](data/processed/collection_summary.md), which the pipeline regenerates
> from the raw data every time it runs, so the numbers there are always the real ones.

## Question

Students often need to join a video call or upload a large file (project videos, CAD files, datasets)
between classes. Campus Wi-Fi and cell coverage vary a lot by building, floor and corner, and nobody
publishes that. **Which spots can handle a Zoom call or a big upload, on which network, at what time of day?**

## Where and when

- **Buildings:** Zachry Engineering Education Complex, Sterling C. Evans Library & Annex, Memorial Student
  Center, plus walkways and plazas outside each.
- **When:** during TAMU Datathon, `[start date/time]` to `[end date/time]` (America/Chicago). Exact
  first and last reading times are in the collection summary.
- **Who:** `[N]` team members, identified in the data only as collector IDs C1…C`[N]`.

## Sampling design

- **Spots** (`docs/data/spots.csv`): for each building and floor, 4 indoor zones chosen to cover different
  radio conditions: a study area, a lobby or main hall, a far hallway or corner, and a stairwell. Plus 5
  outdoor points per building (four sides and the nearest plaza). `[edit if you changed the list]`
- **Networks:** campus Wi-Fi (`TAMU_WiFi`, plus `eduroam` where tested) and each team member's own carrier
  (`[AT&T / Verizon / T-Mobile]`). At every spot the collector runs Wi-Fi, then cellular with Wi-Fi turned off.
- **Time:** readings are grouped into five campus-time windows (morning, midday, afternoon, evening, night).
  The target was ≥2 readings per spot per network type across ≥2 windows. `02_coverage` tracked progress live.
- **Repeatability:** at about 1 in 10 spots the collector ran two tests back to back (`retest_group`). The
  median difference between the pair is reported in the collection summary, so readers can see how
  noisy a single test is.

## Instrument

The collector (`docs/collector/`) is a web page opened on each phone. It runs Cloudflare's open-source
speed test engine (`@cloudflare/speedtest`) against Cloudflare's edge servers with a fixed measurement
sequence for every test:

| Step | Detail |
|---|---|
| Latency | 21 unloaded round trips (1 warm-up + 20); latency = median, jitter = mean absolute difference between successive pings |
| Download | 100 kB ×4, 1 MB ×4, 10 MB ×1. Bigger sizes are skipped once a request takes over 1 s, so slow links use less data |
| Upload | Same sizes as download |
| Reported bandwidth | 90th percentile of per-request throughput (library default) |
| Limits | Whole test stops after 75 s, single request after 20 s, about 30 MB of data at most per test |

Result logging to Cloudflare's aggregate endpoint is turned off (`logAimApiUrl: null`). The phone records
the time, spot, network, cellular generation shown in the status bar, crowd level (low/medium/high, by eye),
optional GPS, and on Android the browser-reported connection type.

**Protocol:** the collector shows a "where to stand" card for every spot (building, floor, instructions, a
small map with the phone's position) and warns before a test if GPS places the phone more than 400 m from the
selected building. Stand still at the spot, phone at chest height. Wi-Fi test first; then turn Wi-Fi off in settings
and run the cellular test. Tests that move no data are saved as `failed`. These are dead-zone measurements,
not errors. Tests with an operator problem are flagged on the phone, kept in the export, and removed by the
pipeline with reason `flagged_by_collector`.

## Processing (`pipeline/wifimap`, run by `databricks/01_pipeline.py` or `pipeline/build_local.py`)

1. **Ingest:** all CSV exports are read as text. Overlapping exports are de-duplicated by `reading_id`
   (a flag on any copy wins).
2. **Validate:** a row is removed, with the reason written to `qa_log`, if it was flagged, has a bad timestamp,
   falls outside the event window, has an unknown spot, was labeled with the wrong building (the phone's GPS,
   accurate to ±100 m or better, was more than 400 m from the labeled building), an invalid network or status, implausible values
   (>3000 Mbps, negative numbers, >10 s latency), or a network mismatch (Android reported Wi-Fi when the
   collector chose cellular, or the reverse).
3. **Normalize:** carrier names are unified; times are converted to campus time and assigned a window.
   The engine reports 0 rather than "missing" when nothing could be measured, so a latency ≤ 0 is treated as
   missing, and a test counts as having data only if something was actually measured.
4. **Aggregate:** for each spot × network × window (and "all"): number of tests, failure rate, median
   and quartiles of download and upload, median latency and jitter. **Failed tests count as 0 Mbps**, because
   a test that cannot move data is evidence of a dead zone, not a missing value.
5. **Score:** each group gets a Zoom tier:

   | Tier | Rule (medians) |
   |---|---|
   | Zoom-ready | ≥2.6 Mbps up, ≥1.8 down (Zoom's 720p group-call guidance), latency ≤100 ms, jitter ≤30 ms, failure rate ≤20% |
   | Usable | ≥1.2 up and down (Zoom's 1:1 720p guidance), latency ≤150 ms, jitter ≤50 ms, failure rate ≤40% |
   | Weak | has data but below Usable |
   | Dead zone | no successful test, ≥50% of tests failed, or <0.1 Mbps both ways |

   Bandwidth cutoffs follow Zoom's published requirements; latency, jitter and failure-rate cutoffs are
   our own choices. All thresholds live in `docs/shared/config.json`.
6. **Export:** Delta tables (`readings`, `spot_stats`, `spots`, `qa_log`), CSV copies in `data/processed/`, and
   `docs/data/spots_scored.json` for the app.

## How the app uses the data

- *Right now* picks the time window for the current campus time. If a spot has no readings in that window,
  the app uses all of its readings and labels the result "all-day data".
- *Zoom call* ranks by tier, then by walking distance if you give a starting point, otherwise by a 0–100 score.
- *Big upload* estimates time = file size ÷ median upload (range from the upper and lower quartiles), adds walking
  time at 80 m/min, and never suggests a dead zone.
- Spots with fewer than 2 readings carry an `n=1` badge.

## Limitations

- **Snapshot, not a survey.** `[N]` readings over one weekend. Results with n=1 are indicative only.
- **Weekend traffic.** Collected on `[Sat/Sun]`; weekday class changes load the network more. The app warns
  weekday visitors.
- **Test server ≠ Zoom's servers.** We measure the path to Cloudflare's nearest edge. Video calls also depend
  on the route to Zoom and the other participants.
- **Device effects.** Antennas differ between phone models (`device_model` is recorded). Each carrier was
  measured mostly by one phone.
- **Upload estimates** assume the measured speed holds for the whole upload; real transfers can be slower.
- **Indoor positions are schematic.** GPS is unreliable indoors, so indoor spots are drawn around their building
  and identified by floor and label.
- **Crowd level** is a subjective three-level judgement.
- **eduroam** was tested at fewer spots than TAMU_WiFi. `[edit to match your data]`

## Privacy and ethics

No people were recorded, photographed or surveyed. Collectors are pseudonymous IDs; notes were kept free of
personal information. Tests were run only in publicly accessible areas during open hours, never in a class in
session. Test sizes are capped (~30 MB) to avoid loading shared networks. Speed tests reveal the phone's IP
address to Cloudflare, as any website visit does; no IP addresses are stored in the dataset. GPS is rounded to
4 decimals (~10 m) and only marks public campus spots.

# Methodology

> Counts and dates here come from [data/processed/collection_summary.md](data/processed/collection_summary.md),
> which the pipeline regenerates from the raw data on every run.

## Question

Students often need to join a video call or upload a large file (project videos, CAD files, datasets)
between classes. Cell coverage varies a lot by building, floor and corner, and nobody publishes that.
**Which campus spots can handle a Zoom call or a big upload on cellular data, and which ones should you avoid?**

## What we measured

| | |
|---|---|
| **Where** | Memorial Student Center (MSC) and Sterling C. Evans Library & Annex, Texas A&M, College Station. Indoors on floors 1–2 (MSC) and 1–3 (Evans), and outside each building |
| **When** | Saturday, November 7, 2026, 5:36 PM – 6:28 PM campus time (America/Chicago), during TAMU Datathon |
| **Network** | AT&T cellular (5G on 23 tests, LTE on 2), measured on one iPhone 17 Pro |
| **Who** | One team member, recorded as collector `C1` |
| **How many** | 25 speed tests at 24 spots: 12 at the MSC (8 indoor, 4 outdoor) and 12 at Evans (8 indoor, 4 outdoor). One spot (Evans floor 2 study area) was tested twice |
| **Kept / removed** | 40 rows were exported. 12 were duplicate copies from exporting the same phone twice. 3 were removed because the phone's GPS showed they were taken at the MSC while labeled as Zachry. 25 readings remain |

## Sampling design

- **Spot list** (`docs/data/spots.csv`): we planned 55 spots across Zachry, Evans and the MSC. On each floor there are
  four indoor zones chosen to cover different radio conditions: an open study area, the main lobby, a far hallway
  or corner, and a stairwell. Each building also has five outdoor points (four sides and the nearest plaza).
- **What we covered:** 24 of the 55 planned spots, in two buildings. Zachry, the upper floors of Evans and a few
  outdoor points were not reached in the time we had. The app hides untested spots by default and can show them.
- **Order:** we walked the MSC first (5:36–6:08 PM), then Evans (6:14–6:28 PM).

## Instrument

The collector (`docs/collector/`) is a web page on the phone. It runs Cloudflare's open-source speed test engine
(`@cloudflare/speedtest`) against Cloudflare's edge servers with the same measurement sequence every time:

| Step | Detail |
|---|---|
| Latency | 21 unloaded round trips (1 warm-up + 20); latency = median, jitter = mean absolute difference between successive pings |
| Download | 100 kB ×4, 1 MB ×4, 10 MB ×1. Larger sizes are skipped once a request takes over 1 s, so slow links use less data |
| Upload | Same sizes as download |
| Reported bandwidth | 90th percentile of per-request throughput (library default) |
| Limits | The whole test stops after 75 s and any single request after 20 s; at most about 30 MB of data per test |

Logging of results to Cloudflare's aggregate endpoint is turned off. For each test the phone also records the time,
spot, network, cellular generation shown in the status bar, how crowded the area looked (low/medium/high), and GPS.

**Protocol.** For every spot the collector shows a "where to stand" card: building, floor, an instruction such as
"inside a stairwell at the floor 2 landing", and a small map with the phone's position. It warns before the test if
GPS puts the phone more than 400 m from the selected building. Stand still at the spot with the phone at chest height
and run the test.

**Tests that ran out of time.** 6 MSC tests hit the 75-second limit. In 3 of them (MSC floor 1 lobby, floor 1 hallway,
floor 2 study area) the upload never finished. These are kept as `partial`, not discarded: the missing upload is
counted as 0 Mbps, because a connection that cannot finish an upload in 75 s is the thing a student needs to know about.

## Processing (`pipeline/wifimap`, run by `databricks/01_pipeline.py` or `pipeline/build_local.py`)

1. **Ingest:** all CSV exports are read as text and de-duplicated by `reading_id`.
2. **Validate:** a row is removed, with the reason written to `qa_log`, if it was flagged on the phone, has a bad
   timestamp, falls outside the event window, has an unknown spot, was labeled with the wrong building (accurate GPS
   fix more than 400 m from the labeled building), has invalid values, or reports a network type that contradicts
   the phone.
3. **Normalize:** carrier names are unified and times are converted to campus time and assigned a window. The engine
   reports 0 rather than "missing" when nothing could be measured, so a latency ≤ 0 is treated as missing.
4. **Aggregate:** for each spot × network × time window (and "all"), the pipeline computes the number of tests, the
   failure rate, the median and quartiles of download and upload, and the median latency and jitter. Failed or
   missing directions count as 0 Mbps.
5. **Score:** each spot gets a Zoom tier:

   | Tier | Rule (medians) |
   |---|---|
   | Zoom-ready | ≥2.6 Mbps up, ≥1.8 down (Zoom's 720p group-call guidance), latency ≤100 ms, jitter ≤30 ms, failure rate ≤20% |
   | Usable | ≥1.2 up and down (Zoom's 1:1 720p guidance), latency ≤150 ms, jitter ≤50 ms, failure rate ≤40% |
   | Weak | has data but below Usable |
   | Dead zone | no successful test, ≥50% of tests failed, or <0.1 Mbps both ways |

   Bandwidth cutoffs follow Zoom's published requirements; the latency, jitter and failure-rate cutoffs are our own.
   All thresholds live in `docs/shared/config.json`.
6. **Place on the map:** phone GPS indoors is only accurate to about ±20–40 m, which can't separate floors, so indoor
   spots are drawn on a ring around their building. The building's position is the median of the GPS fixes taken
   inside it. Outdoor spots use the median of their own GPS fixes (accuracy ≤30 m).
7. **Export:** Delta tables in Databricks (`readings`, `spot_stats`, `spots`, `qa_log`), CSV copies in
   `data/processed/`, and `docs/data/spots_scored.json` for the app.

**Verification.** All 48 spot × time-window results were recomputed independently from the raw CSVs with separate
code (Python standard library only) and matched the pipeline. The Databricks notebook and the local script produce
identical output.

## What we found

| | MSC indoors | MSC outdoors | Evans indoors | Evans outdoors |
|---|---|---|---|---|
| Tests | 8 | 4 | 9 | 4 |
| Median upload (Mbps) | **0.48** | 3.96 | 2.84 | 3.18 |
| Median download (Mbps) | 9.0 | 42.4 | 31.4 | 100.0 |
| Median latency (ms) | 76.5 | 50.0 | 56.5 | 53.5 |

- **Evans works; the MSC interior mostly doesn't.** 11 of 12 Evans spots can handle a video call, against 5 of 12 at
  the MSC. All four MSC floor-1 spots are Weak.
- **Upload is the bottleneck, not download.** Even where download is fine, AT&T upload is usually 2–4 Mbps, close to
  Zoom's 2.6 Mbps cutoff for 720p group calls.
- **Walk outside.** Moving from inside the MSC (median upload 0.48 Mbps) to its plaza (6.2 Mbps) changes a 2 GB
  upload from about 9 hours to about 43 minutes.
- **Latency under load.** While uploading, latency rose to a median of 133 ms and peaked at 2.3 s in Evans' floor-1
  stairwell. Cloudflare's own real-time-communication rating was "bad" on 15 of 25 tests, including 12 at spots that
  pass our Zoom tiers. Our tiers use unloaded latency, so they may be optimistic for a call made while the phone is
  also uploading.

## Limitations

- **One snapshot.** 25 tests in under an hour, one test per spot (two at one spot). Each tier rests on a single
  measurement, and phone speed tests vary from run to run; we did not run repeat tests, so we cannot report that
  variation. Every result in the app carries an `n=1` badge.
- **One carrier, one phone.** Only AT&T on one iPhone 17 Pro. Verizon or T-Mobile users may see different results.
- **No Wi-Fi.** The collector supports campus Wi-Fi (TAMU_WiFi, eduroam), but we did not collect Wi-Fi tests, so the
  app only answers for AT&T cellular. Most students indoors would be on Wi-Fi.
- **Two buildings, partly covered.** Zachry and Evans floors above 3 were planned but not measured.
- **One time of day.** All tests were on a Saturday evening, so "right now" mostly falls back to the same readings
  (labeled "all-day data" in the app). Weekday class-change crowds may be slower; the app warns weekday visitors.
- **Test server is not Zoom.** We measure the path to Cloudflare's nearest edge server. A video call also depends
  on the route to Zoom and to the other participants.
- **Upload estimates** assume the measured speed holds for the whole upload.
- **Indoor positions are schematic** (see Processing step 6).
- **Crowd level** is a subjective three-level judgement (24 of 25 tests were "low").

## Privacy and ethics

No people were recorded, photographed or surveyed. The collector is identified only by a pseudonymous ID, and notes
were left empty. Tests were run only in public areas during open hours, never in a class in session, and capped at
about 30 MB to avoid loading shared networks. Speed tests reveal the phone's IP address to Cloudflare, as any website
visit does; no IP addresses are stored. GPS is rounded to 4 decimals (~10 m) and only marks public campus spots.

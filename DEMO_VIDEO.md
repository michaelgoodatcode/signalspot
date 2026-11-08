# 2-minute demo video: shot list and narration

The challenge asks the video to show (a) how and where we collected the data, (b) what the dataset contains,
and (c) the working app using it. Every number below is from our real data
(`data/processed/collection_summary.md`, `METHODOLOGY.md`).

| Time | Show on screen | Say |
|---|---|---|
| 0:00–0:12 | Phone showing a Zoom call stuck "reconnecting", inside the MSC (staged) | "Ever had a Zoom call drop between classes? Nobody publishes where on campus your phone's data actually holds up, so we went and measured it." |
| 0:12–0:40 | Walking in the MSC with the collector open: the "where to stand" card with the green GPS check, tapping **Run**, the numbers climbing, the result card | "We built a phone page that runs an open-source Cloudflare speed test and records the spot, 5G or LTE, how busy it is, and GPS. It even catches you if you pick the wrong building. We tested 24 spots in the MSC and Evans, inside on every floor we reached and outside." |
| 0:40–0:58 | Databricks: the CSVs in the `raw` volume, `01_pipeline` output ("Kept 25 of 40 rows", removed by reason), the `readings` table | "Each export goes into Databricks. The pipeline removes the 12 duplicate copies, drops 3 tests our GPS check showed were labeled with the wrong building, and scores every spot against Zoom's bandwidth requirements. We re-checked every number with separate code." |
| 0:58–1:08 | `collection_summary.md` and the findings table in `METHODOLOGY.md` | "25 tests. Evans: 11 of 12 spots handle a video call. MSC: only 5 of 12, and upload inside the MSC had a median of half a megabit." |
| 1:08–1:45 | The app: **Zoom call** → ranked list → tap a spot → popup. Switch to **Big upload**, 2 GB, *I'm near: Memorial Student Center* | "Ask where to take a Zoom call and it ranks the spots from our measurements. For a 2 GB upload from inside the MSC you'd wait about 9 hours; it tells you to walk to the plaza and finish in about 45 minutes." |
| 1:45–2:00 | Limitations section of METHODOLOGY.md | "It's one evening, one carrier (AT&T) and one test per spot, so treat it as a snapshot. The collector already supports Wi-Fi and more carriers, and everything is in the repo." |

**Recording tips:** use QuickTime (File → New Screen Recording) for the laptop parts and the iPhone's screen
recorder for the phone parts. Film the phone in the actual building if you can. Upload to YouTube as *Unlisted*.

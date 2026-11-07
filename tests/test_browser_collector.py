"""Headless-browser tests for the phone collector (docs/collector).

A fake speed-test engine is injected through window.__SIGNALSPOT_ENGINE_FACTORY__ so the
UI flow can be tested without network access. One test also runs the REAL vendored
Cloudflare engine; in a sandbox with no route to Cloudflare it must end as a 'failed'
(dead-zone) reading instead of hanging.

Requires: pip install playwright pytest && playwright install chromium
Run:      python -m pytest -q tests/test_browser_collector.py
"""
from __future__ import annotations

import csv
import functools
import http.server
import io
import sys
import threading
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
REPO = Path(__file__).resolve().parents[1]
(REPO / "tests" / "screenshots").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(REPO / "pipeline"))

FAKE_ENGINE = r"""
window.__FAKE = { mode: 'ok', configs: [] };
window.__SIGNALSPOT_ENGINE_FACTORY__ = (cfg) => {
  window.__FAKE.configs.push(cfg);
  const mode = window.__FAKE.mode;
  const summaries = {
    ok: { download: 85.2e6, upload: 31.7e6, latency: 23.4, jitter: 3.1, downLoadedLatency: 60, upLoadedLatency: 80 },
    weak: { download: 3.1e6, upload: 0.9e6, latency: 140, jitter: 40 },
    partial: { download: 4.0e6, latency: 210 },
    dead: {},
  };
  const summary = summaries[mode] || {};
  const results = {
    getSummary: () => summary,
    getScores: () => (mode === 'ok' ? { rtc: { classificationName: 'great' } } : {}),
    raw: { error: mode === 'dead' || mode === 'partial' ? 'Test aborted. Test exceeded abortTestDuration (75000ms)' : undefined },
  };
  const eng = {
    results, paused: false,
    pause() { this.paused = true; },
    play() {
      if (mode === 'hang') return;
      setTimeout(() => this.onPhaseChange && this.onPhaseChange({ measurement: { type: 'latency' } }), 20);
      setTimeout(() => this.onPhaseChange && this.onPhaseChange({ measurement: { type: 'download' } }), 40);
      setTimeout(() => this.onResultsChange && this.onResultsChange({ type: 'download' }), 50);
      setTimeout(() => this.onPhaseChange && this.onPhaseChange({ measurement: { type: 'upload' } }), 60);
      if (mode === 'dead') setTimeout(() => this.onError && this.onError('Failed to fetch'), 70);
      setTimeout(() => this.onFinish && this.onFinish(results), 120);
    },
  };
  return eng;
};
"""


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def server():
    handler = functools.partial(QuietHandler, directory=str(REPO / "docs"))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def new_page(browser, server, fake=True, geo=None):
    extra = {"geolocation": geo, "permissions": ["geolocation"]} if geo else {}
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, accept_downloads=True, **extra)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    if fake:
        page.add_init_script(FAKE_ENGINE)
    page.goto(f"{server}/collector/")
    page.wait_for_function("window.__signalspot !== undefined")
    return ctx, page, errors


def readings(page):
    return page.evaluate("window.__signalspot.state.readings")


def setup_phone(page):
    page.select_option("#set-collector", "C2")
    page.fill("#set-device", "Test Phone")
    page.select_option("#set-carrier", "Verizon")
    page.click("#set-save")


def run_and_wait(page, n_before):
    page.click("#run")
    page.wait_for_function(f"window.__signalspot.state.readings.length > {n_before} && !window.__signalspot.state.running",
                           timeout=15000)


def test_collector_full_flow(browser, server, tmp_path):
    ctx, page, errors = new_page(browser, server)
    # Run is blocked until the phone is set up.
    assert page.is_disabled("#run")
    assert "Finish phone setup" in page.inner_text("#run-blocker")
    setup_phone(page)
    assert page.is_enabled("#run")
    assert "C2" in page.inner_text("#settings-summary")

    # 1) Wi-Fi test at Zachry floor 2 study area.
    page.select_option("#sel-building", "ZACH")
    page.select_option("#sel-spot", "ZACH-2-STUDY")
    page.click("#net-seg button[data-net=wifi]")
    page.click("#crowd-seg button[data-crowd=medium]")
    page.fill("#notes", 'by the window, "north"')
    run_and_wait(page, 0)
    r = readings(page)[0]
    assert r["spot_id"] == "ZACH-2-STUDY" and r["network_type"] == "wifi" and r["network_name"] == "TAMU_WiFi"
    assert r["status"] == "ok" and r["download_mbps"] == "85.2" and r["upload_mbps"] == "31.7"
    assert r["latency_ms"] == "23.4" and r["crowd_level"] == "medium" and r["cf_rtc_class"] == "great"
    assert r["collector_id"] == "C2" and r["app_version"] == "1.0.0" and r["ts_utc"].endswith("Z")
    assert "Zoom-ready" in page.inner_text("#result")
    # The collector suggests the same spot on cellular next.
    assert page.get_attribute("#net-seg button[data-net=cell]", "aria-pressed") == "true"
    assert "Turn Wi-Fi OFF" in page.inner_text("#net-hint")

    # 2) Cellular test that fails completely -> saved as a dead-zone reading.
    page.evaluate("window.__FAKE.mode = 'dead'")
    page.select_option("#sel-gen", "LTE")
    run_and_wait(page, 1)
    r = readings(page)[1]
    assert r["network_type"] == "cell" and r["network_name"] == "Verizon" and r["cell_gen"] == "LTE"
    assert r["status"] == "failed" and r["download_mbps"] == "" and "Failed to fetch" in r["error"]
    assert "Dead zone" in page.inner_text("#result")
    # After the cell test it moves to the next spot that still needs data, back on Wi-Fi.
    assert page.input_value("#sel-spot") != "ZACH-2-STUDY"
    assert page.get_attribute("#net-seg button[data-net=wifi]", "aria-pressed") == "true"

    # 3) Partial (aborted) test and a weak one.
    page.evaluate("window.__FAKE.mode = 'partial'")
    run_and_wait(page, 2)
    assert readings(page)[2]["status"] == "partial" and readings(page)[2]["upload_mbps"] == ""
    page.evaluate("window.__FAKE.mode = 'weak'")
    page.click("#net-seg button[data-net=wifi]")
    run_and_wait(page, 3)
    assert "Weak" in page.inner_text("#result")

    # 4) Retest: two readings that share a retest_group.
    page.evaluate("window.__FAKE.mode = 'ok'")
    page.click("#net-seg button[data-net=wifi]")
    page.check("#retest")
    page.click("#run")
    page.wait_for_function("window.__signalspot.state.readings.length >= 6 && !window.__signalspot.state.running", timeout=15000)
    rs = readings(page)
    assert rs[4]["retest_group"] and rs[4]["retest_group"] == rs[5]["retest_group"]
    assert not page.is_checked("#retest")

    # 5) Cancel a test that hangs: nothing is saved.
    page.evaluate("window.__FAKE.mode = 'hang'")
    page.click("#run")
    page.wait_for_selector("#progress:not([hidden])")
    page.click("#cancel")
    page.wait_for_function("!window.__signalspot.state.running")
    assert len(readings(page)) == 6

    # Engine configuration sent to the speed test library.
    cfg = page.evaluate("window.__FAKE.configs[0]")
    assert cfg["logAimApiUrl"] is None and cfg["autoStart"] is False and cfg["abortTestDuration"] == 75000
    assert sum(1 for m in cfg["measurements"] if m["type"] in ("download", "upload")) >= 6

    # 6) Flag one reading as bad.
    page.click("#recent li:first-child button[data-flag]")
    flagged = [x for x in readings(page) if x["exclude"] == "1"]
    assert len(flagged) == 1 and flagged[0]["exclude_reason"] == "flagged on phone"

    # Coverage table reflects readings and the selected spot.
    cov = page.inner_text("#coverage")
    assert "Floor 2 – study area" in cov

    # 7) Export the CSV and feed it through the real pipeline.
    with page.expect_download() as dl:
        page.click("#export")
    path = tmp_path / dl.value.suggested_filename
    dl.value.save_as(path)
    assert path.name.startswith("signalspot_C2_") and path.suffix == ".csv"
    text = path.read_text(encoding="utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    from wifimap.config import RAW_COLUMNS
    assert list(rows[0].keys()) == RAW_COLUMNS and len(rows) == 6
    assert any(r["notes"] == 'by the window, "north"' for r in rows)
    from wifimap.pipeline import run
    res = run([str(path)], REPO / "docs" / "data" / "spots.csv")
    m = res.app_json["meta"]
    assert m["n_readings"] == 5 and m["dropped_by_reason"] == {"flagged_by_collector": 1}
    assert m["synthetic"] is False
    assert "0 not exported yet" in page.inner_text("#store-line")

    # 8) Readings survive a reload (localStorage).
    page.reload()
    page.wait_for_function("window.__signalspot !== undefined")
    assert len(readings(page)) == 6
    assert page.input_value("#set-device") == "Test Phone"

    # 9) Delete needs two taps.
    page.click("#clear")
    assert len(readings(page)) == 6 and "Tap again" in page.inner_text("#clear")
    page.click("#clear")
    assert len(readings(page)) == 0

    page.screenshot(path=str(REPO / "tests" / "screenshots" / "collector_mobile.png"), full_page=False)
    assert errors == [], errors
    ctx.close()


def test_collector_screenshot_after_test(browser, server):
    ctx, page, errors = new_page(browser, server)
    setup_phone(page)
    page.select_option("#sel-building", "EVAN")
    # Wi-Fi selected: SSID picker visible, cellular options hidden (and vice versa).
    assert page.is_visible("#sel-ssid") and not page.is_visible("#sel-gen") and not page.is_visible("#ssid-other")
    run_and_wait(page, 0)
    assert page.is_visible("#sel-gen") and not page.is_visible("#sel-ssid")
    page.select_option("#sel-gen", "5G")
    page.click("#net-seg button[data-net=wifi]")
    page.select_option("#sel-ssid", "__other")
    assert page.is_visible("#ssid-other") and page.is_disabled("#run")
    page.fill("#ssid-other", "MSC_Guest")
    assert page.is_enabled("#run")
    page.select_option("#sel-ssid", "TAMU_WiFi")
    page.evaluate("document.querySelector('.top').style.position = 'static'")  # avoid sticky header in element shots
    page.locator("#test-card").screenshot(path=str(REPO / "tests" / "screenshots" / "collector_result.png"))
    assert errors == []
    ctx.close()


def test_real_engine_fails_cleanly_without_network(browser, server):
    """No fake engine: the vendored Cloudflare library runs for real. With no route to
    Cloudflare (blocked in the sandbox) the reading must be saved as 'failed'."""
    ctx, page, errors = new_page(browser, server, fake=False)
    page.route("**/speed.cloudflare.com/**", lambda route: route.abort("internetdisconnected"))
    setup_phone(page)
    page.click("#run")
    page.wait_for_function("window.__signalspot.state.readings.length === 1 && !window.__signalspot.state.running",
                           timeout=110000)
    r = readings(page)[0]
    assert r["status"] == "failed", r
    assert r["error"]
    assert errors == [], errors
    ctx.close()


def test_collector_offline_uses_cached_spots(browser, server):
    ctx, page, errors = new_page(browser, server)
    page.route("**/data/spots.csv", lambda route: route.abort())
    page.route("**/shared/config.json", lambda route: route.abort())
    page.reload()
    page.wait_for_function("window.__signalspot !== undefined")
    assert "Offline: using the spot list" in page.inner_text("#banner")
    assert page.locator("#sel-spot option").count() > 0
    ctx.close()


AT_MSC = {"latitude": 30.6126, "longitude": -96.3414, "accuracy": 12}   # GPS fix from the first real export


def test_where_card_and_wrong_building_warning(browser, server):
    ctx, page, errors = new_page(browser, server, geo=AT_MSC)
    page.route("**/tile.openstreetmap.org/**", lambda r: r.abort())
    setup_phone(page)
    # Default building is Zachry, but the phone is at the MSC.
    page.select_option("#sel-building", "ZACH")
    page.select_option("#sel-spot", "ZACH-2-STAIR")
    page.wait_for_function("document.getElementById('gps-status').classList.contains('mismatch')")
    card = page.inner_text("#spot-card")
    assert "ZACH" in card and "Zachry Engineering Education Complex" in card
    assert "Floor 2 – stairwell" in card and "stairwell at the floor 2 landing" in card
    assert "ZACH-2-STAIR" in card
    status = page.inner_text("#gps-status")
    assert "960 m from Zachry" in status and "Memorial Student Center" in status
    assert page.is_visible("#switch-bldg") and "Memorial Student Center" in page.inner_text("#switch-bldg")
    assert page.locator(".bldg-pin").count() == 1 and page.locator(".you-dot").count() == 1
    page.locator("#spot-card").screenshot(path=str(REPO / "tests" / "screenshots" / "collector_wrong_building.png"))

    # First tap only arms the run; nothing is saved.
    page.click("#run")
    assert "tap again to run anyway" in page.inner_text("#run")
    assert readings(page) == []
    # Second tap runs anyway (e.g. building coordinates in spots.csv are off).
    page.click("#run")
    page.wait_for_function("window.__signalspot.state.readings.length === 1 && !window.__signalspot.state.running", timeout=15000)
    r = readings(page)[0]
    assert r["spot_id"] == "ZACH-2-STAIR" and r["gps_lat"] == "30.6126" and r["gps_acc_m"] == "12"
    assert "Saved for ZACH · Floor 2 – stairwell" in page.inner_text("#result")
    assert page.inner_text("#recent li:first-child").startswith("ZACH")

    # One tap fixes it: switch to the nearest building.
    page.click("#switch-bldg")
    assert page.input_value("#sel-building") == "MSC"
    page.wait_for_function("document.getElementById('gps-status').classList.contains('ok')")
    assert "about 50 m from Memorial Student Center" in page.inner_text("#gps-status")
    assert not page.is_visible("#switch-bldg")
    assert page.inner_text("#run") == "Run speed test"
    assert "Coverage in Memorial Student Center" in page.inner_text("main")
    # Spots are grouped by floor and outside.
    groups = page.eval_on_selector_all("#sel-spot optgroup", "els => els.map(e => e.label)")
    assert groups == ["Floor 1", "Floor 2", "Outside the building"]
    page.select_option("#sel-spot", "MSC-OUT-PLAZA")
    assert "nearest plaza or outdoor seating" in page.inner_text("#where-desc")
    page.locator("#spot-card").screenshot(path=str(REPO / "tests" / "screenshots" / "collector_right_building.png"))

    # The pipeline drops the mislabeled reading.
    import tempfile
    from wifimap.config import RAW_COLUMNS
    from wifimap.pipeline import run
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "x.csv"
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=RAW_COLUMNS)
            w.writeheader()
            w.writerows(readings(page))
        res = run([str(path)], REPO / "docs" / "data" / "spots.csv")
        assert res.app_json["meta"]["dropped_by_reason"] == {"gps_building_mismatch": 1}
    assert errors == [], errors
    ctx.close()


def test_location_permission_denied_does_not_block(browser, server):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    page.add_init_script(FAKE_ENGINE)
    page.goto(f"{server}/collector/")
    page.wait_for_function("window.__signalspot !== undefined")
    setup_phone(page)
    page.wait_for_function("document.getElementById('gps-status').textContent.length > 0")
    assert "mismatch" not in page.get_attribute("#gps-status", "class")
    run_and_wait(page, 0)                       # a single tap runs
    assert len(readings(page)) == 1
    ctx.close()

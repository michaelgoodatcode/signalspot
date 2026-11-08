"""Headless-browser tests for the public map app (docs/index.html).

Builds SYNTHETIC data with the real pipeline into a temporary copy of docs/, serves it,
and drives the UI. Map tiles are blocked (no network needed).
Run: python -m pytest -q tests/test_browser_app.py
"""
from __future__ import annotations

import functools
import http.server
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
REPO = Path(__file__).resolve().parents[1]
SHOTS = REPO / "tests" / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(directory: Path):
    handler = functools.partial(QuietHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("site")
    docs = tmp / "docs"
    shutil.copytree(REPO / "docs", docs)
    (docs / "data" / "spots_scored.json").unlink(missing_ok=True)
    raw = tmp / "raw"
    subprocess.run([sys.executable, str(REPO / "tests" / "make_synthetic.py"), str(raw)], check=True, capture_output=True)
    subprocess.run([sys.executable, str(REPO / "pipeline" / "build_local.py"), "--raw-dir", str(raw), "--allow-synthetic",
                    "--app-json", str(docs / "data" / "spots_scored.json"), "--processed-dir", str(tmp / "processed")],
                   check=True, capture_output=True)
    empty = tmp / "empty_docs"
    shutil.copytree(REPO / "docs", empty)
    (empty / "data" / "spots_scored.json").unlink(missing_ok=True)
    httpd, url = serve(docs)
    httpd2, url2 = serve(empty)
    yield {"url": url, "empty_url": url2}
    httpd.shutdown()
    httpd2.shutdown()


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def open_app(browser, url, viewport=(1280, 800)):
    ctx = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, device_scale_factor=1)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/tile.openstreetmap.org/**", lambda r: r.abort())
    page.goto(url + "/")
    return ctx, page, errors


def ready(page):
    page.wait_for_function("window.__signalspotApp !== undefined")
    # Pin "now" to Saturday 2:30 PM campus time so "right now" = afternoon.
    page.evaluate("window.__signalspotApp.state.now = () => new Date('2026-11-07T20:30:00Z'); window.__signalspotApp.render()")


def test_app_flow(site, browser):
    ctx, page, errors = open_app(browser, site["url"])
    ready(page)
    assert "SYNTHETIC DEMO DATA" in page.inner_text("#banners")
    assert page.locator(".leaflet-marker-icon.spot-icon").count() == 55
    assert page.get_attribute("#map", "data-collapsed") == "false"
    assert not page.is_visible("#untested-wrap")          # every synthetic spot was tested
    assert "Right now · Afternoon" in page.inner_text("#when")
    page.wait_for_timeout(300)
    page.screenshot(path=str(SHOTS / "app_desktop.png"))

    # Zoom task (default): best result is Zoom-ready, list capped at 8.
    items = page.locator("#results li.result")
    assert 1 <= items.count() <= 8
    assert "Zoom-ready" in items.first.inner_text()
    assert "can handle a video call" in page.inner_text("#summary-text")
    first_net_summary = page.inner_text("#summary-text")

    # Network switch changes the summary.
    page.select_option("#net", "cell:Verizon")
    assert page.inner_text("#summary-text") != first_net_summary and "Cell · Verizon · Afternoon (2–6 PM)" in page.inner_text("#summary-text")
    page.select_option("#net", "wifi:TAMU_WiFi")

    # Upload task with a 10 GB file.
    page.click("button[data-task=upload]")
    assert page.is_visible("#size")
    page.click("button[data-size='10']")
    assert "Fastest place to upload 10 GB" in page.inner_text("#results-title")
    assert "Upload ≈" in items.first.inner_text()
    # Dead zones are never suggested for uploads.
    assert "Dead zone" not in page.inner_text("#results")

    # Starting near a building adds walking time.
    page.select_option("#origin", "b:MSC")
    assert "near Memorial Student Center" in page.inner_text("#results-title")
    assert "total" in items.first.inner_text()

    # Other time windows and "any time".
    page.select_option("#when", "evening")
    assert items.count() >= 1
    page.select_option("#when", "morning")       # no synthetic readings in the morning -> all-day fallback
    assert "all-day data" in page.inner_text("#results")
    page.select_option("#when", "all")

    # Click a result: marker selected and popup with the per-network table.
    page.click("button[data-task=zoom]")
    items.first.click()
    page.wait_for_selector(".leaflet-popup .popup table")
    page.wait_for_timeout(400)
    page.screenshot(path=str(SHOTS / "app_desktop_selected.png"))
    popup = page.inner_text(".leaflet-popup")
    assert "Wi-Fi · TAMU_WiFi" in popup and "Cell · " in popup
    assert page.locator(".spot-icon.selected").count() == 1
    assert "selected" in (items.first.get_attribute("class") or "")

    # Accessible table view lists every spot.
    page.click(".table-view summary")
    assert page.locator("#table-wrap tbody tr").count() == 55

    assert "speed tests at" in page.inner_text("#about")
    page.select_option("#when", "now")
    page.select_option("#origin", "")
    assert errors == [], errors
    ctx.close()


def test_app_mobile_layout(site, browser):
    ctx, page, errors = open_app(browser, site["url"], viewport=(390, 844))
    ready(page)
    map_box = page.locator("#map").bounding_box()
    assert map_box["width"] == pytest.approx(390, abs=2) and map_box["height"] < 844 * 0.55
    # Zoomed out on a small screen: one badge per building instead of overlapping rings.
    assert page.get_attribute("#map", "data-collapsed") == "true"
    assert page.locator(".bldg-badge").count() == 3 and page.locator(".spot-icon").count() == 0
    assert "ZACH" in page.inner_text(".leaflet-marker-pane")
    page.screenshot(path=str(SHOTS / "app_mobile.png"), full_page=False)
    # Tapping a result zooms in, expands the rings and opens the popup.
    page.locator("#results li.result").first.click()
    page.wait_for_selector(".leaflet-popup .popup table", timeout=5000)
    assert page.get_attribute("#map", "data-collapsed") == "false"
    page.screenshot(path=str(SHOTS / "app_mobile_selected.png"), full_page=False)
    page.locator(".leaflet-popup-close-button").click()
    for _ in range(5):                                   # zoom out until the rings collapse
        if page.get_attribute("#map", "data-collapsed") == "true":
            break
        page.locator(".leaflet-control-zoom-out").click()
        page.wait_for_timeout(500)
    assert page.get_attribute("#map", "data-collapsed") == "true"
    page.locator(".bldg-badge > div").first.click()     # tapping a badge zooms in and expands
    page.wait_for_timeout(1000)
    assert page.get_attribute("#map", "data-collapsed") == "false"
    page.evaluate("window.scrollTo(0, 400)")
    page.screenshot(path=str(SHOTS / "app_mobile_scrolled.png"), full_page=False)
    assert errors == [], errors
    ctx.close()


def test_app_without_data_shows_instructions(site, browser):
    ctx, page, errors = open_app(browser, site["empty_url"])
    page.wait_for_selector("#banners .banner")
    assert "No data published yet" in page.inner_text("#banners")
    assert not page.is_visible(".results")
    assert errors == [], errors
    ctx.close()


@pytest.fixture(scope="module")
def real_site(tmp_path_factory):
    """The app built from the team's real collector exports in data/raw/ (skipped if none yet)."""
    raw_files = sorted((REPO / "data" / "raw").glob("*.csv"))
    if not raw_files:
        pytest.skip("no real data in data/raw yet")
    tmp = tmp_path_factory.mktemp("realsite")
    docs = tmp / "docs"
    shutil.copytree(REPO / "docs", docs)
    subprocess.run([sys.executable, str(REPO / "pipeline" / "build_local.py"), "--raw-dir", str(REPO / "data" / "raw"),
                    "--app-json", str(docs / "data" / "spots_scored.json"), "--processed-dir", str(tmp / "processed")],
                   check=True, capture_output=True)
    import json
    data = json.loads((docs / "data" / "spots_scored.json").read_text())
    httpd, url = serve(docs)
    yield {"url": url, "data": data}
    httpd.shutdown()


def test_real_data_hides_untested_spots(real_site, browser):
    data = real_site["data"]
    assert data["meta"]["synthetic"] is False
    n_tested = sum(1 for s in data["spots"] if s["stats"])
    n_total = len(data["spots"])
    tested_buildings = {s["building_id"] for s in data["spots"] if s["stats"]}
    ctx, page, errors = open_app(browser, real_site["url"])
    page.wait_for_function("window.__signalspotApp !== undefined")
    assert "SYNTHETIC" not in page.inner_text("#banners")
    if page.get_attribute("#map", "data-collapsed") == "false":
        assert page.locator(".spot-icon").count() == n_tested
        assert set(page.locator(".bldg-label span").all_inner_texts()) == tested_buildings
    else:
        assert page.locator(".bldg-badge").count() == len(tested_buildings)
    page.click(".table-view summary")
    assert page.locator("#table-wrap tbody tr").count() == n_tested
    if n_tested < n_total:
        assert page.is_visible("#untested-wrap") and str(n_total - n_tested) in page.inner_text("#untested-wrap")
        page.check("#show-untested")
        assert page.locator("#table-wrap tbody tr").count() == n_total
        if page.get_attribute("#map", "data-collapsed") == "false":
            assert page.locator(".spot-icon").count() == n_total
        page.uncheck("#show-untested")
        assert page.locator("#table-wrap tbody tr").count() == n_tested
    assert page.locator("#results li.result").count() >= 1
    page.screenshot(path=str(SHOTS / "app_real_data.png"))
    assert errors == [], errors
    ctx.close()

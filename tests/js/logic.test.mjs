// Unit tests for docs/js/logic.js.  Run:  node --test tests/js/
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  windowForDate, localHour, tierForReading, pickStats, rankSpots, networkSummary, uploadSeconds,
  formatDuration, haversineM, toCSV, parseCSV, summaryToReading, layoutSpots, fmtMbps,
  buildingCentroids, nearestBuilding, locationCheck, spotWhereText,
} from '../../docs/js/logic.js';

const cfg = JSON.parse(readFileSync(new URL('../../docs/shared/config.json', import.meta.url)));
const TZ = cfg.timezone;

test('time windows use campus time, not the browser time zone', () => {
  // 2026-11-07 20:00 UTC = 2:00 PM CST
  assert.equal(localHour(new Date('2026-11-07T20:00:00Z'), TZ), 14);
  assert.equal(windowForDate(new Date('2026-11-07T20:00:00Z'), cfg.windows, TZ), 'afternoon');
  assert.equal(windowForDate(new Date('2026-11-08T05:30:00Z'), cfg.windows, TZ), 'night');   // 11:30 PM
  assert.equal(windowForDate(new Date('2026-11-08T10:00:00Z'), cfg.windows, TZ), 'night');   // 4 AM
  assert.equal(windowForDate(new Date('2026-11-08T12:00:00Z'), cfg.windows, TZ), 'morning'); // 6 AM
  for (let h = 0; h < 24; h++) {
    const d = new Date(Date.UTC(2026, 10, 7, h + 6));
    assert.ok(windowForDate(d, cfg.windows, TZ), `hour ${h} has a window`);
  }
});

test('single-reading tier mirrors the pipeline thresholds', () => {
  const t = cfg.zoom_tiers;
  assert.equal(tierForReading({ status: 'ok', download_mbps: 50, upload_mbps: 20, latency_ms: 20, jitter_ms: 2 }, t), 'good');
  assert.equal(tierForReading({ status: 'ok', download_mbps: 2, upload_mbps: 1.5, latency_ms: 120, jitter_ms: 10 }, t), 'ok');
  assert.equal(tierForReading({ status: 'ok', download_mbps: 2, upload_mbps: 1.0, latency_ms: 50, jitter_ms: 10 }, t), 'poor');
  assert.equal(tierForReading({ status: 'failed' }, t), 'dead');
  assert.equal(tierForReading({ status: 'partial', download_mbps: 3, upload_mbps: null, latency_ms: 40 }, t), 'poor');
});

test('summaryToReading treats the engine\'s all-zero result as a failed test', () => {
  // Exactly what @cloudflare/speedtest returned when every request failed.
  const r = summaryToReading({ download: 0, upload: 0, latency: 0, jitter: 0, downLoadedLatency: 0, upLoadedLatency: 0 },
    {}, '', ['Connection error while measuring latency'], undefined, 1.3);
  assert.equal(r.status, 'failed');
  assert.equal(r.download_mbps, null); assert.equal(r.latency_ms, null); assert.equal(r.jitter_ms, null);
  assert.match(r.error, /Connection error/);

  const ok = summaryToReading({ download: 85.2e6, upload: 31.7e6, latency: 23.44, jitter: 3.06 },
    { rtc: { classificationName: 'great' } }, undefined, [], undefined, 21.04);
  assert.deepEqual([ok.status, ok.download_mbps, ok.upload_mbps, ok.latency_ms, ok.jitter_ms, ok.cf_rtc_class, ok.duration_s],
    ['ok', 85.2, 31.7, 23.4, 3.1, 'great', 21]);

  const aborted = summaryToReading({ download: 4e6, latency: 200 }, {}, 'Test aborted. Test exceeded abortTestDuration (75000ms)', [], undefined, 75);
  assert.equal(aborted.status, 'partial');
  const watchdog = summaryToReading({ download: 4e6, upload: 1e6, latency: 200 }, {}, '', [], 'watchdog timeout', 95);
  assert.equal(watchdog.status, 'partial');
});

const spot = (id, lat, lng, statsByNet) => ({ spot_id: id, label: id, building_id: 'B', building_name: 'B', floor: '1',
  indoor: 1, lat, lng, coord_source: 'gps_median', stats: statsByNet });
const st = (tier, up, down, lat, score, extra = {}) => ({ tier, up_med: up, up_p25: up * 0.7, up_p75: up * 1.2, down_med: down,
  lat_med: lat, zoom_score: score, n: 3, low_confidence: false, ...extra });

const SPOTS = [
  spot('far-good', 30.6250, -96.3400, { 'wifi:X': { all: st('good', 30, 80, 20, 95), afternoon: st('good', 25, 70, 22, 90) } }),
  spot('near-good', 30.6171, -96.3401, { 'wifi:X': { all: st('good', 5, 20, 40, 70) } }),
  spot('near-ok', 30.6170, -96.3400, { 'wifi:X': { all: st('ok', 1.5, 4, 120, 40) } }),
  spot('dead', 30.6170, -96.3399, { 'wifi:X': { all: st('dead', 0, 0, null, 0) } }),
  spot('cell-only', 30.6170, -96.3400, { 'cell:V': { all: st('good', 10, 50, 40, 80) } }),
];

test('pickStats falls back to all-day data and flags it', () => {
  assert.deepEqual(pickStats(SPOTS[0], 'wifi:X', 'afternoon').fallback, false);
  const p = pickStats(SPOTS[1], 'wifi:X', 'afternoon');
  assert.equal(p.fallback, true); assert.equal(p.windowUsed, 'all');
  assert.equal(pickStats(SPOTS[4], 'wifi:X', 'all'), null);
  assert.equal(pickStats(SPOTS[1], 'wifi:X', 'all').fallback, false);
});

test('zoom ranking: tier first, then distance when an origin is set, else score', () => {
  const noOrigin = rankSpots({ spots: SPOTS, netKey: 'wifi:X', windowId: 'all', task: 'zoom' }).map((i) => i.spot.spot_id);
  assert.deepEqual(noOrigin, ['far-good', 'near-good', 'near-ok', 'dead']);
  const origin = { lat: 30.6170, lng: -96.3400 };
  const withOrigin = rankSpots({ spots: SPOTS, netKey: 'wifi:X', windowId: 'all', task: 'zoom', origin }).map((i) => i.spot.spot_id);
  assert.deepEqual(withOrigin, ['near-good', 'far-good', 'near-ok', 'dead']);
});

test('upload ranking: walking + upload time, dead zones excluded', () => {
  const origin = { lat: 30.6170, lng: -96.3400 };
  const items = rankSpots({ spots: SPOTS, netKey: 'wifi:X', windowId: 'all', task: 'upload', sizeGB: 2, origin, walkSpeed: 80 });
  assert.ok(!items.some((i) => i.spot.spot_id === 'dead'));
  const far = items.find((i) => i.spot.spot_id === 'far-good');
  const near = items.find((i) => i.spot.spot_id === 'near-good');
  // 2 GB at 30 Mbps = 533 s; far spot is ~890 m away = ~11 min walk -> ~20 min total
  assert.ok(Math.abs(far.uploadSec - 533.3) < 1);
  assert.ok(far.walkMin > 10 && far.walkMin < 12.5);
  // 2 GB at 5 Mbps = 3200 s = 53 min, no walk -> far spot wins despite the walk
  assert.equal(items[0].spot.spot_id, 'far-good');
  assert.ok(near.totalMin > far.totalMin);
  // a 0.1 GB file: walking dominates, so the nearby spot wins
  const small = rankSpots({ spots: SPOTS, netKey: 'wifi:X', windowId: 'all', task: 'upload', sizeGB: 0.1, origin, walkSpeed: 80 });
  assert.equal(small[0].spot.spot_id, 'near-good');
});

test('network summary counts tiers', () => {
  assert.deepEqual(networkSummary(SPOTS, 'wifi:X', 'all'), { tested: 4, good: 2, ok: 1, poor: 0, dead: 1, ready: 3 });
});

test('upload time and formatting helpers', () => {
  assert.equal(uploadSeconds(1, 8), 1000);
  assert.equal(uploadSeconds(1, 0), Infinity);
  assert.equal(formatDuration(30), '30 s');
  assert.equal(formatDuration(600), '10 min');
  assert.equal(formatDuration(3 * 3600 + 15 * 60), '3 h 15 min');
  assert.equal(formatDuration(Infinity), 'never');
  assert.equal(fmtMbps(null), '—'); assert.equal(fmtMbps(3.14159), '3.1'); assert.equal(fmtMbps(123.4), '123');
  assert.ok(Math.abs(haversineM({ lat: 30, lng: -96 }, { lat: 30.001, lng: -96 }) - 111.2) < 0.5);
});

test('CSV round trip keeps commas, quotes and newlines', () => {
  const rows = [{ a: 'x, y', b: 'say "hi"', c: 'line1\nline2' }, { a: '', b: 'plain', c: ' padded ' }];
  const back = parseCSV(toCSV(rows, ['a', 'b', 'c']));
  assert.equal(back[0].a, 'x, y'); assert.equal(back[0].b, 'say "hi"'); assert.equal(back[0].c, 'line1\nline2');
  assert.equal(back[1].b, 'plain');
  const spots = parseCSV(readFileSync(new URL('../../docs/data/spots.csv', import.meta.url), 'utf8'));
  assert.equal(spots.length, 55); assert.equal(spots[0].spot_id, 'ZACH-1-STUDY');
});

test('layoutSpots rings indoor spots around their building and keeps GPS-placed spots', () => {
  const spots = [
    { spot_id: 'a', building_id: 'B', indoor: 1, floor: '1', lat: 30.617, lng: -96.34, coord_source: 'approx' },
    { spot_id: 'b', building_id: 'B', indoor: 1, floor: '2', lat: 30.617, lng: -96.34, coord_source: 'approx' },
    { spot_id: 'c', building_id: 'B', indoor: 0, floor: '', lat: 30.6181, lng: -96.341, coord_source: 'gps_median' },
  ];
  const pos = layoutSpots(spots, [{ building_id: 'B', lat: 30.617, lng: -96.34 }]);
  assert.notDeepEqual([pos.a.lat, pos.a.lng], [pos.b.lat, pos.b.lng]);
  assert.ok(pos.a.schematic && !pos.c.schematic);
  assert.equal(pos.c.lat, 30.6181);
  const d = haversineM({ lat: 30.617, lng: -96.34 }, pos.a);
  assert.ok(d > 10 && d < 30, `ring radius ${d}`);
});

test('location check catches a reading saved under the wrong building', () => {
  const spots = parseCSV(readFileSync(new URL('../../docs/data/spots.csv', import.meta.url), 'utf8'));
  const cents = buildingCentroids(spots);
  assert.deepEqual(Object.keys(cents).sort(), ['EVAN', 'MSC', 'ZACH']);
  // The phone's real GPS fix from the first export (at the MSC, but Zachry was selected).
  const atMsc = { lat: 30.6126, lng: -96.3414, acc: 12 };
  const chk = locationCheck(atMsc, 'ZACH', cents);
  assert.equal(chk.state, 'mismatch');
  assert.equal(chk.nearest.building_id, 'MSC');
  assert.match(chk.message, /Memorial Student Center/);
  assert.equal(locationCheck(atMsc, 'MSC', cents).state, 'ok');
  assert.equal(locationCheck({ ...atMsc, acc: 500 }, 'ZACH', cents).state, 'imprecise');
  assert.equal(locationCheck(null, 'ZACH', cents).state, 'waiting');
  assert.equal(locationCheck(atMsc, 'NOPE', cents).state, 'nocoords');
  assert.equal(nearestBuilding(atMsc, cents).building_id, 'MSC');
});

test('every template spot gets a plain-language "where to stand" text', () => {
  const spots = parseCSV(readFileSync(new URL('../../docs/data/spots.csv', import.meta.url), 'utf8'));
  for (const s of spots) {
    const t = spotWhereText(s);
    assert.ok(t.length > 20, `${s.spot_id}: ${t}`);
  }
  const byId = Object.fromEntries(spots.map((s) => [s.spot_id, spotWhereText(s)]));
  assert.match(byId['ZACH-2-STAIR'], /stairwell at the floor 2 landing/);
  assert.match(byId['EVAN-3-HALL'], /far end of a hallway/);
  assert.match(byId['MSC-1-LOBBY'], /lobby/);
  assert.match(byId['ZACH-OUT-N'], /north side of Zachry/);
  assert.match(byId['MSC-OUT-PLAZA'], /plaza/);
  assert.equal(spotWhereText({ ...spots[0], description: 'Booths by the east windows' }), 'Booths by the east windows');
});

// SignalSpot collector: runs speed tests on a phone and stores readings locally until exported.
import { runSpeedTest } from './engine.js';
import {
  parseCSV, toCSV, tierForReading, TIER_LABEL, TIER_GLYPH, windowForDate, fmtMbps,
  buildingCentroids, locationCheck, spotWhereText, DEFAULT_BUILDING_MISMATCH_M,
} from '../js/logic.js';

// Keep in sync with pipeline/wifimap/config.py RAW_COLUMNS.
export const RAW_COLUMNS = [
  'reading_id', 'ts_utc', 'collector_id', 'device_model', 'spot_id',
  'network_type', 'network_name', 'cell_gen', 'crowd_level', 'retest_group',
  'status', 'download_mbps', 'upload_mbps', 'latency_ms', 'jitter_ms',
  'down_loaded_latency_ms', 'up_loaded_latency_ms', 'cf_rtc_class', 'duration_s',
  'gps_lat', 'gps_lng', 'gps_acc_m', 'browser_conn_type',
  'exclude', 'exclude_reason', 'error', 'notes', 'app_version',
];
const APP_VERSION = '1.0.0';
const TARGET_PER_NET = 2;
const KEYS = {
  readings: 'signalspot.readings.v1', settings: 'signalspot.settings.v1', draft: 'signalspot.draft.v1',
  cache: 'signalspot.cache.v1', lastExport: 'signalspot.lastExport.v1',
};

// ---------- storage (falls back to memory if localStorage is blocked) ----------
const memory = {};
let storageOk = true;
const store = {
  get(k, fallback) {
    try { const v = localStorage.getItem(k); return v == null ? fallback : JSON.parse(v); }
    catch { storageOk = false; return k in memory ? memory[k] : fallback; }
  },
  set(k, v) {
    memory[k] = v;
    try { localStorage.setItem(k, JSON.stringify(v)); } catch { storageOk = false; }
  },
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const state = {
  config: null,
  spots: [],
  settings: store.get(KEYS.settings, { collector_id: '', device_model: '', carrier: '', record_gps: true }),
  readings: store.get(KEYS.readings, []),
  draft: store.get(KEYS.draft, { building: '', spot: '', net: 'wifi', ssid: 'TAMU_WiFi', ssid_other: '', gen: '5G', crowd: 'low' }),
  running: null,
  clearArmed: false,
  centroids: {},
  pos: null,          // latest GPS fix {lat, lng, acc, at}
  geoState: 'off',    // off | waiting | denied | on
  geoWatch: null,
  runArmed: false,    // second tap needed to run despite a location mismatch
};

function saveReadings() { store.set(KEYS.readings, state.readings); }
function saveDraft() { store.set(KEYS.draft, state.draft); }

function uuid() {
  if (crypto && crypto.randomUUID) return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

// ---------- loading ----------
async function loadData() {
  try {
    const [cfgRes, spotsRes] = await Promise.all([
      fetch('../shared/config.json', { cache: 'no-cache' }),
      fetch('../data/spots.csv', { cache: 'no-cache' }),
    ]);
    if (!cfgRes.ok || !spotsRes.ok) throw new Error(`HTTP ${cfgRes.status}/${spotsRes.status}`);
    state.config = await cfgRes.json();
    state.spots = parseCSV(await spotsRes.text());
    store.set(KEYS.cache, { config: state.config, spots: state.spots, at: new Date().toISOString() });
  } catch (e) {
    const cached = store.get(KEYS.cache, null);
    if (!cached) throw new Error(`Could not load the spot list (${e.message}). Open this page once with a connection.`);
    state.config = cached.config;
    state.spots = cached.spots;
    showBanner(`Offline: using the spot list saved on ${new Date(cached.at).toLocaleString()}.`);
  }
  if (!state.spots.length) throw new Error('spots.csv has no rows.');
  state.centroids = buildingCentroids(state.spots);
}

function showBanner(msg) { const b = $('banner'); b.textContent = msg; b.hidden = !msg; }

// ---------- helpers ----------
const buildings = () => {
  const seen = new Map();
  for (const s of state.spots) if (!seen.has(s.building_id)) seen.set(s.building_id, s.building_name);
  return [...seen.entries()].map(([id, name]) => ({ id, name }));
};
const spotsIn = (bid) => state.spots.filter((s) => s.building_id === bid);
const activeReadings = () => state.readings.filter((r) => r.exclude !== '1');
function countsFor(spotId) {
  const rs = activeReadings().filter((r) => r.spot_id === spotId);
  const windows = new Set(rs.map((r) => windowForDate(new Date(r.ts_utc), state.config.windows, state.config.timezone)));
  return { wifi: rs.filter((r) => r.network_type === 'wifi').length, cell: rs.filter((r) => r.network_type === 'cell').length, windows: windows.size };
}
const settingsComplete = () => Boolean(state.settings.collector_id && state.settings.device_model.trim() && state.settings.carrier);
const ssidValue = () => (state.draft.ssid === '__other' ? state.draft.ssid_other.trim() : state.draft.ssid);

// ---------- render ----------
function renderSettings() {
  const s = state.settings;
  const coll = $('set-collector');
  coll.innerHTML = '<option value="">Choose…</option>' + Array.from({ length: 9 }, (_, i) => `C${i + 1}`)
    .map((c) => `<option ${c === s.collector_id ? 'selected' : ''}>${c}</option>`).join('');
  $('set-device').value = s.device_model;
  $('set-carrier').innerHTML = '<option value="">Choose…</option>' + state.config.carriers
    .map((c) => `<option ${c === s.carrier ? 'selected' : ''}>${esc(c)}</option>`).join('');
  $('set-gps').checked = Boolean(s.record_gps);
  $('settings-summary').textContent = settingsComplete() ? `· ${s.collector_id} · ${s.device_model} · ${s.carrier}` : '· not set';
  $('settings-card').open = !settingsComplete();
}

function renderTest() {
  const d = state.draft;
  const bs = buildings();
  if (!bs.some((b) => b.id === d.building)) d.building = bs[0].id;
  $('sel-building').innerHTML = bs.map((b) => `<option value="${esc(b.id)}" ${b.id === d.building ? 'selected' : ''}>${esc(b.name)}</option>`).join('');
  const sp = spotsIn(d.building);
  if (!sp.some((s) => s.spot_id === d.spot)) d.spot = sp[0].spot_id;
  const groups = new Map();
  for (const s of sp) {
    const g = String(s.indoor) === '0' ? 'Outside the building' : (s.floor ? `Floor ${s.floor}` : 'Inside');
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(s);
  }
  $('sel-spot').innerHTML = [...groups.entries()].map(([g, list]) => `<optgroup label="${esc(g)}">${list.map((s) => {
    const c = countsFor(s.spot_id);
    const done = c.wifi >= TARGET_PER_NET && c.cell >= TARGET_PER_NET ? '✓ ' : '';
    return `<option value="${esc(s.spot_id)}" ${s.spot_id === d.spot ? 'selected' : ''}>${done}${esc(s.label)} · W${c.wifi} C${c.cell}</option>`;
  }).join('')}</optgroup>`).join('');
  renderWhere();

  for (const b of $('net-seg').querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.net === d.net));
  $('wifi-opts').hidden = d.net !== 'wifi';
  $('cell-opts').hidden = d.net !== 'cell';
  const nets = state.config.wifi_networks;
  $('sel-ssid').innerHTML = nets.map((n) => `<option ${n === d.ssid ? 'selected' : ''}>${esc(n)}</option>`).join('')
    + `<option value="__other" ${d.ssid === '__other' ? 'selected' : ''}>Other…</option>`;
  $('ssid-other-wrap').hidden = d.ssid !== '__other';
  $('ssid-other').value = d.ssid_other;
  $('sel-gen').innerHTML = state.config.cell_generations.map((g) => `<option ${g === d.gen ? 'selected' : ''}>${esc(g)}</option>`).join('');
  $('carrier-line').textContent = `Carrier: ${state.settings.carrier || '— set it in Phone setup'}`;
  $('net-hint').innerHTML = d.net === 'wifi'
    ? `Check you are connected to <b>${esc(ssidValue() || '…')}</b> (Settings → Wi-Fi). Hold the phone still at chest height.`
    : '<b>Turn Wi-Fi OFF</b> in phone settings first, otherwise this measures Wi-Fi. Hold the phone still at chest height.';

  $('crowd-seg').innerHTML = '<legend>How crowded is it?</legend>' + state.config.crowd_levels
    .map((c) => `<button type="button" data-crowd="${esc(c)}" aria-pressed="${c === d.crowd}">${esc(c)}</button>`).join('');

  const blocker = !settingsComplete() ? 'Finish phone setup first.'
    : (d.net === 'wifi' && !ssidValue()) ? 'Enter the Wi-Fi network name.' : '';
  $('run').disabled = Boolean(blocker) || Boolean(state.running);
  $('run-blocker').hidden = !blocker;
  $('run-blocker').textContent = blocker;
  $('run').textContent = state.runArmed ? 'Location doesn\'t match — tap again to run anyway' : 'Run speed test';
  $('run').classList.toggle('warnrun', state.runArmed);
}

// ---------- where to stand + GPS check ----------
let miniMap = null;
const miniLayers = {};

function currentSpot() { return state.spots.find((s) => s.spot_id === state.draft.spot); }

function currentCheck() {
  if (!state.settings.record_gps) return { state: 'off', message: 'Location check is off (turn on “Record GPS” in Phone setup).' };
  if (state.geoState === 'denied') return { state: 'off', message: 'Location permission is blocked, so there is no location check. Double-check the building yourself.' };
  return locationCheck(state.pos, state.draft.building, state.centroids, { thresholdM: (state.config.gps && state.config.gps.building_mismatch_m) || DEFAULT_BUILDING_MISMATCH_M });
}

function renderWhere() {
  const s = currentSpot();
  if (!s) return;
  $('where-code').textContent = s.building_id;
  $('where-bldg-name').textContent = s.building_name;
  $('where-label').textContent = s.label;
  $('where-desc').textContent = spotWhereText(s);
  $('where-id').textContent = s.spot_id;
  const chk = currentCheck();
  const st = $('gps-status');
  st.className = `gps-status ${chk.state}`;
  st.textContent = (chk.state === 'ok' ? '✓ ' : chk.state === 'mismatch' ? '⚠ ' : '') + chk.message;
  const sw = $('switch-bldg');
  const canSwitch = chk.state === 'mismatch' && chk.nearest && chk.nearest.building_id !== state.draft.building
    && chk.nearest.distanceM <= ((state.config.gps && state.config.gps.building_mismatch_m) || DEFAULT_BUILDING_MISMATCH_M);
  sw.hidden = !canSwitch;
  if (canSwitch) { sw.textContent = `Switch to ${chk.nearest.building_name}`; sw.dataset.building = chk.nearest.building_id; }
  if (chk.state !== 'mismatch') state.runArmed = false;
  renderMiniMap(s);
}

function renderMiniMap(spot) {
  const L = window.L;
  if (!L) return;
  if (!miniMap) {
    miniMap = L.map('mini-map', { zoomControl: false, attributionControl: true, scrollWheelZoom: false });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap' }).addTo(miniMap);
  }
  for (const k of Object.keys(miniLayers)) { miniLayers[k].remove(); delete miniLayers[k]; }
  const c = state.centroids[spot.building_id];
  const lat = parseFloat(spot.lat);
  const lng = parseFloat(spot.lng);
  const ownCoords = String(spot.indoor) === '0' && Number.isFinite(lat) && Number.isFinite(lng) && spot.coord_source === 'manual';
  const target = ownCoords ? [lat, lng] : c ? [c.lat, c.lng] : null;
  const pts = [];
  if (target) {
    miniLayers.bldg = L.marker(target, { icon: L.divIcon({ className: 'bldg-pin', html: `<span>${esc(spot.building_id)}${spot.floor ? ` · floor ${esc(spot.floor)}` : ''}</span>`, iconSize: [0, 0] }), interactive: false }).addTo(miniMap);
    pts.push(target);
  }
  if (state.pos) {
    miniLayers.acc = L.circle([state.pos.lat, state.pos.lng], { radius: Math.min(state.pos.acc, 300), weight: 1, color: '#1a73e8', fillOpacity: 0.12, interactive: false }).addTo(miniMap);
    miniLayers.you = L.marker([state.pos.lat, state.pos.lng], { icon: L.divIcon({ className: '', html: '<div class="you-dot"></div>', iconSize: [14, 14] }), interactive: false }).addTo(miniMap);
    if (!target || miniMap.distance(target, [state.pos.lat, state.pos.lng]) < 2500) pts.push([state.pos.lat, state.pos.lng]);
  }
  if (pts.length === 2) miniMap.fitBounds(pts, { padding: [28, 28], maxZoom: 18, animate: false });
  else if (pts.length === 1) miniMap.setView(pts[0], 17, { animate: false });
  else miniMap.setView([30.617, -96.34], 15, { animate: false });
  miniMap.invalidateSize();
}

function startGeo() {
  if (!state.settings.record_gps || !navigator.geolocation || state.geoWatch != null) return;
  state.geoState = 'waiting';
  state.geoWatch = navigator.geolocation.watchPosition(
    (p) => {
      state.pos = { lat: p.coords.latitude, lng: p.coords.longitude, acc: p.coords.accuracy, at: Date.now() };
      state.geoState = 'on';
      renderTest(); // also refreshes the where-to-stand card
    },
    (err) => {
      if (err && err.code === 1) state.geoState = 'denied';
      renderWhere();
    },
    { enableHighAccuracy: true, maximumAge: 10000, timeout: 20000 },
  );
}

function stopGeo() {
  if (state.geoWatch != null && navigator.geolocation) navigator.geolocation.clearWatch(state.geoWatch);
  state.geoWatch = null;
  state.pos = null;
  state.geoState = 'off';
}

function renderCoverage() {
  const sp = spotsIn(state.draft.building);
  $('cov-bldg').textContent = sp.length ? sp[0].building_name : 'this building';
  const rows = sp.map((s) => {
    const c = countsFor(s.spot_id);
    const cls = (n) => `n${n < TARGET_PER_NET ? ' lack' : ''}`;
    return `<tr class="${s.spot_id === state.draft.spot ? 'current' : ''}" data-spot="${esc(s.spot_id)}">
      <td>${esc(s.label)}</td><td class="${cls(c.wifi)}">${c.wifi}</td><td class="${cls(c.cell)}">${c.cell}</td>
      <td class="n">${c.windows}</td></tr>`;
  }).join('');
  const done = sp.filter((s) => { const c = countsFor(s.spot_id); return c.wifi >= TARGET_PER_NET && c.cell >= TARGET_PER_NET; }).length;
  $('coverage').innerHTML = `<p class="muted small">${done} of ${sp.length} spots have ≥${TARGET_PER_NET} Wi-Fi and ≥${TARGET_PER_NET} cell readings from this phone. Tap a row to select it.</p>
    <table class="cov"><thead><tr><th>Spot</th><th>Wi-Fi</th><th>Cell</th><th>Time windows</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function renderRecent() {
  const list = [...state.readings].reverse().slice(0, 15);
  $('recent').innerHTML = list.map((r) => {
    const spot = state.spots.find((s) => s.spot_id === r.spot_id);
    const flagged = r.exclude === '1';
    const t = new Date(r.ts_utc).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    return `<li class="${flagged ? 'flagged' : ''}">
      <div><span class="bcode">${esc(spot ? spot.building_id : '?')}</span> <b>${esc(spot ? spot.label : r.spot_id)}</b> · ${esc(r.network_type === 'wifi' ? r.network_name : `${r.network_name} ${r.cell_gen}`)}
        <div class="meta">${t} · ${esc(r.status)} · ↓${fmtMbps(num(r.download_mbps))} ↑${fmtMbps(num(r.upload_mbps))} Mbps · ${r.latency_ms === '' ? '—' : esc(r.latency_ms)} ms${r.retest_group ? ' · retest' : ''}</div></div>
      <button type="button" data-flag="${esc(r.reading_id)}">${flagged ? 'Unflag' : 'Flag bad'}</button></li>`;
  }).join('') || '<li class="muted">No readings yet.</li>';
  const n = state.readings.length;
  const last = store.get(KEYS.lastExport, null);
  const newSince = last ? state.readings.filter((r) => r.ts_utc > last.at).length : n;
  $('store-line').textContent = `${n} reading${n === 1 ? '' : 's'} on this phone · ${newSince} not exported yet`;
  $('export-line').textContent = last ? `Last export: ${new Date(last.at).toLocaleString()} (${last.count} rows).` : 'Not exported yet.';
  $('share').hidden = !canShareFiles();
  $('clear').textContent = state.clearArmed ? `Tap again to delete ${n} readings` : 'Delete all readings on this phone';
  if (!storageOk) showBanner('This browser is blocking storage. Readings are only in memory: export before closing the page.');
}

function renderAll() { renderSettings(); renderTest(); renderCoverage(); renderRecent(); }

const num = (v) => (v === '' || v == null ? null : Number(v));

// ---------- running a test ----------
function getPosition() {
  return new Promise((resolve) => {
    if (!navigator.geolocation) return resolve(null);
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ lat: p.coords.latitude, lng: p.coords.longitude, acc: p.coords.accuracy }),
      () => resolve(null),
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 },
    );
  });
}

const PHASE_TEXT = { latency: 'Measuring latency…', download: 'Measuring download…', upload: 'Measuring upload…' };
const PHASE_PCT = { latency: 15, download: 45, upload: 80 };

function showProgress(p, label) {
  if (p.phase) {
    $('progress-text').textContent = `${label}${PHASE_TEXT[p.phase] || 'Testing…'}`;
    $('progress-fill').style.width = `${PHASE_PCT[p.phase] || 10}%`;
  }
  const s = p.summary || {};
  if (s.download != null) $('live-down').textContent = fmtMbps(s.download / 1e6);
  if (s.upload != null) $('live-up').textContent = fmtMbps(s.upload / 1e6);
  if (s.latency != null) $('live-lat').textContent = Math.round(s.latency);
}

async function runOnce(ctx, retestGroup, label) {
  const ts = new Date();
  const conn = (navigator.connection && navigator.connection.type) || '';
  const gpsP = state.settings.record_gps ? getPosition() : Promise.resolve(null);
  const test = runSpeedTest({ onProgress: (p) => showProgress(p, label) });
  state.running = test;
  const [m, gps] = await Promise.all([test.promise, gpsP]);
  if (m.cancelled) return null;
  const d = state.config.gps.round_decimals;
  const row = Object.fromEntries(RAW_COLUMNS.map((c) => [c, '']));
  Object.assign(row, {
    reading_id: uuid(), ts_utc: ts.toISOString(),
    collector_id: state.settings.collector_id, device_model: state.settings.device_model.trim(),
    spot_id: ctx.spot_id, network_type: ctx.net, network_name: ctx.network_name,
    cell_gen: ctx.net === 'cell' ? ctx.gen : '', crowd_level: ctx.crowd, retest_group: retestGroup,
    status: m.status, error: m.error, notes: ctx.notes, app_version: APP_VERSION, browser_conn_type: conn,
  });
  for (const k of ['download_mbps', 'upload_mbps', 'latency_ms', 'jitter_ms', 'down_loaded_latency_ms',
    'up_loaded_latency_ms', 'duration_s']) row[k] = m[k] == null ? '' : String(m[k]);
  row.cf_rtc_class = m.cf_rtc_class || '';
  if (gps) {
    row.gps_lat = gps.lat.toFixed(d);
    row.gps_lng = gps.lng.toFixed(d);
    row.gps_acc_m = String(Math.round(gps.acc));
  }
  state.readings.push(row);
  saveReadings();
  return row;
}

async function startTest() {
  if (state.running || !settingsComplete()) return;
  const d = state.draft;
  if (currentCheck().state === 'mismatch' && !state.runArmed) {
    state.runArmed = true;
    renderTest();
    setTimeout(() => { if (state.runArmed && !state.running) { state.runArmed = false; renderTest(); } }, 6000);
    return;
  }
  state.runArmed = false;
  const ctx = {
    spot_id: d.spot, net: d.net, gen: d.gen, crowd: d.crowd, notes: $('notes').value.trim(),
    network_name: d.net === 'wifi' ? ssidValue() : state.settings.carrier,
  };
  const repeats = $('retest').checked ? 2 : 1;
  const group = repeats === 2 ? `rt-${uuid().slice(0, 8)}` : '';
  let wakeLock = null;
  try { wakeLock = await navigator.wakeLock?.request('screen'); } catch { /* optional */ }
  $('progress').hidden = false;
  $('result').hidden = true;
  $('live-down').textContent = $('live-up').textContent = $('live-lat').textContent = '—';
  $('progress-fill').style.width = '5%';
  $('progress-text').textContent = 'Starting…';
  renderTest();
  const done = [];
  try {
    for (let i = 0; i < repeats; i++) {
      const label = repeats === 2 ? `Run ${i + 1}/2 · ` : '';
      const row = await runOnce(ctx, group, label);
      if (!row) break; // cancelled
      done.push(row);
    }
  } finally {
    state.running = null;
    try { await wakeLock?.release(); } catch { /* ignore */ }
    $('progress').hidden = true;
  }
  if (done.length) {
    $('notes').value = '';
    $('retest').checked = false;
    showResult(done[done.length - 1], done.length);
    suggestNext(ctx);
  }
  renderAll();
}

function showResult(r, n) {
  const tier = tierForReading({
    status: r.status, download_mbps: num(r.download_mbps), upload_mbps: num(r.upload_mbps),
    latency_ms: num(r.latency_ms), jitter_ms: num(r.jitter_ms),
  }, state.config.zoom_tiers);
  const el = $('result');
  el.hidden = false;
  el.innerHTML = `<div><span class="pill ${tier}">${TIER_GLYPH[tier]} ${TIER_LABEL[tier]}</span>
      <span class="muted small"> single test${n > 1 ? ` (last of ${n})` : ''} · saved</span></div>
    <p class="small">Saved for <b>${esc(savedWhere(r))}</b></p>
    <div class="nums">
      <div><b>${fmtMbps(num(r.download_mbps))}</b><small>down Mbps</small></div>
      <div><b>${fmtMbps(num(r.upload_mbps))}</b><small>up Mbps</small></div>
      <div><b>${r.latency_ms === '' ? '—' : Math.round(Number(r.latency_ms))}</b><small>latency ms</small></div>
      <div><b>${r.jitter_ms === '' ? '—' : Math.round(Number(r.jitter_ms))}</b><small>jitter ms</small></div>
    </div>
    ${r.status !== 'ok' ? `<p class="small">Status <b>${esc(r.status)}</b>: ${r.status === 'failed' ? 'no data could be moved here — that is a dead-zone reading, keep it.' : 'the test was cut short; what was measured is kept.'}</p>` : ''}
    <p class="small muted" id="next-line"></p>`;
}

function savedWhere(r) {
  const s = state.spots.find((x) => x.spot_id === r.spot_id);
  return s ? `${s.building_id} · ${s.label}` : r.spot_id;
}

function suggestNext(ctx) {
  const d = state.draft;
  let msg;
  if (ctx.net === 'wifi') {
    d.net = 'cell';
    msg = 'Next: same spot on cellular. Turn Wi-Fi off first.';
  } else {
    const sp = spotsIn(d.building);
    const idx = sp.findIndex((s) => s.spot_id === ctx.spot_id);
    const ordered = [...sp.slice(idx + 1), ...sp.slice(0, idx + 1)];
    const need = ordered.find((s) => { const c = countsFor(s.spot_id); return c.wifi < TARGET_PER_NET || c.cell < TARGET_PER_NET; });
    if (need) {
      d.spot = need.spot_id;
      msg = `Next: ${need.label} on Wi-Fi. Turn Wi-Fi back on.`;
    } else msg = 'Every spot in this building is at target on this phone. Pick another building.';
    d.net = 'wifi';
  }
  saveDraft();
  const line = $('next-line');
  if (line) line.textContent = msg;
}

// ---------- export ----------
function csvFile() {
  const stamp = new Date().toISOString().replace(/[-:]/g, '').slice(0, 13).replace('T', '-');
  const name = `signalspot_${state.settings.collector_id || 'CX'}_${stamp}.csv`;
  const text = toCSV(state.readings, RAW_COLUMNS);
  return { name, text, blob: new Blob([text], { type: 'text/csv' }) };
}

function markExported() {
  store.set(KEYS.lastExport, { at: new Date().toISOString(), count: state.readings.length });
  renderRecent();
}

function canShareFiles() {
  try { return Boolean(navigator.canShare && navigator.canShare({ files: [new File(['x'], 'x.csv', { type: 'text/csv' })] })); }
  catch { return false; }
}

function exportCSV() {
  const { name, blob } = csvFile();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
  markExported();
}

async function shareCSV() {
  const { name, text } = csvFile();
  try {
    await navigator.share({ files: [new File([text], name, { type: 'text/csv' })], title: name });
    markExported();
  } catch { /* user cancelled */ }
}

// ---------- events ----------
function wire() {
  $('set-save').addEventListener('click', () => {
    state.settings = {
      collector_id: $('set-collector').value, device_model: $('set-device').value.trim(),
      carrier: $('set-carrier').value, record_gps: $('set-gps').checked,
    };
    store.set(KEYS.settings, state.settings);
    if (state.settings.record_gps) startGeo(); else stopGeo();
    renderAll();
  });
  $('switch-bldg').addEventListener('click', (e) => {
    const b = e.currentTarget.dataset.building;
    if (!b) return;
    state.draft.building = b;
    state.draft.spot = '';
    state.runArmed = false;
    saveDraft();
    renderAll();
    $('spot-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  $('sel-building').addEventListener('change', (e) => { state.draft.building = e.target.value; state.draft.spot = ''; state.runArmed = false; saveDraft(); renderAll(); });
  $('sel-spot').addEventListener('change', (e) => { state.draft.spot = e.target.value; saveDraft(); renderTest(); renderCoverage(); });
  $('net-seg').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-net]');
    if (b) { state.draft.net = b.dataset.net; saveDraft(); renderTest(); }
  });
  $('sel-ssid').addEventListener('change', (e) => { state.draft.ssid = e.target.value; saveDraft(); renderTest(); });
  $('ssid-other').addEventListener('input', (e) => { state.draft.ssid_other = e.target.value; saveDraft(); $('run').disabled = !ssidValue() || !settingsComplete(); });
  $('sel-gen').addEventListener('change', (e) => { state.draft.gen = e.target.value; saveDraft(); });
  $('crowd-seg').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-crowd]');
    if (b) { state.draft.crowd = b.dataset.crowd; saveDraft(); renderTest(); }
  });
  $('coverage').addEventListener('click', (e) => {
    const tr = e.target.closest('tr[data-spot]');
    if (tr) { state.draft.spot = tr.dataset.spot; saveDraft(); renderTest(); renderCoverage(); $('test-card').scrollIntoView({ behavior: 'smooth' }); }
  });
  $('run').addEventListener('click', startTest);
  $('cancel').addEventListener('click', () => { if (state.running) state.running.cancel(); });
  $('export').addEventListener('click', exportCSV);
  $('share').addEventListener('click', shareCSV);
  $('recent').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-flag]');
    if (!b) return;
    const r = state.readings.find((x) => x.reading_id === b.dataset.flag);
    if (!r) return;
    const flag = r.exclude !== '1';
    r.exclude = flag ? '1' : '';
    r.exclude_reason = flag ? 'flagged on phone' : '';
    saveReadings();
    renderAll();
  });
  $('clear').addEventListener('click', () => {
    if (!state.clearArmed) {
      state.clearArmed = true;
      renderRecent();
      setTimeout(() => { state.clearArmed = false; renderRecent(); }, 4000);
      return;
    }
    state.readings = [];
    state.clearArmed = false;
    saveReadings();
    store.set(KEYS.lastExport, null);
    renderAll();
  });
  window.addEventListener('beforeunload', (e) => { if (state.running) { e.preventDefault(); e.returnValue = ''; } });
}

// ---------- boot ----------
(async function main() {
  try {
    await loadData();
  } catch (e) {
    showBanner(e.message);
    $('store-line').textContent = 'Could not start.';
    return;
  }
  wire();
  renderAll();
  startGeo();
  window.__signalspot = { state, RAW_COLUMNS }; // for debugging and automated tests
})();

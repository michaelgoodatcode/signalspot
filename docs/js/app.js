// SignalSpot web app: reads data/spots_scored.json (built by the pipeline) and answers
// "where can I take a Zoom call / upload a big file right now?".
import {
  TIER_LABEL, TIER_GLYPH, windowForDate, localWeekday, pickStats, rankSpots, networkSummary,
  layoutSpots, fmtMbps, formatDuration, spotWhereText,
} from './logic.js';

const L = window.L;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const MAX_RESULTS = 8;

const state = {
  data: null, task: 'zoom', sizeGB: 2, netKey: null, when: 'now', originKey: '', origin: null,
  selected: null, now: () => new Date(),
};
let map; const markers = {}; let youMarker = null; let positions = {};
const badges = {}; const labels = {}; let collapsed = false;

// ---------- derived ----------
const meta = () => state.data.meta;
const windowsById = () => Object.fromEntries(meta().windows.map((w) => [w.id, w]));
function effectiveWindow() {
  if (state.when === 'now') return windowForDate(state.now(), meta().windows, meta().timezone);
  return state.when;
}
const network = () => state.data.networks.find((n) => n.key === state.netKey);
const netLabel = (n) => (n.type === 'wifi' ? `Wi-Fi · ${n.name}` : `Cell · ${n.name}`);

// ---------- boot ----------
async function load() {
  let res;
  try { res = await fetch('data/spots_scored.json', { cache: 'no-cache' }); } catch (e) { res = null; }
  if (!res || !res.ok) {
    showEmpty('No data published yet. Collect readings with the collector, run the pipeline, and commit <code>docs/data/spots_scored.json</code>.');
    return false;
  }
  state.data = await res.json();
  if (!state.data.networks.length) {
    showEmpty('The pipeline ran but no readings survived cleaning yet. Collect and upload some readings, then re-run it.');
    return false;
  }
  return true;
}

function showEmpty(html) {
  $('banners').innerHTML = `<div class="banner info">${html}</div>`;
  document.querySelector('.controls').hidden = true;
  document.querySelector('.summary').hidden = true;
  document.querySelector('.results').hidden = true;
  document.querySelector('.table-view').hidden = true;
  initMap([30.6170, -96.3400]);
}

function initMap(center) {
  map = L.map('map', { zoomControl: true, attributionControl: true }).setView(center, 17);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 20, maxNativeZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
}

function setupControls() {
  const nets = state.data.networks;
  state.netKey = nets[0].key;
  $('net').innerHTML = nets.map((n) => `<option value="${esc(n.key)}">${esc(netLabel(n))} (${n.n_spots} spots)</option>`).join('');
  const nowWin = windowsById()[effectiveWindowFor('now')];
  $('when').innerHTML = `<option value="now">Right now · ${esc(nowWin ? nowWin.label : '')}</option>`
    + meta().windows.map((w) => `<option value="${w.id}">${esc(w.label)}</option>`).join('')
    + '<option value="all">Any time (all readings)</option>';
  $('origin').innerHTML = '<option value="">Anywhere (rank by quality)</option><option value="geo">Use my location</option>'
    + state.data.buildings.filter((b) => b.lat != null).map((b) => `<option value="b:${esc(b.building_id)}">${esc(b.building_name)}</option>`).join('');

  document.querySelector('.seg').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-task]');
    if (!b) return;
    state.task = b.dataset.task;
    for (const x of document.querySelectorAll('.seg button')) x.setAttribute('aria-selected', String(x === b));
    $('size-row').hidden = state.task !== 'upload';
    render();
  });
  $('size').addEventListener('input', () => { const v = parseFloat($('size').value); if (v > 0) { state.sizeGB = v; render(); } });
  $('size-row').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-size]');
    if (b) { $('size').value = b.dataset.size; state.sizeGB = parseFloat(b.dataset.size); render(); }
  });
  $('net').addEventListener('change', (e) => { state.netKey = e.target.value; render(); });
  $('when').addEventListener('change', (e) => { state.when = e.target.value; render(); });
  $('origin').addEventListener('change', (e) => setOrigin(e.target.value));
  $('results').addEventListener('click', (e) => { const li = e.target.closest('li[data-spot]'); if (li) select(li.dataset.spot, true); });
  $('results').addEventListener('keydown', (e) => {
    const li = e.target.closest('li[data-spot]');
    if (li && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); select(li.dataset.spot, true); }
  });
}

function effectiveWindowFor(when) {
  return when === 'now' ? windowForDate(state.now(), meta().windows, meta().timezone) : when;
}

function setOrigin(key) {
  state.originKey = key;
  if (youMarker) { youMarker.remove(); youMarker = null; }
  if (!key) { state.origin = null; render(); return; }
  if (key.startsWith('b:')) {
    const b = state.data.buildings.find((x) => `b:${x.building_id}` === key);
    state.origin = { lat: b.lat, lng: b.lng, label: b.building_name };
    render();
    return;
  }
  if (!navigator.geolocation) { geoFailed('This browser has no location support.'); return; }
  navigator.geolocation.getCurrentPosition((p) => {
    state.origin = { lat: p.coords.latitude, lng: p.coords.longitude, label: 'you' };
    youMarker = L.marker([state.origin.lat, state.origin.lng], {
      icon: L.divIcon({ className: '', html: '<div class="you-dot"></div>', iconSize: [16, 16] }), interactive: false,
    }).addTo(map);
    render();
  }, () => geoFailed('Could not get your location.'), { enableHighAccuracy: true, timeout: 10000 });
}

function geoFailed(msg) {
  state.originKey = '';
  state.origin = null;
  $('origin').value = '';
  addBanner('info', `${msg} Pick a building instead.`, 'geo');
  render();
}

function addBanner(kind, html, id) {
  const existing = id && document.querySelector(`.banner[data-id="${id}"]`);
  if (existing) existing.remove();
  $('banners').insertAdjacentHTML('beforeend', `<div class="banner ${kind}" ${id ? `data-id="${id}"` : ''}>${html}</div>`);
}

function staticBanners() {
  const m = meta();
  if (m.synthetic) {
    addBanner('synthetic', '⚠ SYNTHETIC DEMO DATA: these numbers were generated for testing, not measured. Run the pipeline on real collector exports before presenting.', 'syn');
  }
  const today = localWeekday(state.now(), m.timezone);
  const weekendOnly = m.days_of_week.length && m.days_of_week.every((d) => d === 'Saturday' || d === 'Sunday');
  if (weekendOnly && today !== 'Saturday' && today !== 'Sunday') {
    addBanner('warn', `Measured on ${m.days_of_week.join(' & ')}. Weekday class-change crowds may make campus Wi-Fi slower than shown.`, 'weekday');
  }
}

// ---------- map ----------
function buildMap() {
  const bs = state.data.buildings.filter((b) => b.lat != null);
  const center = bs.length ? [bs.reduce((a, b) => a + b.lat, 0) / bs.length, bs.reduce((a, b) => a + b.lng, 0) / bs.length] : [30.617, -96.34];
  initMap(center);
  positions = layoutSpots(state.data.spots, state.data.buildings);
  const pts = [];
  for (const s of state.data.spots) {
    const p = positions[s.spot_id];
    if (!p) continue;
    pts.push([p.lat, p.lng]);
    const m = L.marker([p.lat, p.lng], { icon: iconFor('none', false), keyboard: true, title: s.label, riseOnHover: true });
    m.on('click', () => select(s.spot_id, false));
    m.bindPopup(() => popupHTML(s), { maxWidth: Math.min(340, map.getSize().x - 70), minWidth: Math.min(250, map.getSize().x - 70), autoPan: true, autoPanPaddingTopLeft: [50, 10], autoPanPaddingBottomRight: [10, 10] });
    m.addTo(map);
    markers[s.spot_id] = m;
  }
  for (const b of bs) {
    labels[b.building_id] = L.marker([b.lat, b.lng], { icon: L.divIcon({ className: 'bldg-label', html: `<span>${esc(b.building_id)}</span>`, iconSize: [0, 0] }), interactive: false, zIndexOffset: -1000 }).addTo(map);
    const badge = L.marker([b.lat, b.lng], { icon: badgeIcon(b, {}), keyboard: true, title: `${b.building_name}: zoom in` });
    badge.on('click', () => map.flyTo([b.lat, b.lng], Math.max(17, map.getZoom() + 2), { duration: 0.5 }));
    badges[b.building_id] = badge;
  }
  if (pts.length > 1) map.fitBounds(pts, { padding: [90, 90], maxZoom: 17, animate: false });
  placeRings();
  map.on('zoomend', placeRings);
}

// Indoor spots have no real coordinates of their own, so they are drawn on a ring around
// their building. The ring has a fixed size in screen pixels so markers never overlap,
// whatever the zoom level; it is recomputed after every zoom.
function ringGroups() {
  const groups = {};
  for (const s of state.data.spots) {
    if (!positions[s.spot_id] || !positions[s.spot_id].schematic) continue;
    const key = `${s.building_id}|${s.indoor === 1 ? 'in' : 'out'}`;
    (groups[key] = groups[key] || []).push(s);
  }
  for (const list of Object.values(groups)) {
    list.sort((a, b) => String(a.floor).localeCompare(String(b.floor), undefined, { numeric: true }) || a.spot_id.localeCompare(b.spot_id));
  }
  return groups;
}

function placeRings() {
  const z = map.getZoom();
  const groups = ringGroups();
  const cent = Object.fromEntries(state.data.buildings.map((b) => [b.building_id, b]));
  const radii = {};
  for (const [key, list] of Object.entries(groups)) {
    const [bid, kind] = key.split('|');
    if (kind === 'in') radii[bid] = Math.max(30, (list.length * 27) / (2 * Math.PI));
  }
  for (const [key, list] of Object.entries(groups)) {
    const [bid, kind] = key.split('|');
    const c = cent[bid];
    if (!c || c.lat == null) continue;
    const centerPx = map.project([c.lat, c.lng], z);
    const r = kind === 'in' ? radii[bid] : (radii[bid] || 30) + 30;
    list.forEach((s, i) => {
      const ang = (2 * Math.PI * i) / list.length - Math.PI / 2 + (kind === 'out' ? Math.PI / list.length : 0);
      const p = centerPx.add([r * Math.cos(ang), r * Math.sin(ang)]);
      if (markers[s.spot_id]) markers[s.spot_id].setLatLng(map.unproject(p, z));
    });
  }
  // Outdoor spots with real (GPS) coordinates: at overview zooms they can fall inside a
  // building's ring, so push them just outside it along the same bearing (display only).
  // Zoomed in far enough, they sit at their true position.
  for (const s of state.data.spots) {
    const pos = positions[s.spot_id];
    if (!pos || pos.schematic || s.indoor === 1 || !markers[s.spot_id]) continue;
    const c = cent[s.building_id];
    const rIn = radii[s.building_id];
    if (!c || c.lat == null || !rIn) { markers[s.spot_id].setLatLng([pos.lat, pos.lng]); continue; }
    const centerPx = map.project([c.lat, c.lng], z);
    const truePx = map.project([pos.lat, pos.lng], z);
    const v = truePx.subtract(centerPx);
    const d = Math.hypot(v.x, v.y);
    const minD = rIn + 28;
    if (d >= minD) { markers[s.spot_id].setLatLng([pos.lat, pos.lng]); continue; }
    const ang = d > 1 ? Math.atan2(v.y, v.x) : 0;
    markers[s.spot_id].setLatLng(map.unproject(centerPx.add([minD * Math.cos(ang), minD * Math.sin(ang)]), z));
  }
  updateCollapse(radii);
}

// When buildings are so close on screen that their rings would overlap (zoomed out, small
// screens), show one summary badge per building instead; tapping it zooms in.
function updateCollapse(radii) {
  const z = map.getZoom();
  const bs = state.data.buildings.filter((b) => b.lat != null);
  let next = false;
  for (let i = 0; i < bs.length && !next; i++) {
    for (let j = i + 1; j < bs.length; j++) {
      const d = map.project([bs[i].lat, bs[i].lng], z).distanceTo(map.project([bs[j].lat, bs[j].lng], z));
      const need = (radii[bs[i].building_id] || 30) + (radii[bs[j].building_id] || 30) + 2 * 40;
      if (d < need) { next = true; break; }
    }
  }
  collapsed = next;
  for (const s of state.data.spots) {
    const m = markers[s.spot_id];
    if (!m) continue;
    if (collapsed && map.hasLayer(m)) m.remove();
    if (!collapsed && !map.hasLayer(m)) m.addTo(map);
  }
  for (const [bid, badge] of Object.entries(badges)) {
    if (collapsed && !map.hasLayer(badge)) badge.addTo(map);
    if (!collapsed && map.hasLayer(badge)) badge.remove();
    const lab = labels[bid];
    if (lab) { if (collapsed && map.hasLayer(lab)) lab.remove(); if (!collapsed && !map.hasLayer(lab)) lab.addTo(map); }
  }
  document.getElementById('map').dataset.collapsed = String(collapsed);
}

function badgeIcon(b, counts) {
  const parts = ['good', 'ok', 'poor', 'dead', 'none'].filter((t) => counts[t])
    .map((t) => `<span class="cnt"><span class="dot ${t}">${TIER_GLYPH[t]}</span>${counts[t]}</span>`).join('');
  return L.divIcon({ className: 'bldg-badge', html: `<div><b>${esc(b.building_id)}</b>${parts}</div>`, iconSize: [0, 0] });
}

function iconFor(tier, selected, dim = false) {
  return L.divIcon({
    className: `spot-icon${selected ? ' selected' : ''}${dim ? ' dim' : ''}`,
    html: `<span class="dot ${tier}" aria-label="${TIER_LABEL[tier]}">${TIER_GLYPH[tier]}</span>`,
    iconSize: [22, 22], iconAnchor: [11, 11], popupAnchor: [0, -10],
  });
}

function popupHTML(spot) {
  const win = effectiveWindow();
  const winLabel = win === 'all' ? 'all readings' : (windowsById()[win] || {}).label;
  const tested = [];
  const untested = [];
  for (const n of state.data.networks) {
    const p = pickStats(spot, n.key, win);
    if (p) tested.push({ n, p }); else untested.push(n);
  }
  const rows = tested.map(({ n, p }) => {
    const s = p.s;
    return `<tr class="${n.key === state.netKey ? 'current' : ''}">
      <td>${esc(netLabel(n))}${p.fallback ? '*' : ''}</td>
      <td><span class="pill ${s.tier}">${TIER_GLYPH[s.tier]} ${TIER_LABEL[s.tier]}</span></td>
      <td>${fmtMbps(s.up_med)}↑ ${fmtMbps(s.down_med)}↓</td><td>${s.lat_med == null ? '—' : Math.round(s.lat_med) + ' ms'}</td><td class="ncol">n=${s.n}</td></tr>`;
  }).join('');
  const anyFallback = tested.some(({ p }) => p.fallback);
  const schematic = positions[spot.spot_id] && positions[spot.spot_id].schematic;
  return `<div class="popup"><h3>${esc(spot.label)}</h3>
    <p class="where">${esc(spot.building_name)}${spot.floor ? ` · floor ${esc(spot.floor)}` : ''} · ${esc(winLabel)}</p>
    <p class="note stand">${esc(spotWhereText(spot))}</p>
    ${rows ? `<table><thead><tr><th>Network</th><th>Zoom</th><th>Mbps</th><th>Latency</th><th class="ncol">Tests</th></tr></thead><tbody>${rows}</tbody></table>` : '<p class="note">No readings here yet.</p>'}
    ${untested.length && rows ? `<p class="note">Not tested here: ${untested.map((n) => esc(n.name)).join(', ')}.</p>` : ''}
    ${anyFallback ? '<p class="note">* no readings in this time window; showing all readings.</p>' : ''}
    ${schematic ? '<p class="note">Indoor spot: drawn around its building, find it by floor and label.</p>' : ''}</div>`;
}

// ---------- render ----------
function render() {
  const win = effectiveWindow();
  const items = rankSpots({
    spots: state.data.spots, netKey: state.netKey, windowId: win, task: state.task,
    sizeGB: state.sizeGB, origin: state.origin, walkSpeed: meta().walk_speed_m_per_min,
  });
  renderSummary(win);
  renderResults(items, win);
  renderTable(win);
  renderMarkers(win);
  const nowWin = windowsById()[effectiveWindowFor('now')];
  const nowOpt = $('when').querySelector('option[value="now"]');
  if (nowOpt && nowWin) nowOpt.textContent = `Right now · ${nowWin.label}`;
}

function renderSummary(win) {
  const sum = networkSummary(state.data.spots, state.netKey, win);
  const n = network();
  const winText = win === 'all' ? 'any time' : (windowsById()[win] || {}).label;
  $('summary-text').innerHTML = sum.tested
    ? `<b>${esc(netLabel(n))}</b> · ${esc(winText)}: <b>${sum.ready} of ${sum.tested}</b> tested spots can handle a video call; <b>${sum.dead}</b> ${sum.dead === 1 ? 'is a dead zone' : 'are dead zones'}.`
    : `No readings on ${esc(netLabel(n))} yet.`;
  const bar = $('tier-bar');
  bar.innerHTML = ['good', 'ok', 'poor', 'dead'].filter((t) => sum[t] > 0)
    .map((t) => `<span style="flex:${sum[t]};background:var(--${t})" title="${TIER_LABEL[t]}: ${sum[t]}"></span>`).join('');
  bar.setAttribute('aria-label', ['good', 'ok', 'poor', 'dead'].map((t) => `${TIER_LABEL[t]} ${sum[t]}`).join(', '));
  $('legend').innerHTML = ['good', 'ok', 'poor', 'dead', 'none'].map((t) =>
    `<li><span class="dot ${t}">${TIER_GLYPH[t]}</span>${TIER_LABEL[t]}${t === 'none' ? '' : ` <b>${sum[t]}</b>`}</li>`).join('');
}

function walkText(it) {
  if (it.walkMin == null) return '';
  return it.walkMin < 1 ? 'here' : `${Math.round(it.walkMin)} min walk`;
}

function renderResults(items, win) {
  const fromText = state.origin ? ` near ${esc(state.origin.label)}` : '';
  $('results-title').innerHTML = state.task === 'zoom'
    ? `Best spots for a Zoom call${fromText}`
    : `Fastest place to upload ${esc(String(state.sizeGB))} GB${fromText}`;
  const top = items.slice(0, MAX_RESULTS);
  if (!top.length) {
    $('results').innerHTML = `<li class="empty">${state.task === 'upload' ? 'No spot on this network can move data right now.' : 'No tested spots on this network.'}</li>`;
    return;
  }
  $('results').innerHTML = top.map((it, i) => {
    const s = it.s;
    const sp = it.spot;
    const tags = [
      it.fallback ? '<span class="tag" title="No readings in this time window">all-day data</span>' : '',
      s.low_confidence ? `<span class="tag" title="Fewer than ${meta().low_confidence_n} readings">n=${s.n}</span>` : '',
    ].join('');
    let metrics;
    if (state.task === 'upload') {
      const range = Number.isFinite(it.uploadSecFast) && Number.isFinite(it.uploadSecSlow) && it.uploadSecSlow !== it.uploadSecFast
        ? ` <span class="sub">(${formatDuration(it.uploadSecFast)}–${formatDuration(it.uploadSecSlow)})</span>` : '';
      const walk = it.walkMin != null ? ` + ${walkText(it)} = <b>${formatDuration(it.totalMin * 60)}</b> total` : '';
      metrics = `Upload ≈ <b>${formatDuration(it.uploadSec)}</b>${range}${walk} · ${fmtMbps(s.up_med)} Mbps up`;
    } else {
      metrics = `<b>${fmtMbps(s.up_med)}</b> up · <b>${fmtMbps(s.down_med)}</b> down Mbps · <b>${s.lat_med == null ? '—' : Math.round(s.lat_med)}</b> ms${it.walkMin != null ? ` · ${walkText(it)}` : ''}`;
    }
    return `<li class="result${state.selected === sp.spot_id ? ' selected' : ''}" data-spot="${esc(sp.spot_id)}" tabindex="0">
      <span class="rank">${i + 1}</span>
      <span class="name">${esc(sp.label)}</span>
      <span class="right"><span class="pill ${s.tier}">${TIER_GLYPH[s.tier]} ${TIER_LABEL[s.tier]}</span></span>
      <span class="sub">${esc(sp.building_name)}${sp.floor ? ` · floor ${esc(sp.floor)}` : ''}${tags}</span>
      <span class="metrics">${metrics}</span></li>`;
  }).join('');
}

function renderTable(win) {
  const rows = state.data.spots.map((sp) => ({ sp, p: pickStats(sp, state.netKey, win) }))
    .sort((a, b) => a.sp.building_id.localeCompare(b.sp.building_id) || String(a.sp.floor).localeCompare(String(b.sp.floor), undefined, { numeric: true }));
  $('table-wrap').innerHTML = `<table><thead><tr><th>Spot</th><th>Bldg</th><th>Zoom</th><th>Up</th><th>Down</th><th>ms</th><th>n</th></tr></thead><tbody>${
    rows.map(({ sp, p }) => {
      const s = p && p.s;
      return `<tr><td>${esc(sp.label)}</td><td>${esc(sp.building_id)}</td><td>${s ? TIER_LABEL[s.tier] : 'No data'}</td>
        <td>${s ? fmtMbps(s.up_med) : '—'}</td><td>${s ? fmtMbps(s.down_med) : '—'}</td><td>${s && s.lat_med != null ? Math.round(s.lat_med) : '—'}</td><td>${s ? s.n : 0}</td></tr>`;
    }).join('')}</tbody></table>`;
}

function renderMarkers(win) {
  for (const b of state.data.buildings) {
    if (!badges[b.building_id]) continue;
    const counts = {};
    for (const sp of state.data.spots.filter((x) => x.building_id === b.building_id)) {
      const p = pickStats(sp, state.netKey, win);
      const t = p ? p.s.tier : 'none';
      counts[t] = (counts[t] || 0) + 1;
    }
    badges[b.building_id].setIcon(badgeIcon(b, counts));
  }
  for (const sp of state.data.spots) {
    const m = markers[sp.spot_id];
    if (!m) continue;
    const p = pickStats(sp, state.netKey, win);
    const tier = p ? p.s.tier : 'none';
    const dim = state.task === 'upload' && tier === 'dead';
    m.setIcon(iconFor(tier, state.selected === sp.spot_id, dim));
    m.setZIndexOffset(state.selected === sp.spot_id ? 1000 : (tier === 'none' ? -100 : 0));
    if (m.isPopupOpen()) m.setPopupContent(popupHTML(sp));
  }
}

function select(spotId, fromList) {
  state.selected = spotId;
  render();
  const m = markers[spotId];
  if (m) {
    if (fromList) {
      const sp = state.data.spots.find((s) => s.spot_id === spotId);
      const b = state.data.buildings.find((x) => x.building_id === sp.building_id);
      // Ring markers move with zoom, so center on the building, then open the popup.
      const target = positions[spotId] && positions[spotId].schematic && b ? L.latLng(b.lat, b.lng) : m.getLatLng();
      const zoom = Math.max(map.getZoom(), 17);
      if (collapsed || map.getZoom() !== zoom || !map.getBounds().pad(-0.2).contains(target)) {
        map.once('moveend', () => { if (map.hasLayer(m)) m.openPopup(); });
        map.flyTo(target, zoom, { duration: 0.5 });
        return;
      }
    }
    m.openPopup();
  }
  const li = document.querySelector(`#results li[data-spot="${CSS.escape(spotId)}"]`);
  if (li && !fromList) li.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function renderAbout() {
  const m = meta();
  const fmt = (s) => (s ? new Date(`${s}`).toLocaleString('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—');
  const repo = m.repo_url ? ` · <a href="${esc(m.repo_url)}" target="_blank" rel="noopener">Methodology, dataset &amp; code</a>` : '';
  $('about').innerHTML = `Based on <b>${m.n_readings}</b> speed tests at <b>${m.n_spots_covered}</b> of ${m.n_spots_total} spots,
    measured on foot by ${m.n_collectors} team member${m.n_collectors === 1 ? '' : 's'} between ${fmt(m.collection_start_local)} and ${fmt(m.collection_end_local)} (campus time).
    Tiers use Zoom's published bandwidth guidance plus our latency/jitter cutoffs. Medians shown; failed tests count as 0 Mbps.${repo}
    <br><a href="collector/">Collector (team use)</a>`;
}

(async function main() {
  const ok = await load();
  if (!ok) return;
  staticBanners();
  buildMap();
  setupControls();
  renderAbout();
  render();
  // Keep "right now" current if the page stays open across a window boundary.
  setInterval(() => { if (state.when === 'now') render(); }, 60000);
  window.__signalspotApp = { state, render, select, get map() { return map; } };
})();

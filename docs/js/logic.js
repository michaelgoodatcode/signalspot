// Pure functions shared by the app and the collector. No DOM access, so they can be
// unit-tested with `node --test tests/js`.

export const TIER_ORDER = { good: 0, ok: 1, poor: 2, dead: 3 };
export const TIER_LABEL = { good: 'Zoom-ready', ok: 'Usable', poor: 'Weak', dead: 'Dead zone', none: 'No data' };
export const TIER_GLYPH = { good: '✓', ok: '~', poor: '!', dead: '✕', none: '?' };

/** Local hour (0-23) of a Date in an IANA time zone. */
export function localHour(date, timeZone) {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone, hour: 'numeric', hourCycle: 'h23' }).formatToParts(date);
  const h = Number(parts.find((p) => p.type === 'hour').value);
  return h === 24 ? 0 : h;
}

export function hoursOf(w) {
  const out = [];
  if (w.start_hour === w.end_hour) for (let h = 0; h < 24; h++) out.push(h);
  else if (w.start_hour < w.end_hour) for (let h = w.start_hour; h < w.end_hour; h++) out.push(h);
  else {
    for (let h = w.start_hour; h < 24; h++) out.push(h);
    for (let h = 0; h < w.end_hour; h++) out.push(h);
  }
  return out;
}

/** Id of the configured time window that contains `date` (in the data's time zone). */
export function windowForDate(date, windows, timeZone) {
  const h = localHour(date, timeZone);
  const w = windows.find((win) => hoursOf(win).includes(h));
  return w ? w.id : null;
}

export function localWeekday(date, timeZone) {
  return new Intl.DateTimeFormat('en-US', { timeZone, weekday: 'long' }).format(date);
}

/** Zoom tier for a single speed test (used by the collector's instant verdict). Mirrors aggregate.zoom_tier. */
export function tierForReading(r, tiers) {
  const down = r.download_mbps ?? 0;
  const up = r.upload_mbps ?? 0;
  if (r.status === 'failed' || (up < 0.1 && down < 0.1)) return 'dead';
  for (const name of ['good', 'ok']) {
    const t = tiers[name];
    const latOk = r.latency_ms != null && r.latency_ms <= t.latency_ms;
    const jitOk = r.jitter_ms == null || r.jitter_ms <= t.jitter_ms;
    if (up >= t.up_mbps && down >= t.down_mbps && latOk && jitOk) return name;
  }
  return 'poor';
}

/**
 * Stats for a spot on a network in a time window. Falls back to all-day stats when
 * the spot has no readings in that window. Returns null if the spot has no data at all.
 */
export function pickStats(spot, netKey, windowId) {
  const byWin = spot.stats && spot.stats[netKey];
  if (!byWin) return null;
  if (windowId && windowId !== 'all' && byWin[windowId]) return { s: byWin[windowId], fallback: false, windowUsed: windowId };
  if (byWin.all) return { s: byWin.all, fallback: Boolean(windowId && windowId !== 'all'), windowUsed: 'all' };
  return null;
}

export function haversineM(a, b) {
  const R = 6371000;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLng = toRad(b.lng - a.lng);
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}

/** Seconds to upload `sizeGB` (decimal GB) at `mbps` megabits per second. */
export function uploadSeconds(sizeGB, mbps) {
  if (!(mbps > 0)) return Infinity;
  return (sizeGB * 8000) / mbps;
}

export function formatDuration(sec) {
  if (!Number.isFinite(sec)) return 'never';
  if (sec < 60) return `${Math.max(1, Math.round(sec))} s`;
  const min = sec / 60;
  if (min < 60) return `${Math.round(min)} min`;
  const h = Math.floor(min / 60);
  const m = Math.round(min - h * 60);
  return m ? `${h} h ${m} min` : `${h} h`;
}

export function fmtMbps(x) {
  if (x == null) return '—';
  if (x >= 100) return String(Math.round(x));
  if (x >= 10) return x.toFixed(0);
  return x.toFixed(1);
}

/**
 * Rank spots for a task.
 *  task 'zoom'   → best tier first; within a tier the closest (if origin given) else highest zoom_score.
 *  task 'upload' → fastest total time (walk + upload) if origin given, else fastest upload. Dead zones excluded.
 */
export function rankSpots({ spots, netKey, windowId, task, sizeGB = 2, origin = null, walkSpeed = 80 }) {
  const items = [];
  for (const spot of spots) {
    const picked = pickStats(spot, netKey, windowId);
    if (!picked) continue;
    const { s, fallback } = picked;
    const distanceM = origin && spot.lat != null ? haversineM(origin, spot) : null;
    const walkMin = distanceM == null ? null : distanceM / walkSpeed;
    const item = { spot, s, fallback, distanceM, walkMin };
    if (task === 'upload') {
      if (s.tier === 'dead') continue;
      item.uploadSec = uploadSeconds(sizeGB, s.up_med);
      item.uploadSecSlow = uploadSeconds(sizeGB, s.up_p25);
      item.uploadSecFast = uploadSeconds(sizeGB, s.up_p75);
      item.totalMin = (walkMin ?? 0) + item.uploadSec / 60;
    }
    items.push(item);
  }
  const byScore = (a, b) => (b.s.zoom_score ?? 0) - (a.s.zoom_score ?? 0);
  if (task === 'upload') {
    items.sort((a, b) => (a.totalMin - b.totalMin) || byScore(a, b));
  } else {
    items.sort((a, b) => {
      const t = TIER_ORDER[a.s.tier] - TIER_ORDER[b.s.tier];
      if (t) return t;
      if (origin && a.walkMin != null && b.walkMin != null && Math.abs(a.walkMin - b.walkMin) > 0.25) return a.walkMin - b.walkMin;
      return byScore(a, b);
    });
  }
  return items;
}

/** Tier counts for a network/window across all spots that have data. */
export function networkSummary(spots, netKey, windowId) {
  const counts = { good: 0, ok: 0, poor: 0, dead: 0 };
  let tested = 0;
  for (const spot of spots) {
    const p = pickStats(spot, netKey, windowId);
    if (!p) continue;
    tested += 1;
    counts[p.s.tier] += 1;
  }
  return { tested, ...counts, ready: counts.good + counts.ok };
}

/** Deterministic display positions: indoor spots drawn on a small ring around their building. */
export function layoutSpots(spots, buildings) {
  const centroid = Object.fromEntries(buildings.map((b) => [b.building_id, b]));
  const groups = {};
  for (const s of spots) {
    const fallback = s.coord_source === 'building_fallback' || s.coord_source === 'unplaced';
    const ringed = (s.indoor === 1 && s.coord_source !== 'gps_median' && s.coord_source !== 'manual') || fallback;
    if (!ringed) continue;
    const key = `${s.building_id}|${s.indoor === 1 ? 'in' : 'out'}`;
    (groups[key] = groups[key] || []).push(s);
  }
  const pos = {};
  for (const s of spots) if (s.lat != null) pos[s.spot_id] = { lat: s.lat, lng: s.lng, schematic: false };
  for (const [key, list] of Object.entries(groups)) {
    const [bid, kind] = key.split('|');
    const c = centroid[bid];
    if (!c || c.lat == null) continue;
    list.sort((a, b) => String(a.floor).localeCompare(String(b.floor), undefined, { numeric: true }) || a.spot_id.localeCompare(b.spot_id));
    const radiusM = (kind === 'in' ? 16 : 34) + list.length * 0.8;
    list.forEach((s, i) => {
      const ang = (2 * Math.PI * i) / list.length - Math.PI / 2;
      const dLat = (radiusM * Math.cos(ang)) / 111320;
      const dLng = (radiusM * Math.sin(ang)) / (111320 * Math.cos((c.lat * Math.PI) / 180));
      pos[s.spot_id] = { lat: c.lat + dLat, lng: c.lng + dLng, schematic: true };
    });
  }
  return pos;
}

// ---------- where is a spot? (collector) ----------

export const DEFAULT_BUILDING_MISMATCH_M = 400;
const OUTDOOR_SIDES = { N: 'north', S: 'south', E: 'east', W: 'west' };

/** Building centroids from spots.csv rows (indoor spots with coordinates; else any spot with coordinates). */
export function buildingCentroids(spots) {
  const by = {};
  for (const s of spots) {
    const lat = parseFloat(s.lat);
    const lng = parseFloat(s.lng);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) continue;
    const b = (by[s.building_id] = by[s.building_id] || { indoor: [], all: [], name: s.building_name });
    (String(s.indoor) === '1' ? b.indoor : b.all).push([lat, lng]);
  }
  const out = {};
  for (const [id, b] of Object.entries(by)) {
    const pts = b.indoor.length ? b.indoor : b.all;
    out[id] = { building_id: id, building_name: b.name, lat: pts.reduce((a, p) => a + p[0], 0) / pts.length, lng: pts.reduce((a, p) => a + p[1], 0) / pts.length };
  }
  return out;
}

/** Closest building to a position: { building_id, building_name, distanceM } or null. */
export function nearestBuilding(pos, centroids) {
  let best = null;
  for (const c of Object.values(centroids)) {
    const d = haversineM(pos, c);
    if (!best || d < best.distanceM) best = { building_id: c.building_id, building_name: c.building_name, distanceM: d };
  }
  return best;
}

/**
 * Location check for the collector. Returns { state, message, distanceM, nearest }.
 * state: 'off' (GPS disabled), 'waiting', 'imprecise', 'ok', 'mismatch', 'nocoords'.
 */
export function locationCheck(pos, buildingId, centroids, { thresholdM = DEFAULT_BUILDING_MISMATCH_M, maxAccuracyM = 150 } = {}) {
  const c = centroids[buildingId];
  if (!c) return { state: 'nocoords', message: 'No coordinates for this building in spots.csv, so no location check.' };
  if (!pos) return { state: 'waiting', message: 'Waiting for GPS…' };
  if (pos.acc > maxAccuracyM) return { state: 'imprecise', message: `GPS is too rough right now (±${Math.round(pos.acc)} m) for a check.` };
  const d = haversineM(pos, c);
  const nearest = nearestBuilding(pos, centroids);
  const r10 = (m) => (m < 1000 ? `${Math.max(10, Math.round(m / 10) * 10)} m` : `${(m / 1000).toFixed(1)} km`);
  if (d <= thresholdM) return { state: 'ok', distanceM: d, nearest, message: `GPS check: you're about ${r10(d)} from ${c.building_name}.` };
  const near = nearest && nearest.building_id !== buildingId && nearest.distanceM <= thresholdM
    ? ` You look closer to ${nearest.building_name} (${r10(nearest.distanceM)}).` : '';
  return { state: 'mismatch', distanceM: d, nearest, message: `GPS says you're ${r10(d)} from ${c.building_name}.${near}` };
}

/** Plain-language "where to stand" text for a spot. Uses the optional `description` column if filled in. */
export function spotWhereText(spot) {
  if (spot.description && String(spot.description).trim()) return String(spot.description).trim();
  const f = spot.floor ? `floor ${spot.floor}` : '';
  const zone = String(spot.zone || '').toLowerCase();
  if (String(spot.indoor) === '0' || zone === 'outdoor') {
    const suffix = String(spot.spot_id || '').split('-').pop();
    if (OUTDOOR_SIDES[suffix]) return `Outside, about 10 m from the ${OUTDOOR_SIDES[suffix]} side of ${spot.building_name}.`;
    if (suffix === 'PLAZA') return `Outside ${spot.building_name}, at the nearest plaza or outdoor seating.`;
    return `Outside ${spot.building_name}: ${spot.label}.`;
  }
  if (zone.includes('study')) return `On ${f}, sit at the main open study / seating area (not inside a closed room).`;
  if (zone.includes('lobby') || (zone.includes('hall') && !zone.includes('hallway'))) return `On ${f}, stand in the main hall or lobby by the elevators / main stairs.`;
  if (zone.includes('hallway') || zone.includes('corner')) return `On ${f}, go to the far end of a hallway or a corner, as far from the lobby as you can.`;
  if (zone.includes('stair')) return `Inside a stairwell at the ${f} landing, with the door closed behind you.`;
  return `${spot.label}${f ? ` (${f})` : ''}.`;
}

// ---------- speed test result → reading (collector) ----------

const posNum = (x, d = 1) => (typeof x === 'number' && Number.isFinite(x) && x > 0 ? Number(x.toFixed(d)) : null);
const posMbps = (bps) => (typeof bps === 'number' && Number.isFinite(bps) && bps > 0 ? Number((bps / 1e6).toFixed(3)) : null);

/**
 * Convert a Cloudflare speed test summary into reading fields.
 * When every request fails, the engine reports 0 for bandwidth AND latency rather than
 * "missing", so non-positive values are treated as "nothing measured". A test with no
 * measurements at all is a 'failed' (dead-zone) reading.
 */
export function summaryToReading(summary, scores, rawError, errors, forcedError, durationS) {
  summary = summary || {};
  const latency = posNum(summary.latency);
  const r = {
    download_mbps: posMbps(summary.download),
    upload_mbps: posMbps(summary.upload),
    latency_ms: latency,
    jitter_ms: latency == null ? null : (typeof summary.jitter === 'number' && Number.isFinite(summary.jitter) && summary.jitter >= 0 ? Number(summary.jitter.toFixed(1)) : null),
    down_loaded_latency_ms: posNum(summary.downLoadedLatency),
    up_loaded_latency_ms: posNum(summary.upLoadedLatency),
    cf_rtc_class: (latency != null && scores && scores.rtc && scores.rtc.classificationName) || '',
    duration_s: typeof durationS === 'number' && Number.isFinite(durationS) ? Number(durationS.toFixed(1)) : null,
  };
  const hasAny = r.download_mbps != null || r.upload_mbps != null || r.latency_ms != null;
  const aborted = Boolean(forcedError) || /abort/i.test(rawError || '');
  r.status = !hasAny ? 'failed' : (aborted || r.download_mbps == null || r.upload_mbps == null) ? 'partial' : 'ok';
  const msgs = [...new Set([forcedError, rawError, ...(errors || [])].filter(Boolean).map(String))];
  r.error = msgs.join(' | ').slice(0, 300);
  return r;
}

// ---------- CSV helpers (collector) ----------

export function csvEscape(v) {
  if (v === null || v === undefined) return '';
  const s = String(v);
  return /[",\r\n]/.test(s) || /^\s|\s$/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCSV(rows, columns) {
  const lines = [columns.join(',')];
  for (const r of rows) lines.push(columns.map((c) => csvEscape(r[c])).join(','));
  return lines.join('\r\n') + '\r\n';
}

/** Minimal RFC 4180 parser (handles quotes, commas and newlines inside quotes). */
export function parseCSV(text) {
  const rows = [];
  let row = [];
  let field = '';
  let q = false;
  text = text.replace(/^﻿/, '');
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) {
      if (ch === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; } else q = false;
      } else field += ch;
    } else if (ch === '"') q = true;
    else if (ch === ',') { row.push(field); field = ''; }
    else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++;
      row.push(field); field = '';
      if (row.length > 1 || row[0] !== '') rows.push(row);
      row = [];
    } else field += ch;
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  if (!rows.length) return [];
  const header = rows[0].map((h) => h.trim());
  return rows.slice(1).map((r) => Object.fromEntries(header.map((h, i) => [h, (r[i] ?? '').trim()])));
}

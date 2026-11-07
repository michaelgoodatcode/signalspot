// Wraps Cloudflare's open-source speed test engine (@cloudflare/speedtest, MIT, vendored in
// docs/vendor/speedtest) and turns one run into one SignalSpot reading.
import SpeedTest from '../vendor/speedtest/speedtest.js';
import { summaryToReading } from '../js/logic.js';

export { summaryToReading };

// ~30 MB worst case per test. The engine stops escalating to bigger files once a request
// takes over 1 s (bandwidthFinishRequestDuration), so slow connections use much less data.
export const MEASUREMENTS = [
  { type: 'latency', numPackets: 1 },
  { type: 'download', bytes: 1e5, count: 1, bypassMinDuration: true },
  { type: 'latency', numPackets: 20 },
  { type: 'download', bytes: 1e5, count: 4 },
  { type: 'download', bytes: 1e6, count: 4 },
  { type: 'upload', bytes: 1e5, count: 4 },
  { type: 'upload', bytes: 1e6, count: 4 },
  { type: 'download', bytes: 1e7, count: 1 },
  { type: 'upload', bytes: 1e7, count: 1 },
];

export const ENGINE_CONFIG = {
  autoStart: false,
  measurements: MEASUREMENTS,
  logAimApiUrl: null,          // don't send results to Cloudflare's aggregate logging endpoint
  logMeasurementApiUrl: null,
  abortTestDuration: 75000,    // a dead zone ends the test after 75 s with whatever was measured
  abortRequestDuration: 20000, // a single stuck request is abandoned after 20 s
};

const WATCHDOG_MS = 95000;

const safe = (fn, fallback) => { try { const v = fn(); return v === undefined ? fallback : v; } catch { return fallback; } };

/**
 * Run one speed test. Returns { promise, cancel }.
 * promise resolves to a partial reading (measurement fields + status + error); never rejects.
 * cancel() stops the test and resolves with { cancelled: true }.
 */
export function runSpeedTest({ onProgress = () => {}, factory } = {}) {
  const make = factory || (typeof window !== 'undefined' && window.__SIGNALSPOT_ENGINE_FACTORY__) || ((cfg) => new SpeedTest(cfg));
  let engine = null;
  let done = false;
  let resolveFn;
  const errors = [];
  const started = (typeof performance !== 'undefined' ? performance : Date).now();
  const elapsed = () => ((typeof performance !== 'undefined' ? performance : Date).now() - started) / 1000;
  const promise = new Promise((resolve) => { resolveFn = resolve; });

  const finish = (results, forcedError) => {
    if (done) return;
    done = true;
    clearTimeout(watchdog);
    const summary = results ? safe(() => results.getSummary(), {}) : {};
    const scores = results ? safe(() => results.getScores(), {}) : {};
    const rawErr = results ? safe(() => results.raw && results.raw.error, '') : '';
    resolveFn(summaryToReading(summary || {}, scores, rawErr, errors, forcedError, elapsed()));
  };

  const watchdog = setTimeout(() => {
    safe(() => engine && engine.pause());
    finish(engine ? safe(() => engine.results, null) : null, 'watchdog timeout');
  }, WATCHDOG_MS);

  try {
    engine = make({ ...ENGINE_CONFIG });
    engine.onPhaseChange = (p) => onProgress({ phase: p && p.measurement ? p.measurement.type : '', summary: safe(() => engine.results.getSummary(), {}) });
    engine.onResultsChange = () => onProgress({ summary: safe(() => engine.results.getSummary(), {}) });
    engine.onError = (msg) => { errors.push(String(msg)); };
    engine.onFinish = (results) => finish(results);
    engine.play();
  } catch (e) {
    errors.push(String((e && e.message) || e));
    finish(null, 'engine failed to start');
  }

  const cancel = () => {
    if (done) return;
    done = true;
    clearTimeout(watchdog);
    safe(() => engine && engine.pause());
    resolveFn({ cancelled: true });
  };
  return { promise, cancel };
}

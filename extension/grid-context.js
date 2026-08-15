/* Pure, bounded extraction of Euphoria quote-grid context.
 * Loaded in the page world and required directly by Node regression tests.
 */
(function attachGridContext(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.__euphoriaGridContext = api;
})(typeof window !== "undefined" ? window : globalThis, function gridContextFactory() {
  "use strict";

  const MAX_HISTORY_SAMPLES = 120;
  const FORWARD_COLUMNS = 3;
  const ROW_RADIUS = 2;

  function finitePositive(value) {
    const n = Number(value);
    return Number.isFinite(n) && n > 0 ? n : null;
  }

  function summarizeHistory(rawHistory, nowMs) {
    const valid = (Array.isArray(rawHistory) ? rawHistory : [])
      .map((item) => ({ ts: Number(item && item.ts), price: Number(item && item.price) }))
      .filter((item) => Number.isFinite(item.ts) && finitePositive(item.price))
      .filter((item) => item.ts <= nowMs + 1000 && item.ts >= nowMs - 120000)
      .sort((a, b) => a.ts - b.ts);
    const samples = valid.slice(-MAX_HISTORY_SAMPLES);
    if (!samples.length) {
      return { window_s: 120, sample_count: 0, change_bps: 0, range_bps: 0, samples: [] };
    }
    const first = samples[0].price;
    const last = samples[samples.length - 1].price;
    const prices = samples.map((item) => item.price);
    return {
      window_s: Math.max(0, Math.round((samples[samples.length - 1].ts - samples[0].ts) / 1000)),
      sample_count: samples.length,
      change_bps: Number((((last - first) / first) * 10000).toFixed(4)),
      range_bps: Number((((Math.max(...prices) - Math.min(...prices)) / first) * 10000).toFixed(4)),
      samples,
    };
  }

  function buildGridContext(options) {
    const opts = options || {};
    const chart = opts.chart;
    const feed = chart && chart.quotesFeed;
    const nowMs = Number(opts.nowMs);
    const price = finitePositive(opts.price);
    const squareDuration = finitePositive(opts.squareDuration) || 5000;
    const dollarsPerLine = finitePositive(opts.dollarsPerLine) || 0.5;
    const history = summarizeHistory(opts.history, Number.isFinite(nowMs) ? nowMs : Date.now());
    const base = {
      authoritative: false,
      multiplier_source: null,
      quoted_grid_ref_time: feed && feed.lastGridTimestamp != null ? Number(feed.lastGridTimestamp) : null,
      now_ms: Number.isFinite(nowMs) ? nowMs : Date.now(),
      square_duration: squareDuration,
      dollars_per_line: dollarsPerLine,
      price,
      forward_columns: FORWARD_COLUMNS,
      cells: [],
      history,
    };
    if (!feed || typeof feed.getMultiplierForCell !== "function") {
      return { ...base, reason: "quote grid unavailable" };
    }
    if (feed.isStale === true) {
      return { ...base, reason: "quote grid stale" };
    }
    if (!price) return { ...base, reason: "price unavailable" };

    const currentX = Math.floor(base.now_ms / squareDuration);
    const currentY = Math.floor(price / dollarsPerLine);
    let boundaries = null;
    if (typeof feed.getMultipliersBoundries === "function") {
      try { boundaries = feed.getMultipliersBoundries(); } catch { boundaries = null; }
    }
    const cells = [];
    for (let forward = 1; forward <= FORWARD_COLUMNS; forward += 1) {
      const cellX = currentX + forward;
      for (let dy = -ROW_RADIUS; dy <= ROW_RADIUS; dy += 1) {
        const cellY = currentY + dy;
        if (boundaries) {
          const minX = Number(boundaries.startTime);
          const maxX = Number(boundaries.endTime);
          const minY = Number(boundaries.startPrice);
          const maxY = Number(boundaries.endPrice);
          if (Number.isFinite(minX) && cellX < minX) continue;
          if (Number.isFinite(maxX) && cellX > maxX) continue;
          if (Number.isFinite(minY) && cellY < minY) continue;
          if (Number.isFinite(maxY) && cellY > maxY) continue;
        }
        let raw = null;
        try { raw = feed.getMultiplierForCell(cellX, cellY); } catch { raw = null; }
        const multiplier = finitePositive(raw);
        if (!multiplier) continue;
        cells.push({
          cell_x: cellX,
          cell_y: cellY,
          forward,
          forward_s: (forward * squareDuration) / 1000,
          row_offset: dy,
          side: dy < 0 ? "down" : dy > 0 ? "up" : "at-price",
          distance: Math.abs(dy),
          multiplier,
          break_even_probability: Number((1 / multiplier).toFixed(6)),
        });
      }
    }
    return {
      ...base,
      authoritative: cells.length > 0 && Number.isFinite(base.quoted_grid_ref_time),
      multiplier_source: "quotesFeed",
      reason: cells.length ? null : "no valid quoted cells",
      current_cell_x: currentX,
      current_cell_y: currentY,
      boundaries,
      cells,
    };
  }

  function normalCdf(value) {
    const x = Number(value);
    if (!Number.isFinite(x)) return x > 0 ? 1 : 0;
    const sign = x < 0 ? -1 : 1;
    const z = Math.abs(x) / Math.sqrt(2);
    const t = 1 / (1 + 0.3275911 * z);
    const erf = sign * (1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-z * z));
    return 0.5 * (1 + erf);
  }

  function buildProjection(options) {
    const opts = options || {};
    const nowMs = Number.isFinite(Number(opts.nowMs)) ? Number(opts.nowMs) : Date.now();
    const price = finitePositive(opts.price);
    const dpl = finitePositive(opts.dollarsPerLine) || 0.5;
    const squareDuration = finitePositive(opts.squareDuration) || 5000;
    const history = summarizeHistory(opts.history && opts.history.samples ? opts.history.samples : opts.history, nowMs);
    const projector = opts.projector && typeof opts.projector === "object" ? opts.projector : {};
    const accuracy = projector.rolling_accuracy && typeof projector.rolling_accuracy === "object" ? projector.rolling_accuracy : {};
    const hitRate = Number(accuracy.cone_hit_rate);
    const accuracyN = Math.max(0, Math.floor(Number(accuracy.n) || 0));
    const minimumAccuracySamples = Math.max(1, Math.floor(Number(projector.minimum_accuracy_samples) || 20));
    const validRate = Number.isFinite(hitRate) && hitRate >= 0 && hitRate <= 1 ? hitRate : 0;
    const lowConfidence = accuracyN < minimumAccuracySamples || validRate < 0.60;
    const sampleSuffix = accuracyN < minimumAccuracySamples ? ` · n=${accuracyN}/${minimumAccuracySamples}` : "";
    const accuracyBadge = `${lowConfidence ? "LOW CONFIDENCE · " : ""}${Math.round(validRate * 100)}%${sampleSuffix}`;
    const base = {
      schema_version: 1,
      generated_at_ms: nowMs,
      current_cell_y: price ? Math.floor(price / dpl) : null,
      square_duration: squareDuration,
      dollars_per_line: dpl,
      history_sample_count: history.sample_count,
      vol_bucket: "UNKNOWN",
      rolling_accuracy: { n: accuracyN, cone_hit_rate: validRate },
      minimum_accuracy_samples: minimumAccuracySamples,
      accuracy_badge: accuracyBadge,
      low_confidence: lowConfidence,
      enabled_default: accuracyN >= minimumAccuracySamples && validRate > 0.60,
      points: [],
    };
    if (!price || history.samples.length < 3) return { ...base, reason: "INSUFFICIENT_TICKS" };

    const velocities = [];
    for (let i = 1; i < history.samples.length; i += 1) {
      const left = history.samples[i - 1];
      const right = history.samples[i];
      const dt = (right.ts - left.ts) / 1000;
      if (dt > 0 && dt <= 30) velocities.push(((right.price - left.price) / dpl) / dt);
    }
    if (velocities.length < 2) return { ...base, reason: "INSUFFICIENT_TICKS" };
    let drift = velocities[0];
    for (const velocity of velocities.slice(1)) drift = 0.35 * velocity + 0.65 * drift;
    const mean = velocities.reduce((total, value) => total + value, 0) / velocities.length;
    const variance = velocities.reduce((total, value) => total + Math.pow(value - mean, 2), 0) / velocities.length;
    const sigmaPerS = Math.sqrt(Math.max(0, variance));
    const rangeCells = history.samples.reduce((acc, row) => ({ min: Math.min(acc.min, row.price), max: Math.max(acc.max, row.price) }), { min: Infinity, max: -Infinity });
    const spanCells = (rangeCells.max - rangeCells.min) / dpl;
    const compression = spanCells <= 2;
    const volBucket = sigmaPerS < 0.10 ? "LOW" : sigmaPerS < 0.35 ? "MED" : "HIGH";
    const coefficients = projector.coefficients && typeof projector.coefficients === "object" ? projector.coefficients : {};
    const driftWeight = Number.isFinite(Number(coefficients.drift_weight)) ? Number(coefficients.drift_weight) : 1;
    const leanCells = Number.isFinite(Number(coefficients.lean_cells)) ? Number(coefficients.lean_cells) : 0.25;
    const widthScale = Math.min(3, Math.max(0.5, Number(coefficients.width_scale) || 1));
    const compressionScale = compression ? Math.min(1, Math.max(0.5, Number(coefficients.compression_scale) || 0.75)) : 1;
    const frames = opts.think && opts.think.timeframes || {};
    const leanValue = (key) => frames[key] && frames[key].lean === "up" ? 1 : frames[key] && frames[key].lean === "down" ? -1 : 0;
    const leanScore = (leanValue("1m") + leanValue("5m")) / 2;
    const currentY = base.current_cell_y;
    const points = [5, 10, 15].map((horizon, index) => {
      const delta = drift * horizon * driftWeight + leanScore * leanCells;
      const expected = currentY + delta;
      const width = Math.max(0.5, sigmaPerS * Math.sqrt(horizon) * widthScale * compressionScale);
      const low = Math.floor(expected - width);
      const high = Math.ceil(expected + width);
      const pUp = sigmaPerS > 1e-9 ? normalCdf(delta / Math.max(width, 1e-9)) : delta > 0 ? 1 : delta < 0 ? 0 : 0.5;
      return {
        horizon_s: horizon,
        forward: index + 1,
        expected_cell_y: Number(expected.toFixed(4)),
        cell_range_68pct: [low, high],
        p_up: Number(pUp.toFixed(6)),
        p_down: Number((1 - pUp).toFixed(6)),
      };
    });
    return {
      ...base,
      reason: null,
      vol_bucket: volBucket,
      drift_cells_per_s: Number(drift.toFixed(6)),
      sigma_cells_per_s: Number(sigmaPerS.toFixed(6)),
      compression_box: compression,
      lean_score: leanScore,
      points,
    };
  }

  return { buildGridContext, summarizeHistory, buildProjection };
});

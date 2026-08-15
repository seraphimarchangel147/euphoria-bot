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

  return { buildGridContext, summarizeHistory };
});

/* Pure advice formatting for the manual-player overlay. No action API. */
(function attachPlayerAdvice(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.__euphoriaPlayerAdvice = api;
})(typeof window !== "undefined" ? window : globalThis, function playerAdviceFactory() {
  "use strict";

  function finite(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function formatPlayerAdvice(model) {
    const source = model && typeof model === "object" ? model : {};
    const context = source.current_context && typeof source.current_context === "object" ? source.current_context : null;
    const rows = Array.isArray(source.hot_squares_current) ? source.hot_squares_current : [];
    const hot = rows.find((row) => row && row.hot === true && row.status === "HOT" && finite(row.ev_lower_bound) > 0 && ["up", "down"].includes(String(row.side).toLowerCase()) && finite(row.distance) != null && finite(row.n) >= 20 && finite(row.win_rate) != null && finite(row.ev) != null);
    let edge;
    if (hot && context && context.vol_bucket && context.hour_bucket) {
      const side = String(hot.side).toUpperCase();
      const sign = side === "UP" ? "+" : "−";
      const ev = finite(hot.ev);
      edge = `${context.vol_bucket}-vol ${context.hour_bucket}: ${sign}${Math.abs(finite(hot.distance))} ${side} ${Math.round(finite(hot.win_rate) * 100)}% n=${Math.floor(finite(hot.n))} EV${ev >= 0 ? "+" : ""}${ev.toFixed(2)}`;
    } else if (context && finite(source.settled_taps) != null && finite(source.settled_taps) < 20) {
      edge = `${context.vol_bucket || "UNKNOWN"}-vol ${context.hour_bucket || ""}: INSUFFICIENT n=${Math.floor(finite(source.settled_taps))}`.trim();
    } else {
      edge = "no proven player edge";
    }
    const tilt = source.tilt && typeof source.tilt === "object" ? source.tilt : {};
    const baseline = finite(tilt.baseline_win_rate);
    const rolling = finite(tilt.rolling_10_win_rate);
    const banner = tilt.flagged === true && baseline != null && rolling != null
      ? `TILT WARNING · WR ${Math.round(baseline * 100)}% → ${Math.round(rolling * 100)}% · pause and reset`
      : null;
    return { edge, tilt: banner };
  }

  function buildVisualPlan(options) {
    const opts = options || {};
    const model = opts.model && typeof opts.model === "object" ? opts.model : {};
    const projection = opts.projection && typeof opts.projection === "object" ? opts.projection : {};
    const accuracy = projection.rolling_accuracy && typeof projection.rolling_accuracy === "object" ? projection.rolling_accuracy : {};
    const rate = finite(accuracy.cone_hit_rate);
    const lowConfidence = projection.low_confidence === true || rate == null || rate < 0.60;
    const accuracyBadge = typeof projection.accuracy_badge === "string" && projection.accuracy_badge
      ? projection.accuracy_badge
      : `${lowConfidence ? "LOW CONFIDENCE · " : ""}${Math.round((rate || 0) * 100)}%`;
    const context = model.current_context && typeof model.current_context === "object" ? model.current_context : {};
    const squares = model.squares && typeof model.squares === "object" ? Object.values(model.squares) : [];
    const inContext = squares.filter((row) => row && row.vol_bucket === context.vol_bucket && row.hour_bucket === context.hour_bucket && finite(row.n) >= 20 && ["up", "down"].includes(String(row.side).toLowerCase()) && finite(row.distance) != null && finite(row.ev_lower_bound) != null);
    const projectSquare = (row) => ({ side: row.side, distance: finite(row.distance), ev_lower_bound: finite(row.ev_lower_bound), n: Math.floor(finite(row.n)) });
    const requested = opts.requested === true;
    const hotSquares = requested ? inContext.filter((row) => row.hot === true && row.status === "HOT" && finite(row.ev_lower_bound) > 0).map(projectSquare) : [];
    const coldSquares = requested ? inContext.filter((row) => row.status === "COLD" && finite(row.ev_lower_bound) <= 0).map(projectSquare) : [];
    const points = Array.isArray(projection.points) ? projection.points.filter((point) => point && [1, 2, 3].includes(Number(point.forward)) && Array.isArray(point.cell_range_68pct) && point.cell_range_68pct.length === 2 && finite(point.expected_cell_y) != null) : [];
    return {
      accuracy_badge: accuracyBadge,
      low_confidence: lowConfidence,
      cone_enabled: opts.requested === true && !lowConfidence && points.length > 0,
      cone_points: points,
      hot_squares: hotSquares,
      cold_squares: coldSquares,
    };
  }

  function buildVisualGeometry(options) {
    const opts = options || {};
    const plan = opts.plan && typeof opts.plan === "object" ? opts.plan : {};
    const currentX = opts.currentCellX == null ? null : finite(opts.currentCellX);
    const currentY = opts.currentCellY == null ? null : finite(opts.currentCellY);
    if (currentX == null || currentY == null || typeof plan.accuracy_badge !== "string" || !plan.accuracy_badge) return null;
    const toLearnedCell = (row) => {
      const distance = finite(row && row.distance);
      const side = String(row && row.side || "").toLowerCase();
      if (distance == null || distance < 0 || !["up", "down"].includes(side)) return null;
      return {
        cell_x: Math.floor(currentX) + 1,
        cell_y: Math.floor(currentY + (side === "up" ? distance : -distance)),
        ev_lower_bound: finite(row.ev_lower_bound),
        n: Math.floor(finite(row.n) || 0),
      };
    };
    const cone = plan.cone_enabled === true && Array.isArray(plan.cone_points)
      ? plan.cone_points.map((point) => {
          const forward = finite(point && point.forward);
          const expected = finite(point && point.expected_cell_y);
          const range = point && point.cell_range_68pct;
          const low = Array.isArray(range) ? finite(range[0]) : null;
          const high = Array.isArray(range) ? finite(range[1]) : null;
          if (![1, 2, 3].includes(forward) || expected == null || low == null || high == null || low > expected || high < expected) return null;
          return { cell_x: Math.floor(currentX) + forward, expected_y: expected, low_y: low, high_y: high };
        }).filter(Boolean)
      : [];
    return {
      accuracy_badge: plan.accuracy_badge,
      low_confidence: plan.low_confidence === true,
      cone,
      hot_cells: (Array.isArray(plan.hot_squares) ? plan.hot_squares : []).map(toLearnedCell).filter(Boolean),
      cold_cells: (Array.isArray(plan.cold_squares) ? plan.cold_squares : []).map(toLearnedCell).filter(Boolean),
    };
  }

  return { formatPlayerAdvice, buildVisualPlan, buildVisualGeometry };
});

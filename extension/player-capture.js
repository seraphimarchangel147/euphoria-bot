/* Pure capture helpers for manual-player learning.
 * Capture and advice only: this module never dispatches pointer/click/trade events.
 */
(function attachPlayerCapture(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.__euphoriaPlayerCapture = api;
})(typeof window !== "undefined" ? window : globalThis, function playerCaptureFactory() {
  "use strict";

  function finite(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function cellPair(value) {
    if (!value || typeof value !== "object") return null;
    const x = finite(value.x ?? value.cell_x ?? value.cellX ?? value.timeIndex);
    const y = finite(value.y ?? value.cell_y ?? value.cellY ?? value.priceIndex);
    return x == null || y == null ? null : { x: Math.floor(x), y: Math.floor(y) };
  }

  function resolveTap(options) {
    const opts = options || {};
    const ts = finite(opts.ts) ?? Date.now();
    const context = opts.context && typeof opts.context === "object" ? opts.context : {};
    const rect = opts.rect || {};
    const canvas = opts.canvasSize || {};
    const width = finite(rect.width);
    const height = finite(rect.height);
    let resolved = null;
    if (width && height && typeof opts.screenToCell === "function") {
      const sx = (finite(canvas.width) || width) / width;
      const sy = (finite(canvas.height) || height) / height;
      const x = ((finite(opts.clientX) ?? 0) - (finite(rect.left) ?? 0)) * sx;
      const y = ((finite(opts.clientY) ?? 0) - (finite(rect.top) ?? 0)) * sy;
      try { resolved = cellPair(opts.screenToCell(x, y)); } catch { resolved = null; }
    }
    let quoted = resolved && Array.isArray(context.cells)
      ? context.cells.find((row) => Number(row.cell_x) === resolved.x && Number(row.cell_y) === resolved.y)
      : null;
    if (!quoted && resolved && context.authoritative === true && typeof opts.quoteCell === "function") {
      const bounds = context.boundaries && typeof context.boundaries === "object" ? context.boundaries : {};
      const minX = finite(bounds.startTime), maxX = finite(bounds.endTime);
      const minY = finite(bounds.startPrice), maxY = finite(bounds.endPrice);
      const withinBounds = (minX == null || resolved.x >= minX) && (maxX == null || resolved.x <= maxX)
        && (minY == null || resolved.y >= minY) && (maxY == null || resolved.y <= maxY);
      let multiplier = null;
      if (withinBounds) {
        try { multiplier = finite(opts.quoteCell(resolved.x, resolved.y)); } catch { multiplier = null; }
      }
      const currentY = finite(context.current_cell_y);
      const distance = currentY == null ? null : Math.abs(resolved.y - currentY);
      if (multiplier != null && multiplier > 0 && distance != null) {
        quoted = {
          side: resolved.y > currentY ? "up" : resolved.y < currentY ? "down" : "at-price",
          distance,
          multiplier,
          break_even_probability: Number((1 / multiplier).toFixed(6)),
        };
      }
    }
    const cell = quoted ? resolved : null;
    return {
      event_type: "player-tap",
      ts,
      tap_id: null,
      cell,
      cell_x: cell ? cell.x : null,
      cell_y: cell ? cell.y : null,
      side: quoted ? quoted.side : null,
      distance: quoted ? finite(quoted.distance) : null,
      multiplier: quoted ? finite(quoted.multiplier) : null,
      quoted_breakeven: quoted ? finite(quoted.break_even_probability) : null,
      price: finite(context.price),
      volatility_snapshot: context.history || null,
      quoted_grid_ref_time: finite(context.quoted_grid_ref_time),
      pointer: { client_x: finite(opts.clientX), client_y: finite(opts.clientY) },
      capture_reason: quoted ? null : "pointer did not resolve to an authoritative quoted cell",
    };
  }

  function deepCandidates(value, out, depth) {
    if (depth > 8 || value == null) return;
    if (typeof value === "string") {
      const text = value.trim();
      if (text && (text[0] === "{" || text[0] === "[")) {
        try { deepCandidates(JSON.parse(text), out, depth + 1); } catch { /* mixed frame */ }
      }
      return;
    }
    if (Array.isArray(value)) {
      for (const item of value) deepCandidates(item, out, depth + 1);
      return;
    }
    if (typeof value !== "object") return;
    out.push(value);
    for (const nested of Object.values(value)) deepCandidates(nested, out, depth + 1);
  }

  function extractSettlement(raw, ts, source) {
    const sourceUrl = String(source && source.url || "");
    const trustedSource = source && ["fetch", "xhr", "websocket"].includes(source.kind)
      && /^(?:https?|wss?):\/\/[^/]*\.?euphoria\.finance(?::\d+)?\//i.test(sourceUrl)
      && /\/(?:executeTrade|trade\/settlement|settlements)(?:[/?#]|$)/i.test(sourceUrl);
    if (!trustedSource) return null;
    let value = raw;
    if (typeof raw === "string") {
      try { value = JSON.parse(raw); } catch { return null; }
    }
    const candidates = [];
    deepCandidates(value, candidates, 0);
    for (const row of candidates) {
      const marker = String(row.type ?? row.event ?? row.status ?? row.kind ?? "").toLowerCase();
      const winRaw = row.win ?? row.won ?? row.isWin;
      const payout = finite(row.payout ?? row.payoutAmount ?? row.returnAmount ?? row.profit);
      const stake = finite(row.stake ?? row.stakeAmount ?? row.amount ?? row.betAmount);
      const terminal = /settle|result|resolved|won|lost|win|loss/.test(marker);
      if (!terminal) continue;
      let win = null;
      if (typeof winRaw === "boolean") win = winRaw;
      else if (/lost|loss/.test(marker)) win = false;
      else if (/won|win/.test(marker)) win = true;
      if (win == null || payout == null || stake == null || stake <= 0 || payout < 0) continue;
      const cell = cellPair(row.cell || row);
      return {
        event_type: "player-result",
        ts: finite(ts) ?? Date.now(),
        win,
        payout,
        stake,
        cell,
        cell_x: cell ? cell.x : null,
        cell_y: cell ? cell.y : null,
      };
    }
    return null;
  }

  class PlayerEventJoiner {
    constructor(options) {
      const opts = options || {};
      this.now = typeof opts.now === "function" ? opts.now : () => Date.now();
      this.emit = typeof opts.emit === "function" ? opts.emit : () => {};
      this.maxDelayMs = finite(opts.maxDelayMs) || 10_000;
      this.pending = [];
      this.sequence = 0;
    }

    prune(now) {
      this.pending = this.pending.filter((tap) => now - tap.ts <= this.maxDelayMs);
    }

    addTap(rawTap) {
      const now = finite(rawTap && rawTap.ts) ?? this.now();
      this.prune(now);
      const tap = { ...(rawTap || {}), event_type: "player-tap", ts: now };
      tap.tap_id = tap.tap_id || `tap-${Math.floor(now)}-${++this.sequence}`;
      this.pending.push(tap);
      this.emit(tap);
      return tap;
    }

    addResult(rawResult) {
      const now = finite(rawResult && rawResult.ts) ?? this.now();
      this.prune(now);
      const resultCell = cellPair(rawResult && (rawResult.cell || rawResult));
      let index = -1;
      if (resultCell) {
        const matches = this.pending
          .map((tap, candidateIndex) => ({ tap, candidateIndex }))
          .filter(({ tap }) => tap.cell && tap.cell.x === resultCell.x && tap.cell.y === resultCell.y);
        if (matches.length === 1) index = matches[0].candidateIndex;
      }
      const tap = index >= 0 ? this.pending.splice(index, 1)[0] : null;
      const joined = {
        ...(rawResult || {}),
        event_type: "player-settle",
        ts: now,
        tap_id: tap ? tap.tap_id : null,
        join_status: tap ? "joined" : "unmatched",
        join_delay_ms: tap ? now - tap.ts : null,
        cell: tap ? tap.cell : resultCell,
        cell_x: tap && tap.cell ? tap.cell.x : resultCell ? resultCell.x : null,
        cell_y: tap && tap.cell ? tap.cell.y : resultCell ? resultCell.y : null,
      };
      if (tap) {
        for (const key of ["side", "distance", "multiplier", "quoted_breakeven", "price", "volatility_snapshot", "quoted_grid_ref_time"]) {
          joined[key] = tap[key] ?? null;
        }
        joined.tap_ts = tap.ts;
      }
      this.emit(joined);
      return joined;
    }
  }

  function validatePlayerEvent(value) {
    if (!value || typeof value !== "object") return null;
    const tapId = typeof value.tap_id === "string" && value.tap_id.length >= 3 && value.tap_id.length <= 96 ? value.tap_id : null;
    const ts = finite(value.ts);
    const cell = value.cell == null ? null : cellPair(value.cell);
    if (!tapId || ts == null || ts <= 0 || (value.cell != null && !cell)) return null;
    if (value.event_type === "player-tap") {
      // A tap read from the outgoing order needs no pointer: the order is
      // stronger evidence than a click near a canvas. The pointer check exists
      // to prove a human acted, and an order leaving the page proves it better.
      const fromOrder = value.capture_source === "order";
      const pointer = value.pointer;
      if (!fromOrder && (!pointer || finite(pointer.client_x) == null || finite(pointer.client_y) == null)) return null;
      if (fromOrder && !cell) return null;
      if (cell && (finite(value.multiplier) == null || finite(value.multiplier) <= 0 || !["up", "down", "at-price"].includes(value.side))) return null;
      return { ...value, ts, tap_id: tapId, cell };
    }
    if (value.event_type === "player-settle") {
      const delay = finite(value.join_delay_ms);
      const stake = finite(value.stake);
      const payout = finite(value.payout);
      if (value.join_status !== "joined" || delay == null || delay < 0 || delay > 10_000) return null;
      if (typeof value.win !== "boolean" || stake == null || stake <= 0 || payout == null || payout < 0) return null;
      return { ...value, ts, tap_id: tapId, cell, join_delay_ms: delay, stake, payout };
    }
    return null;
  }

  return { resolveTap, extractSettlement, PlayerEventJoiner, validatePlayerEvent };
});

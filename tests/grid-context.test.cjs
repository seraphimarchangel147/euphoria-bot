const test = require('node:test');
const assert = require('node:assert/strict');
const { buildGridContext, buildProjection } = require('../extension/grid-context.js');

test('captures authoritative multipliers for three forward columns', () => {
  const calls = [];
  const chart = {
    quotesFeed: {
      isStale: false,
      lastGridTimestamp: 1700000000123,
      getMultipliersBoundries: () => ({ startTime: 340000000, endTime: 340000010, startPrice: 5990, endPrice: 6010 }),
      getMultiplierForCell: (x, y) => {
        calls.push([x, y]);
        return 2 + (x - 340000000) * 0.1 + (y - 6000) * 0.01;
      },
    },
  };
  const grid = buildGridContext({
    chart,
    nowMs: 1700000000000,
    price: 3000.25,
    squareDuration: 5000,
    dollarsPerLine: 0.5,
    history: [
      { ts: 1699999995000, price: 2999.0 },
      { ts: 1700000000000, price: 3000.25 },
    ],
  });
  assert.equal(grid.authoritative, true);
  assert.equal(grid.multiplier_source, 'quotesFeed');
  assert.equal(grid.quoted_grid_ref_time, 1700000000123);
  assert.equal(grid.cells.length, 15);
  assert.deepEqual([...new Set(grid.cells.map((c) => c.forward))], [1, 2, 3]);
  assert.ok(grid.cells.every((c) => c.multiplier > 1 && c.break_even_probability > 0));
  assert.equal(grid.history.sample_count, 2);
  assert.ok(grid.history.change_bps > 0);
  assert.equal(calls.length, 15);
});

test('fails closed when quote grid is stale', () => {
  const chart = {
    quotesFeed: {
      isStale: true,
      lastGridTimestamp: 1700000000123,
      getMultiplierForCell: () => 9.9,
    },
  };
  const grid = buildGridContext({
    chart,
    nowMs: 1700000000000,
    price: 3000,
    squareDuration: 5000,
    dollarsPerLine: 0.5,
    history: [],
  });
  assert.equal(grid.authoritative, false);
  assert.equal(grid.cells.length, 0);
  assert.equal(grid.reason, 'quote grid stale');
});

test('rejects malformed multipliers and bounds history payload', () => {
  let n = 0;
  const chart = {
    quotesFeed: {
      isStale: false,
      lastGridTimestamp: 1700000000123,
      getMultiplierForCell: () => [null, NaN, -1, 0, 2.5][n++ % 5],
    },
  };
  const history = Array.from({ length: 500 }, (_, i) => ({ ts: 1699999500000 + i * 1000, price: 2900 + i * 0.1 }));
  const grid = buildGridContext({
    chart,
    nowMs: 1700000000000,
    price: 3000,
    squareDuration: 5000,
    dollarsPerLine: 0.5,
    history,
  });
  assert.ok(grid.cells.length > 0);
  assert.ok(grid.cells.every((c) => c.multiplier === 2.5));
  assert.ok(grid.history.samples.length <= 120);
});

test('builds an honest 5/10/15 second probability cone from bounded ticks', () => {
  const now = 1_700_000_000_000;
  const history = Array.from({ length: 25 }, (_, i) => ({ ts: now - (24 - i) * 1000, price: 3000 + i * 0.02 }));
  const projection = buildProjection({
    nowMs: now,
    price: 3000.48,
    dollarsPerLine: 0.5,
    squareDuration: 5000,
    history,
    think: { timeframes: { '1m': { lean: 'up' }, '5m': { lean: 'up' } } },
    projector: { coefficients: { drift_weight: 1, lean_cells: 0.25, width_scale: 1 }, rolling_accuracy: { n: 80, cone_hit_rate: 0.75 } },
  });
  assert.equal(projection.points.length, 3);
  assert.deepEqual(projection.points.map((p) => p.horizon_s), [5, 10, 15]);
  assert.deepEqual(projection.points.map((p) => p.forward), [1, 2, 3]);
  assert.ok(projection.points[2].expected_cell_y > projection.current_cell_y);
  assert.ok(projection.points.every((p) => p.cell_range_68pct[0] <= p.expected_cell_y && p.cell_range_68pct[1] >= p.expected_cell_y));
  assert.ok(projection.points.every((p) => Math.abs((p.p_up + p.p_down) - 1) < 1e-6));
  assert.equal(projection.accuracy_badge, '75%');
  assert.equal(projection.low_confidence, false);
});

test('projection is low confidence below sixty percent and unavailable without ticks', () => {
  const low = buildProjection({ nowMs: 1000, price: 3000, dollarsPerLine: 0.5, history: [{ ts: 1000, price: 3000 }], projector: { rolling_accuracy: { n: 100, cone_hit_rate: 0.59 } } });
  assert.equal(low.low_confidence, true);
  assert.equal(low.accuracy_badge, 'LOW CONFIDENCE · 59%');
  assert.equal(low.points.length, 0);
});

test('projection stays low confidence with fewer than twenty graded samples', () => {
  const now = 1_700_000_000_000;
  const history = Array.from({ length: 25 }, (_, i) => ({ ts: now - (24 - i) * 1000, price: 3000 + i * 0.02 }));
  const projection = buildProjection({ nowMs: now, price: 3000.48, dollarsPerLine: 0.5, history, projector: { rolling_accuracy: { n: 1, cone_hit_rate: 1 } } });
  assert.equal(projection.low_confidence, true);
  assert.equal(projection.enabled_default, false);
  assert.equal(projection.accuracy_badge, 'LOW CONFIDENCE · 100% · n=1/20');
});

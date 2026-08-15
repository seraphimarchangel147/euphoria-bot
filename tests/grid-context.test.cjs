const test = require('node:test');
const assert = require('node:assert/strict');
const { buildGridContext } = require('../extension/grid-context.js');

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

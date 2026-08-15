const test = require('node:test');
const assert = require('node:assert/strict');
const {
  resolveTap,
  PlayerEventJoiner,
  extractSettlement,
  validatePlayerEvent,
} = require('../extension/player-capture.js');

const NOW = 1_700_000_000_000;

function fixtureContext() {
  return {
    authoritative: true,
    current_cell_x: 339999999,
    current_cell_y: 6000,
    now_ms: NOW,
    price: 3000.25,
    dollars_per_line: 0.5,
    history: { sample_count: 3, samples: [
      { ts: NOW - 2000, price: 3000.0 },
      { ts: NOW - 1000, price: 3000.5 },
      { ts: NOW, price: 3000.25 },
    ] },
    cells: [
      { cell_x: 340000000, cell_y: 6001, side: 'up', distance: 1, multiplier: 2.5, break_even_probability: 0.4 },
    ],
  };
}

test('resolves an actual pointer to an authoritative quoted cell', () => {
  const tap = resolveTap({
    ts: NOW,
    clientX: 125,
    clientY: 75,
    rect: { left: 100, top: 50, width: 200, height: 200 },
    canvasSize: { width: 400, height: 400 },
    context: fixtureContext(),
    screenToCell: (x, y) => ({ x: x === 50 ? 340000000 : -1, y: y === 50 ? 6001 : -1 }),
  });
  assert.equal(tap.event_type, 'player-tap');
  assert.deepEqual(tap.cell, { x: 340000000, y: 6001 });
  assert.equal(tap.side, 'up');
  assert.equal(tap.distance, 1);
  assert.equal(tap.multiplier, 2.5);
  assert.equal(tap.quoted_breakeven, 0.4);
  assert.equal(tap.price, 3000.25);
  assert.equal(tap.volatility_snapshot.sample_count, 3);
});

test('fails closed for pointer outside an authoritative quoted cell', () => {
  const tap = resolveTap({
    ts: NOW,
    clientX: 125,
    clientY: 75,
    rect: { left: 100, top: 50, width: 200, height: 200 },
    canvasSize: { width: 400, height: 400 },
    context: fixtureContext(),
    screenToCell: () => ({ x: 1, y: 2 }),
  });
  assert.equal(tap.cell, null);
  assert.equal(tap.multiplier, null);
  assert.match(tap.capture_reason, /quoted cell/);
});

test('resolves a trusted quoted tap beyond the bounded display snapshot without guessing', () => {
  const context = fixtureContext();
  context.cells = [];
  context.boundaries = { startTime: 340000000, endTime: 340000003, startPrice: 5990, endPrice: 6010 };
  const tap = resolveTap({
    ts: NOW,
    clientX: 125,
    clientY: 75,
    rect: { left: 100, top: 50, width: 200, height: 200 },
    canvasSize: { width: 400, height: 400 },
    context,
    screenToCell: () => ({ x: 340000000, y: 6004 }),
    quoteCell: (x, y) => x === 340000000 && y === 6004 ? 3.2 : null,
  });
  assert.deepEqual(tap.cell, { x: 340000000, y: 6004 });
  assert.equal(tap.side, 'up');
  assert.equal(tap.distance, 4);
  assert.equal(tap.multiplier, 3.2);
  assert.equal(tap.quoted_breakeven, 0.3125);
});

test('direct quote fallback rejects cells outside authoritative grid boundaries', () => {
  const context = fixtureContext();
  context.cells = [];
  context.boundaries = { startTime: 340000000, endTime: 340000003, startPrice: 5990, endPrice: 6002 };
  const tap = resolveTap({
    ts: NOW, clientX: 125, clientY: 75,
    rect: { left: 100, top: 50, width: 200, height: 200 }, canvasSize: { width: 400, height: 400 },
    context, screenToCell: () => ({ x: 340000000, y: 6004 }), quoteCell: () => 9,
  });
  assert.equal(tap.cell, null);
  assert.equal(tap.multiplier, null);
});

test('fixture joins tap to settle within ten seconds and preserves tap provenance', () => {
  let now = NOW;
  const emitted = [];
  const joiner = new PlayerEventJoiner({ now: () => now, emit: (row) => emitted.push(row), maxDelayMs: 10_000 });
  const tap = joiner.addTap(resolveTap({
    ts: now,
    clientX: 125,
    clientY: 75,
    rect: { left: 100, top: 50, width: 200, height: 200 },
    canvasSize: { width: 400, height: 400 },
    context: fixtureContext(),
    screenToCell: () => ({ x: 340000000, y: 6001 }),
  }));
  now += 4_200;
  const settlement = extractSettlement(
    JSON.stringify({ result: { type: 'settle', cellX: 340000000, cellY: 6001, won: true, payout: '2.50', stake: '1.00' } }),
    now,
    { kind: 'fetch', url: 'https://api.mainnet.euphoria.finance/executeTrade' },
  );
  const joined = joiner.addResult(settlement);
  assert.equal(joined.event_type, 'player-settle');
  assert.equal(joined.tap_id, tap.tap_id);
  assert.equal(joined.join_status, 'joined');
  assert.equal(joined.join_delay_ms, 4200);
  assert.equal(joined.win, true);
  assert.equal(joined.payout, 2.5);
  assert.equal(joined.stake, 1);
  assert.deepEqual(joined.cell, tap.cell);
  assert.equal(emitted.length, 2);
});

test('does not guess a stale tap/result join', () => {
  let now = NOW;
  const joiner = new PlayerEventJoiner({ now: () => now, emit: () => {}, maxDelayMs: 10_000 });
  joiner.addTap({ event_type: 'player-tap', ts: now, cell: { x: 1, y: 2 } });
  now += 10_001;
  const joined = joiner.addResult({ event_type: 'player-result', ts: now, win: false, payout: 0, stake: 1, cell: null });
  assert.equal(joined.join_status, 'unmatched');
  assert.equal(joined.tap_id, null);
});

test('does not treat generic RPC success as a winning settlement', () => {
  assert.equal(extractSettlement({ success: true, payout: 2, stake: 1 }, NOW), null);
  assert.equal(extractSettlement({ type: 'executeTrade', success: true, payout: 2, stake: 1 }, NOW), null);
});

test('rejects result-shaped payloads unless they come from an exact trade settlement source', () => {
  const payload = { type: 'settled', win: true, payout: 2, stake: 1, cell: { x: 10, y: 20 } };
  assert.equal(extractSettlement(payload, NOW), null);
  assert.equal(extractSettlement(payload, NOW, { kind: 'fetch', url: 'https://api.mainnet.euphoria.finance/profile/result' }), null);
  assert.equal(extractSettlement(payload, NOW, { kind: 'fetch', url: 'https://attacker.example/executeTrade' }), null);
  const accepted = extractSettlement(payload, NOW, { kind: 'fetch', url: 'https://api.mainnet.euphoria.finance/executeTrade' });
  assert.equal(accepted.win, true);
  assert.deepEqual(accepted.cell, { x: 10, y: 20 });
});

test('does not guess between two recent taps when a result has no cell', () => {
  const joiner = new PlayerEventJoiner({ now: () => NOW, emit: () => {}, maxDelayMs: 10_000 });
  joiner.addTap({ event_type: 'player-tap', ts: NOW - 100, cell: { x: 1, y: 1 } });
  joiner.addTap({ event_type: 'player-tap', ts: NOW - 50, cell: { x: 2, y: 2 } });
  const joined = joiner.addResult({ event_type: 'player-result', ts: NOW, win: true, payout: 2, stake: 1, cell: null });
  assert.equal(joined.join_status, 'unmatched');
  assert.equal(joined.tap_id, null);
});

test('does not guess a tap-result join when the result has no cell', () => {
  const joiner = new PlayerEventJoiner({ now: () => NOW, emit: () => {}, maxDelayMs: 10_000 });
  joiner.addTap({ event_type: 'player-tap', ts: NOW - 100, cell: { x: 1, y: 1 } });
  const joined = joiner.addResult({ event_type: 'player-result', ts: NOW, win: true, payout: 2, stake: 1, cell: null });
  assert.equal(joined.join_status, 'unmatched');
  assert.equal(joined.tap_id, null);
});

test('does not guess between repeated taps on the same cell', () => {
  const joiner = new PlayerEventJoiner({ now: () => NOW, emit: () => {}, maxDelayMs: 10_000 });
  joiner.addTap({ event_type: 'player-tap', ts: NOW - 100, cell: { x: 1, y: 1 } });
  joiner.addTap({ event_type: 'player-tap', ts: NOW - 50, cell: { x: 1, y: 1 } });
  const joined = joiner.addResult({ event_type: 'player-result', ts: NOW, win: true, payout: 2, stake: 1, cell: { x: 1, y: 1 } });
  assert.equal(joined.join_status, 'unmatched');
  assert.equal(joined.tap_id, null);
});

test('strict event validation rejects malformed or over-age joins', () => {
  assert.equal(validatePlayerEvent({ event_type: 'player-settle' }), null);
  assert.equal(validatePlayerEvent({ event_type: 'player-tap', tap_id: 'x', ts: NOW, cell: null }), null);
  const tap = resolveTap({
    ts: NOW, clientX: 125, clientY: 75,
    rect: { left: 100, top: 50, width: 200, height: 200 }, canvasSize: { width: 400, height: 400 },
    context: fixtureContext(), screenToCell: () => ({ x: 340000000, y: 6001 }),
  });
  tap.tap_id = 'tap-valid';
  tap.pointer = { client_x: 125, client_y: 75 };
  assert.equal(validatePlayerEvent(tap).tap_id, 'tap-valid');
  assert.equal(validatePlayerEvent({ ...tap, event_type: 'player-settle', tap_ts: NOW, ts: NOW + 10_001, join_status: 'joined', join_delay_ms: 10_001, win: true, payout: 2, stake: 1 }), null);
});

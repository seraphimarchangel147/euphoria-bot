const test = require('node:test');
const assert = require('node:assert/strict');
const { formatPlayerAdvice, buildVisualPlan, buildVisualGeometry } = require('../extension/player-advice.js');

test('formats current-context hot squares as advice', () => {
  const advice = formatPlayerAdvice({
    current_context: { vol_bucket: 'LOW', hour_bucket: '00-05Z' },
    hot_squares_current: [{ side: 'up', distance: 1, n: 41, win_rate: 0.62, ev: 0.09, ev_lower_bound: 0.01, hot: true, status: 'HOT' }],
    tilt: { flagged: false, session_length: 41 },
  });
  assert.equal(advice.edge, 'LOW-vol 00-05Z: +1 UP 62% n=41 EV+0.09');
  assert.equal(advice.tilt, null);
});

test('labels sparse contexts insufficient and shows tilt advice banner', () => {
  const advice = formatPlayerAdvice({
    current_context: { vol_bucket: 'MED', hour_bucket: '18-23Z' },
    hot_squares_current: [],
    settled_taps: 12,
    tilt: { flagged: true, baseline_win_rate: 0.7, rolling_10_win_rate: 0.4, drop_points: 30, session_length: 22 },
  });
  assert.match(advice.edge, /INSUFFICIENT/);
  assert.match(advice.tilt, /TILT WARNING/);
  assert.match(advice.tilt, /70% → 40%/);
});

test('malformed model fails closed without claiming an edge', () => {
  const advice = formatPlayerAdvice({ hot_squares_current: [{ n: 999, ev: 'boom' }] });
  assert.match(advice.edge, /no proven player edge/i);
  assert.equal(advice.tilt, null);
});

test('does not display an unproven or negative lower-bound row as hot', () => {
  const advice = formatPlayerAdvice({
    current_context: { vol_bucket: 'LOW', hour_bucket: '00-05Z' },
    hot_squares_current: [{ side: 'up', distance: 1, n: 20, win_rate: 0.6, ev: 0.2, ev_lower_bound: -0.1, hot: false, status: 'COLD' }],
    settled_taps: 20,
  });
  assert.equal(advice.edge, 'no proven player edge');
  assert.equal(advice.tilt, null);
});

test('visual plan gates cone, labels accuracy, and selects only proven current-context squares', () => {
  const model = {
    current_context: { vol_bucket: 'LOW', hour_bucket: '00-05Z' },
    squares: {
      hot: { side: 'up', distance: 1, vol_bucket: 'LOW', hour_bucket: '00-05Z', n: 25, ev_lower_bound: 0.08, hot: true, status: 'HOT' },
      cold: { side: 'down', distance: 1, vol_bucket: 'LOW', hour_bucket: '00-05Z', n: 30, ev_lower_bound: -0.12, hot: false, status: 'COLD' },
      sparse: { side: 'up', distance: 2, vol_bucket: 'LOW', hour_bucket: '00-05Z', n: 19, ev_lower_bound: 0.5, hot: false, status: 'INSUFFICIENT' },
      wrongContext: { side: 'up', distance: 3, vol_bucket: 'HIGH', hour_bucket: '00-05Z', n: 99, ev_lower_bound: 0.5, hot: true, status: 'HOT' },
    },
  };
  const projection = { low_confidence: false, accuracy_badge: '75%', rolling_accuracy: { n: 80, cone_hit_rate: 0.75 }, points: [{ forward: 1, expected_cell_y: 6001, cell_range_68pct: [6000, 6002] }] };
  const plan = buildVisualPlan({ model, projection, requested: true });
  assert.equal(plan.cone_enabled, true);
  assert.equal(plan.accuracy_badge, '75%');
  assert.deepEqual(plan.hot_squares, [{ side: 'up', distance: 1, ev_lower_bound: 0.08, n: 25 }]);
  assert.deepEqual(plan.cold_squares, [{ side: 'down', distance: 1, ev_lower_bound: -0.12, n: 30 }]);
});

test('visual plan always has badge and forces cone off at low confidence', () => {
  const plan = buildVisualPlan({ model: {}, projection: { low_confidence: true, accuracy_badge: 'LOW CONFIDENCE · 59%', points: [{ forward: 1 }] }, requested: true });
  assert.equal(plan.cone_enabled, false);
  assert.equal(plan.accuracy_badge, 'LOW CONFIDENCE · 59%');
  assert.equal(plan.low_confidence, true);
});

test('visual toggle off suppresses cone and square layers while retaining its badge', () => {
  const model = {
    current_context: { vol_bucket: 'LOW', hour_bucket: '00-05Z' },
    squares: { hot: { side: 'up', distance: 1, vol_bucket: 'LOW', hour_bucket: '00-05Z', n: 25, ev_lower_bound: 0.08, hot: true, status: 'HOT' } },
  };
  const projection = { low_confidence: false, accuracy_badge: '75%', rolling_accuracy: { n: 80, cone_hit_rate: 0.75 }, points: [{ forward: 1, expected_cell_y: 6001, cell_range_68pct: [6000, 6002] }] };
  const plan = buildVisualPlan({ model, projection, requested: false });
  assert.equal(plan.accuracy_badge, '75%');
  assert.equal(plan.cone_enabled, false);
  assert.deepEqual(plan.hot_squares, []);
  assert.deepEqual(plan.cold_squares, []);
});

test('visual geometry maps cone and learned squares onto exact forward grid cells', () => {
  const geometry = buildVisualGeometry({
    currentCellX: 340000000,
    currentCellY: 6000,
    plan: {
      accuracy_badge: '75%',
      low_confidence: false,
      cone_enabled: true,
      cone_points: [
        { forward: 1, expected_cell_y: 6001.25, cell_range_68pct: [6000, 6002] },
        { forward: 2, expected_cell_y: 6002, cell_range_68pct: [6001, 6003] },
        { forward: 3, expected_cell_y: 6002.5, cell_range_68pct: [6001, 6004] },
      ],
      hot_squares: [{ side: 'up', distance: 1, ev_lower_bound: 0.08, n: 25 }],
      cold_squares: [{ side: 'down', distance: 2, ev_lower_bound: -0.12, n: 30 }],
    },
  });
  assert.equal(geometry.accuracy_badge, '75%');
  assert.deepEqual(geometry.cone.map((row) => [row.cell_x, row.low_y, row.high_y]), [
    [340000001, 6000, 6002],
    [340000002, 6001, 6003],
    [340000003, 6001, 6004],
  ]);
  assert.deepEqual(geometry.hot_cells, [{ cell_x: 340000001, cell_y: 6001, ev_lower_bound: 0.08, n: 25 }]);
  assert.deepEqual(geometry.cold_cells, [{ cell_x: 340000001, cell_y: 5998, ev_lower_bound: -0.12, n: 30 }]);
});

test('visual geometry fails closed when accuracy badge or grid anchor is missing', () => {
  assert.deepEqual(buildVisualGeometry({ currentCellX: 1, currentCellY: 2, plan: { cone_enabled: true } }), null);
  assert.deepEqual(buildVisualGeometry({ currentCellX: null, currentCellY: 2, plan: { accuracy_badge: '70%' } }), null);
});

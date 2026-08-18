const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

const SRC = fs.readFileSync(require.resolve('../extension/bridge.js'), 'utf8');

function tick() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function runBridge(grid, nowMs = 1700000005000) {
  const posted = [];
  const token = 'a'.repeat(64);
  class FakeDate extends Date {
    constructor(...args) { super(...(args.length ? args : [nowMs])); }
    static now() { return nowMs; }
  }
  const chrome = {
    storage: { local: {
      async get(keys) {
        const all = { euphoriaBridgeToken: token, controlPort: 8765 };
        const list = Array.isArray(keys) ? keys : [keys];
        return Object.fromEntries(list.filter((key) => key in all).map((key) => [key, all[key]]));
      },
      async set() {},
    } },
    cookies: { async getAll() { return []; } },
    tabs: {
      async query() { return [{ id: 7, url: 'https://euphoria.finance/trade', title: 'Euphoria', active: true, audible: false }]; },
      async sendMessage(tabId, message) {
        assert.equal(tabId, 7);
        assert.equal(message.type, 'bridge_read_page');
        return { grid };
      },
    },
    runtime: { getManifest() { return { version: '0.4.2' }; } },
    alarms: { create() {}, onAlarm: { addListener() {} } },
  };
  async function fetch(url, options = {}) {
    if (url === 'http://127.0.0.1:8765/status') throw new Error('control loop down');
    if (url.endsWith('/euphoria/state')) {
      posted.push(JSON.parse(options.body));
      return { async json() { return { ok: true }; } };
    }
    if (url.endsWith('/euphoria/commands')) return { async json() { return { commands: [] }; } };
    throw new Error(`unexpected URL ${url}`);
  }
  const context = { chrome, crypto: webcrypto, fetch, console, Date: FakeDate, Uint8Array, setInterval() { return 1; } };
  vm.runInNewContext(SRC, context, { filename: 'bridge.js' });
  await tick();
  await tick();
  assert.equal(posted.length, 1);
  assert.equal(posted[0].control, null);
  return posted[0];
}

function quotedGrid(receivedAt) {
  return {
    authoritative: true,
    multiplier_source: 'quotesFeed',
    quoted_grid_ref_time: receivedAt,
    received_at: receivedAt,
    cells: [{ cell_x: 340000001, cell_y: 6001, side: 'up', distance: 1, multiplier: 2.5, break_even_probability: 0.4 }],
  };
}

test('bridge relays a grid at the five-second boundary while control is stopped', async () => {
  const now = 1700000005000;
  const posted = await runBridge(quotedGrid(now - 5000), now);
  assert.equal(posted.grid.authoritative, true);
  assert.equal(posted.grid.stale, false);
  assert.equal(posted.grid.cells[0].multiplier, 2.5);
});

test('bridge expires a frozen grid even when the state envelope is fresh', async () => {
  const now = 1700000005000;
  const posted = await runBridge(quotedGrid(now - 5001), now);
  assert.equal(posted.grid.authoritative, false);
  assert.equal(posted.grid.stale, true);
  assert.equal(posted.grid.cells[0].multiplier, 2.5);
});

test('bridge rejects authority when the grid has no received_at timestamp', async () => {
  const grid = quotedGrid(1700000005000);
  delete grid.received_at;
  const posted = await runBridge(grid, 1700000005000);
  assert.equal(posted.grid.authoritative, false);
  assert.equal(posted.grid.stale, true);
});

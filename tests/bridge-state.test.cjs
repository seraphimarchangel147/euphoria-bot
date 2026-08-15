const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

const SRC = fs.readFileSync(require.resolve('../extension/bridge.js'), 'utf8');

function tick() {
  return new Promise((resolve) => setImmediate(resolve));
}

test('bridge state relays authoritative quoted grid while control room is stopped', async () => {
  const grid = {
    authoritative: true,
    multiplier_source: 'quotesFeed',
    quoted_grid_ref_time: 1700000000123,
    cells: [{ cell_x: 340000001, cell_y: 6001, side: 'up', distance: 1, multiplier: 2.5, break_even_probability: 0.4 }],
  };
  const posted = [];
  const token = 'a'.repeat(64);
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
    runtime: { getManifest() { return { version: '0.4.0' }; } },
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
  const context = { chrome, crypto: webcrypto, fetch, console, Date, Uint8Array, setInterval() { return 1; } };
  vm.runInNewContext(SRC, context, { filename: 'bridge.js' });
  await tick();
  await tick();

  assert.equal(posted.length, 1);
  assert.equal(posted[0].control, null);
  assert.deepEqual(JSON.parse(JSON.stringify(posted[0].grid)), grid);
  assert.equal(posted[0].grid.cells[0].multiplier, 2.5);
});

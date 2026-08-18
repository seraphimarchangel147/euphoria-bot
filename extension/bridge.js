/* euphoria bridge — connects the Euphoria helper extension to the Legion WSL side.
 *
 * Cherubim 2026-08-14. Loaded via importScripts('bridge.js') from background.js.
 *
 * What it does (poll loop, localhost only — no external hosts):
 *   1. Every 5s while the service worker is awake (plus a 30s chrome.alarm to
 *      re-wake it), POSTs a state snapshot to the WSL bridge server:
 *        http://127.0.0.1:18901/euphoria/state
 *      Snapshot = privy cookie trio (for auto .env refresh), euphoria tab list,
 *      control-room status (if the python control room on :8765 is up).
 *   2. GETs queued commands from /euphoria/commands and answers the safe ones:
 *        {type:"read_page"}  -> asks the content script for page text/prices
 *        {type:"tabs"}       -> lists euphoria tabs
 *        {type:"cookies"}    -> re-sends the cookie trio
 *      Results POST back to /euphoria/result.
 *
 * NO trading commands are accepted over this channel by design — the bridge is
 * EYES (state out, read-only queries in). Taps stay with the human/control room.
 */

const BRIDGE_BASE = "http://127.0.0.1:18901";
const EU_COOKIES = ["privy-token", "privy-id-token", "privy-session"];
const POLL_MS = 5000;
const GRID_MAX_AGE_MS = 5000;

function gridForBridge(grid, nowMs = Date.now()) {
  if (!grid || typeof grid !== "object" || Array.isArray(grid)) return null;
  const receivedAt = Number(grid.received_at);
  const ageMs = Number.isFinite(receivedAt) ? nowMs - receivedAt : null;
  const fresh = ageMs !== null && ageMs >= 0 && ageMs <= GRID_MAX_AGE_MS;
  const authoritative = grid.authoritative === true && fresh;
  return {
    ...grid,
    authoritative,
    stale: !authoritative,
    grid_age_ms: ageMs === null ? null : Math.round(ageMs),
  };
}

function randomBridgeToken() {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
}

async function bridgeToken() {
  const stored = await chrome.storage.local.get(["euphoriaBridgeToken"]);
  if (/^[0-9a-f]{64}$/.test(stored.euphoriaBridgeToken || "")) return stored.euphoriaBridgeToken;
  const token = randomBridgeToken();
  await chrome.storage.local.set({ euphoriaBridgeToken: token });
  return token;
}

async function bridgeFetch(path, opts = {}) {
  try {
    const token = await bridgeToken();
    const r = await fetch(BRIDGE_BASE + path, {
      method: opts.method || "GET",
      headers: { "Content-Type": "application/json", "X-Euphoria-Bridge-Token": token },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    return await r.json().catch(() => ({}));
  } catch {
    return null; // WSL server down — silent, retry next tick
  }
}

async function euCookieTrio() {
  const out = {};
  for (const name of EU_COOKIES) {
    try {
      const found = await chrome.cookies.getAll({ name, domain: "euphoria.finance" });
      const alt = found.length ? found : await chrome.cookies.getAll({ name, url: "https://api.mainnet.euphoria.finance/" });
      const c = (alt || [])[0];
      if (c) out[name] = { value: c.value, domain: c.domain, expires: c.expirationDate || null };
    } catch { /* cookie api hiccup — skip */ }
  }
  return out;
}

async function euTabs() {
  try {
    const tabs = await chrome.tabs.query({ url: ["https://euphoria.finance/*", "https://www.euphoria.finance/*"] });
    return tabs.map((t) => ({ id: t.id, url: t.url, title: t.title, active: t.active, audible: !!t.audible }));
  } catch { return []; }
}

async function controlRoomStatus() {
  try {
    const s = await chrome.storage.local.get(["controlPort"]);
    const port = Number(s.controlPort) || 8765;
    const r = await fetch(`http://127.0.0.1:${port}/status`);
    return await r.json();
  } catch { return null; }
}

async function pushState() {
  const [cookies, tabs, control] = await Promise.all([euCookieTrio(), euTabs(), controlRoomStatus()]);
  // Read the quoted grid directly from the page content script. This is
  // intentionally independent of the control-room loop: manual mode and a
  // stopped/offline control room must not erase the offered multipliers needed
  // for read-only EV logging.
  const page = tabs[0] ? await readPage(tabs[0].id) : null;
  const grid = gridForBridge(page && page.grid);
  await bridgeFetch("/euphoria/state", {
    method: "POST",
    body: {
      ts: new Date().toISOString(),
      cookieNames: Object.keys(cookies),
      cookies, // localhost-only transport; WSL server stores 0600
      tabs,
      control,
      grid,
      extVersion: chrome.runtime.getManifest().version,
    },
  });
}

async function readPage(tabId) {
  try {
    const resp = await chrome.tabs.sendMessage(tabId, { type: "bridge_read_page" });
    return resp || { error: "no content-script response" };
  } catch (e) {
    return { error: String(e && e.message || e) };
  }
}

async function pollCommands() {
  const q = await bridgeFetch("/euphoria/commands");
  if (!q || !Array.isArray(q.commands) || !q.commands.length) return;
  for (const cmd of q.commands) {
    let result;
    if (cmd.type === "tabs") result = await euTabs();
    else if (cmd.type === "cookies") result = await euCookieTrio();
    else if (cmd.type === "read_page") {
      const tabs = await euTabs();
      const target = cmd.tabId || (tabs[0] && tabs[0].id);
      result = target ? await readPage(target) : { error: "no euphoria tab open" };
    } else result = { error: `unknown/forbidden command: ${cmd.type}` };
    await bridgeFetch("/euphoria/result", { method: "POST", body: { id: cmd.id, type: cmd.type, ts: new Date().toISOString(), result } });
  }
}

let bridgeTimer = null;
function startBridgeLoop() {
  if (bridgeTimer) return;
  bridgeTimer = setInterval(async () => { await pushState(); await pollCommands(); }, POLL_MS);
  pushState(); pollCommands();
}

chrome.alarms.create("euphoria-bridge-keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "euphoria-bridge-keepalive") startBridgeLoop(); });
startBridgeLoop();

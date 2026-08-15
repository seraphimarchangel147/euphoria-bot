/* bridge.js: Legion WSL eyes (state out + read-only queries). Cherubim 2026-08-14 */
try { importScripts("bridge.js", "player-capture.js"); } catch (e) { console.warn("bridge helpers not loaded:", e); }

/* Helper for a normal logged-in Euphoria tab: session cookies, page prices, overlay.
   host_permissions cover 127.0.0.1 — content scripts on https://euphoria.finance
   must not fetch the control room themselves. */
const DEFAULT_PORT = 8765;
const COOKIE_NAMES = new Set(["privy-token", "privy-id-token", "privy-session"]);

function controlBase() {
  return chrome.storage.local.get(["controlPort"]).then((s) => {
    const port = Number(s.controlPort) || DEFAULT_PORT;
    return `http://127.0.0.1:${port}`;
  });
}

async function helper(path, { method = "GET", body } = {}) {
  const base = await controlBase();
  try {
    const opts = { method, headers: { "Content-Type": "application/json" } };
    if (method !== "GET") opts.body = JSON.stringify(body || {});
    const resp = await fetch(base + path, opts);
    return { ok: true, data: await resp.json() };
  } catch {
    return { ok: false, error: "offline" };
  }
}

async function post(path, body) {
  const out = await helper(path, { method: "POST", body });
  return out.ok ? out.data : { ok: false, error: "offline" };
}

async function get(path) {
  const out = await helper(path, { method: "GET" });
  return out.ok ? out.data : null;
}

async function controlCommand(path, body) {
  /* /start /stop /mode already return status() — still re-fetch /status. */
  await helper(path, { method: "POST", body: body || {} });
  const status = await helper("/status");
  return status;
}

async function collectCookies() {
  const bags = await Promise.all([
    chrome.cookies.getAll({ domain: "euphoria.finance" }),
    chrome.cookies.getAll({ url: "https://euphoria.finance/" }),
  ]);
  const cookies = {};
  for (const list of bags) {
    for (const c of list || []) {
      if (COOKIE_NAMES.has(c.name) && c.value) cookies[c.name] = c.value;
    }
  }
  return cookies;
}

async function pushSession(extra) {
  const cookies = await collectCookies();
  const payload = { ...(extra || {}), cookies };
  if (!payload.privyUserId) {
    const stored = await chrome.storage.local.get(["privyUserId"]);
    if (stored.privyUserId) payload.privyUserId = stored.privyUserId;
  }
  return post("/session", payload);
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    if (!msg || !msg.type) return;
    if (msg.type === "control") {
      const path = msg.path || "/status";
      const method = String(msg.method || "GET").toUpperCase();
      if (method === "GET") {
        sendResponse(await helper(path));
        return;
      }
      if (path === "/start" || path === "/stop" || path === "/mode") {
        sendResponse(await controlCommand(path, msg.body));
        return;
      }
      sendResponse(await helper(path, { method, body: msg.body }));
      return;
    }
    if (msg.type === "quotes") {
      const quotes = msg.quotes || [];
      if (quotes.length) await post("/quotes", quotes);
      sendResponse({ ok: true });
      return;
    }
    if (msg.type === "session") {
      if (msg.privyUserId) {
        await chrome.storage.local.set({ privyUserId: msg.privyUserId });
      }
      const extra = {};
      if (msg.privyUserId) extra.privyUserId = msg.privyUserId;
      if (msg.artefacts) Object.assign(extra, msg.artefacts);
      if (msg.quotes) extra.quotes = msg.quotes;
      if (msg.grid) extra.grid = msg.grid;
      sendResponse(await pushSession(extra));
      return;
    }
    if (msg.type === "artefacts") {
      sendResponse(await pushSession(msg.artefacts || {}));
      return;
    }
    if (msg.type === "player-event") {
      const validator = globalThis.__euphoriaPlayerCapture && globalThis.__euphoriaPlayerCapture.validatePlayerEvent;
      const trustedSender = typeof _sender.url === "string" && /^https:\/\/(?:www\.)?euphoria\.finance\//.test(_sender.url);
      const event = trustedSender && typeof validator === "function" ? validator(msg.event) : null;
      const result = event && typeof bridgeFetch === "function"
        ? await bridgeFetch("/euphoria/player-event", { method: "POST", body: event })
        : null;
      sendResponse(result || { ok: false, error: event ? "bridge offline" : "invalid player event" });
      return;
    }
    if (msg.type === "status") {
      sendResponse(await helper("/status"));
      return;
    }
    if (msg.type === "start") {
      sendResponse(await controlCommand("/start"));
      return;
    }
    if (msg.type === "stop") {
      sendResponse(await controlCommand("/stop"));
      return;
    }
    if (msg.type === "mode") {
      sendResponse(await controlCommand("/mode", { mode: msg.mode || "manual" }));
      return;
    }
  })();
  return true;
});

chrome.runtime.onConnect.addListener((p) => {
  if (p.name !== "euphoria-sync") return;
  const iv = setInterval(() => {
    try {
      p.postMessage({ type: "ping" });
    } catch {
      /* port closed */
    }
  }, 20000);
  p.onDisconnect.addListener(() => clearInterval(iv));
});

chrome.cookies.onChanged.addListener((change) => {
  const c = change.cookie;
  if (!c || !COOKIE_NAMES.has(c.name)) return;
  if (!c.domain || !c.domain.includes("euphoria.finance")) return;
  pushSession({});
});

chrome.alarms.create("euphoria-session", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "euphoria-session") pushSession({});
});

pushSession({});

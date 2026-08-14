/* Local control-room bridge. Jobs: session cookies, price forward, overlay RPC.
 * No remote debugging, no captcha solver, no fingerprint minting.
 */
const DEFAULT_PORT = 8765;
const COOKIE_NAMES = new Set(["privy-token", "privy-id-token", "privy-session"]);

function controlBase() {
  return chrome.storage.local.get(["controlPort"]).then((s) => {
    const port = Number(s.controlPort) || DEFAULT_PORT;
    return `http://127.0.0.1:${port}`;
  });
}

async function post(path, body) {
  const base = await controlBase();
  try {
    const resp = await fetch(base + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    return await resp.json();
  } catch {
    return { ok: false, error: "offline" };
  }
}

async function get(path) {
  const base = await controlBase();
  try {
    const resp = await fetch(base + path, { method: "GET" });
    return await resp.json();
  } catch {
    return null;
  }
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
      sendResponse(await pushSession(extra));
      return;
    }
    if (msg.type === "artefacts") {
      sendResponse(await pushSession(msg.artefacts || {}));
      return;
    }
    if (msg.type === "status") {
      sendResponse({ status: await get("/status"), think: await get("/think") });
      return;
    }
    if (msg.type === "start") {
      sendResponse(await post("/start"));
      return;
    }
    if (msg.type === "stop") {
      sendResponse(await post("/stop"));
      return;
    }
  })();
  return true;
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

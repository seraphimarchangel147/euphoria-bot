/* Content script: euphoria.finance only. Injects the page hook + /trade overlay.
   All control-room I/O goes through the background service worker — a page
   fetch() from https://euphoria.finance to 127.0.0.1 is often blocked. */
const SOURCE = "__euphoria_bridge";
let quoteBuffer = [];
let flushTimer = null;
let pollTimer = null;
let port = null;
let lastGridHook = "no canvas";
let lastStatus = null;
let lastVisiblePrice = null;
let lastVisiblePriceAt = 0;
let lastGridSnapshot = null;
let trustedPointer = null;
const pendingPlayerTaps = new Map();
const CONTENT_BUILD = "0.4.0-player-capture";

function injectPageHook() {
  const parent = document.head || document.documentElement;
  const helper = document.createElement("script");
  helper.src = chrome.runtime.getURL("grid-context.js");
  helper.onload = () => {
    helper.remove();
    const capture = document.createElement("script");
    capture.src = chrome.runtime.getURL("player-capture.js");
    capture.onload = () => {
      capture.remove();
      const hook = document.createElement("script");
      hook.src = chrome.runtime.getURL("inject.js");
      hook.onload = () => hook.remove();
      parent.appendChild(hook);
    };
    parent.appendChild(capture);
  };
  parent.appendChild(helper);
}

function findPrivyUserId() {
  try {
    for (const key of Object.keys(localStorage)) {
      if (!/privy/i.test(key)) continue;
      const raw = localStorage.getItem(key);
      if (!raw) continue;
      const match = raw.match(/did:privy:[A-Za-z0-9]+/);
      if (match) return match[0];
      try {
        const parsed = JSON.parse(raw);
        const id = parsed?.user?.id || parsed?.userId || parsed?.id;
        if (typeof id === "string" && id.startsWith("did:privy:")) return id;
      } catch {
        /* not json */
      }
    }
  } catch {
    /* storage blocked */
  }
  return "";
}

function viaWorker(msg) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(msg, (resp) => {
        if (chrome.runtime.lastError) {
          resolve({ ok: false, error: "offline" });
          return;
        }
        resolve(resp == null ? { ok: false, error: "offline" } : resp);
      });
    } catch {
      resolve({ ok: false, error: "offline" });
    }
  });
}

document.addEventListener("pointerup", (ev) => {
  const path = typeof ev.composedPath === "function" ? ev.composedPath() : [];
  const canvas = path.find((node) => node instanceof HTMLCanvasElement) || (ev.target instanceof HTMLCanvasElement ? ev.target : null);
  if (!ev.isTrusted || ev.button !== 0 || !canvas) return;
  const rect = canvas.getBoundingClientRect();
  if (rect.width < 180 || rect.height < 180) return;
  trustedPointer = { ts: Date.now(), client_x: ev.clientX, client_y: ev.clientY };
}, true);

function unwrapStatus(resp) {
  if (!resp) return null;
  if (resp.error === "offline") return null;
  if (resp.data && typeof resp.data === "object" && ("running" in resp.data || "think" in resp.data || "mode" in resp.data)) {
    return resp.data;
  }
  if (resp.running != null || resp.think || resp.mode) return resp;
  if (resp.ok === false) return null;
  return null;
}

function flushQuotes() {
  if (!quoteBuffer.length) return;
  const quotes = quoteBuffer;
  quoteBuffer = [];
  viaWorker({ type: "quotes", quotes });
}

function hookLabel(hook) {
  if (hook === "hooked" || hook === "grid hooked") return "grid hooked";
  if (hook === "fallback" || hook === "grid fallback") return "grid fallback";
  return "no canvas";
}

function paintGridHook(hook) {
  lastGridHook = hookLabel(hook);
  const el = document.getElementById("ebo-grid");
  if (el) el.textContent = lastGridHook;
}

window.addEventListener("message", (ev) => {
  const data = ev.data;
  if (!data || data.source !== SOURCE) return;
  if (data.type === "quotes" && Array.isArray(data.payload)) {
    quoteBuffer.push(...data.payload);
    if (!flushTimer) {
      flushTimer = setTimeout(() => {
        flushTimer = null;
        flushQuotes();
      }, 200);
    }
  }
  if (data.type === "artefacts" && data.payload) {
    viaWorker({ type: "artefacts", artefacts: data.payload });
  }
  if (data.type === "grid" && data.payload) {
    lastGridSnapshot = data.payload;
    viaWorker({ type: "session", grid: data.payload });
  }
  if (data.type === "grid-hook" && data.payload) {
    paintGridHook(data.payload.hook || data.payload);
  }
  if (data.type === "player-event" && data.payload) {
    const validator = globalThis.__euphoriaPlayerCapture && globalThis.__euphoriaPlayerCapture.validatePlayerEvent;
    const event = typeof validator === "function" ? validator(data.payload) : null;
    if (!event) return;
    if (event.event_type === "player-tap") {
      const pointer = event.pointer || {};
      const trusted = trustedPointer && Math.abs(event.ts - trustedPointer.ts) <= 750 &&
        Math.abs(Number(pointer.client_x) - trustedPointer.client_x) <= 3 &&
        Math.abs(Number(pointer.client_y) - trustedPointer.client_y) <= 3;
      trustedPointer = null;
      if (!trusted) return;
      pendingPlayerTaps.set(event.tap_id, event);
      viaWorker({ type: "player-event", event });
      return;
    }
    const tap = pendingPlayerTaps.get(event.tap_id);
    if (!tap || event.ts < tap.ts || event.ts - tap.ts > 10000) return;
    if (tap.cell && event.cell && (tap.cell.x !== event.cell.x || tap.cell.y !== event.cell.y)) return;
    pendingPlayerTaps.delete(event.tap_id);
    viaWorker({ type: "player-event", event });
  }
});

function sendSession() {
  const privyUserId = findPrivyUserId();
  viaWorker({ type: "session", privyUserId });
}

function isTradePage() {
  return location.pathname === "/trade" || location.pathname.startsWith("/trade/");
}

function keepWorkerAwake() {
  try {
    if (port) return;
    port = chrome.runtime.connect({ name: "euphoria-sync" });
    port.onDisconnect.addListener(() => {
      port = null;
      setTimeout(keepWorkerAwake, 1000);
    });
  } catch {
    port = null;
  }
}

async function command(path, body) {
  await viaWorker({ type: "control", path, method: "POST", body: body || {} });
  const status = await viaWorker({ type: "control", path: "/status", method: "GET" });
  const snap = unwrapStatus(status);
  paintSnapshot(snap);
}

async function refreshStatus() {
  const status = await viaWorker({ type: "control", path: "/status", method: "GET" });
  paintSnapshot(unwrapStatus(status));
}

function ensureOverlay() {
  if (!isTradePage()) return;
  if (document.getElementById("euphoria-bot-overlay")) return;
  const root = document.createElement("div");
  root.id = "euphoria-bot-overlay";
  root.innerHTML = `
    <div class="ebo-head">
      <span>indicator</span>
      <span id="ebo-dry" class="ebo-pill">dry-run</span>
      <span id="ebo-run" class="ebo-pill">stopped</span>
      <span id="ebo-link" class="ebo-pill">sync</span>
    </div>
    <div id="ebo-bias" class="ebo-bias flat">flat</div>
    <div id="ebo-hint" class="ebo-reason">connecting to local helper…</div>
    <div id="ebo-tf" class="ebo-tf"></div>
    <div id="ebo-conf" class="ebo-meta">confidence —</div>
    <div id="ebo-reason" class="ebo-meta"></div>
    <div id="ebo-grade" class="ebo-grade"></div>
    <div class="ebo-actions">
      <button type="button" id="ebo-start">Start</button>
      <button type="button" id="ebo-stop">Stop</button>
      <button type="button" id="ebo-manual">Manual</button>
      <button type="button" id="ebo-auto">Auto</button>
    </div>
    <div id="ebo-grid" class="ebo-meta">${lastGridHook}</div>
    <div id="ebo-note" class="ebo-meta">tiles on the grid · you tap</div>
  `;
  document.documentElement.appendChild(root);
  document.getElementById("ebo-start").addEventListener("click", () => command("/start", {}));
  document.getElementById("ebo-stop").addEventListener("click", () => command("/stop", {}));
  document.getElementById("ebo-manual").addEventListener("click", () => command("/mode", { mode: "manual" }));
  document.getElementById("ebo-auto").addEventListener("click", () => command("/mode", { mode: "auto" }));
}

function paintSnapshot(st) {
  const root = document.getElementById("euphoria-bot-overlay");
  if (!root) return;
  paintGridHook(lastGridHook);
  if (!st || st.error === "offline" || (st.ok === false && !st.mode && st.running == null)) {
    document.getElementById("ebo-hint").textContent = "helper offline — start python -m src.ui";
    const link = document.getElementById("ebo-link");
    if (link) link.textContent = "helper offline";
    const runEl = document.getElementById("ebo-run");
    if (runEl) runEl.textContent = "stopped";
    window.postMessage({ source: SOURCE, type: "think", payload: null, quotes: {} }, "*");
    return;
  }
  lastStatus = st;
  const th = st.think || {};
  document.getElementById("ebo-dry").textContent = st.dry_run ? "dry-run" : "live";
  const runEl = document.getElementById("ebo-run");
  if (runEl) runEl.textContent = st.running ? (st.mode || "manual") : "stopped";
  const link = document.getElementById("ebo-link");
  if (link) link.textContent = "synced";
  document.getElementById("ebo-start").classList.toggle("active", !!st.running);
  document.getElementById("ebo-stop").classList.toggle("active", !st.running);
  document.getElementById("ebo-manual").classList.toggle("active", st.mode === "manual");
  document.getElementById("ebo-auto").classList.toggle("active", st.mode === "auto");
  const bias = th.bias || "flat";
  const biasEl = document.getElementById("ebo-bias");
  biasEl.textContent = bias;
  biasEl.className = "ebo-bias " + bias;
  document.getElementById("ebo-conf").textContent =
    "confidence " + Number(th.confidence || 0).toFixed(2);
  const standAside = th.stand_aside != null ? !!th.stand_aside : !th.pick;
  const hint = th.sit_reason || th.why || th.hint || (th.suggested && th.suggested.hint) || th.reason || "waiting on ticks";
  const setup = th.setup && th.setup !== "none" ? th.setup + " · " + (th.action || (standAside ? "sit" : "tap")) : "";
  document.getElementById("ebo-hint").textContent = standAside
    ? ((setup ? setup + " — " : "no trade — ") + hint)
    : ((setup ? setup + " — " : "") + hint);
  const tf = document.getElementById("ebo-tf");
  if (tf) {
    const line = th.tf_line || "";
    const lean = th.tf_lean && th.tf_lean !== "unknown" ? th.tf_lean : "";
    const align = th.alignment && th.alignment !== "unknown" ? th.alignment : "";
    tf.textContent = [lean, line, align].filter(Boolean).join(" · ");
  }
  document.getElementById("ebo-reason").textContent = th.looking
    ? ("looking at " + th.looking)
    : (th.reason || "");
  const gradeEl = document.getElementById("ebo-grade");
  if (gradeEl) gradeEl.textContent = (th.grade && th.grade.line) || "";
  document.getElementById("ebo-note").textContent =
    st.mode === "manual"
      ? "indicator — you tap · last window grades the call"
      : (st.dry_run ? "auto dry-run — will not live-submit" : "auto live — still needs the three artefacts");
  window.postMessage({ source: SOURCE, type: "think", payload: th, quotes: st.quotes || {} }, "*");
}

function scanVisiblePrice() {
  const text = document.body && document.body.innerText || "";
  const quotes = lastStatus && lastStatus.quotes;
  if (!text || !quotes || typeof quotes !== "object") return;
  const anchors = ["ETH", "BTC"].map((symbol) => {
    const row = quotes[symbol];
    const price = Number(row && typeof row === "object" ? row.price : row);
    return { symbol, price };
  }).filter((row) => Number.isFinite(row.price) && row.price > 0);
  if (!anchors.length) return;
  const candidates = [];
  const re = /\$\s*([\d,]+(?:\.\d{1,4})?)/g;
  let match;
  while ((match = re.exec(text))) {
    const price = Number(match[1].replace(/,/g, ""));
    if (!Number.isFinite(price) || price <= 0) continue;
    const nearest = anchors.map((row) => ({
      ...row,
      distance: Math.abs(price - row.price) / row.price,
    })).sort((a, b) => a.distance - b.distance)[0];
    if (nearest && nearest.distance <= 0.05) candidates.push({ ...nearest, pagePrice: price });
  }
  if (!candidates.length) return;
  const best = candidates.sort((a, b) => a.distance - b.distance)[0];
  const now = Date.now();
  if (best.pagePrice === lastVisiblePrice && now - lastVisiblePriceAt < 2000) return;
  lastVisiblePrice = best.pagePrice;
  lastVisiblePriceAt = now;
  viaWorker({ type: "quotes", quotes: [{
    symbol: best.symbol,
    price: best.pagePrice,
    ts: now / 1000,
    source: "dom",
  }] });
}

function startPoll() {
  if (pollTimer) return;
  pollTimer = setInterval(() => {
    refreshStatus();
  }, 1500);
}

injectPageHook();
keepWorkerAwake();
sendSession();
setInterval(sendSession, 15000);
ensureOverlay();
startPoll();
refreshStatus();
setInterval(scanVisiblePrice, 500);
setInterval(() => {
  ensureOverlay();
  keepWorkerAwake();
}, 2000);

/* ── Legion bridge: read-only page snapshot (Cherubim 2026-08-14) ─────────── */
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.type !== "bridge_read_page") return false;
  try {
    const text = (document.body && document.body.innerText || "").slice(0, 20000);
    const priceEls = Array.from(document.querySelectorAll('[class*="price" i], [data-price]')).slice(0, 20)
      .map((el) => ({ cls: el.className && String(el.className).slice(0, 60), text: (el.textContent || "").trim().slice(0, 80) }));
    sendResponse({ url: location.href, title: document.title, ts: new Date().toISOString(), contentBuild: CONTENT_BUILD, grid: lastGridSnapshot, text, priceEls });
  } catch (e) {
    sendResponse({ error: String(e && e.message || e) });
  }
  return true;
});

/* Content script: euphoria.finance only. Injects the page hook + /trade overlay.
   Talks to the local control room directly so a sleeping service worker
   does not stall start/stop/mode or the live read. */
const SOURCE = "__euphoria_bridge";
const DEFAULT_PORT = 8765;
let quoteBuffer = [];
let flushTimer = null;
let eventSource = null;
let pollTimer = null;
let port = null;

function injectPageHook() {
  const src = chrome.runtime.getURL("inject.js");
  const el = document.createElement("script");
  el.src = src;
  el.onload = () => el.remove();
  (document.head || document.documentElement).appendChild(el);
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

async function controlBase() {
  const s = await chrome.storage.local.get(["controlPort"]);
  return `http://127.0.0.1:${Number(s.controlPort) || DEFAULT_PORT}`;
}

async function api(method, path, body) {
  const base = await controlBase();
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const resp = await fetch(base + path, opts);
  return resp.json();
}

function flushQuotes() {
  if (!quoteBuffer.length) return;
  const quotes = quoteBuffer;
  quoteBuffer = [];
  api("POST", "/quotes", quotes).catch(() => {
    chrome.runtime.sendMessage({ type: "quotes", quotes });
  });
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
    chrome.runtime.sendMessage({ type: "artefacts", artefacts: data.payload });
  }
  if (data.type === "grid" && data.payload) {
    chrome.runtime.sendMessage({ type: "session", grid: data.payload });
  }
});

function sendSession() {
  const privyUserId = findPrivyUserId();
  chrome.runtime.sendMessage({ type: "session", privyUserId });
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
    <div id="ebo-note" class="ebo-meta">tiles on the grid · you tap</div>
  `;
  document.documentElement.appendChild(root);
  const command = (method, path, body) => {
    api(method, path, body).then(paintSnapshot).catch(() => {
      const type = path.replace("/", "");
      chrome.runtime.sendMessage({ type, mode: body && body.mode }, paintSnapshot);
    });
  };
  document.getElementById("ebo-start").addEventListener("click", () => command("POST", "/start", {}));
  document.getElementById("ebo-stop").addEventListener("click", () => command("POST", "/stop", {}));
  document.getElementById("ebo-manual").addEventListener("click", () => command("POST", "/mode", { mode: "manual" }));
  document.getElementById("ebo-auto").addEventListener("click", () => command("POST", "/mode", { mode: "auto" }));
}

function paintSnapshot(st) {
  const root = document.getElementById("euphoria-bot-overlay");
  if (!root) return;
  if (!st || st.error === "offline" || (st.ok === false && !st.mode && st.running == null)) {
    document.getElementById("ebo-hint").textContent = "helper offline — start python -m src.ui";
    const link = document.getElementById("ebo-link");
    if (link) link.textContent = "offline";
    window.postMessage({ source: SOURCE, type: "think", payload: null }, "*");
    return;
  }
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
  const sug = th.suggested;
  const standAside = th.action === "sit" || !th.pick || sug === "no trade";
  const hint = th.sit_reason || th.why || th.hint || (sug && sug.hint) || th.reason || "waiting on ticks";
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
  window.postMessage({ source: SOURCE, type: "think", payload: th }, "*");
}

function stopEvents() {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function startPoll() {
  if (pollTimer) return;
  pollTimer = setInterval(() => {
    api("GET", "/status").then(paintSnapshot).catch(() => paintSnapshot(null));
  }, 2000);
}

async function connectEvents() {
  if (!isTradePage()) return;
  stopEvents();
  const base = await controlBase();
  try {
    eventSource = new EventSource(base + "/events");
    eventSource.addEventListener("state", (ev) => {
      try {
        paintSnapshot(JSON.parse(ev.data));
      } catch {
        /* ignore */
      }
    });
    eventSource.onerror = () => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      startPoll();
      setTimeout(connectEvents, 3000);
    };
  } catch {
    startPoll();
  }
}

injectPageHook();
keepWorkerAwake();
sendSession();
setInterval(sendSession, 15000);
ensureOverlay();
connectEvents();
api("GET", "/status").then(paintSnapshot).catch(() => paintSnapshot(null));
setInterval(() => {
  ensureOverlay();
  keepWorkerAwake();
}, 2000);

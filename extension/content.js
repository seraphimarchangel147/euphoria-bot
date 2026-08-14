/* Content script: euphoria.finance only. Injects the page hook + /trade overlay. */
const SOURCE = "__euphoria_bridge";
let quoteBuffer = [];
let flushTimer = null;

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

function flushQuotes() {
  if (!quoteBuffer.length) return;
  const quotes = quoteBuffer;
  quoteBuffer = [];
  chrome.runtime.sendMessage({ type: "quotes", quotes });
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

function ensureOverlay() {
  if (!isTradePage()) return;
  if (document.getElementById("euphoria-bot-overlay")) return;
  const root = document.createElement("div");
  root.id = "euphoria-bot-overlay";
  root.innerHTML = `
    <div class="ebo-head">
      <span>helper</span>
      <span id="ebo-dry" class="ebo-pill">dry-run</span>
      <span id="ebo-mode" class="ebo-pill">manual</span>
    </div>
    <div id="ebo-bias" class="ebo-bias flat">flat</div>
    <div id="ebo-hint" class="ebo-reason">connecting to local helper…</div>
    <div id="ebo-conf" class="ebo-meta">confidence —</div>
    <div id="ebo-reason" class="ebo-meta"></div>
    <div class="ebo-actions">
      <button type="button" id="ebo-start">Start</button>
      <button type="button" id="ebo-stop">Stop</button>
    </div>
    <div id="ebo-note" class="ebo-meta">tiles on the grid · you tap</div>
  `;
  document.documentElement.appendChild(root);
  document.getElementById("ebo-start").addEventListener("click", () => {
    chrome.runtime.sendMessage({ type: "start" }, refreshOverlay);
  });
  document.getElementById("ebo-stop").addEventListener("click", () => {
    chrome.runtime.sendMessage({ type: "stop" }, refreshOverlay);
  });
}

function refreshOverlay() {
  const root = document.getElementById("euphoria-bot-overlay");
  if (!root) return;
  chrome.runtime.sendMessage({ type: "status" }, (resp) => {
    if (!resp || !resp.status) {
      document.getElementById("ebo-hint").textContent = "helper offline — start python -m src.ui";
      window.postMessage({ source: SOURCE, type: "think", payload: null }, "*");
      return;
    }
    const st = resp.status;
    const th = resp.think || st.think || {};
    document.getElementById("ebo-dry").textContent = st.dry_run ? "dry-run" : "live";
    document.getElementById("ebo-mode").textContent = st.running ? (st.mode || "manual") : "stopped";
    const bias = th.bias || "flat";
    const biasEl = document.getElementById("ebo-bias");
    biasEl.textContent = bias;
    biasEl.className = "ebo-bias " + bias;
    document.getElementById("ebo-conf").textContent =
      "confidence " + Number(th.confidence || 0).toFixed(2);
    const sug = th.suggested;
    const hint = th.hint || (sug && sug.hint) || th.reason || "waiting for ticks";
    document.getElementById("ebo-hint").textContent = hint;
    document.getElementById("ebo-reason").textContent = th.reason || "";
    document.getElementById("ebo-note").textContent =
      st.mode === "manual"
        ? "manual — advisory only, you tap"
        : (st.dry_run ? "auto dry-run — will not live-submit" : "auto live — still needs the three artefacts");
    window.postMessage({ source: SOURCE, type: "think", payload: th }, "*");
  });
}

injectPageHook();
sendSession();
setInterval(sendSession, 15000);
ensureOverlay();
refreshOverlay();
setInterval(() => {
  ensureOverlay();
  refreshOverlay();
}, 1000);

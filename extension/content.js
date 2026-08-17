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
const CONTENT_BUILD = "0.4.2-deployment-ready";

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
  if (data.type === "wallet" && data.payload) {
    viaWorker({ type: "session", wallet: data.payload });
  }
  if (data.type === "frames" && data.payload) {
    viaWorker({ type: "session", frames: data.payload });
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
      // An order-sourced tap is self-evidencing — it was read off a request
      // the page itself sent. The pointer cross-check only applies to taps
      // inferred from a click, and it was dropping most of them.
      if (event.capture_source !== "order") {
        const pointer = event.pointer || {};
        const trusted = trustedPointer && Math.abs(event.ts - trustedPointer.ts) <= 750 &&
          Math.abs(Number(pointer.client_x) - trustedPointer.client_x) <= 3 &&
          Math.abs(Number(pointer.client_y) - trustedPointer.client_y) <= 3;
        trustedPointer = null;
        if (!trusted) return;
      }
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
  try {
    paintSnapshot(unwrapStatus(status));
  } catch (e) {
    // A paint failure must not silently stop the poll loop. It did once: the
    // card sat on its initial markup looking disconnected while the helper was
    // fine, and nothing said why.
    console.warn("[euphoria] overlay paint failed:", e && e.message);
  }
}

function bindOverlayChrome(root) {
  const head = root.querySelector(".ebo-head");
  const minBtn = root.querySelector("#ebo-min");
  if (!head || head.dataset.eboDrag === "1") return;
  head.dataset.eboDrag = "1";
  chrome.storage.local.get(["overlayPos", "overlayMin"], (s) => {
    if (s.overlayPos && Number.isFinite(s.overlayPos.left) && Number.isFinite(s.overlayPos.top)) {
      root.style.left = s.overlayPos.left + "px";
      root.style.top = s.overlayPos.top + "px";
      root.style.right = "auto";
      root.style.bottom = "auto";
    }
    root.classList.toggle("ebo-min", !!s.overlayMin);
    if (minBtn) minBtn.textContent = root.classList.contains("ebo-min") ? "+" : "\u2013";
  });
  if (minBtn) {
    minBtn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      root.classList.toggle("ebo-min");
      minBtn.textContent = root.classList.contains("ebo-min") ? "+" : "\u2013";
      chrome.storage.local.set({ overlayMin: root.classList.contains("ebo-min") });
    });
  }
  let drag = null;
  head.addEventListener("pointerdown", (ev) => {
    if (ev.target.closest("button")) return;
    const box = root.getBoundingClientRect();
    drag = { dx: ev.clientX - box.left, dy: ev.clientY - box.top };
    head.setPointerCapture(ev.pointerId);
    head.classList.add("ebo-dragging");
  });
  head.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    const x = Math.max(0, Math.min(window.innerWidth - 48, ev.clientX - drag.dx));
    const y = Math.max(0, Math.min(window.innerHeight - 32, ev.clientY - drag.dy));
    root.style.left = x + "px";
    root.style.top = y + "px";
    root.style.right = "auto";
    root.style.bottom = "auto";
  });
  const endDrag = () => {
    if (!drag) return;
    drag = null;
    head.classList.remove("ebo-dragging");
    const box = root.getBoundingClientRect();
    chrome.storage.local.set({ overlayPos: { left: box.left, top: box.top } });
  };
  head.addEventListener("pointerup", endDrag);
  head.addEventListener("pointercancel", endDrag);
}

function ensureOverlay() {
  if (!isTradePage()) return;
  if (document.getElementById("euphoria-bot-overlay")) return;
  const root = document.createElement("div");
  root.id = "euphoria-bot-overlay";
  root.innerHTML = `
    <div class="ebo-head">
      <span class="ebo-drag">indicator</span>
      <span id="ebo-dry" class="ebo-pill">dry-run</span>
      <span id="ebo-run" class="ebo-pill">stopped</span>
      <span id="ebo-link" class="ebo-pill">sync</span>
      <button type="button" id="ebo-min" class="ebo-minbtn" title="Minimize">\u2013</button>
    </div>
    <div id="ebo-body">
      <div id="ebo-tilt" class="ebo-tilt" hidden></div>
      <div id="ebo-verdict" class="ebo-verdict sit">NO TRADE</div>
      <div id="ebo-hint" class="ebo-reason">connecting to local helper\u2026</div>

      <div class="ebo-coil">
        <div class="ebo-coil-head">
          <span id="ebo-coil-state" class="ebo-coil-pill">\u2014</span>
          <span id="ebo-coil-line"></span>
        </div>
        <div class="ebo-coil-meter"><i id="ebo-coil-fill"></i></div>
      </div>

      <div class="ebo-sect">
        <div class="ebo-sect-h">this row</div>
        <div id="ebo-dwell" class="ebo-dwell">\u2014</div>
        <div class="ebo-updown"><i id="ebo-up" class="u"></i><i id="ebo-dn" class="d"></i></div>
        <div id="ebo-break" class="ebo-meta">\u2014</div>
        <div id="ebo-closes" class="ebo-closes"></div>
        <div id="ebo-closes-sum" class="ebo-meta">\u2014</div>
      </div>

      <div class="ebo-sect">
        <div class="ebo-sect-h">board</div>
        <div id="ebo-board" class="ebo-meta">\u2014</div>
        <div id="ebo-model" class="ebo-meta">\u2014</div>
        <div id="ebo-tf" class="ebo-tf"></div>
      </div>

      <div class="ebo-sect">
        <div class="ebo-sect-h">money</div>
        <div id="ebo-money" class="ebo-meta">\u2014</div>
        <div id="ebo-grade" class="ebo-grade"></div>
      </div>

      <div class="ebo-actions">
        <button type="button" id="ebo-start">Start</button>
        <button type="button" id="ebo-stop">Stop</button>
        <button type="button" id="ebo-manual">Manual</button>
        <button type="button" id="ebo-auto">Auto</button>
      </div>

      <details id="ebo-key" class="ebo-key">
        <summary>what the colours mean</summary>
        <div class="ebo-key-row"><i style="background:rgba(48,209,88,0.75)"></i>positive edge \u2014 pays more than our odds</div>
        <div class="ebo-key-row"><i class="hatch" style="background:rgba(48,209,88,0.75)"></i>positive but unproven \u2014 too few samples</div>
        <div class="ebo-key-row"><i style="background:rgba(120,132,150,0.45)"></i>negative \u2014 quoted, house wins</div>
        <div class="ebo-key-row"><i class="dot" style="background:rgba(110,168,254,0.08)"></i>unreachable \u2014 tape cannot get there</div>
        <div class="ebo-key-row"><i style="background:rgba(254,160,219,0.6)"></i>unquoted \u2014 no multiplier</div>
        <div class="ebo-key-row"><i class="dash" style="background:rgba(140,140,150,0.2)"></i>no fit \u2014 price feed is down</div>
        <div class="ebo-key-note">intensity = size of the edge, not probability</div>
      </details>

      <div id="ebo-grid" class="ebo-meta">${lastGridHook}</div>
      <div id="ebo-note" class="ebo-meta">manual \u00b7 you tap \u00b7 nothing is submitted</div>
    </div>
  `;
  document.documentElement.appendChild(root);
  if (window.eboGlassify) window.eboGlassify(root, { scale: 52, blur: 7 });
  bindOverlayChrome(root);
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
    const hintEl = document.getElementById("ebo-hint");
    if (hintEl) hintEl.textContent = "helper offline — start python -m src.ui";
    const vEl = document.getElementById("ebo-verdict");
    if (vEl) { vEl.textContent = "OFFLINE"; vEl.className = "ebo-verdict off"; }
    const link = document.getElementById("ebo-link");
    if (link) link.textContent = "helper offline";
    const runEl = document.getElementById("ebo-run");
    if (runEl) runEl.textContent = "stopped";
    window.postMessage({ source: SOURCE, type: "think", payload: null, quotes: {} }, "*");
    return;
  }
  lastStatus = st;
  const th = st.think || {};
  /* Every lookup is null-guarded. A single missing element used to throw
     part-way through the paint, which left the card frozen on its initial
     markup while the rest of the system carried on -- a silent failure that
     looked like "the helper is not connected". */
  const set = (id, text) => { const e = document.getElementById(id); if (e) e.textContent = text; };
  const flag = (id, on, cls) => {
    const e = document.getElementById(id);
    if (e) e.classList.toggle(cls || "active", !!on);
  };
  set("ebo-dry", st.dry_run ? "dry-run" : "live");
  set("ebo-run", st.running ? (st.mode || "manual") : "stopped");
  set("ebo-link", "synced");
  flag("ebo-start", st.running);
  flag("ebo-stop", !st.running);
  flag("ebo-manual", st.mode === "manual");
  flag("ebo-auto", st.mode === "auto");
  const standAside = th.stand_aside != null ? !!th.stand_aside : !th.pick;
  const plan = th.plan || null;

  /* The verdict, stated once and plainly. The old card led with a bias word
     that was true even when nothing was worth tapping. */
  const verdictEl = document.getElementById("ebo-verdict");
  if (verdictEl) {
    if (plan && plan.cell_x != null) {
      verdictEl.textContent = "TAP " + (plan.side || "").toUpperCase() +
        " " + plan.distance + " · +" + Math.round(Number(plan.t_end || 0)) + "s";
      verdictEl.className = "ebo-verdict tap";
    } else {
      verdictEl.textContent = "NO TRADE";
      verdictEl.className = "ebo-verdict sit";
    }
  }
  const hint = plan
    ? Number(plan.multiplier).toFixed(2) + "x pays above our odds · EV +" +
      Number(plan.ev_lcb || 0).toFixed(2) + " · stake " + Number(plan.stake || 0).toFixed(2)
    : (th.sit_reason || th.why || th.hint || th.reason || "waiting on ticks");
  set("ebo-hint", hint);

  /* Tilt: pace against drawdown, stated on the chart where the tapping happens. */
  const tilt = th.tilt || {};
  const tiltEl = document.getElementById("ebo-tilt");
  if (tiltEl) {
    if (tilt.flagged) {
      tiltEl.hidden = false;
      tiltEl.className = "ebo-tilt " + (tilt.severity || "");
      tiltEl.textContent = (tilt.severity === "tilted" ? "TILT · " : "HEADS UP · ") +
        (tilt.headline || "") +
        (tilt.reasons && tilt.reasons.length ? " — " + tilt.reasons.join(", ") : "");
    } else {
      tiltEl.hidden = true;
    }
  }

  /* Coil: is the tape winding up? Never a direction — that is the walk's job. */
  const coil = th.coil || {};
  const coilPill = document.getElementById("ebo-coil-state");
  if (coilPill) {
    coilPill.textContent = coil.state || "—";
    coilPill.className = "ebo-coil-pill " + (coil.state || "") + (coil.ready ? " ready" : "");
  }
  set("ebo-coil-line", coil.headline || "");
  const coilFill = document.getElementById("ebo-coil-fill");
  if (coilFill) coilFill.style.width = Math.round(Number(coil.score || 0) * 100) + "%";

  /* This row: how long it has held, and which way it usually breaks. */
  const tv = th.traversal || {};
  const cur = tv.current || {};
  const brk = cur.break || {};
  const dir = cur.direction || {};
  if (cur.dwell_columns) {
    const pos = cur.position_in_row;
    const where = pos == null ? "" : (pos < 0 ? " · on the floor"
      : pos > 1 ? " · on the ceiling" : " · " + Math.round(pos * 100) + "% up");
    set("ebo-dwell", "held " + cur.dwell_columns + " square" +
      (cur.dwell_columns === 1 ? "" : "s") + " (" + Math.round(cur.dwell_s || 0) + "s)" + where);
  } else {
    set("ebo-dwell", "waiting for the tape");
  }
  const pUp = dir.p_up != null ? Number(dir.p_up) : null;
  const up = document.getElementById("ebo-up");
  const dn = document.getElementById("ebo-dn");
  if (up && dn) {
    up.style.width = (pUp == null ? 50 : pUp * 100) + "%";
    dn.style.width = (pUp == null ? 50 : (1 - pUp) * 100) + "%";
  }
  set("ebo-break", brk.p_break == null ? "—"
    : "breaks next " + Math.round(Number(brk.p_break) * 100) + "%"
      + (pUp == null ? "" : " · " + Math.round(pUp * 100) + "% up / "
        + Math.round((1 - pUp) * 100) + "% down")
      + (brk.trusted ? "" : " · n=" + brk.n + " thin"));

  /* Last nine closes, newest first. The surface says where price might go;
     this says where it has just been -- which row each 5s column actually
     settled on, and whether that was a step up, down or a hold. Read together
     they separate a grid that is genuinely drifting from one sitting on the
     same row printing near-certainties, which is the state the book has been
     quietly paying the vig into. */
  const cl = th.closes || {};
  const strip = document.getElementById("ebo-closes");
  if (strip) {
    const items = (cl.closes || []).slice(0, 9);
    strip.innerHTML = items.map(function (c) {
      const d = c.dir || "flat";
      const glyph = d === "up" ? "▲" : d === "down" ? "▼" : "─";
      const step = c.step == null ? "" : (c.step > 0 ? "+" + c.step : String(c.step));
      return '<i class="c ' + d + '" title="close ' + (c.close == null ? "?" : c.close)
        + (c.row == null ? "" : " · row " + c.row)
        + (step ? " · " + step + " row" + (Math.abs(c.step) === 1 ? "" : "s") : "")
        + '">' + glyph + '</i>';
    }).join("");
    strip.style.display = items.length ? "" : "none";
  }
  set("ebo-closes-sum", cl.n
    ? "last " + cl.n + " closes · " + (cl.up || 0) + " up / " + (cl.down || 0)
      + " down / " + (cl.flat || 0) + " held · net "
      + (cl.net_rows > 0 ? "+" : "") + (cl.net_rows || 0) + " rows"
    : "no closes yet");

  /* The board, and how much of it is even worth looking at. */
  const stt = th.surface_stats;
  const vd = (stt && stt.verdicts) || {};
  /* "0 edge" was being shown while a cell carried +6.2% EV on the lower bound
     and a Kelly fraction of 0.23 -- the roll was simply empty, so every stake
     sized to zero. That reads as "the market has nothing", which is a
     different fact and the wrong one to act on. Out of money is not out of
     edge, and the card has to say which. `untouched` is also counted now: the
     reachability rail was vetoing 72 of 120 cells and none of that appeared
     anywhere on this line. */
  set("ebo-board", stt
    ? stt.quoted + " quoted · " + (vd.tap || 0) + " edge · " + (vd.unproven || 0) +
      " unproven · " + ((vd.unreachable || 0) + (vd.untouched || 0)) + " out of reach" +
      (vd["no-funds"] ? " · " + vd["no-funds"] + " BLOCKED: no funds" : "")
    : "no quote grid yet");
  const d = th.diffusion || {};
  set("ebo-model", d.sigma != null
    ? "σ " + Number(d.sigma).toFixed(4) + "/√s · scale T^" + Number(d.hurst || 0.5).toFixed(2) +
      (d.ok === false ? " · NO FIT" : "") + (d.degraded ? " · degraded feed" : "")
    : "—");
  const tf = document.getElementById("ebo-tf");
  if (tf) {
    const lean = th.tf_lean && th.tf_lean !== "unknown" ? th.tf_lean : "";
    tf.textContent = [lean, th.tf_line || ""].filter(Boolean).join(" · ");
  }

  /* Money: the real balance, and what the paper policy has done with it. */
  const learn = th.learning || {};
  const bank = learn.bankroll || {};
  const wal = learn.wallet || {};
  const bal = (wal.page && wal.page.balance != null) ? Number(wal.page.balance) : null;
  const pnl = Number(bank.pnl || 0);
  set("ebo-money", (bal != null ? bal.toFixed(2) + " USDM" : "balance —") +
    " · paper " + (pnl >= 0 ? "+" : "") + pnl.toFixed(2) +
    " (" + (bank.wins || 0) + "W/" + (bank.losses || 0) + "L)" +
    (wal.max_stake ? " · cap " + Number(wal.max_stake).toFixed(2) : ""));
  const gradeEl = document.getElementById("ebo-grade");
  if (gradeEl) gradeEl.textContent = (th.grade && th.grade.line) || "";

  set("ebo-note", st.mode === "manual"
    ? "manual · you tap · nothing is submitted"
    : (st.dry_run ? "auto dry-run · will not submit"
      : "auto live · still needs botSignature / fingerprint / permit"));
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

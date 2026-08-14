/* Page-world helper: listen to the tab's own prices and draw on the live canvas grid. */
(function () {
  if (window.__euphoriaBridgeInjected) return;
  window.__euphoriaBridgeInjected = true;
  const SOURCE = "__euphoria_bridge";
  const LAYER_ID = "euphoria-helper-layer";

  let lastThink = null;
  let lastPrice = null;
  let chart = null;
  let gridState = null;
  let host = null;
  let layer = null;
  let tileCanvas = null;
  let labelEl = null;
  let lastGridEmit = 0;
  let lastGridKey = "";
  let tfEl = null;
  let reasonEl = null;
  let gradeEl = null;
  let rafId = 0;
  const tileAnim = new Map();

  function emit(type, payload) {
    window.postMessage({ source: SOURCE, type, payload }, "*");
  }

  function sane(symbol, price) {
    const p = Number(price);
    if (!Number.isFinite(p) || p <= 0) return null;
    const s = String(symbol).toUpperCase();
    if (s === "ETH" && (p < 50 || p > 1e6)) return null;
    if (s === "BTC" && (p < 100 || p > 1e7)) return null;
    if (s !== "ETH" && s !== "BTC") return null;
    return { symbol: s, price: p, ts: Date.now() / 1000, source: "page" };
  }

  function fromObject(obj) {
    if (!obj || typeof obj !== "object") return [];
    const out = [];
    const push = (sym, price) => {
      const q = sane(sym, price);
      if (q) out.push(q);
    };
    if (obj.symbol || obj.asset) {
      push(obj.symbol || obj.asset, obj.price ?? obj.value ?? obj.px);
    }
    for (const key of ["ETH", "BTC"]) {
      const v = obj[key];
      if (v == null) continue;
      if (typeof v === "object") push(key, v.price ?? v.value);
      else push(key, v);
    }
    if (Array.isArray(obj)) {
      for (const item of obj) out.push(...fromObject(item));
    }
    if (obj.data) out.push(...fromObject(obj.data));
    if (obj.quotes) out.push(...fromObject(obj.quotes));
    return out;
  }

  function parseUnknown(data) {
    if (data == null) return [];
    if (typeof data === "object") return fromObject(data);
    if (typeof data !== "string") return [];
    const text = data.trim();
    if (!text) return [];
    try {
      return fromObject(JSON.parse(text));
    } catch {
      /* NATS / mixed frames */
    }
    const out = [];
    const re = /\b(ETH|BTC)\b[^0-9]{0,24}(\d{2,7}(?:\.\d+)?)/gi;
    let m;
    while ((m = re.exec(text))) {
      const q = sane(m[1], m[2]);
      if (q) out.push(q);
    }
    return out;
  }

  function onQuotes(raw) {
    const quotes = parseUnknown(raw);
    if (!quotes.length) return;
    emit("quotes", quotes);
    const eth = quotes.find((q) => q.symbol === "ETH") || quotes[0];
    if (eth) lastPrice = eth.price;
  }

  function hookPricesObject(target) {
    if (!target || target.__euphoriaBridged) return;
    if (typeof target.onPriceUpdate === "function") {
      const orig = target.onPriceUpdate.bind(target);
      target.onPriceUpdate = function (update) {
        onQuotes(update);
        return orig(update);
      };
      target.__euphoriaBridged = true;
    }
    if (typeof target.subscribe === "function" && !target.__euphoriaSub) {
      try {
        target.subscribe((update) => onQuotes(update));
        target.__euphoriaSub = true;
      } catch {
        /* ignore */
      }
    }
  }

  function scanGlobals() {
    hookPricesObject(window.prices);
    hookPricesObject(window.__prices);
    hookPricesObject(window.__euphoriaPrices);
  }

  const OrigWS = window.WebSocket;
  if (OrigWS && !OrigWS.__euphoriaWrapped) {
    const Wrapped = new Proxy(OrigWS, {
      construct(Target, args) {
        const ws = new Target(...args);
        ws.addEventListener("message", (ev) => onQuotes(ev.data));
        return ws;
      },
    });
    Wrapped.__euphoriaWrapped = true;
    window.WebSocket = Wrapped;
  }

  const origFetch = window.fetch;
  window.fetch = function (...args) {
    tryInspectRequest(args[0], args[1]);
    return origFetch.apply(this, args);
  };

  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__euphoriaUrl = url;
    return origOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    tryInspectRequest(this.__euphoriaUrl, { body });
    return origSend.apply(this, arguments);
  };

  function tryInspectRequest(url, init) {
    const href = String(url || "");
    const body = init && init.body;
    if (typeof body !== "string") return;
    if (!/botSignature|deviceFingerprint|approvalPermit/.test(body)) return;
    if (href && !/euphoria\.finance|executeTrade/i.test(href) && !/executeTrade/.test(body)) return;
    try {
      const parsed = JSON.parse(body);
      const input = parsed.input || parsed;
      const artefacts = {};
      for (const key of ["botSignature", "deviceFingerprint", "approvalPermit", "blob"]) {
        if (typeof input[key] === "string" && input[key]) artefacts[key] = input[key];
      }
      if (Object.keys(artefacts).length) emit("artefacts", artefacts);
    } catch {
      /* ignore */
    }
  }

  function isGridState(o) {
    if (!o || typeof o !== "object") return false;
    if (typeof o.getAdjustedNowMs === "function" && (o.squareDuration != null || o.config)) return true;
    if (o.coords && typeof o.coords.cellToScreen === "function") return true;
    if (typeof o.screenToCell === "function" && typeof o.cellToScreen === "function") return true;
    return false;
  }

  function isChart(o) {
    return !!(o && typeof o === "object" && o.gridState && (o.priceFeed || o.chartConfig));
  }

  function fiberOf(node) {
    if (!node) return null;
    for (const key of Object.keys(node)) {
      if (key.startsWith("__reactFiber") || key.startsWith("__reactInternalInstance")) return node[key];
    }
    return null;
  }

  function consider(value, seen) {
    if (!value || typeof value !== "object") return;
    if (seen.has(value)) return;
    if (value instanceof Node || value instanceof Window) return;
    seen.add(value);
    try {
      if (!chart && isChart(value)) chart = value;
      if (!gridState && isGridState(value)) gridState = value;
      if (value.gridState && isGridState(value.gridState)) gridState = value.gridState;
    } catch {
      /* ignore exotic proxies */
    }
  }

  function walkFiber(fiber, seen, depth) {
    if (!fiber || depth > 50 || seen.has(fiber)) return;
    seen.add(fiber);
    try {
      consider(fiber.memoizedProps, seen);
      consider(fiber.stateNode, seen);
      let hook = fiber.memoizedState;
      let hops = 0;
      while (hook && hops < 40) {
        consider(hook.memoizedState, seen);
        consider(hook.queue, seen);
        hook = hook.next;
        hops += 1;
      }
    } catch {
      return;
    }
    walkFiber(fiber.return, seen, depth + 1);
    walkFiber(fiber.child, seen, depth + 1);
  }

  function discoverChart() {
    const canvases = findCanvases();
    const seen = new Set();
    for (const canvas of canvases) {
      walkFiber(fiberOf(canvas), seen, 0);
      let el = canvas.parentElement;
      for (let i = 0; i < 8 && el; i += 1) {
        walkFiber(fiberOf(el), seen, 0);
        el = el.parentElement;
      }
    }
    const root = document.getElementById("root");
    if (root) walkFiber(fiberOf(root), seen, 0);
    if (chart && chart.gridState) gridState = chart.gridState;
    return gridState;
  }

  function findCanvases() {
    return [...document.querySelectorAll("canvas")].filter((c) => {
      const r = c.getBoundingClientRect();
      return r.width >= 180 && r.height >= 180;
    }).sort((a, b) => {
      const aa = a.getBoundingClientRect();
      const bb = b.getBoundingClientRect();
      return bb.width * bb.height - aa.width * aa.height;
    });
  }

  function findHost() {
    const canvases = findCanvases();
    if (!canvases.length) return null;
    const canvas = canvases[0];
    let el = canvas.parentElement;
    let best = canvas.parentElement;
    while (el && el !== document.body) {
      const cls = String(el.className || "");
      if (cls.includes("relative") && (cls.includes("size-full") || cls.includes("min-h-0"))) {
        return el;
      }
      if (el.childElementCount >= 1 && el.getBoundingClientRect().height >= 180) best = el;
      el = el.parentElement;
    }
    return best;
  }

  function snapshotState(gs) {
    if (!gs) return null;
    const offset = typeof gs.getOffset === "function" ? gs.getOffset() : gs.state?.offset;
    const zoom = typeof gs.getZoom === "function" ? gs.getZoom() : gs.state?.zoom;
    const w = gs.canvasWidth || gs.state?.canvasWidth;
    const h = gs.canvasHeight || gs.state?.canvasHeight;
    const squareDuration = gs.squareDuration || gs.config?.squareDuration || 5000;
    const dpl = gs.config?.dollarsPerLine || gs.state?.dollarsPerLine || 0.5;
    const gridSize = (typeof gs.getGridSize === "function" ? gs.getGridSize() : null) || gs.gridSize || gs.config?.gridSize || 6;
    const now = typeof gs.getAdjustedNowMs === "function" ? gs.getAdjustedNowMs() : Date.now();
    if (!offset || !w || !h) return null;
    const cellSize = (h / gridSize) * (zoom || 1);
    return { offset, zoom: zoom || 1, w, h, squareDuration, dpl, gridSize, now, cellSize };
  }

  function livePrice(gs) {
    const feed = chart?.priceFeed;
    const last = feed && typeof feed.getLastPrice === "function" ? feed.getLastPrice() : null;
    if (last && Number(last.price) > 0) return Number(last.price);
    if (lastPrice) return lastPrice;
    return null;
  }

  function cellBounds(gs, snap, gx, gy) {
    const coords = gs.coords;
    if (coords && typeof coords.getCellScreenBounds === "function") {
      try {
        const st = gs.state || {
          offset: snap.offset,
          zoom: snap.zoom,
          canvasWidth: snap.w,
          canvasHeight: snap.h,
        };
        return coords.getCellScreenBounds(gx, gy, st);
      } catch {
        /* fall through */
      }
    }
    const left = gx * snap.cellSize + snap.offset.x;
    const top = snap.offset.y - (gy + 1) * snap.cellSize;
    return { left, top, right: left + snap.cellSize, bottom: top + snap.cellSize };
  }

  function ensureLayer() {
    const next = findHost();
    if (!next) return null;
    if (host !== next) {
      layer?.remove();
      layer = null;
      host = next;
      const style = getComputedStyle(host);
      if (style.position === "static") host.style.position = "relative";
    }
    if (!layer) {
      layer = document.createElement("div");
      layer.id = LAYER_ID;
      layer.setAttribute("aria-hidden", "true");
      tileCanvas = document.createElement("canvas");
      labelEl = document.createElement("div");
      labelEl.id = "euphoria-helper-tile-label";
      tfEl = document.createElement("div");
      tfEl.id = "euphoria-helper-tf-strip";
      reasonEl = document.createElement("div");
      reasonEl.id = "euphoria-helper-reason";
      gradeEl = document.createElement("div");
      gradeEl.id = "euphoria-helper-grade";
      layer.appendChild(tileCanvas);
      layer.appendChild(labelEl);
      layer.appendChild(tfEl);
      layer.appendChild(reasonEl);
      layer.appendChild(gradeEl);
      host.appendChild(layer);
    }
    return layer;
  }

  function sizeCanvas() {
    if (!layer || !tileCanvas || !host) return { sx: 1, sy: 1, cssW: 0, cssH: 0 };
    const r = host.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const cssW = Math.max(1, r.width);
    const cssH = Math.max(1, r.height);
    tileCanvas.style.width = cssW + "px";
    tileCanvas.style.height = cssH + "px";
    const bw = Math.round(cssW * dpr);
    const bh = Math.round(cssH * dpr);
    if (tileCanvas.width !== bw || tileCanvas.height !== bh) {
      tileCanvas.width = bw;
      tileCanvas.height = bh;
    }
    return { sx: 1, sy: 1, cssW, cssH, dpr };
  }

  function tileFromPayload(item, next, gy) {
    if (!item || typeof item !== "object") return null;
    const side = item.side === "down" ? "down" : "up";
    const dist = Number(item.distance) || 1;
    let x = next;
    let y = side === "down" ? gy - dist : gy + dist;
    if (item.cell_x != null && item.cell_y != null) {
      const tx = Number(item.cell_x);
      const ty = Number(item.cell_y);
      if (Number.isFinite(tx) && Number.isFinite(ty) && Math.abs(tx - next) <= 3 && Math.abs(ty - gy) <= 6) {
        x = tx;
        y = ty;
      }
    }
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return { x, y, role: item.role || "look", side, hint: item.hint || item.label || "" };
  }

  function tilesForThink(think, snap, price) {
    const gx = Math.floor(snap.now / snap.squareDuration);
    const gy = Math.floor(price / snap.dpl);
    const next = gx + 1;
    const raw = (think && (think.candidates || think.looking_at)) || [];
    let looking = [];
    if (Array.isArray(raw) && raw.length) {
      looking = raw.map((item) => tileFromPayload(item, next, gy)).filter(Boolean);
    }
    if (!looking.length) {
      looking = [
        { x: next, y: gy + 1, role: "look", side: "up", hint: "" },
        { x: next, y: gy - 1, role: "look", side: "down", hint: "" },
      ];
    }
    let selected = null;
    const pick = think && think.pick;
    const sug = think && think.suggested;
    if (pick && pick !== "no trade") {
      selected = tileFromPayload(pick, next, gy);
    } else if (sug && sug !== "no trade") {
      selected = tileFromPayload(sug, next, gy);
    }
    if (selected) {
      selected.role = "sel";
      selected.hint = (think && (think.hint || think.reason)) || selected.hint || (sug && (sug.hint || sug.label)) || "";
    }
    return { looking, selected, gx, gy, next };
  }

  function lerp(a, b, t) {
    return a + (b - a) * t;
  }

  function ease(t) {
    const x = Math.max(0, Math.min(1, t));
    return 1 - Math.pow(1 - x, 3);
  }

  function tfGlyph(lean) {
    if (lean === "up") return "↑";
    if (lean === "down") return "↓";
    if (lean === "flat") return "→";
    return "·";
  }

  function tfLine(think) {
    if (think && think.tf_line) return think.tf_line;
    const frames = (think && think.timeframes) || {};
    return ["1m", "5m", "1h", "4h", "D", "M"]
      .map((key) => key + tfGlyph(frames[key] && frames[key].lean))
      .join(" ");
  }

  function draw() {
    if (!location.pathname.startsWith("/trade") && location.pathname !== "/trade") {
      layer?.remove();
      layer = null;
      return;
    }
    if (!gridState) discoverChart();
    if (!ensureLayer() || !tileCanvas) return;
    const gs = gridState || chart?.gridState;
    const snap = snapshotState(gs);
    const { dpr, cssW, cssH } = sizeCanvas();
    const ctx = tileCanvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    if (labelEl) labelEl.style.display = "none";
    if (tfEl) tfEl.style.display = "none";
    if (reasonEl) reasonEl.style.display = "none";
    if (gradeEl) gradeEl.style.display = "none";
    if (!snap) return;

    const scaleX = cssW / snap.w;
    const scaleY = cssH / snap.h;
    const price = livePrice(gs);
    if (!price) return;

    const think = lastThink || {};
    const { looking, selected } = tilesForThink(think, snap, price);
    const nowMs = performance.now();
    const pulse = 0.62 + 0.38 * (0.5 + 0.5 * Math.sin(nowMs / 520));
    const seen = new Set();

    const targetBox = (tile) => {
      const b = cellBounds(gs, snap, tile.x, tile.y);
      const x = b.left * scaleX;
      const y = b.top * scaleY;
      const w = (b.right - b.left) * scaleX;
      const h = (b.bottom - b.top) * scaleY;
      if (w < 4 || h < 4) return null;
      if (x + w < 0 || y + h < 0 || x > cssW || y > cssH) return null;
      return { x, y, w, h };
    };

    const paint = (tile, kind) => {
      const dest = targetBox(tile);
      if (!dest) return null;
      const key = kind + ":" + tile.x + ":" + tile.y;
      seen.add(key);
      let anim = tileAnim.get(key);
      if (!anim) {
        anim = { x: dest.x, y: dest.y, w: dest.w, h: dest.h, a: 0 };
      }
      const t = 0.18;
      anim.x = lerp(anim.x, dest.x, t);
      anim.y = lerp(anim.y, dest.y, t);
      anim.w = lerp(anim.w, dest.w, t);
      anim.h = lerp(anim.h, dest.h, t);
      anim.a = lerp(anim.a, 1, 0.12);
      tileAnim.set(key, anim);
      const alpha = ease(anim.a);
      ctx.save();
      if (kind === "look") {
        ctx.globalAlpha = 0.55 + 0.45 * alpha;
        ctx.fillStyle = "rgba(254, 160, 219, 0.14)";
        ctx.strokeStyle = "rgba(254, 160, 219, 0.72)";
        ctx.lineWidth = 1.6;
      } else {
        ctx.globalAlpha = alpha;
        ctx.fillStyle = "rgba(110, 168, 254, " + (0.16 + 0.16 * pulse) + ")";
        ctx.strokeStyle = "#6ea8fe";
        ctx.lineWidth = 2.2 + pulse * 0.6;
        ctx.shadowColor = "rgba(110, 168, 254, 0.45)";
        ctx.shadowBlur = 8 + pulse * 6;
      }
      const pad = kind === "sel" ? 0.5 : 1.5;
      ctx.beginPath();
      ctx.rect(anim.x + pad, anim.y + pad, Math.max(2, anim.w - pad * 2), Math.max(2, anim.h - pad * 2));
      ctx.fill();
      ctx.stroke();
      ctx.restore();
      return { x: anim.x, y: anim.y, w: anim.w, h: anim.h };
    };

    for (const tile of looking) {
      if (selected && tile.x === selected.x && tile.y === selected.y) continue;
      paint(tile, "look");
    }
    let pickBox = null;
    if (selected) pickBox = paint(selected, "sel");
    for (const key of [...tileAnim.keys()]) {
      if (seen.has(key)) continue;
      const anim = tileAnim.get(key);
      anim.a = lerp(anim.a, 0, 0.2);
      if (anim.a < 0.04) tileAnim.delete(key);
      else tileAnim.set(key, anim);
    }

    const line = tfLine(think);
    const align = think.alignment && think.alignment !== "unknown" ? think.alignment : "";
    const lookingAt = think.looking || (selected && selected.hint) || "nearby above and below";
    const why = think.why || (think.hint && think.hint !== "no trade" ? think.hint : "") ||
      (think.reason || "").split("→").pop().trim();
    const standAside = !selected || think.suggested === "no trade" || think.lesson === "chop" ||
      think.lesson === "quiet" || think.lesson === "waiting" || think.lesson === "fade" ||
      think.lesson === "faded" || think.lesson === "weak";
    const liveLine = standAside
      ? ("no trade — " + (why || "standing aside"))
      : (why || lookingAt);
    const anchor = pickBox || (looking[0] && targetBox(looking[0]));
    if (tfEl && line) {
      tfEl.textContent = (think.tf_lean ? think.tf_lean + " · " : "") + line;
      tfEl.style.display = "block";
      if (anchor) {
        tfEl.style.left = Math.min(cssW - 220, Math.max(6, anchor.x)) + "px";
        tfEl.style.top = Math.max(6, anchor.y - 44) + "px";
      } else {
        tfEl.style.left = "10px";
        tfEl.style.top = "10px";
      }
    }
    if (labelEl) {
      labelEl.textContent = standAside ? "no trade" : lookingAt;
      labelEl.style.display = "block";
      labelEl.classList.toggle("aside", !!standAside);
      if (anchor) {
        labelEl.style.left = Math.min(cssW - 200, Math.max(4, anchor.x)) + "px";
        labelEl.style.top = Math.max(4, anchor.y - 22) + "px";
      }
    }
    if (reasonEl && liveLine) {
      reasonEl.textContent = liveLine;
      reasonEl.style.display = "block";
      if (anchor) {
        reasonEl.style.left = Math.min(cssW - 260, Math.max(4, anchor.x)) + "px";
        reasonEl.style.top = Math.min(cssH - 40, (anchor.y + (anchor.h || 0) + 4)) + "px";
      }
    }
    const gradeLine = think.grade && think.grade.line;
    if (gradeEl && gradeLine) {
      gradeEl.textContent = gradeLine;
      gradeEl.style.display = "block";
      if (anchor) {
        gradeEl.style.left = Math.min(cssW - 260, Math.max(4, anchor.x)) + "px";
        gradeEl.style.top = Math.min(cssH - 22, (anchor.y + (anchor.h || 0) + 22)) + "px";
      } else {
        gradeEl.style.left = "10px";
        gradeEl.style.top = "34px";
      }
    }

    const meta = {
      gridX: Math.floor(snap.now / snap.squareDuration),
      gridY: Math.floor(price / snap.dpl),
      price,
      dollars_per_line: snap.dpl,
      priceInterval: snap.dpl,
      square_duration: snap.squareDuration,
      now_ms: snap.now,
      cell_height: snap.dpl,
    };
    const key = `${meta.gridX}:${meta.gridY}:${meta.dollars_per_line}`;
    const now = Date.now();
    if (key !== lastGridKey || now - lastGridEmit > 1000) {
      lastGridKey = key;
      lastGridEmit = now;
      emit("grid", meta);
    }
  }

  window.addEventListener("message", (ev) => {
    const data = ev.data;
    if (!data || data.source !== SOURCE) return;
    if (data.type === "think") lastThink = data.payload || null;
  });

  function scrapeDom() {
    const root = document.body;
    if (!root) return;
    const text = root.innerText || "";
    const re = /\b(ETH|BTC)\b[^\n]{0,48}?\$?([\d,]+\.\d{1,2})/g;
    let m;
    const quotes = [];
    while ((m = re.exec(text))) {
      const q = sane(m[1], m[2].replace(/,/g, ""));
      if (q) {
        q.source = "dom";
        quotes.push(q);
      }
    }
    if (quotes.length) onQuotes(quotes);
  }

  function loop() {
    draw();
    rafId = window.requestAnimationFrame(loop);
  }

  scanGlobals();
  setInterval(scanGlobals, 2000);
  setInterval(scrapeDom, 2000);
  setInterval(discoverChart, 2000);
  discoverChart();
  if (!rafId) rafId = window.requestAnimationFrame(loop);
})();

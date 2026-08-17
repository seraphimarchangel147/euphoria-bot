/* Page-world helper: listen to the tab's own prices and draw on the live canvas grid. */
(function () {
  if (window.__euphoriaBridgeInjected) return;
  window.__euphoriaBridgeInjected = true;
  const SOURCE = "__euphoria_bridge";
  const LAYER_ID = "euphoria-helper-layer";

  let lastThink = null;
  let lastQuotes = null;
  let lastPrice = null;
  let lastSymbol = "ETH";
  let chart = null;
  let gridState = null;
  let quotesFeed = null;
  let tradeCanvas = null;
  let host = null;
  let layer = null;
  let tileCanvas = null;
  let labelEl = null;
  let lastGridEmit = 0;
  let lastGridKey = "";
  let lastHook = "";
  let lastHookEmit = 0;
  let lastChartQuoteAt = 0;
  let lastChartQuotePrice = null;
  let pageHistory = [];
  let lastCaptureContext = null;
  let lastCaptureSnap = null;

  let tfEl = null;
  let reasonEl = null;
  let gradeEl = null;
  let rafId = 0;
  const tileAnim = new Map();

  /* Keep in lockstep with src/control/grid_map.py */
  const ETH_DPL = 0.5;
  const BTC_DPL = 10;
  const SQUARE_MS = 5000;
  const FALLBACK_ROWS = 24;
  const FALLBACK_COLS = 12;

  function emit(type, payload) {
    window.postMessage({ source: SOURCE, type, payload }, "*");
  }

  const captureApi = window.__euphoriaPlayerCapture;
  const playerJoiner = captureApi && typeof captureApi.PlayerEventJoiner === "function"
    ? new captureApi.PlayerEventJoiner({
        emit: (row) => {
          const event = captureApi.validatePlayerEvent(row);
          if (event) emit("player-event", event);
        },
        maxDelayMs: 10000,
      })
    : null;

  function inspectSettlement(raw, source) {
    if (!playerJoiner || !captureApi || typeof captureApi.extractSettlement !== "function") return;
    const result = captureApi.extractSettlement(raw, Date.now(), source);
    if (!result) return;
    playerJoiner.addResult(result);
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

  /* tRPC subscription frames carry no symbol -- the symbol was named once, in
     the SUB request, and every tick after that is just {id, price, timestamp}.
     Without this map the whole ~16Hz settlement feed is unattributable and gets
     dropped, leaving volatility to be fitted on a ~0.4Hz trickle of DOM scrapes
     and oracle prints. That under-measures sigma by an order of magnitude, and
     an under-measured sigma is what invents an edge at the money. */
  const subSymbol = new Map();

  function noteSubscription(text) {
    if (typeof text !== "string" || text.indexOf("subscription") < 0) return;
    let parsed;
    try { parsed = JSON.parse(text); } catch { return; }
    for (const row of (Array.isArray(parsed) ? parsed : [parsed])) {
      const id = row && row.id;
      const path = row && row.params && row.params.path;
      const sym = row && row.params && row.params.input && row.params.input.json
        && row.params.input.json.symbol;
      if (id != null && typeof sym === "string" && /price/i.test(String(path || ""))) {
        subSymbol.set(String(id), sym.toUpperCase());
      }
    }
  }

  function parseTrpcTick(data) {
    if (!data || typeof data !== "object") return null;
    const id = data.id;
    const json = data.result && data.result.data && data.result.data.json;
    if (id == null || !json || json.price == null) return null;
    if (json.isHistorical) return null;                 // backfill, not live tape
    const symbol = subSymbol.get(String(id));
    if (!symbol) return null;
    const q = sane(symbol, json.price);
    if (!q) return null;
    const ts = Number(json.timestamp);
    if (Number.isFinite(ts) && ts > 1e12) q.ts = ts / 1000;
    q.source = "page";
    return q;
  }

  function parseUnknown(data) {
    if (data == null) return [];
    if (typeof data === "object") {
      const tick = parseTrpcTick(data);
      return tick ? [tick] : fromObject(data);
    }
    if (typeof data !== "string") return [];
    const text = data.trim();
    if (!text) return [];
    try {
      const parsed = JSON.parse(text);
      const rows = Array.isArray(parsed) ? parsed : [parsed];
      const ticks = [];
      for (const row of rows) {
        const tick = parseTrpcTick(row);
        if (tick) ticks.push(tick);
      }
      if (ticks.length) return ticks;
      return fromObject(parsed);
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

  /* Frame shapes, for decoding the quote grid.
     The page fetches nothing over HTTP -- prices, multipliers and balance all
     arrive on one socket -- and the `quotesFeed.getMultiplierForCell` API the
     old code expected no longer exists in the bundle. So collect one example of
     each distinct frame layout (digits masked, length capped) and let the
     control room show them, instead of guessing at the wire format. */
  const frameShapes = new Map();
  let lastFrameEmit = 0;

  function sampleFrame(raw, source) {
    if (typeof raw !== "string" || !raw || raw.length > 200000) return;
    const kind = (source && source.kind) || "websocket";
    const url = String((source && source.url) || "");
    if (kind !== "websocket" && kind !== "ws-send" && !/euphoria\.finance/i.test(url)) return;
    const path = url ? url.replace(/^https?:\/\/[^/]+/i, "").split("?")[0].slice(0, 120) : "";
    const key = kind + " " + path + " " +
      raw.replace(/-?\d+(\.\d+)?/g, "#").replace(/\s+/g, " ").slice(0, 160);
    if (!frameShapes.has(key)) {
      if (frameShapes.size >= 60) return;
      frameShapes.set(key, {
        kind, path, bytes: raw.length,
        shape: raw.replace(/-?\d+(\.\d+)?/g, "#").replace(/\s+/g, " ").slice(0, 200),
        sample: raw.slice(0, 900),
        count: 0,
      });
    }
    frameShapes.get(key).count += 1;
    const now = Date.now();
    if (now - lastFrameEmit < 10000) return;
    lastFrameEmit = now;
    emit("frames", { shapes: [...frameShapes.values()], collected: frameShapes.size });
  }

  /* The quote grid.
   *
   * NATS subject `quotes.<asset>_<duration>_<interval>` carries the whole board
   * on every tick:
   *
   *   {"asset":"ETH","timeInterval":5.0,"priceInterval":0.5,
   *    "currentPrice":1879.79,"volatility":0.0332,"timestamp":...,
   *    "timeSteps":18,"priceSteps":50,"grid":"<base64>"}
   *
   * `grid` is timeSteps*priceSteps uint16 little-endian, time-major, so
   * index = t * priceSteps + p. Each value is the multiplier times 100, and
   * 10000 is the sentinel for "not quoted" (only ~105 of 900 cells are live).
   * Price step 24 of 50 is the at-the-money row.
   */
  const QUOTE_SENTINEL = 10000;
  const QUOTE_MIN_GAP_MS = 400;
  let quoteGrid = null;
  let lastQuoteParse = 0;

  function parseQuoteGrid(text) {
    const start = text.indexOf("MSG quotes.");
    if (start < 0) return;
    const now = Date.now();
    if (now - lastQuoteParse < QUOTE_MIN_GAP_MS) return;
    const nl = text.indexOf("\r\n", start);
    if (nl < 0) return;
    const body = text.slice(nl + 2).trim();
    if (!body.startsWith("{")) return;
    let j;
    try { j = JSON.parse(body.slice(0, body.lastIndexOf("}") + 1)); } catch { return; }
    const T = Number(j.timeSteps), P = Number(j.priceSteps);
    const dpl = Number(j.priceInterval), dur = Number(j.timeInterval);
    const price = Number(j.currentPrice), ts = Number(j.timestamp);
    if (!(T > 0 && P > 0 && dpl > 0 && dur > 0 && price > 0 && ts > 0) || typeof j.grid !== "string") return;
    lastQuoteParse = now;

    let buf;
    try {
      const bin = atob(j.grid);
      buf = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i += 1) buf[i] = bin.charCodeAt(i);
    } catch { return; }
    if (buf.length < T * P * 2) return;
    const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);

    const centerP = Math.floor((P - 1) / 2);
    const currentX = Math.floor(ts / (dur * 1000));
    const currentY = Math.floor(price / dpl);
    const cells = [];
    for (let t = 0; t < T; t += 1) {
      for (let p = 0; p < P; p += 1) {
        const v = dv.getUint16((t * P + p) * 2, true);
        if (v === QUOTE_SENTINEL || v <= 0) continue;
        const rowOffset = p - centerP;
        cells.push({
          cell_x: currentX + 1 + t,
          cell_y: currentY + rowOffset,
          forward: t + 1,
          forward_s: (t + 1) * dur,
          row_offset: rowOffset,
          side: rowOffset < 0 ? "down" : rowOffset > 0 ? "up" : "at-price",
          distance: Math.abs(rowOffset),
          multiplier: v / 100,
          break_even_probability: Number((100 / v).toFixed(6)),
        });
      }
    }
    if (!cells.length) return;
    quoteGrid = {
      cells,
      authoritative: true,
      multiplier_source: "nats:quotes",
      quoted_grid_ref_time: ts,
      server_volatility: Number(j.volatility),
      server_price: price,
      dollars_per_line: dpl,
      price_interval: dpl,
      cell_height: dpl,
      square_duration: dur * 1000,
      current_cell_x: currentX,
      current_cell_y: currentY,
      received_at: now,
    };
    // The quote frame states the price the house is pricing against. Feeding it
    // in as a tick means our volatility is fitted to the same series the house
    // settles on, which is the only series that makes an EV comparison mean
    // anything.
    const settlementTick = sane(String(j.asset || "ETH"), price);
    if (settlementTick) {
      settlementTick.source = "page";
      settlementTick.ts = ts > 1e12 ? ts / 1000 : ts;
      onQuotes([settlementTick]);
    }

    /* Ship the board from here, not from the render loop.
     *
     * The grid used to be emitted at the bottom of draw(), which returns early
     * whenever the chart canvas cannot be found. So a rendering problem -- a
     * layout change, a chart that has not mounted yet -- silently cut off every
     * multiplier the bot receives, and it went blind to EV while still looking
     * healthy. Prices are data; drawing is decoration. They must not share a
     * failure mode. */
    if (now - lastGridEmit > 900) {
      lastGridEmit = now;
      lastGridKey = `${currentX}:${currentY}:${dpl}`;
      emit("grid", {
        gridX: currentX,
        gridY: currentY,
        price,
        dollars_per_line: dpl,
        priceInterval: dpl,
        cell_height: dpl,
        square_duration: dur * 1000,
        now_ms: ts,
        ...quoteGrid,
      });
    }
    emit("quote-grid", { cells: cells.length, volatility: quoteGrid.server_volatility });
  }

  function onQuotes(raw) {
    const quotes = parseUnknown(raw);
    if (!quotes.length) return;
    emit("quotes", quotes);
    const eth = quotes.find((q) => q.symbol === "ETH") || quotes[0];
    if (eth) {
      lastPrice = eth.price;
      lastSymbol = eth.symbol || lastSymbol;
      if (eth.symbol === "ETH") {
        const ts = Number(eth.ts) > 1e12 ? Number(eth.ts) : Number(eth.ts) * 1000;
        pageHistory.push({ ts: Number.isFinite(ts) ? ts : Date.now(), price: eth.price });
        const cutoff = Date.now() - 120000;
        pageHistory = pageHistory.filter((item) => item.ts >= cutoff).slice(-500);
      }
    }
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

  /* One decoder for every socket, however we got hold of it. The tRPC socket
     speaks JSON text; the NATS `_events` socket that carries the quote grid
     speaks binary. Decoding both is what makes `quotes.<asset>_<duration>_
     <interval>` visible at all.

     The flag is per socket and is set by whichever path reaches it first, so a
     socket cannot be sniffed twice. That matters more than it looks: settlement
     frames are read here, and a duplicated settlement once paid a single square
     ten times. */
  function sniffSocket(ws, url) {
    if (!ws || ws.__euphoriaSniffing) return;
    ws.__euphoriaSniffing = true;
    const src = { kind: "websocket", url: String(url || ws.url || "") };
    const handleText = (text) => {
      if (typeof text !== "string" || !text) return;
      onQuotes(text);
      parseQuoteGrid(text);
      inspectSettlement(text, src);
      inspectBalance(text, src);
      sampleFrame(text, src);
    };
    const onFrame = (ev) => {
      const d = ev.data;
      try {
        if (typeof d === "string") handleText(d);
        else if (d instanceof ArrayBuffer) handleText(new TextDecoder().decode(d));
        else if (ArrayBuffer.isView(d)) handleText(new TextDecoder().decode(d.buffer));
        else if (typeof Blob !== "undefined" && d instanceof Blob) {
          d.text().then(handleText).catch(() => {});
        }
      } catch (e) { /* undecodable frame */ }
    };
    try {
      (rawAddListener || EventTarget.prototype.addEventListener).call(ws, "message", onFrame);
    } catch (e) { /* some sockets refuse listeners */ }
  }

  const OrigWS = window.WebSocket;
  const rawAddListener = OrigWS && OrigWS.prototype && OrigWS.prototype.addEventListener;

  /* Belt and braces. The constructor wrap below only sees sockets built through
     `window.WebSocket` after we replaced it, and the grid feed died in the
     middle of a 50-minute measurement while prices kept flowing -- the shape of
     a reconnect that escaped the wrap. Every consumer of a socket must
     eventually listen for messages, one of these two ways, so hooking both
     catches it no matter how it was constructed. */
  if (OrigWS && OrigWS.prototype && !OrigWS.prototype.__euphoriaSniffed) {
    const proto = OrigWS.prototype;
    if (rawAddListener) {
      proto.addEventListener = function (type, ...rest) {
        if (type === "message") { try { sniffSocket(this); } catch (e) {} }
        return rawAddListener.call(this, type, ...rest);
      };
    }
    const desc = Object.getOwnPropertyDescriptor(proto, "onmessage");
    if (desc && desc.set) {
      Object.defineProperty(proto, "onmessage", {
        configurable: true,
        enumerable: desc.enumerable,
        get: desc.get,
        set(fn) { try { sniffSocket(this); } catch (e) {} return desc.set.call(this, fn); },
      });
    }
    proto.__euphoriaSniffed = true;
  }

  if (OrigWS && !OrigWS.__euphoriaWrapped) {
    const Wrapped = new Proxy(OrigWS, {
      construct(Target, args) {
        const ws = new Target(...args);
        // Outgoing frames name every subscription the app opens, which is the
        // only reliable way to learn whether a quote/multiplier stream exists
        // at all or whether the grid is priced client-side.
        try {
          const origSend = ws.send.bind(ws);
          ws.send = function (payload) {
            try {
              let text = payload;
              if (payload instanceof ArrayBuffer) text = new TextDecoder().decode(payload);
              else if (ArrayBuffer.isView(payload)) text = new TextDecoder().decode(payload.buffer);
              noteSubscription(text);
              // An order may leave over the socket as easily as over HTTP.
              inspectOrder(text, String(args[0] || ""));
              sampleFrame(text, { kind: "ws-send", url: String(args[0] || "") });
            } catch (e) {}
            return origSend(payload);
          };
        } catch (e) { /* some sockets refuse patching */ }
        sniffSocket(ws, String(args[0] || ""));
        return ws;
      },
    });
    Wrapped.__euphoriaWrapped = true;
    window.WebSocket = Wrapped;
  }

  const origFetch = window.fetch;
  window.fetch = function (...args) {
    tryInspectRequest(args[0], args[1]);
    return origFetch.apply(this, args).then((response) => {
      try {
        const url = String(response.url || args[0] || "");
        response.clone().text().then((body) => {
          const src = { kind: "fetch", url };
          inspectSettlement(body, src);
          inspectBalance(body, src);
          sampleFrame(body, src);
        }).catch(() => {});
      } catch { /* opaque response */ }
      return response;
    });
  };

  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__euphoriaUrl = url;
    return origOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    tryInspectRequest(this.__euphoriaUrl, { body });
    this.addEventListener("load", () => {
      const src = { kind: "xhr", url: String(this.responseURL || this.__euphoriaUrl || "") };
      inspectSettlement(this.responseText, src);
      inspectBalance(this.responseText, src);
      sampleFrame(this.responseText, src);
    });
    return origSend.apply(this, arguments);
  };

  /* The in-game balance is server-side, so the chain shows zero even when the
     account is funded. The page already asks for it with credentials we do not
     have to reproduce -- so read the answer as it goes past, rather than trying
     to re-authenticate from Python. */
  const BALANCE_KEY = /^(usdm_?balance|available_?balance|balance|cash|equity)$/i;
  let lastBalance = null;
  let lastBalanceAt = 0;

  function extractBalance(raw, url) {
    const href = String(url || "");
    if (!/euphoria\.finance/i.test(href)) return null;
    let value = raw;
    if (typeof raw === "string") {
      if (raw.length > 400000 || !/balance|cash|equity/i.test(raw)) return null;
      try { value = JSON.parse(raw); } catch { return null; }
    }
    if (!value || typeof value !== "object") return null;
    let best = null;
    (function walk(node, depth) {
      if (best || depth > 8 || node == null || typeof node !== "object") return;
      for (const key of Object.keys(node)) {
        const val = node[key];
        if (BALANCE_KEY.test(key)) {
          const n = Number(val);
          if (Number.isFinite(n) && n >= 0 && n < 1e12) { best = { key, value: n }; return; }
        }
        walk(val, depth + 1);
      }
    })(value, 0);
    return best;
  }

  /* users.getTier carries the account's real caps -- maxTrade, per-square and
     per-column exposure. Sizing against those beats sizing against a constant
     in our own config that may not match the tier at all. */
  const LIMIT_KEYS = [
    "tier", "maxTrade", "maxStakePerSquare", "maxExposurePerSquare",
    "maxExposurePerColumn", "maxExposurePerUser", "settledTrades",
  ];

  function extractLimits(value) {
    let hit = null;
    (function walk(node, depth) {
      if (hit || depth > 8 || node == null || typeof node !== "object") return;
      if (node.maxTrade != null && node.tier != null) {
        const out = {};
        for (const k of LIMIT_KEYS) {
          const v = node[k];
          if (typeof v === "string") out[k] = v;
          else if (Number.isFinite(Number(v))) out[k] = Number(v);
        }
        hit = out;
        return;
      }
      for (const key of Object.keys(node)) walk(node[key], depth + 1);
    })(value, 0);
    return hit;
  }

  function inspectBalance(raw, source) {
    const href = String((source && source.url) || "");
    if (!/euphoria\.finance/i.test(href)) return;
    let parsed = raw;
    if (typeof raw === "string") {
      if (raw.length > 400000 || !/balance|maxTrade/i.test(raw)) return;
      try { parsed = JSON.parse(raw); } catch { return; }
    }
    const found = extractBalance(parsed, href);
    const limits = extractLimits(parsed);
    if (!found && !limits) return;
    const now = Date.now();
    if (found && found.value === lastBalance && !limits && now - lastBalanceAt < 5000) return;
    if (found) { lastBalance = found.value; lastBalanceAt = now; }
    const payload = { source: "api", ts: now / 1000 };
    if (found) { payload.balance = found.value; payload.field = found.key; }
    if (limits) payload.limits = limits;
    emit("wallet", payload);
  }

  /* The order itself, read off the page's own outgoing request.
   *
   * Mapping a click back to a square by inverting the canvas geometry needs
   * `gridState.screenToCell`, which this build does not have -- so every
   * captured tap resolved to nothing. The order payload states the square
   * outright: cellX, cellY, the quoted multiplier and the stake. That is the
   * authoritative record of what was bet, it does not care whether the pointer
   * cross-check caught the click, and it survives the next redesign of the
   * chart.
   */
  function findOrder(value, depth) {
    if (!value || typeof value !== "object" || depth > 8) return null;
    if (Array.isArray(value)) {
      for (const item of value) {
        const hit = findOrder(item, depth + 1);
        if (hit) return hit;
      }
      return null;
    }
    const cx = value.cellX ?? value.cell_x;
    const cy = value.cellY ?? value.cell_y;
    const amount = value.amount ?? value.betSize ?? value.stake;
    if (cx != null && cy != null && amount != null) return value;
    for (const nested of Object.values(value)) {
      const hit = findOrder(nested, depth + 1);
      if (hit) return hit;
    }
    return null;
  }

  function extractOrder(body, url) {
    if (typeof body !== "string" || !body || body.length > 200000) return null;
    if (!/cellX|cell_x/.test(body)) return null;
    const href = String(url || "");
    if (href && !/euphoria\.finance/i.test(href)) return null;
    let parsed;
    try { parsed = JSON.parse(body); } catch { return null; }
    const order = findOrder(parsed, 0);
    if (!order) return null;

    const cellX = Number(order.cellX ?? order.cell_x);
    const cellY = Number(order.cellY ?? order.cell_y);
    const amount = Number(order.amount ?? order.betSize ?? order.stake);
    const mult = Number(order.quote ?? order.multiplier ?? order.requestedMultiplier);
    const startPrice = Number(order.startPrice ?? order.price);
    const dpl = Number(order.priceInterval ?? order.dollarsPerLine) ||
      (quoteGrid && quoteGrid.dollars_per_line) || 0.5;
    if (!Number.isFinite(cellX) || !Number.isFinite(cellY) || !(amount > 0)) return null;

    // Side and distance relative to where price actually was on the order.
    let side = "at-price";
    let distance = 0;
    if (Number.isFinite(startPrice) && dpl > 0) {
      const row = Math.floor(startPrice / dpl);
      distance = Math.abs(cellY - row);
      side = cellY > row ? "up" : cellY < row ? "down" : "at-price";
    }
    return {
      event_type: "player-tap",
      ts: Date.now(),
      tap_id: null,
      capture_source: "order",
      cell: { x: cellX, y: cellY },
      cell_x: cellX,
      cell_y: cellY,
      side,
      distance,
      multiplier: Number.isFinite(mult) && mult > 0 ? mult : null,
      quoted_breakeven: Number.isFinite(mult) && mult > 0
        ? Number((1 / mult).toFixed(6)) : null,
      price: Number.isFinite(startPrice) ? startPrice : null,
      stake_requested: amount,
      quoted_grid_ref_time: Number(order.quotedGridRefTime) || null,
      pointer: { client_x: null, client_y: null },
      capture_reason: null,
    };
  }

  function inspectOrder(body, url) {
    if (!playerJoiner) return;
    const tap = extractOrder(body, url);
    if (tap) playerJoiner.addTap(tap);
  }

  function tryInspectRequest(url, init) {
    const href = String(url || "");
    const body = init && init.body;
    if (typeof body !== "string") return;
    inspectOrder(body, href);
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

  /* The multiplier feed is the whole game: without it there is no EV and the
     policy can only watch. Finding it by walking down from a `chart` wrapper
     fails whenever the bundle renames that wrapper, so hunt for the capability
     itself -- any object exposing getMultiplierForCell is the feed. */
  function isQuotesFeed(o) {
    return !!(o && typeof o === "object" && typeof o.getMultiplierForCell === "function");
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
      if (!quotesFeed && isQuotesFeed(value)) quotesFeed = value;
      if (!quotesFeed && isQuotesFeed(value.quotesFeed)) quotesFeed = value.quotesFeed;
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
    for (const el of [document.getElementById("root"), document.body, document.getElementById("app")]) {
      if (el) walkFiber(fiberOf(el), seen, 0);
    }
    if (chart && chart.gridState) gridState = chart.gridState;
    if (!quotesFeed && chart && isQuotesFeed(chart.quotesFeed)) quotesFeed = chart.quotesFeed;
    return gridState;
  }

  /* What discovery actually found, so /grid can say why there are no
     multipliers instead of leaving the operator to guess. */
  function discoveryReport() {
    return {
      chart: !!chart,
      grid_state: !!gridState,
      quotes_feed: !!quotesFeed,
      quotes_feed_stale: quotesFeed ? quotesFeed.isStale === true : null,
      canvases: findCanvases().length,
      react_fiber: !!fiberOf(findHost()),
    };
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
    return canvases[0];
  }

  function dollarsPerLine(symbol) {
    return String(symbol || "ETH").toUpperCase() === "BTC" ? BTC_DPL : ETH_DPL;
  }

  function fallbackSnap(cssW, cssH, price, symbol, nowMs) {
    const dpl = dollarsPerLine(symbol);
    const now = nowMs || Date.now();
    const curX = Math.floor(now / SQUARE_MS) + 1;
    const curY = Math.floor(price / dpl);
    return {
      mode: "fallback",
      offset: { x: 0, y: 0 },
      zoom: 1,
      w: cssW,
      h: cssH,
      squareDuration: SQUARE_MS,
      dpl,
      gridSize: FALLBACK_ROWS,
      now,
      cellSize: cssH / FALLBACK_ROWS,
      cellW: cssW / FALLBACK_COLS,
      cellH: cssH / FALLBACK_ROWS,
      curX,
      curY,
    };
  }

  function fallbackCellBounds(snap, gx, gy) {
    const cellW = snap.cellW || snap.w / FALLBACK_COLS;
    const cellH = snap.cellH || snap.h / FALLBACK_ROWS;
    const left = snap.w / 2 + (gx - snap.curX) * cellW - cellW / 2;
    const top = snap.h / 2 - (gy - snap.curY) * cellH - cellH / 2;
    return { left, top, right: left + cellW, bottom: top + cellH };
  }

  function emitHook(hook, force) {
    const now = Date.now();
    if (!force && hook === lastHook && now - lastHookEmit < 1500) return;
    lastHook = hook;
    lastHookEmit = now;
    emit("grid-hook", { hook });
  }

  function standAsideFromThink(think) {
    const pick = think && think.pick;
    if (pick && pick !== "no trade") return false;
    const sug = think && think.suggested;
    if (sug && sug !== "no trade") return false;
    if (think && think.stand_aside != null) return !!think.stand_aside;
    return true;
  }

  function quotePrice(quotes, symbol) {
    if (!quotes || typeof quotes !== "object") return null;
    const row = quotes[symbol] || quotes[String(symbol).toLowerCase()];
    if (row == null) return null;
    const p = Number(typeof row === "object" ? row.price : row);
    return p > 0 ? p : null;
  }

  function resolveSymbol(think, quotes) {
    if (think && think.asset) return String(think.asset).toUpperCase();
    if (quotes && (quotes.ETH || quotes.eth)) return "ETH";
    if (quotes && (quotes.BTC || quotes.btc)) return "BTC";
    return lastSymbol || "ETH";
  }

  function resolvePrice(gs, think, quotes) {
    const feed = livePrice(gs);
    if (feed) return feed;
    const fromThink = think && Number(think.price || think.last_price);
    if (fromThink > 0) return fromThink;
    const symbol = resolveSymbol(think, quotes);
    const fromQuotes = quotePrice(quotes, symbol) || quotePrice(quotes, "ETH") || quotePrice(quotes, "BTC");
    if (fromQuotes) return fromQuotes;
    return lastPrice;
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
    if (!snap || snap.mode === "fallback" || !gs) {
      return fallbackCellBounds(snap, gx, gy);
    }
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

  function pointerCell(gs, snap, x, y) {
    if (!gs || !snap) return null;
    const state = gs.state || {
      offset: snap.offset,
      zoom: snap.zoom,
      canvasWidth: snap.w,
      canvasHeight: snap.h,
    };
    const attempts = [];
    if (typeof gs.screenToCell === "function") attempts.push(() => gs.screenToCell(x, y));
    if (gs.coords && typeof gs.coords.screenToCell === "function") {
      attempts.push(() => gs.coords.screenToCell(x, y, state));
      attempts.push(() => gs.coords.screenToCell({ x, y }, state));
    }
    for (const attempt of attempts) {
      try {
        const value = attempt();
        if (value && Number.isFinite(Number(value.x ?? value.cellX)) && Number.isFinite(Number(value.y ?? value.cellY))) {
          return { x: Number(value.x ?? value.cellX), y: Number(value.y ?? value.cellY) };
        }
      } catch { /* try the observed signature variants */ }
    }
    for (const cell of (lastCaptureContext && lastCaptureContext.cells) || []) {
      const bounds = cellBounds(gs, snap, Number(cell.cell_x), Number(cell.cell_y));
      if (bounds && x >= bounds.left && x <= bounds.right && y >= bounds.top && y <= bounds.bottom) {
        return { x: Number(cell.cell_x), y: Number(cell.cell_y) };
      }
    }
    return null;
  }

  function observePlayerTap(ev) {
    if (!playerJoiner || !captureApi || typeof captureApi.resolveTap !== "function") return;
    if (!tradeCanvas || ev.button !== 0 || !ev.isTrusted) return;
    const path = typeof ev.composedPath === "function" ? ev.composedPath() : [];
    if (ev.target !== tradeCanvas && !path.includes(tradeCanvas)) return;
    const rect = tradeCanvas.getBoundingClientRect();
    const snap = lastCaptureSnap;
    const gs = gridState || chart?.gridState;
    const tap = captureApi.resolveTap({
      ts: Date.now(),
      clientX: ev.clientX,
      clientY: ev.clientY,
      rect,
      canvasSize: snap ? { width: snap.w, height: snap.h } : { width: tradeCanvas.width, height: tradeCanvas.height },
      context: lastCaptureContext,
      screenToCell: (x, y) => pointerCell(gs, snap, x, y),
      quoteCell: (cellX, cellY) => {
        const feed = quotesFeed || (chart && chart.quotesFeed);
        if (!feed || feed.isStale === true || typeof feed.getMultiplierForCell !== "function") return null;
        return feed.getMultiplierForCell(cellX, cellY);
      },
    });
    playerJoiner.addTap(tap);
  }

  function ensureLayer() {
    const canvas = findHost();
    if (!canvas) {
      emitHook("no-canvas");
      if (layer) {
        layer.remove();
        layer = null;
      }
      tradeCanvas = null;
      host = null;
      return null;
    }
    tradeCanvas = canvas;
    host = canvas.parentElement || canvas;
    if (!layer || !layer.isConnected) {
      layer = document.createElement("div");
      layer.id = LAYER_ID;
      layer.setAttribute("aria-hidden", "true");
      layer.style.position = "fixed";
      layer.style.setProperty("pointer-events", "none");
      layer.style.zIndex = "2147483645";
      layer.style.overflow = "visible";
      tileCanvas = document.createElement("canvas");
      tileCanvas.style.setProperty("pointer-events", "none");
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
      (document.documentElement || document.body).appendChild(layer);
    }
    return layer;
  }

  function sizeCanvas() {
    if (!layer || !tileCanvas || !tradeCanvas) return { sx: 1, sy: 1, cssW: 0, cssH: 0 };
    const r = tradeCanvas.getBoundingClientRect();
    layer.style.left = r.left + "px";
    layer.style.top = r.top + "px";
    layer.style.width = r.width + "px";
    layer.style.height = r.height + "px";
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
    // Always draw pink nearest candidates even when the signal sits.
    const nearby = [
      { x: next, y: gy + 1, role: "look", side: "up", hint: "above" },
      { x: next, y: gy - 1, role: "look", side: "down", hint: "below" },
    ];
    const raw = (think && (think.candidates || think.looking_at)) || [];
    const extra = Array.isArray(raw)
      ? raw.map((item) => tileFromPayload(item, next, gy)).filter(Boolean)
      : [];
    const looking = nearby.slice();
    for (const tile of extra) {
      if (!looking.some((row) => row.x === tile.x && row.y === tile.y)) looking.push(tile);
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

  /* The scored grid from /think. Each row is one cell priced against its own
     quoted multiplier, so the overlay can paint the whole board instead of the
     two squares next to the money. */
  function surfaceTiles(think) {
    const rows = (think && think.surface) || [];
    if (!Array.isArray(rows) || !rows.length) return [];
    const out = [];
    for (const r of rows) {
      const x = Number(r.cell_x);
      const y = Number(r.cell_y);
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      const p = Number(r.p_cal != null ? r.p_cal : r.p_touch);
      out.push({
        x,
        y,
        p: Number.isFinite(p) ? Math.max(0, Math.min(1, p)) : 0,
        ev: r.ev_lcb == null ? null : Number(r.ev_lcb),
        mult: r.multiplier == null ? null : Number(r.multiplier),
        verdict: String(r.verdict || "unscored"),
        forward: Number(r.forward) || 0,
        side: r.side || "",
      });
    }
    return out;
  }

  /* Colour carries the verdict, opacity carries the probability. Green is the
     only colour that means "this pays" -- everything else is context. */
  function surfaceStyle(tile) {
    const p = tile.p;
    if (tile.verdict === "tap") {
      return { fill: "rgba(48, 209, 88, " + (0.20 + 0.50 * p) + ")",
               stroke: "rgba(48, 209, 88, 0.95)", width: 2.2 };
    }
    if (tile.verdict === "thin") {
      return { fill: "rgba(255, 214, 10, " + (0.10 + 0.34 * p) + ")",
               stroke: "rgba(255, 214, 10, 0.66)", width: 1.4 };
    }
    if (tile.verdict === "unquoted" || tile.verdict === "unscored") {
      return { fill: "rgba(254, 160, 219, " + (0.08 + 0.42 * p) + ")",
               stroke: "rgba(254, 160, 219, 0.60)", width: 1.2 };
    }
    return { fill: "rgba(110, 168, 254, " + (0.04 + 0.30 * p) + ")",
             stroke: "rgba(110, 168, 254, 0.30)", width: 1.0 };
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

  function paintTileCaption(ctx, dest, tile, think, kind) {
    const tf = (think && think.tf_lean) || "";
    const why = String((think && (think.why || think.hint)) || tile.hint || tile.side || "").slice(0, 28);
    ctx.save();
    ctx.globalAlpha = 0.95;
    ctx.fillStyle = kind === "sel" ? "#c9dcff" : "#ffe0f4";
    ctx.font = "10px ui-sans-serif, system-ui, sans-serif";
    ctx.textBaseline = "top";
    const line1 = (tile.side || "") + (tf ? " · " + tf : "");
    ctx.fillText(line1, dest.x + 4, dest.y + 3);
    if (why && dest.h > 22) ctx.fillText(why, dest.x + 4, dest.y + dest.h - 13);
    ctx.restore();
  }

  function draw() {
    if (!location.pathname.startsWith("/trade") && location.pathname !== "/trade") {
      layer?.remove();
      layer = null;
      emitHook("no-canvas");
      return;
    }
    if (!gridState) discoverChart();
    if (!ensureLayer() || !tileCanvas) return;
    const gs = gridState || chart?.gridState;
    const reactSnap = snapshotState(gs);
    const { dpr, cssW, cssH } = sizeCanvas();
    const ctx = tileCanvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    if (labelEl) labelEl.style.display = "none";
    if (tfEl) tfEl.style.display = "none";
    if (reasonEl) reasonEl.style.display = "none";
    if (gradeEl) gradeEl.style.display = "none";

    const think = lastThink || {};
    const symbol = resolveSymbol(think, lastQuotes);
    const price = resolvePrice(gs, think, lastQuotes);
    if (!price) {
      emitHook(reactSnap ? "hooked" : "fallback");
      return;
    }

    let snap = reactSnap;
    let hook = reactSnap ? "hooked" : "fallback";
    if (!snap) snap = fallbackSnap(cssW, cssH, price, symbol);

    let { looking, selected } = tilesForThink(think, snap, price);
    const nowMs = performance.now();
    const pulse = 0.62 + 0.38 * (0.5 + 0.5 * Math.sin(nowMs / 520));
    const seen = new Set();

    const targetBox = (tile, useSnap) => {
      const b = cellBounds(gs, useSnap || snap, tile.x, tile.y);
      const sx = cssW / (useSnap || snap).w;
      const sy = cssH / (useSnap || snap).h;
      const x = b.left * sx;
      const y = b.top * sy;
      const w = (b.right - b.left) * sx;
      const h = (b.bottom - b.top) * sy;
      if (w < 4 || h < 4) return null;
      if (x + w < 0 || y + h < 0 || x > cssW || y > cssH) return null;
      return { x, y, w, h };
    };

    if (hook === "hooked" && !looking.some((tile) => targetBox(tile))) {
      snap = fallbackSnap(cssW, cssH, price, symbol);
      hook = "fallback";
      ({ looking, selected } = tilesForThink(think, snap, price));
    }
    emitHook(hook);

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
      const painted = { x: anim.x, y: anim.y, w: anim.w, h: anim.h };
      paintTileCaption(ctx, painted, tile, think, kind);
      return painted;
    };

    const surface = surfaceTiles(think);
    const plan = think.plan || null;
    let pickBox = null;

    if (surface.length) {
      /* Whole-grid heatmap. Weakest first so the live cells paint on top. */
      const ordered = surface.slice().sort((a, b) => a.p - b.p);
      for (const tile of ordered) {
        const dest = targetBox(tile);
        if (!dest) continue;
        const style = surfaceStyle(tile);
        ctx.save();
        ctx.fillStyle = style.fill;
        ctx.strokeStyle = style.stroke;
        ctx.lineWidth = style.width;
        ctx.beginPath();
        ctx.rect(dest.x + 1, dest.y + 1, Math.max(2, dest.w - 2), Math.max(2, dest.h - 2));
        ctx.fill();
        ctx.stroke();
        if (dest.w > 34 && dest.h > 18) {
          ctx.globalAlpha = 0.92;
          ctx.fillStyle = "rgba(255,255,255,0.88)";
          ctx.font = "9px ui-sans-serif, system-ui, sans-serif";
          ctx.textBaseline = "top";
          ctx.fillText(Math.round(tile.p * 100) + "%", dest.x + 3, dest.y + 2);
          if (tile.mult && dest.h > 30) {
            ctx.fillStyle = tile.ev != null && tile.ev > 0
              ? "rgba(48,209,88,0.95)" : "rgba(255,255,255,0.55)";
            ctx.fillText(tile.mult.toFixed(1) + "x", dest.x + 3, dest.y + 12);
          }
        }
        ctx.restore();
        if (plan && tile.x === Number(plan.cell_x) && tile.y === Number(plan.cell_y)) {
          pickBox = paint({ x: tile.x, y: tile.y, side: tile.side, hint: "" }, "sel");
        }
      }
    } else {
      /* No surface yet (helper offline, or no ticks): the original nearby pair. */
      for (const tile of looking) {
        if (selected && tile.x === selected.x && tile.y === selected.y) continue;
        paint(tile, "look");
      }
      if (selected) pickBox = paint(selected, "sel");
    }
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
    const setupName = think.setup && think.setup !== "none" ? think.setup : "";
    const action = think.action || (selected ? "tap" : "sit");
    const standAside = standAsideFromThink(think);
    const setupLine = setupName ? (setupName + " · " + action) : "";
    const liveLine = standAside
      ? ((setupLine ? setupLine + " — " : "no trade — ") + (think.sit_reason || why || "standing aside"))
      : ((setupLine ? setupLine + " — " : "") + (why || lookingAt));
    const anchor = pickBox || (looking[0] && targetBox(looking[0]));
    const st = think.surface_stats;
    const vol = think.vol_bucket ? think.vol_bucket.toLowerCase() + " vol" : "";
    const gridLine = st
      ? st.quoted + "/" + st.cells + " quoted · " + st.tappable + " with edge" +
        (st.best_ev_lcb != null
          ? " · best EV " + (st.best_ev_lcb >= 0 ? "+" : "") + st.best_ev_lcb.toFixed(2)
          : "")
      : "";
    if (tfEl && line) {
      tfEl.textContent = [(think.tf_lean ? think.tf_lean : ""), line, vol, gridLine]
        .filter(Boolean).join(" · ");
      tfEl.style.display = "block";
      if (anchor) {
        tfEl.style.left = Math.min(cssW - 360, Math.max(6, anchor.x)) + "px";
        tfEl.style.top = Math.max(6, anchor.y - 44) + "px";
      } else {
        tfEl.style.left = "10px";
        tfEl.style.top = "10px";
      }
    }
    if (labelEl) {
      labelEl.textContent = standAside
        ? (setupLine || "no trade")
        : (setupLine ? setupLine + " · " + lookingAt : lookingAt);
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

    const baseMeta = {
      gridX: Math.floor(snap.now / snap.squareDuration),
      gridY: Math.floor(price / snap.dpl),
      price,
      dollars_per_line: snap.dpl,
      priceInterval: snap.dpl,
      square_duration: snap.squareDuration,
      now_ms: snap.now,
      cell_height: snap.dpl,
    };
    const gridBuilder = window.__euphoriaGridContext && window.__euphoriaGridContext.buildGridContext;
    const context = typeof gridBuilder === "function"
      ? gridBuilder({
          chart,
          quotesFeed,
          nowMs: snap.now,
          price,
          squareDuration: snap.squareDuration,
          dollarsPerLine: snap.dpl,
          history: pageHistory,
        })
      : { authoritative: false, reason: "grid context helper unavailable", cells: [] };
    // The NATS quote grid is the authority when it is fresh: it is the exact
    // board the house is pricing against, not our reconstruction of it.
    const fresh = quoteGrid && Date.now() - quoteGrid.received_at < 10000;
    const meta = fresh
      ? { ...baseMeta, ...context, ...quoteGrid, hook, discovery: discoveryReport() }
      : { ...baseMeta, ...context, hook, discovery: discoveryReport() };
    lastCaptureContext = meta;
    lastCaptureSnap = snap;
    const key = `${meta.gridX}:${meta.gridY}:${meta.dollars_per_line}`;
    const now = Date.now();
    /* Only publish canvas-derived geometry when the quote frame is NOT already
       doing it. Otherwise this emit -- which carries no cells when the quote
       grid has gone stale -- overwrites a perfectly good board with an empty
       one on the next frame. */
    if (!fresh && (key !== lastGridKey || now - lastGridEmit > 1000)) {
      lastGridKey = key;
      lastGridEmit = now;
      emit("grid", meta);
    }
  }

  window.addEventListener("message", (ev) => {
    const data = ev.data;
    if (!data || data.source !== SOURCE) return;
    if (data.type === "think") {
      lastThink = data.payload || null;
      if (data.quotes) lastQuotes = data.quotes;
    }
  });

  function emitChartQuote() {
    const gs = discoverChart();
    const price = livePrice(gs);
    const quote = sane(lastSymbol || "ETH", price);
    if (!quote) return;
    const now = Date.now();
    if (quote.price === lastChartQuotePrice && now - lastChartQuoteAt < 2000) return;
    quote.source = "page";
    lastChartQuotePrice = quote.price;
    lastChartQuoteAt = now;
    onQuotes([quote]);
  }

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

    /* The live canvas renders the selected price as a standalone "$1,880.08"
       with no nearby asset label. Bind only to a fresh control-room quote that
       is within 5%; this rejects balances, stakes, and unrelated dollar text. */
    const anchors = ["ETH", "BTC"]
      .map((symbol) => ({ symbol, price: quotePrice(lastQuotes, symbol) }))
      .filter((row) => row.price > 0);
    const dollarRe = /\$\s*([\d,]+(?:\.\d{1,4})?)/g;
    while ((m = dollarRe.exec(text))) {
      const price = Number(m[1].replace(/,/g, ""));
      if (!Number.isFinite(price) || price <= 0 || !anchors.length) continue;
      const nearest = anchors
        .map((row) => ({ ...row, distance: Math.abs(price - row.price) / row.price }))
        .sort((a, b) => a.distance - b.distance)[0];
      if (!nearest || nearest.distance > 0.05) continue;
      const q = sane(nearest.symbol, price);
      if (!q) continue;
      q.source = "dom";
      if (!quotes.some((row) => row.symbol === q.symbol && row.price === q.price)) quotes.push(q);
    }
    if (quotes.length) onQuotes(quotes);
  }

  let drawFails = 0;

  function loop() {
    // Reschedule first. If draw() throws and the next frame is only requested
    // afterwards, one bad frame silently kills the overlay for the rest of the
    // session -- it simply disappears, with nothing in the console to say why.
    rafId = window.requestAnimationFrame(loop);
    try {
      draw();
      drawFails = 0;
    } catch (e) {
      drawFails += 1;
      if (drawFails <= 3 || drawFails % 300 === 0) {
        console.warn("[euphoria] overlay draw failed:", e && e.message);
      }
    }
  }

  scanGlobals();
  setInterval(scanGlobals, 2000);
  setInterval(scrapeDom, 2000);
  setInterval(emitChartQuote, 500);
  setInterval(discoverChart, 2000);
  document.addEventListener("pointerup", observePlayerTap, true);
  discoverChart();
  if (!rafId) rafId = window.requestAnimationFrame(loop);
})();

/* Page-world hook. Listens to the tab's own prices / WS / user-gesture artefacts.
 * Does not open a second socket, does not mint signatures, does not touch Turnstile.
 */
(function () {
  if (window.__euphoriaBridgeInjected) return;
  window.__euphoriaBridgeInjected = true;
  const SOURCE = "__euphoria_bridge";

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
    if (quotes.length) emit("quotes", quotes);
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
      /* ignore non-JSON */
    }
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
    if (quotes.length) emit("quotes", quotes);
  }

  scanGlobals();
  setInterval(scanGlobals, 2000);
  setInterval(scrapeDom, 1000);
})();

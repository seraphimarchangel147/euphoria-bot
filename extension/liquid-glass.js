(function () {
  if (window.__eboLiquidGlass) return;
  window.__eboLiquidGlass = true;

  function displacementDataUrl(w, h) {
    w = Math.max(32, Math.round(w));
    h = Math.max(32, Math.round(h));
    const c = document.createElement("canvas");
    c.width = w;
    c.height = h;
    const ctx = c.getContext("2d");
    const img = ctx.createImageData(w, h);
    const data = img.data;
    const band = Math.max(20, Math.min(w, h) * 0.28);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4;
        const dx = x < w / 2 ? x : w - 1 - x;
        const dy = y < h / 2 ? y : h - 1 - y;
        const t = Math.pow(Math.max(0, 1 - Math.min(dx, dy) / band), 1.35);
        const nx = x < w / 2 ? -1 : 1;
        const ny = y < h / 2 ? -1 : 1;
        data[i] = 128 + nx * t * 127;
        data[i + 1] = 128 + ny * t * 127;
        data[i + 2] = 128;
        data[i + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
    return c.toDataURL("image/png");
  }

  function ensureFilter(id, w, h, scale) {
    let svg = document.getElementById(id + "-svg");
    if (!svg) {
      svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.id = id + "-svg";
      svg.setAttribute("width", "0");
      svg.setAttribute("height", "0");
      svg.style.position = "absolute";
      svg.innerHTML = "<filter id=\"" + id + "\" x=\"-8%\" y=\"-8%\" width=\"116%\" height=\"116%\" color-interpolation-filters=\"sRGB\"></filter>";
      document.body.appendChild(svg);
    }
    const filter = svg.querySelector("filter");
    const href = displacementDataUrl(w, h);
    const off = (scale * 0.045).toFixed(2);
    filter.innerHTML =
      "<feImage href=\"" + href + "\" x=\"0\" y=\"0\" width=\"100%\" height=\"100%\" preserveAspectRatio=\"none\" result=\"map\"></feImage>" +
      "<feDisplacementMap in=\"SourceGraphic\" in2=\"map\" scale=\"" + scale + "\" xChannelSelector=\"R\" yChannelSelector=\"G\" result=\"disp\"></feDisplacementMap>" +
      "<feColorMatrix in=\"disp\" type=\"matrix\" values=\"1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0\" result=\"r\"></feColorMatrix>" +
      "<feOffset in=\"r\" dx=\"" + off + "\" dy=\"0\" result=\"r2\"></feOffset>" +
      "<feColorMatrix in=\"disp\" type=\"matrix\" values=\"0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0\" result=\"g\"></feColorMatrix>" +
      "<feColorMatrix in=\"disp\" type=\"matrix\" values=\"0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0\" result=\"b\"></feColorMatrix>" +
      "<feOffset in=\"b\" dx=\"-" + off + "\" dy=\"0\" result=\"b2\"></feOffset>" +
      "<feBlend in=\"r2\" in2=\"g\" mode=\"screen\" result=\"rg\"></feBlend>" +
      "<feBlend in=\"rg\" in2=\"b2\" mode=\"screen\"></feBlend>";
  }

  function ensureLayer(el, cls, after) {
    let node = el.querySelector(":scope > ." + cls);
    if (node) return node;
    node = document.createElement("div");
    node.className = cls;
    node.setAttribute("aria-hidden", "true");
    if (after && after.parentNode === el) after.after(node);
    else el.insertBefore(node, el.firstChild);
    return node;
  }

  function wrapFront(el, skip) {
    let front = el.querySelector(":scope > .ebo-glass-front");
    if (front) return front;
    front = document.createElement("div");
    front.className = "ebo-glass-front";
    Array.from(el.childNodes).forEach(function (n) {
      if (skip.indexOf(n) !== -1) return;
      front.appendChild(n);
    });
    el.appendChild(front);
    return front;
  }

  function glassify(el, opt) {
    if (!el || el.dataset.eboGlass === "1") return;
    el.dataset.eboGlass = "1";
    const id = "ebo-glass-" + Math.random().toString(36).slice(2, 8);
    const scale = (opt && opt.scale) || 56;
    const blur = (opt && opt.blur) || 8;
    if (getComputedStyle(el).position === "static") el.style.position = "relative";
    el.style.isolation = "isolate";
    el.style.filter = "none";
    el.style.backdropFilter = "none";
    el.style.webkitBackdropFilter = "none";
    const back = ensureLayer(el, "ebo-glass-back");
    const shine = ensureLayer(el, "ebo-glass-shine", back);
    const front = wrapFront(el, [back, shine]);
    const apply = function () {
      const r = el.getBoundingClientRect();
      ensureFilter(id, r.width, r.height, scale);
      back.style.position = "absolute";
      back.style.inset = "0";
      back.style.borderRadius = "inherit";
      back.style.pointerEvents = "none";
      back.style.zIndex = "0";
      back.style.isolation = "isolate";
      back.style.filter = "none";
      const f = "blur(" + blur + "px) saturate(150%) url(#" + id + ")";
      back.style.backdropFilter = f;
      back.style.webkitBackdropFilter = f;
      shine.style.position = "absolute";
      shine.style.inset = "0";
      shine.style.borderRadius = "inherit";
      shine.style.pointerEvents = "none";
      shine.style.zIndex = "0";
      shine.style.filter = "none";
      shine.style.backdropFilter = "none";
      shine.style.webkitBackdropFilter = "none";
      front.style.position = "relative";
      front.style.zIndex = "1";
      front.style.isolation = "isolate";
      front.style.transform = "translateZ(0)";
      front.style.filter = "none";
      front.style.backdropFilter = "none";
      front.style.webkitBackdropFilter = "none";
    };
    apply();
    if (window.ResizeObserver) new ResizeObserver(apply).observe(el);
  }

  window.eboGlassify = glassify;
  window.eboGlassifyAll = function (sel) {
    document.querySelectorAll(sel).forEach(function (el) { glassify(el); });
  };
})();

(function () {
  if (window.__eboLiquidGlass) return;
  window.__eboLiquidGlass = true;

  function bgFromSaved() {
    try {
      var raw = (document.documentElement.style.getPropertyValue("--ebo-bg") ||
        (window.getComputedStyle && getComputedStyle(document.documentElement).getPropertyValue("--ebo-bg")) || "");
      var m = String(raw).match(/url\((['"]?)([^'")]+)\1\)/);
      if (m && m[2]) return m[2];
    } catch (e) {}
    try {
      var id = localStorage.getItem("ebo-bg");
      if (id && /^[a-z0-9][a-z0-9_-]*$/.test(id)) return "/bg/" + id + ".png";
    } catch (e) {}
    return "/bg/terrace.png";
  }
  var BG_URL = bgFromSaved();
  var RADIUS = 22.0;
  var MAX_DPR = 1.5;
  var REST_DEFAULT = { ior: 1.50, thick: 9.0, band: 22.0, lightZ: 44.0, spec: 0.38, shine: 0.12, ca: 1.20, frost: 0.0, tintR: 1.0, tintG: 1.0, tintB: 1.0, tintAmt: 0.0, fill: 0.12, fillR: 0.07, fillG: 0.08, fillB: 0.10 };
  var REST = { ior: 1.50, thick: 9.0, band: 22.0, lightZ: 44.0, spec: 0.38, shine: 0.12, ca: 1.20, frost: 0.0, tintR: 1.0, tintG: 1.0, tintB: 1.0, tintAmt: 0.0, fill: 0.12, fillR: 0.07, fillG: 0.08, fillB: 0.10 };
  var HOVER = { ior: 1.52, thick: 12.0, band: 26.0, lightZ: 32.0, spec: 0.46, shine: 0.16 };
  var IN_MS = 180;
  var OUT_MS = 280;
  var OVERSHOOT = 1.08;

  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var cards = [];
  var raf = 0;
  var dirty = false;
  var mouseX = 0;
  var mouseY = 0;
  var pointerBound = false;
  var bgImg = null;
  var bgFailed = false;
  var gpu = null;
  var canvas = null;
  var booting = false;
  var glLive = false;
  var glFailed = false;
  var pending = [];

  var VERT =
    "#ifdef GL_ES\nprecision mediump float;\n#endif\n" +
    "attribute vec2 aPos;" +
    "varying vec2 vWorld;" +
    "uniform vec2 uView;" +
    "void main(){" +
    "  vWorld=vec2((aPos.x*0.5+0.5)*uView.x,(0.5-aPos.y*0.5)*uView.y);" +
    "  gl_Position=vec4(aPos,0.0,1.0);" +
    "}";

  var FRAG =
    "#ifdef GL_ES\nprecision mediump float;\n#endif\n" +
    "varying vec2 vWorld;" +
    "uniform sampler2D uBg;" +
    "uniform vec2 uView;" +
    "uniform vec2 uImg;" +
    "uniform vec2 uCardPos;" +
    "uniform vec2 uCardSize;" +
    "uniform vec2 uMouse;" +
    "uniform float uIOR;" +
    "uniform float uThick;" +
    "uniform float uBand;" +
    "uniform float uLightZ;" +
    "uniform float uSpec;" +
    "uniform float uShine;" +
    "uniform float uCA;" +
    "uniform float uFrost;" +
    "uniform vec3 uTint;" +
    "uniform float uTintAmt;" +
    "uniform float uFill;" +
    "uniform vec3 uFillCol;" +
    "float sdRoundedBox(vec2 p,vec2 b,float r){" +
    "  vec2 q=abs(p)-b+vec2(r);" +
    "  return min(max(q.x,q.y),0.0)+length(max(q,0.0))-r;" +
    "}" +
    "vec2 coverUV(vec2 world){" +
    "  float scale=max(uView.x/max(uImg.x,1.0),uView.y/max(uImg.y,1.0));" +
    "  vec2 drawn=uImg*scale;" +
    "  vec2 offset=(uView-drawn)*0.5;" +
    "  return (world-offset)/drawn;" +
    "}" +
    "void main(){" +
    "  vec2 halfSize=uCardSize*0.5;" +
    "  vec2 cardCenter=uCardPos+halfSize;" +
    "  vec2 p=vWorld-cardCenter;" +
    "  float sd=sdRoundedBox(p,halfSize,22.0);" +
    "  if(sd>0.6) discard;" +
    "  float aa=1.0-smoothstep(-0.6,0.6,sd);" +
    "  float edge=pow(clamp(1.0+sd/uBand,0.0,1.0),1.15);" +
    "  float e=1.35;" +
    "  vec2 g=vec2(" +
    "    sdRoundedBox(p+vec2(e,0.0),halfSize,22.0)-sdRoundedBox(p-vec2(e,0.0),halfSize,22.0)," +
    "    sdRoundedBox(p+vec2(0.0,e),halfSize,22.0)-sdRoundedBox(p-vec2(0.0,e),halfSize,22.0)" +
    "  );" +
    "  float glen=length(g);" +
    "  vec2 n=g/max(glen,0.0001);" +
    "  float iorR=uIOR-0.015;" +
    "  float iorG=uIOR;" +
    "  float iorB=uIOR+0.015;" +
    "  float ca=uCA*edge*edge;" +
    "  float meniscus=6.0*(1.0-edge); float bend=uThick*(1.0-1.0/iorG)*edge*2.5+26.0*edge+meniscus;" +
    "  vec2 offR=n*(uThick*(1.0-1.0/iorR)*edge*2.5+26.0*edge+meniscus+ca);" +
    "  vec2 offG=n*bend;" +
    "  vec2 offB=n*(uThick*(1.0-1.0/iorB)*edge*2.5+26.0*edge+meniscus-ca);" +
    "  vec2 uvR=clamp(coverUV(vWorld+offR),0.0,1.0);" +
    "  vec2 uvG=clamp(coverUV(vWorld+offG),0.0,1.0);" +
    "  vec2 uvB=clamp(coverUV(vWorld+offB),0.0,1.0);" +
    "  vec3 col;" +
    "  col.r=texture2D(uBg,uvR).r;" +
    "  col.g=texture2D(uBg,uvG).g;" +
    "  col.b=texture2D(uBg,uvB).b;" +
    "  col=mix(col,vec3(0.88,0.92,1.0),uFrost*edge);" +
    "  col=mix(col,uTint,uTintAmt*edge);" +
    "  col=mix(col,uFillCol,uFill*(1.0-edge));" +
    "  float F0=0.04;" +
    "  float cosTheta=clamp(1.0-edge,0.0,1.0);" +
    "  float F=F0+(1.0-F0)*pow(1.0-cosTheta,5.0);" +
    "  col+=vec3(0.92,0.95,1.0)*F*0.40;" +
    "  vec2 vLocal=(vWorld-uCardPos)/max(uCardSize,vec2(1.0));" +
    "  float tl=pow(clamp(1.0-length((vLocal-vec2(0.18,0.08))/vec2(0.20,0.12)),0.0,1.0),3.4);" +
    "  col+=vec3(1.0)*tl*uShine;" +
    "  float bot=smoothstep(0.52,1.0,vLocal.y)*(0.16+0.10*edge);" +
    "  col*=(1.0-bot);" +
    "  float nTilt=mix(0.22,0.65,edge); vec3 N=normalize(vec3(n.x*nTilt,n.y*nTilt,1.0));" +
    "  vec3 L=normalize(vec3(uMouse.x-cardCenter.x,uMouse.y-cardCenter.y,uLightZ));" +
    "  vec3 V=vec3(0.0,0.0,1.0);" +
    "  vec3 H=normalize(L+V);" +
    "  float spec=pow(max(dot(N,H),0.0),28.0)*uSpec;" +
    "  col+=vec3(1.0,0.99,0.97)*spec;" +
    "  gl_FragColor=vec4(col*aa,aa);" +
    "}";

  function bezierEase(t) {
    if (t <= 0) return 0;
    if (t >= 1) return 1;
    var p1x = 0.22, p1y = 1.0, p2x = 0.36, p2y = 1.0;
    var x = t;
    var i;
    for (i = 0; i < 8; i++) {
      var cx = 3 * p1x;
      var bx = 3 * (p2x - p1x) - cx;
      var ax = 1 - cx - bx;
      var cur = ((ax * x + bx) * x + cx) * x;
      var dx = (3 * ax * x + 2 * bx) * x + cx;
      if (Math.abs(dx) < 1e-6) break;
      x -= (cur - t) / dx;
    }
    var cy = 3 * p1y;
    var by = 3 * (p2y - p1y) - cy;
    var ay = 1 - cy - by;
    return ((ay * x + by) * x + cy) * x;
  }

  function hoverInCurve(t) {
    var e = bezierEase(t);
    if (e < 0.85) return (e / 0.85) * OVERSHOOT;
    return OVERSHOOT + (1.0 - OVERSHOOT) * ((e - 0.85) / 0.15);
  }

  function mix(a, b, t) {
    return a + (b - a) * t;
  }

  function displacementDataUrl(w, h) {
    w = Math.max(32, Math.round(w));
    h = Math.max(32, Math.round(h));
    var c = document.createElement("canvas");
    c.width = w;
    c.height = h;
    var ctx = c.getContext("2d");
    var img = ctx.createImageData(w, h);
    var data = img.data;
    var band = Math.max(20, Math.min(w, h) * 0.28);
    var y, x, i, dx, dy, t, nx, ny;
    for (y = 0; y < h; y++) {
      for (x = 0; x < w; x++) {
        i = (y * w + x) * 4;
        dx = x < w / 2 ? x : w - 1 - x;
        dy = y < h / 2 ? y : h - 1 - y;
        t = Math.pow(Math.max(0, 1 - Math.min(dx, dy) / band), 1.35);
        nx = x < w / 2 ? -1 : 1;
        ny = y < h / 2 ? -1 : 1;
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
    var svg = document.getElementById(id + "-svg");
    if (!svg) {
      svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.id = id + "-svg";
      svg.setAttribute("width", "0");
      svg.setAttribute("height", "0");
      svg.style.position = "absolute";
      svg.innerHTML = "<filter id=\"" + id + "\" x=\"-8%\" y=\"-8%\" width=\"116%\" height=\"116%\" color-interpolation-filters=\"sRGB\"></filter>";
      document.body.appendChild(svg);
    }
    var filter = svg.querySelector("filter");
    var href = displacementDataUrl(w, h);
    var off = (scale * 0.045).toFixed(2);
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

  function styleHost(el) {
    if (getComputedStyle(el).position === "static") el.style.position = "relative";
    el.style.isolation = "isolate";
    el.style.filter = "none";
    el.style.backdropFilter = "none";
    el.style.webkitBackdropFilter = "none";
    el.style.overflow = "hidden";
  }

  function styleOverlay(node, z) {
    node.style.position = "absolute";
    node.style.inset = "0";
    node.style.borderRadius = "inherit";
    node.style.pointerEvents = "none";
    node.style.zIndex = String(z);
    node.style.filter = "none";
    node.style.backdropFilter = "none";
    node.style.webkitBackdropFilter = "none";
  }

  function styleFront(front) {
    front.style.position = "relative";
    front.style.zIndex = "1";
    front.style.isolation = "isolate";
    front.style.transform = "translateZ(0)";
    front.style.filter = "none";
    front.style.backdropFilter = "none";
    front.style.webkitBackdropFilter = "none";
    front.style.pointerEvents = "auto";
  }

  function wrapFront(el, skip) {
    var front = el.querySelector(":scope > .ebo-glass-front");
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

  function hideShine(el) {
    var shine = el.querySelector(":scope > .ebo-glass-shine");
    if (!shine) return;
    shine.textContent = "";
    shine.style.display = "none";
    shine.style.background = "none";
    shine.style.boxShadow = "none";
  }

  function applyFallback(el, opt) {
    var id = "ebo-glass-" + Math.random().toString(36).slice(2, 8);
    var scale = (opt && opt.scale) || 56;
    var blur = (opt && opt.blur) || 8;
    var back = el.querySelector(":scope > .ebo-glass-back");
    if (!back || back.tagName === "CANVAS") {
      var div = document.createElement("div");
      div.className = "ebo-glass-back";
      div.setAttribute("aria-hidden", "true");
      if (back) back.replaceWith(div);
      else el.insertBefore(div, el.firstChild);
      back = div;
    }
    var shine = el.querySelector(":scope > .ebo-glass-shine");
    if (!shine) {
      shine = document.createElement("div");
      shine.className = "ebo-glass-shine";
      shine.setAttribute("aria-hidden", "true");
      back.after(shine);
    }
    var front = el.querySelector(":scope > .ebo-glass-front");
    var apply = function () {
      var r = el.getBoundingClientRect();
      ensureFilter(id, r.width, r.height, scale);
      styleOverlay(back, 0);
      back.style.isolation = "isolate";
      var f = "blur(" + blur + "px) saturate(150%) url(#" + id + ")";
      back.style.backdropFilter = f;
      back.style.webkitBackdropFilter = f;
      styleOverlay(shine, 0);
      if (front) styleFront(front);
    };
    apply();
    if (window.ResizeObserver) new ResizeObserver(apply).observe(el);
  }

  function compile(gl, type, src) {
    var sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      console.warn("[ebo-glass] shader", type === gl.VERTEX_SHADER ? "vert" : "frag", gl.getShaderInfoLog(sh));
      gl.deleteShader(sh);
      return null;
    }
    return sh;
  }

  function makeProgram(gl) {
    var vs = compile(gl, gl.VERTEX_SHADER, VERT);
    var fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
    if (!vs || !fs) return null;
    var prog = gl.createProgram();
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.bindAttribLocation(prog, 0, "aPos");
    gl.linkProgram(prog);
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      console.warn("[ebo-glass] program", gl.getProgramInfoLog(prog));
      gl.deleteProgram(prog);
      return null;
    }
    return prog;
  }

  function uploadTexture(gl, img) {
    var tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, 0);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, 0);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
    return tex;
  }

  function loc(gl, prog, name) {
    return gl.getUniformLocation(prog, name);
  }

  function syncCanvasSize() {
    if (!canvas || !gpu) return;
    var dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
    var w = Math.max(1, Math.round(window.innerWidth * dpr));
    var h = Math.max(1, Math.round(window.innerHeight * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
      gpu.gl.viewport(0, 0, w, h);
    }
  }

  function initGL(img) {
    if (!document.body) return null;
    canvas = document.getElementById("ebo-glass-gl");
    if (!canvas) {
      canvas = document.createElement("canvas");
      canvas.id = "ebo-glass-gl";
      canvas.setAttribute("aria-hidden", "true");
      canvas.style.position = "fixed";
      canvas.style.inset = "0";
      canvas.style.width = "100%";
      canvas.style.height = "100%";
      canvas.style.pointerEvents = "none";
      canvas.style.zIndex = "0";
      canvas.style.display = "block";
      document.body.insertBefore(canvas, document.body.firstChild);
    }
    var opts = {
      alpha: true,
      premultipliedAlpha: true,
      antialias: false,
      depth: false,
      stencil: false,
      preserveDrawingBuffer: false,
      powerPreference: "low-power"
    };
    var gl = canvas.getContext("webgl", opts) || canvas.getContext("experimental-webgl", opts);
    if (!gl) return null;
    var prog = makeProgram(gl);
    if (!prog) return null;
    var buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
      -1, -1, 1, -1, -1, 1, 1, 1
    ]), gl.STATIC_DRAW);
    var tex = uploadTexture(gl, img);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.disable(gl.DEPTH_TEST);
    gl.clearColor(0, 0, 0, 0);
    gpu = {
      gl: gl,
      prog: prog,
      buf: buf,
      tex: tex,
      u: {
        uBg: loc(gl, prog, "uBg"),
        uView: loc(gl, prog, "uView"),
        uImg: loc(gl, prog, "uImg"),
        uCardPos: loc(gl, prog, "uCardPos"),
        uCardSize: loc(gl, prog, "uCardSize"),
        uMouse: loc(gl, prog, "uMouse"),
        uIOR: loc(gl, prog, "uIOR"),
        uThick: loc(gl, prog, "uThick"),
        uBand: loc(gl, prog, "uBand"),
        uLightZ: loc(gl, prog, "uLightZ"),
        uSpec: loc(gl, prog, "uSpec"),
        uShine: loc(gl, prog, "uShine"),
        uCA: loc(gl, prog, "uCA"),
        uFrost: loc(gl, prog, "uFrost"),
        uTint: loc(gl, prog, "uTint"),
        uTintAmt: loc(gl, prog, "uTintAmt"),
        uFill: loc(gl, prog, "uFill"),
        uFillCol: loc(gl, prog, "uFillCol")
      }
    };
    syncCanvasSize();
    return gpu;
  }

  function loadBg(done) {
    if (bgImg && bgImg.getAttribute("data-url") === BG_URL) return done(null, bgImg);
    if (bgFailed) return done(new Error("bg"));
    var img = new Image();
    img.setAttribute("data-url", BG_URL);
    img.onload = function () {
      bgImg = img;
      done(null, img);
    };
    img.onerror = function () {
      bgFailed = true;
      done(new Error("bg"));
    };
    img.src = BG_URL;
  }

  function setBackground(url) {
    if (!url) return;
    BG_URL = url;
    bgFailed = false;
    var img = new Image();
    img.setAttribute("data-url", url);
    img.onload = function () {
      bgImg = img;
      if (gpu && gpu.gl && gpu.tex) {
        try {
          var gl = gpu.gl;
          gl.bindTexture(gl.TEXTURE_2D, gpu.tex);
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
        } catch (e) { /* do not fail the engine on a bad swap */ }
        kick();
      } else if (!glLive && !glFailed) {
        bootEngine();
      }
    };
    img.onerror = function () { /* do not fail the engine on a bad swap */ };
    img.src = url;
  }

  function failEngine() {
    glFailed = true;
    glLive = false;
    if (canvas && canvas.parentNode) canvas.parentNode.removeChild(canvas);
    canvas = null;
    gpu = null;
    document.documentElement.classList.remove("ebo-gl-live");
    var i;
    for (i = 0; i < pending.length; i++) pending[i](false);
    pending = [];
  }

  function liveEngine() {
    glLive = true;
    document.documentElement.classList.add("ebo-gl-live");
    var i, el, back;
    for (i = 0; i < cards.length; i++) {
      el = cards[i].el;
      back = el.querySelector(":scope > .ebo-glass-back");
      if (back) back.remove();
      hideShine(el);
      el.style.backdropFilter = "none";
      el.style.webkitBackdropFilter = "none";
    }
    var j;
    for (j = 0; j < pending.length; j++) pending[j](true);
    pending = [];
    bindPointer();
    kick();
  }

  function bootEngine() {
    if (glLive || glFailed) return;
    if (booting) return;
    booting = true;
    loadBg(function (err, img) {
      if (err) {
        failEngine();
        return;
      }
      if (!initGL(img)) {
        failEngine();
        return;
      }
      liveEngine();
    });
  }

  function sampleHover(card, now) {
    if (!card.animating) return card.hover;
    var t = (now - card.start) / card.dur;
    if (t >= 1) {
      card.animating = false;
      card.hover = card.incoming ? 1 : 0;
      return card.hover;
    }
    if (t < 0) t = 0;
    if (card.incoming) {
      var curve = hoverInCurve(t);
      card.hover = card.from + (curve - 0) * (1 - card.from);
    } else {
      card.hover = card.from * (1 - bezierEase(t));
    }
    return card.hover;
  }

  function startHover(card, incoming) {
    if (reduced) {
      card.hover = incoming ? 1 : 0;
      card.animating = false;
      return;
    }
    card.from = card.hover;
    card.incoming = incoming;
    card.start = performance.now();
    card.dur = incoming ? IN_MS : OUT_MS;
    card.animating = true;
  }

  function uniformsFor(hover) {
    var t = hover;
    return {
      ior: mix(REST.ior, HOVER.ior, t),
      thick: mix(REST.thick, HOVER.thick, t),
      band: mix(REST.band, HOVER.band, t),
      lightZ: mix(REST.lightZ, HOVER.lightZ, t),
      spec: mix(REST.spec, HOVER.spec, t),
      shine: mix(REST.shine, HOVER.shine, t),
      ca: REST.ca,
      frost: REST.frost,
      tintR: REST.tintR,
      tintG: REST.tintG,
      tintB: REST.tintB,
      tintAmt: REST.tintAmt,
      fill: REST.fill,
      fillR: REST.fillR,
      fillG: REST.fillG,
      fillB: REST.fillB
    };
  }

  function render(now) {
    if (!gpu || !bgImg || !glLive) return;
    var gl = gpu.gl;
    if (gl.isContextLost && gl.isContextLost()) return;
    syncCanvasSize();
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(gpu.prog);
    gl.bindBuffer(gl.ARRAY_BUFFER, gpu.buf);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, gpu.tex);
    gl.uniform1i(gpu.u.uBg, 0);
    gl.uniform2f(gpu.u.uView, window.innerWidth, window.innerHeight);
    gl.uniform2f(gpu.u.uImg, bgImg.naturalWidth || bgImg.width, bgImg.naturalHeight || bgImg.height);
    gl.uniform2f(gpu.u.uMouse, mouseX, mouseY);
    var i, card, r, u, hit = null;
    var rects = new Array(cards.length);
    for (i = 0; i < cards.length; i++) {
      r = cards[i].el.getBoundingClientRect();
      rects[i] = r;
      if (r.width < 1 || r.height < 1) continue;
      if (hit == null && mouseX >= r.left && mouseX <= r.right && mouseY >= r.top && mouseY <= r.bottom) hit = cards[i];
    }
    if (!reduced) setHoverHits(hit);
    var dprX = canvas.width / Math.max(1, window.innerWidth);
    var dprY = canvas.height / Math.max(1, window.innerHeight);
    var vw = canvas.width;
    var vh = canvas.height;
    var sx, sy, sw, sh;
    for (i = 0; i < cards.length; i++) {
      card = cards[i];
      sampleHover(card, now);
      r = rects[i];
      if (r.width < 1 || r.height < 1) continue;
      u = uniformsFor(card.hover);
      gl.uniform2f(gpu.u.uCardPos, r.left, r.top);
      gl.uniform2f(gpu.u.uCardSize, r.width, r.height);
      gl.uniform1f(gpu.u.uIOR, u.ior);
      gl.uniform1f(gpu.u.uThick, u.thick);
      gl.uniform1f(gpu.u.uBand, u.band);
      gl.uniform1f(gpu.u.uLightZ, u.lightZ);
      gl.uniform1f(gpu.u.uSpec, reduced ? 0 : u.spec);
      gl.uniform1f(gpu.u.uShine, u.shine);
      gl.uniform1f(gpu.u.uCA, u.ca);
      gl.uniform1f(gpu.u.uFrost, u.frost);
      gl.uniform3f(gpu.u.uTint, u.tintR, u.tintG, u.tintB);
      gl.uniform1f(gpu.u.uTintAmt, u.tintAmt);
      gl.uniform1f(gpu.u.uFill, u.fill != null ? u.fill : 0.12);
      gl.uniform3f(gpu.u.uFillCol, u.fillR != null ? u.fillR : 0.07, u.fillG != null ? u.fillG : 0.08, u.fillB != null ? u.fillB : 0.10);
      sx = Math.floor(r.left * dprX);
      sy = Math.floor((window.innerHeight - r.bottom) * dprY);
      sw = Math.ceil(r.width * dprX) + 1;
      sh = Math.ceil(r.height * dprY) + 1;
      if (sx < 0) { sw += sx; sx = 0; }
      if (sy < 0) { sh += sy; sy = 0; }
      if (sx + sw > vw) sw = vw - sx;
      if (sy + sh > vh) sh = vh - sy;
      if (sw < 1 || sh < 1) continue;
      gl.enable(gl.SCISSOR_TEST);
      gl.scissor(sx, sy, sw, sh);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    }
    gl.disable(gl.SCISSOR_TEST);
  }

  function loop(now) {
    raf = 0;
    if (document.hidden) {
      dirty = false;
      return;
    }
    dirty = false;
    render(now);
    var i, keep = false;
    for (i = 0; i < cards.length; i++) {
      if (cards[i].animating) { keep = true; break; }
    }
    if (keep || dirty) raf = requestAnimationFrame(loop);
  }

  function kick() {
    dirty = true;
    if (document.hidden) return;
    if (!raf && glLive) raf = requestAnimationFrame(loop);
  }

  function setHoverHits(hit) {
    var i, c, want;
    for (i = 0; i < cards.length; i++) {
      c = cards[i];
      want = c === hit;
      if (want === c.hovered) continue;
      c.hovered = want;
      startHover(c, want);
    }
  }

  function onPointer(ev) {
    mouseX = ev.clientX;
    mouseY = ev.clientY;
    kick();
  }

  function bindPointer() {
    if (pointerBound) return;
    pointerBound = true;
    document.addEventListener("pointermove", onPointer, { passive: true });
    document.addEventListener("pointerleave", function () {
      mouseX = -1e6;
      mouseY = -1e6;
      kick();
    }, { passive: true });
  }

  function glassify(el, opt) {
    if (!el || el.dataset.eboGlass === "1") return;
    el.dataset.eboGlass = "1";
    styleHost(el);
    var shine = el.querySelector(":scope > .ebo-glass-shine");
    var back = el.querySelector(":scope > .ebo-glass-back");
    var skip = [];
    if (shine) skip.push(shine);
    if (back) skip.push(back);
    var front = wrapFront(el, skip);
    styleFront(front);
    var card = {
      el: el,
      hover: 0,
      from: 0,
      incoming: false,
      hovered: false,
      start: 0,
      dur: IN_MS,
      animating: false,
      opt: opt || {}
    };
    cards.push(card);
    if (glFailed) {
      applyFallback(el, opt);
      return;
    }
    pending.push(function (ok) {
      if (!ok) applyFallback(el, opt);
      else {
        var leftover = el.querySelector(":scope > .ebo-glass-back");
        if (leftover) leftover.remove();
        hideShine(el);
      }
    });
    if (glLive) {
      if (back) back.remove();
      hideShine(el);
      pending.pop();
      kick();
      return;
    }
    bootEngine();
  }

  window.addEventListener("resize", function () {
    if (!glLive) return;
    syncCanvasSize();
    kick();
  });

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      if (raf) {
        cancelAnimationFrame(raf);
        raf = 0;
      }
      dirty = false;
      return;
    }
    kick();
  });

  if (window.ResizeObserver) {
    new ResizeObserver(function () {
      if (glLive) kick();
    }).observe(document.documentElement);
  }

  function syncHoverFromRest() {
    HOVER.ior = REST.ior + 0.02;
    HOVER.thick = REST.thick + 8.0;
    HOVER.band = REST.band + 8.0;
    HOVER.spec = REST.spec + 0.16;
    HOVER.lightZ = 28.0;
    HOVER.shine = REST.shine + 0.08;
  }

  function setGlass(opts) {
    opts = opts || {};
    var d = REST_DEFAULT;
    REST.ior = opts.ior != null ? +opts.ior : d.ior;
    REST.thick = opts.thick != null ? +opts.thick : d.thick;
    REST.band = opts.band != null ? +opts.band : d.band;
    REST.lightZ = opts.lightZ != null ? +opts.lightZ : d.lightZ;
    REST.spec = opts.spec != null ? +opts.spec : d.spec;
    REST.shine = opts.shine != null ? +opts.shine : d.shine;
    REST.ca = opts.ca != null ? +opts.ca : d.ca;
    REST.frost = opts.frost != null ? +opts.frost : d.frost;
    var tint = opts.tint;
    if (tint && tint.length >= 3) {
      REST.tintR = +tint[0];
      REST.tintG = +tint[1];
      REST.tintB = +tint[2];
    } else {
      REST.tintR = d.tintR;
      REST.tintG = d.tintG;
      REST.tintB = d.tintB;
    }
    REST.tintAmt = opts.tintAmt != null ? +opts.tintAmt : d.tintAmt;
    REST.fill = opts.fill != null ? +opts.fill : d.fill;
    var fillCol = opts.fillCol;
    if (fillCol && fillCol.length >= 3) {
      REST.fillR = +fillCol[0];
      REST.fillG = +fillCol[1];
      REST.fillB = +fillCol[2];
    } else {
      REST.fillR = d.fillR;
      REST.fillG = d.fillG;
      REST.fillB = d.fillB;
    }
    syncHoverFromRest();
    kick();
  }

  window.eboGlassify = glassify;
  window.eboGlassifyAll = function (sel) {
    document.querySelectorAll(sel).forEach(function (el) { glassify(el); });
  };
  window.eboSetBackground = setBackground;
  window.eboSetGlass = setGlass;
})();

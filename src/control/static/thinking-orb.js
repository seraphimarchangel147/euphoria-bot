/* thinking-orbs engine (MIT, Jakub Antalik) + Euphoria state map.
   Vanilla mount — no React. Dashboard only.
   Engine is loaded dynamically so a 404 still leaves a visible dotted sphere. */

const LABELS = {
  working: "Working…",
  searching: "Searching…",
  solving: "Solving…",
  listening: "Listening…",
  connecting: "Connecting…",
  weaving: "Weaving…",
  composing: "Composing…",
  breathing: "Thinking…",
  shaping: "Shaping…",
};

const STATES = Object.keys(LABELS);
const FACE_STATES = new Set(["breathing", "connecting"]);

let MODE_DRAWS = null;
let resolvePreset = null;

export function orbStateFromThink(t) {
  if (!t) return "connecting";
  const last = t.grade && (t.grade.last || t.grade);
  const outcome = last && last.outcome;
  const rem = t.tape && Number(t.tape.window_remaining_s);
  const justClosed = Number.isFinite(rem) && rem < 0.45;
  if (justClosed && outcome === "hit") return "solving";
  if (justClosed && outcome === "miss") return "shaping";
  if (justClosed && (outcome === "sit" || t.stand_aside)) return "listening";

  const sit = String(t.sit_reason || t.lesson || t.setup || t.action || "").toLowerCase();
  if (t.stand_aside) {
    if (sit.includes("compress")) return "weaving";
    if (sit.includes("late") || sit.includes("pink")) return "composing";
    if (sit.includes("chop") || sit.includes("fade") || sit.includes("quiet")) return "breathing";
    return "listening";
  }
  if (t.pick && (t.pick.cell || t.pick.label || t.pick.side)) return "solving";
  if ((t.candidates && t.candidates.length) || (t.looking_at && t.looking_at.length)) return "searching";
  if (sit.includes("compress")) return "weaving";
  if (t.tf_lean && t.alignment && t.alignment !== "unknown") return "working";
  return "breathing";
}

function applyCanvasBox(canvas, displaySize, drawSize) {
  const dpr = Math.min(2, (typeof devicePixelRatio !== "undefined" && devicePixelRatio) || 1);
  canvas.width = Math.round(displaySize * dpr);
  canvas.height = Math.round(displaySize * dpr);
  canvas.style.width = displaySize + "px";
  canvas.style.height = displaySize + "px";
  const ctx = canvas.getContext("2d");
  return { ctx, dpr, drawSize, displaySize };
}

function mountEngineOrb(canvas, opts) {
  const o = Object.assign({ state: "breathing", size: 80, dark: true, speed: 1 }, opts || {});
  let state = STATES.includes(o.state) ? o.state : "breathing";
  const displaySize = o.size === 20 ? 20 : 80;
  const drawSize = displaySize === 20 ? 20 : 64;
  let dark = !!o.dark;
  let speed = Number(o.speed) || 1;
  let paused = false;
  let raf = 0;
  let running = false;
  const box = applyCanvasBox(canvas, displaySize, drawSize);
  let ctx = box.ctx;

  const frame = (tSec) => {
    if (!ctx || !MODE_DRAWS || !resolvePreset) return;
    const { mode, speed: baseSpeed, opts: modeOpts } = resolvePreset(state, drawSize);
    const draw = MODE_DRAWS[mode];
    const dpr = Math.min(2, (typeof devicePixelRatio !== "undefined" && devicePixelRatio) || 1);
    ctx.setTransform(dpr * (displaySize / drawSize), 0, 0, dpr * (displaySize / drawSize), 0, 0);
    ctx.clearRect(0, 0, drawSize, drawSize);
    draw(ctx, drawSize, tSec, dark, modeOpts);
  };

  const loop = () => {
    const { speed: baseSpeed } = resolvePreset(state, drawSize);
    frame((performance.now() / 1000) * baseSpeed * speed);
    if (running) raf = requestAnimationFrame(loop);
  };

  let faceOff = false;
  let visible = true;
  const start = () => {
    if (running || paused || faceOff) return;
    running = true;
    raf = requestAnimationFrame(loop);
  };
  const stop = () => {
    running = false;
    cancelAnimationFrame(raf);
    raf = 0;
  };
  const syncFace = () => {
    const agent = document.getElementById("eboAgent");
    faceOff = !!(agent && agent.classList.contains("face"));
    if (faceOff) stop();
    else if (visible && document.visibilityState !== "hidden") start();
  };

  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", LABELS[state] || "Thinking…");
  frame(0.6);

  const io =
    typeof IntersectionObserver !== "undefined"
      ? new IntersectionObserver(([entry]) => {
          visible = entry.isIntersecting;
          if (visible && !faceOff && document.visibilityState !== "hidden") start();
          else stop();
        })
      : null;
  io && io.observe(canvas);
  const onVis = () => {
    if (document.visibilityState === "hidden") stop();
    else if (visible && !faceOff) start();
  };
  document.addEventListener("visibilitychange", onVis);
  const agentEl = document.getElementById("eboAgent");
  let mo = null;
  if (agentEl && typeof MutationObserver !== "undefined") {
    mo = new MutationObserver(syncFace);
    mo.observe(agentEl, { attributes: true, attributeFilter: ["class"] });
  }
  syncFace();
  if (!io && !faceOff) start();

  return {
    setState(next) {
      if (!STATES.includes(next) || next === state) return;
      state = next;
      canvas.setAttribute("aria-label", LABELS[state] || "Thinking…");
    },
    setDark(v) {
      dark = !!v;
    },
    start() {
      faceOff = false;
      if (visible && document.visibilityState !== "hidden") start();
    },
    stop() {
      faceOff = true;
      stop();
    },
    destroy() {
      stop();
      io && io.disconnect();
      mo && mo.disconnect();
      document.removeEventListener("visibilitychange", onVis);
    },
  };
}

function mountFallbackOrb(canvas, opts) {
  const o = Object.assign({ state: "breathing", size: 80, dark: true, speed: 1 }, opts || {});
  let state = STATES.includes(o.state) ? o.state : "breathing";
  const size = o.size === 20 ? 20 : 80;
  let speed = Number(o.speed) || 1;
  let raf = 0;
  let running = false;
  const { ctx, dpr } = applyCanvasBox(canvas, size, size);
  const N = 56;
  const pts = [];
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < N; i++) {
    const y = 1 - ((i + 0.5) / N) * 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const th = i * golden;
    pts.push({ x: r * Math.cos(th), y, z: r * Math.sin(th) });
  }

  const frame = (tSec) => {
    if (!ctx) return;
    const rot = tSec * 0.85 * speed;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size, size);
    ctx.fillStyle = "#111";
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, size / 2, 0, Math.PI * 2);
    ctx.fill();
    const R = size * 0.36;
    for (const p of pts) {
      const x = p.x * Math.cos(rot) - p.z * Math.sin(rot);
      const z = p.x * Math.sin(rot) + p.z * Math.cos(rot);
      const depth = (z + 1) / 2;
      ctx.fillStyle = "rgba(255,255,255," + (0.22 + 0.78 * depth) + ")";
      ctx.beginPath();
      ctx.arc(size / 2 + x * R, size / 2 + p.y * R, 1.15 + 1.7 * depth, 0, Math.PI * 2);
      ctx.fill();
    }
  };

  const loop = () => {
    frame((performance.now() / 1000) * speed);
    if (running) raf = requestAnimationFrame(loop);
  };
  let faceOff = false;
  const start = () => {
    if (running || faceOff) return;
    running = true;
    raf = requestAnimationFrame(loop);
  };
  const stop = () => {
    running = false;
    cancelAnimationFrame(raf);
    raf = 0;
  };
  const syncFace = () => {
    const agent = document.getElementById("eboAgent");
    faceOff = !!(agent && agent.classList.contains("face"));
    if (faceOff) stop();
    else start();
  };

  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", LABELS[state] || "Thinking…");
  frame(0.6);
  const agentEl = document.getElementById("eboAgent");
  let mo = null;
  if (agentEl && typeof MutationObserver !== "undefined") {
    mo = new MutationObserver(syncFace);
    mo.observe(agentEl, { attributes: true, attributeFilter: ["class"] });
  }
  syncFace();
  if (!faceOff) start();

  return {
    setState(next) {
      if (!STATES.includes(next) || next === state) return;
      state = next;
      canvas.setAttribute("aria-label", LABELS[state] || "Thinking…");
    },
    setDark() {},
    start() { faceOff = false; start(); },
    stop() { faceOff = true; stop(); },
    destroy() {
      stop();
      mo && mo.disconnect();
    },
  };
}

export function mountThinkingOrb(canvas, opts) {
  if (MODE_DRAWS && resolvePreset) return mountEngineOrb(canvas, opts);
  return mountFallbackOrb(canvas, opts);
}

export function isFaceState(state) {
  return FACE_STATES.has(state);
}

async function bootOrb() {
  try {
    const eng = await import("./engine.es.js");
    MODE_DRAWS = eng.MODE_DRAWS;
    resolvePreset = eng.resolvePreset;
  } catch (err) {
    MODE_DRAWS = null;
    resolvePreset = null;
  }
  const el = document.getElementById("eboOrb");
  if (!el) return;
  if (window.eboOrb && window.eboOrb.destroy) {
    try { window.eboOrb.destroy(); } catch (e) {}
  }
  window.eboOrb = mountThinkingOrb(el, { size: 80, dark: true });
}

if (typeof window !== "undefined") {
  window.eboOrbStateFromThink = orbStateFromThink;
  window.eboMountThinkingOrb = mountThinkingOrb;
  window.eboOrbIsFaceState = isFaceState;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => { bootOrb(); });
  else bootOrb();
}

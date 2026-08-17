(function () {
  if (window.eboMotion) return;
  const ease = "cubic-bezier(0.22, 1, 0.36, 1)";
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  function run(el, frames, opts) {
    if (!el || reduced || !el.animate) return null;
    return el.animate(frames, Object.assign({ duration: 480, easing: ease, fill: "both" }, opts));
  }
  window.eboMotion = {
    enter: function (sel) {
      document.querySelectorAll(sel).forEach(function (el, i) {
        run(el, [
          { opacity: 0, transform: "translateY(12px) scale(0.984)" },
          { opacity: 1, transform: "translateY(0) scale(1)" }
        ], { delay: 30 + i * 52, duration: 560 });
      });
    },
    flash: function (el) {
      return run(el, [
        { opacity: 0.45, transform: "translateY(3px)" },
        { opacity: 1, transform: "translateY(0)" }
      ], { duration: 320 });
    },
    press: function (root) {
      if (!root || reduced) return;
      var held = null;
      function down(ev) {
        const btn = ev.target.closest("button");
        if (!btn || !root.contains(btn)) return;
        held = btn;
        run(btn, [
          { transform: "scale(1)" },
          { transform: "scale(0.96)" }
        ], { duration: 90, fill: "forwards" });
      }
      function up() {
        if (!held) return;
        var btn = held;
        held = null;
        run(btn, [
          { transform: "scale(0.96)" },
          { transform: "scale(1)" }
        ], { duration: 280 });
      }
      root.addEventListener("pointerdown", down);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", up);
    }
  };
})();

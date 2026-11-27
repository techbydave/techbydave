/* TechByDave holiday easter eggs (shared by techbydave.com and the dashboard).
   On the day (Toronto time) it plays once per visit:
     Christmas  Dec 24-26   snow
     New Year   Dec 31-Jan 1 fireworks
     Easter     Easter Sunday  3 hidden eggs to find on each page
   Secret words, any day of the year: type "snow", "boom" or "eggs".
   Preview a holiday with ?holiday=christmas, ?holiday=newyear or ?holiday=easter */
(function () {
  if (window.__tbdHolidays) return;
  window.__tbdHolidays = true;

  var reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  var pad = function (n) { return (n < 10 ? "0" : "") + n; };

  function todayISO() {
    try { return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Toronto", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date()); }
    catch (e) { var d = new Date(); return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
  }

  // Easter Sunday (Gregorian calendar), as YYYY-MM-DD
  function easter(y) {
    var a = y % 19, b = Math.floor(y / 100), c = y % 100, d = Math.floor(b / 4), e = b % 4,
      f = Math.floor((b + 8) / 25), g = Math.floor((b - f + 1) / 3), h = (19 * a + b - d - g + 15) % 30,
      i = Math.floor(c / 4), k = c % 4, l = (32 + 2 * e + 2 * i - h - k) % 7, m = Math.floor((a + 11 * h + 22 * l) / 451),
      month = Math.floor((h + l - 7 * m + 114) / 31), day = ((h + l - 7 * m + 114) % 31) + 1;
    return y + "-" + pad(month) + "-" + pad(day);
  }

  function holidayOn(iso) {
    var md = iso.slice(5);
    if (md === "12-24" || md === "12-25" || md === "12-26") return "christmas";
    if (md === "12-31" || md === "01-01") return "newyear";
    if (iso === easter(+iso.slice(0, 4))) return "easter";
    return null;
  }

  /* ---------- styles ---------- */
  var css = ".tbd-fx{position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:2147483000}" +
    ".tbd-toast{position:fixed;left:0;right:0;margin:0 auto;width:fit-content;bottom:20px;z-index:2147483001;display:flex;gap:8px;align-items:center;max-width:min(92vw,460px);" +
    "padding:10px 8px 10px 16px;border-radius:14px;background:rgba(10,14,28,.95);color:#fff;font:500 14px/1.45 var(--display,system-ui),system-ui,sans-serif;" +
    "box-shadow:0 12px 32px rgba(0,0,0,.35);opacity:0;transform:translateY(16px);transition:opacity .3s,transform .3s;pointer-events:none}" +
    ".tbd-toast.show{opacity:1;transform:none;pointer-events:auto}" +
    ".tbd-toast b{color:#ffcf6e}" +
    ".tbd-toast button{all:unset;cursor:pointer;flex:none;width:30px;height:30px;display:grid;place-items:center;border-radius:8px;font-size:20px;line-height:1}" +
    ".tbd-toast button:hover,.tbd-toast button:focus-visible{background:rgba(255,255,255,.14)}" +
    ".tbd-egg{position:absolute;z-index:2147482999;width:34px;height:44px;margin:0;padding:0;border:0;background:none;cursor:pointer;" +
    "filter:drop-shadow(0 3px 4px rgba(0,0,0,.3));animation:tbd-wobble 2.6s ease-in-out infinite}" +
    ".tbd-egg svg{display:block;width:100%;height:100%}" +
    ".tbd-egg:focus-visible{outline:2px solid #ffcf6e;outline-offset:3px;border-radius:50%}" +
    ".tbd-egg.found{animation:tbd-pop .4s ease-out forwards;pointer-events:none}" +
    "@keyframes tbd-wobble{0%,100%{transform:rotate(-9deg)}50%{transform:rotate(9deg)}}" +
    "@keyframes tbd-pop{to{transform:scale(1.9) rotate(0);opacity:0}}" +
    "@media (prefers-reduced-motion:reduce){.tbd-egg{animation:none}.tbd-egg.found{animation:none;opacity:0}.tbd-toast{transition:none}}";
  var styled = false;
  function addStyles() {
    if (styled) return; styled = true;
    var s = document.createElement("style"); s.textContent = css; document.head.appendChild(s);
  }

  /* ---------- greeting toast ---------- */
  var toastEl, toastTimer;
  function hideToast() { if (toastEl) toastEl.classList.remove("show"); }
  function toast(html, ms) {
    addStyles();
    if (!toastEl) {
      toastEl = document.createElement("div");
      toastEl.className = "tbd-toast"; toastEl.setAttribute("role", "status"); toastEl.setAttribute("aria-live", "polite");
      document.body.appendChild(toastEl);
    }
    toastEl.innerHTML = "<span>" + html + "</span><button type=\"button\" aria-label=\"Close\">&times;</button>";
    toastEl.querySelector("button").addEventListener("click", hideToast);
    requestAnimationFrame(function () { toastEl.classList.add("show"); });
    clearTimeout(toastTimer); toastTimer = setTimeout(hideToast, ms || 7000);
  }

  /* ---------- canvas effects: snow, fireworks, confetti ---------- */
  var cv, ctx, W, H, running = false, flakes = [], rockets = [], parts = [], snowUntil = 0, fwUntil = 0, nextRocket = 0;
  var COLORS = ["#ff7a45", "#ffcf6e", "#4fc3f7", "#3ddc97", "#ff6b6b", "#b388ff", "#ffffff"];

  function resize() {
    if (!cv) return;
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = innerWidth; H = innerHeight;
    cv.width = W * dpr; cv.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function canvas() {
    if (cv) return;
    addStyles();
    cv = document.createElement("canvas"); cv.className = "tbd-fx"; cv.setAttribute("aria-hidden", "true");
    ctx = cv.getContext("2d"); document.body.appendChild(cv); resize();
    addEventListener("resize", resize);
  }
  function run() { if (!running) { running = true; requestAnimationFrame(tick); } }
  function light() { return document.documentElement.getAttribute("data-theme") === "light"; }

  function snow(ms) {
    if (reduce) return;
    canvas();
    var n = Math.round(Math.min(160, W * H / 9000));
    flakes = [];
    for (var i = 0; i < n; i++) flakes.push({ x: Math.random() * W, y: Math.random() * -H, r: 1.2 + Math.random() * 2.8,
      vy: .6 + Math.random() * 1.4, p: Math.random() * 6.3, s: .01 + Math.random() * .02 });
    snowUntil = performance.now() + (ms || 9000);
    run();
  }

  function burst(x, y, n, speed, colors) {
    canvas();
    for (var i = 0; i < n; i++) {
      var a = Math.random() * Math.PI * 2, v = speed * (.35 + Math.random() * .65);
      parts.push({ x: x, y: y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, life: 1, d: .012 + Math.random() * .012,
        c: colors[Math.floor(Math.random() * colors.length)], r: 1.8 + Math.random() * 2 });
    }
    run();
  }

  function fireworks(ms) {
    if (reduce) return;
    canvas();
    fwUntil = performance.now() + (ms || 6000);
    nextRocket = 0;
    run();
  }

  function tick() {
    var now = performance.now();
    ctx.clearRect(0, 0, W, H);

    if (flakes.length) {
      var left = snowUntil - now, fade = left < 1800 ? Math.max(0, left / 1800) : 1;
      ctx.fillStyle = light() ? "rgba(120,150,200," + (.85 * fade) + ")" : "rgba(255,255,255," + (.9 * fade) + ")";
      ctx.beginPath();
      for (var i = 0; i < flakes.length; i++) {
        var f = flakes[i];
        f.p += f.s; f.y += f.vy; f.x += Math.sin(f.p) * .6;
        if (f.y > H + 5) { f.y = -5; f.x = Math.random() * W; }
        ctx.moveTo(f.x + f.r, f.y); ctx.arc(f.x, f.y, f.r, 0, 6.283);
      }
      ctx.fill();
      if (left <= 0) flakes = [];
    }

    if (now < fwUntil && now > nextRocket) {
      var ty = H * (.12 + Math.random() * .3);
      rockets.push({ x: W * (.12 + Math.random() * .76), y: H, vy: -(H - ty) * .04, ty: ty,
        c: [COLORS[Math.floor(Math.random() * 6)], COLORS[Math.floor(Math.random() * 6)]] });
      nextRocket = now + 220 + Math.random() * 320;
    }
    for (var j = rockets.length - 1; j >= 0; j--) {
      var r = rockets[j];
      r.y += r.vy; r.vy *= .985;
      ctx.fillStyle = "#ffe8b0"; ctx.beginPath(); ctx.arc(r.x, r.y, 2.2, 0, 6.283); ctx.fill();
      if (r.y <= r.ty || r.vy > -1.5) { rockets.splice(j, 1); burst(r.x, r.y, 90, 6.5, r.c.concat("#ffffff")); }
    }

    for (var k = parts.length - 1; k >= 0; k--) {
      var p = parts[k];
      p.vx *= .975; p.vy = p.vy * .975 + .06; p.x += p.vx; p.y += p.vy; p.life -= p.d;
      if (p.life <= 0) { parts.splice(k, 1); continue; }
      ctx.globalAlpha = p.life; ctx.fillStyle = p.c;
      ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 6.283); ctx.fill();
    }
    ctx.globalAlpha = 1;

    if (flakes.length || rockets.length || parts.length || now < fwUntil) requestAnimationFrame(tick);
    else { running = false; removeEventListener("resize", resize); cv.remove(); cv = null; }
  }

  /* ---------- Easter egg hunt ---------- */
  var EGG_COLORS = [["#ff9ec4", "#ffd166", "#ffffff"], ["#8fd3ff", "#b388ff", "#ffffff"], ["#9be89b", "#ff9e6b", "#ffffff"], ["#ffd166", "#ff6b6b", "#ffffff"]];
  function eggSVG(c) {
    return "<svg viewBox=\"0 0 40 52\" aria-hidden=\"true\"><path d=\"M20 2C9 2 2 20 2 32c0 10 8 18 18 18s18-8 18-18C38 20 31 2 20 2z\" fill=\"" + c[0] + "\"/>" +
      "<path d=\"M2.4 27.5Q11 22 20 27.5T37.6 27.5L38 33.5Q29 28.5 20 33.5T2 33.5z\" fill=\"" + c[1] + "\"/>" +
      "<circle cx=\"13\" cy=\"16\" r=\"2.2\" fill=\"" + c[2] + "\"/><circle cx=\"25\" cy=\"12\" r=\"2.2\" fill=\"" + c[2] + "\"/>" +
      "<circle cx=\"27\" cy=\"41\" r=\"2.2\" fill=\"" + c[2] + "\"/><circle cx=\"13\" cy=\"42\" r=\"2.2\" fill=\"" + c[2] + "\"/></svg>";
  }
  var hunting = false;
  function eggHunt(opts) {
    if (hunting) return;
    hunting = true; addStyles();
    var docH = Math.max(document.documentElement.scrollHeight, document.body.scrollHeight);
    var scrolls = docH > innerHeight * 1.3;
    var total = 3, found = 0, eggs = [];
    for (var i = 0; i < total; i++) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "tbd-egg"; b.setAttribute("aria-label", "Hidden Easter egg");
      b.innerHTML = eggSVG(EGG_COLORS[Math.floor(Math.random() * EGG_COLORS.length)]);
      // spread the eggs down the page (or around the screen if the page doesn't scroll)
      var band = scrolls ? docH : innerHeight, top = band * ((i + .2 + Math.random() * .6) / total);
      b.style.top = Math.round(Math.min(top, band - 60)) + "px";
      b.style.left = Math.round(8 + Math.random() * (document.documentElement.clientWidth - 60)) + "px";
      if (!scrolls) b.style.position = "fixed";
      b.style.animationDelay = (-Math.random() * 2.6).toFixed(2) + "s";
      b.addEventListener("click", function (e) {
        var egg = e.currentTarget, box = egg.getBoundingClientRect();
        egg.classList.add("found"); setTimeout(function () { egg.remove(); }, 450);
        found++;
        if (!reduce) burst(box.left + box.width / 2, box.top + box.height / 2, 40, 4, ["#ff9ec4", "#ffd166", "#8fd3ff", "#9be89b", "#b388ff"]);
        if (found < total) toast("Egg " + found + " of " + total + " found. Keep looking!", 4000);
        else {
          hunting = false;
          if (opts && opts.onDone) opts.onDone();
          toast("You found all " + total + " eggs! <b>Happy Easter</b> from TechByDave.", 8000);
          if (!reduce) { var cx = innerWidth / 2; burst(cx * .6, innerHeight * .4, 70, 6, COLORS); burst(cx * 1.4, innerHeight * .35, 70, 6, COLORS); }
        }
      });
      document.body.appendChild(b); eggs.push(b);
    }
  }

  /* ---------- what plays on each holiday ---------- */
  function play(name, today, auto) {
    var year = +today.slice(0, 4);
    if (name === "christmas") {
      snow(10000);
      toast("<b>Merry Christmas</b> from TechByDave! Psst: type <b>snow</b> any time.");
    } else if (name === "newyear") {
      var next = +today.slice(5, 7) >= 7 ? year + 1 : year; // Dec 31 -> next year, Jan 1 -> this year
      fireworks(7000);
      toast("<b>Happy New Year!</b> Here's to " + next + " from TechByDave. Type <b>boom</b> for more.");
    } else if (name === "easter") {
      var key = "tbd-eggs-" + today;
      try { if (auto && localStorage.getItem(key)) return; } catch (e) {}
      eggHunt({ onDone: function () { try { localStorage.setItem(key, "1"); } catch (e) {} } });
      toast("<b>Happy Easter!</b> 3 eggs are hidden on this page. Can you find them all?", 9000);
    }
  }

  function start() {
    var today = todayISO();
    var preview = (location.search.match(/[?&]holiday=(christmas|newyear|easter)\b/) || [])[1];
    var name = preview || holidayOn(today);
    if (name) {
      var seen = "tbd-holiday-" + name + "-" + today, shown = false;
      try { shown = !!sessionStorage.getItem(seen); sessionStorage.setItem(seen, "1"); } catch (e) {}
      // snow and fireworks play once per visit; the egg hunt runs on every page until all eggs are found
      if (preview || name === "easter" || !shown) setTimeout(function () { play(name, today, !preview); }, 600);
    }

    // Secret words, all year round
    var typed = "";
    document.addEventListener("keydown", function (e) {
      var t = e.target;
      if (e.ctrlKey || e.metaKey || e.altKey || !e.key || e.key.length !== 1) return;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      typed = (typed + e.key.toLowerCase()).slice(-4);
      if (typed === "snow") { typed = ""; snow(8000); if (reduce) toast("Let it snow! (Animations are off on your device.)"); }
      else if (typed === "boom") { typed = ""; fireworks(5000); if (reduce) toast("Boom! (Animations are off on your device.)"); }
      else if (typed === "eggs") { typed = ""; if (!hunting) { eggHunt(); toast("3 eggs just hid somewhere on this page. Go find them!", 6000); } }
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();

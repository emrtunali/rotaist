/* Rotaist web app: side-by-side replay of a real January 2025 day + the back-test, visualised. */
"use strict";

const VAN = ["#38bdf8", "#a78bfa", "#f472b6", "#22d3ee", "#818cf8", "#e879f9"];
const PLANS = ["ortools_freeflow", "ortools_average", "rotaist"];
const LABEL = {
  ortools_freeflow: "OR-Tools · free-flow matrix",
  ortools_average: "OR-Tools · typical daytime matrix",
  alns_average: "Rotaist solver · typical matrix (ablation)",
  rotaist: "Rotaist · forecast, time-dependent",
  oracle: "Oracle · knows real traffic in advance",
};
const SHORT = {
  ortools_freeflow: "OR-Tools free-flow", ortools_average: "OR-Tools typical",
  alns_average: "ALNS on static matrix", rotaist: "Rotaist", oracle: "Oracle",
};
const COLOR = {
  ortools_freeflow: "#64748b", ortools_average: "#cbd5e1", alns_average: "#f59e0b",
  rotaist: "#38bdf8", oracle: "#4ade80",
};
const OPT = { ortools_freeflow: "OR-Tools · ignores traffic", ortools_average: "OR-Tools · typical traffic", rotaist: "Rotaist · traffic forecast" };
const AREA = [[29.005, 40.955], [29.2, 41.07]];
const STYLE = "https://tiles.openfreemap.org/styles/dark";
const T0 = 8 * 3600, T1 = 22 * 3600;

const S = { day: null, roads: null, t: 17 * 3600, playing: false, src: "ratio", view: 2, panes: [], hour: -1 };
window.S = S;
const $ = (id) => document.getElementById(id);
const hhmm = (s) => `${String(Math.floor(s / 3600)).padStart(2, "0")}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;
const getJSON = async (u) => { const r = await fetch(u); if (!r.ok) throw new Error(u); return r.json(); };
const fmtDate = (d, opts = { weekday: "short", day: "numeric", month: "short" }) =>
  new Date(d + "T12:00:00").toLocaleDateString("en-GB", opts);

/* ================================================================ replay: data helpers */

function searchTime(times, t) {
  let lo = 0, hi = times.length - 1;
  if (t <= times[0]) return [0, 0, 0];
  if (t >= times[hi]) return [hi, hi, 0];
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (times[m] <= t) lo = m; else hi = m; }
  return [lo, hi, times[hi] > times[lo] ? (t - times[lo]) / (times[hi] - times[lo]) : 0];
}

function vanState(v, t) {
  const [lo, hi, f] = searchTime(v.times, t);
  const a = v.coords[lo], b = v.coords[hi];
  const p = [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
  return { pos: p, done: t <= v.times[0] ? [] : v.coords.slice(0, lo + 1).concat([p]) };
}

let roadFeatures = null;
function roadsFC() {
  const h = Math.min(23, Math.floor(S.t / 3600));
  const ratio = S.day.traffic[S.src][h];
  if (!roadFeatures) {
    roadFeatures = S.roads.roads.map(([cell, cls, coords]) => ({
      type: "Feature", geometry: { type: "LineString", coordinates: coords }, properties: { cell, c: cls, r: 1 },
    }));
  }
  for (const f of roadFeatures) f.properties.r = ratio[f.properties.cell] ?? 1;
  return { type: "FeatureCollection", features: roadFeatures };
}

function planStats(plan, t) {
  const d = S.day;
  let done = 0, late = 0;
  plan.arrival.forEach((a, i) => {
    if (a <= t) { done++; if (Math.max(a, d.stops.tw_start[i]) > d.stops.tw_end[i]) late++; }
  });
  let duty = 0;
  for (const v of plan.vans) duty += Math.max(0, Math.min(t, v.times[v.times.length - 1]) - v.times[0]);
  return { done, late, duty: duty / 3600 };
}

/* ================================================================ replay: map panes */

let syncing = false;

function makePane(plan) {
  const box = document.createElement("div");
  box.className = "mapbox";
  box.innerHTML = `<div class="map"></div>
    <div class="card-ov">
      <select aria-label="Planner">${PLANS.map((m) => `<option value="${m}">${OPT[m]}</option>`).join("")}</select>
      <div class="live-grid">
        <div><div class="k">Delivered</div><div class="v" data-k="done">0</div></div>
        <div><div class="k">Late</div><div class="v" data-k="late">0</div></div>
        <div><div class="k">On time</div><div class="v" data-k="ot">–</div></div>
        <div><div class="k">Driver hours</div><div class="v" data-k="duty">0.0</div></div>
      </div>
      <div class="bar"><i></i></div>
      <div class="eod"></div>
    </div>`;
  $("maps").appendChild(box);
  const sel = box.querySelector("select");
  sel.value = plan;
  const map = new maplibregl.Map({
    container: box.querySelector(".map"), style: STYLE, bounds: AREA,
    attributionControl: { compact: true }, dragRotate: false,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  const pane = { box, map, plan, ready: false, card: box.querySelector(".card-ov") };
  new ResizeObserver(() => map.resize()).observe(box);
  map.on("move", () => {
    if (syncing) return;
    syncing = true;
    for (const o of S.panes) if (o !== pane) o.map.jumpTo({ center: map.getCenter(), zoom: map.getZoom() });
    syncing = false;
  });
  map.on("load", () => {
    const w = box.clientWidth;
    map.fitBounds(AREA, { padding: w > 700 ? { top: 40, bottom: 20, left: 210, right: 50 } : { top: 150, bottom: 10, left: 10, right: 10 }, duration: 0 });
    addLayers(pane); pane.ready = true; setPlan(pane, pane.plan);
  });
  sel.addEventListener("change", () => setPlan(pane, sel.value));
  return pane;
}

function addLayers(p) {
  const m = p.map;
  m.addSource("roads", { type: "geojson", data: roadsFC() });
  m.addLayer({
    id: "roads", type: "line", source: "roads", layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": ["interpolate", ["linear"], ["get", "r"], 0.35, "#b91c1c", 0.55, "#f97316", 0.72, "#facc15", 0.88, "#22c55e"],
      "line-width": ["interpolate", ["linear"], ["zoom"], 10, ["*", 0.35, ["get", "c"]], 14, ["+", 1, ["*", 1.1, ["get", "c"]]]],
      "line-opacity": 0.7,
    },
  });
  m.addSource("routes", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  m.addLayer({ id: "routes", type: "line", source: "routes", layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": ["get", "color"], "line-width": 1.3, "line-opacity": 0.22 } });
  m.addSource("done", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  m.addLayer({ id: "done-glow", type: "line", source: "done", layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": ["get", "color"], "line-width": 6, "line-opacity": 0.14, "line-blur": 4 } });
  m.addLayer({ id: "done", type: "line", source: "done", layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": ["get", "color"], "line-width": 2, "line-opacity": 0.9 } });
  m.addSource("stops", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  m.addLayer({ id: "late-halo", type: "circle", source: "stops", filter: ["==", ["get", "s"], "late"],
    paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 7, 15, 14], "circle-color": "#f43f5e",
      "circle-opacity": 0.28, "circle-blur": 0.4 } });
  m.addLayer({
    id: "stops", type: "circle", source: "stops",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, ["match", ["get", "s"], "wait", 2.2, 3],
        15, ["match", ["get", "s"], "wait", 5, 7]],
      "circle-color": ["match", ["get", "s"], "ok", "#4ade80", "late", "#f43f5e", "rgba(0,0,0,0)"],
      "circle-stroke-color": ["match", ["get", "s"], "wait", "#7c8aa5", "#070a12"],
      "circle-stroke-width": ["match", ["get", "s"], "wait", 1.2, 0.8],
    },
  });
  m.addSource("vans", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  m.addLayer({ id: "vans-glow", type: "circle", source: "vans",
    paint: { "circle-radius": 16, "circle-color": ["get", "color"], "circle-opacity": 0.35, "circle-blur": 1 } });
  m.addLayer({ id: "vans", type: "circle", source: "vans",
    paint: { "circle-radius": 6, "circle-color": ["get", "color"], "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
  m.addSource("depot", { type: "geojson", data: { type: "Point", coordinates: S.day.depot } });
  m.addLayer({ id: "depot-glow", type: "circle", source: "depot",
    paint: { "circle-radius": 18, "circle-color": "#38bdf8", "circle-opacity": 0.18, "circle-blur": 0.6 } });
  m.addLayer({ id: "depot", type: "circle", source: "depot",
    paint: { "circle-radius": 7, "circle-color": "#070a12", "circle-stroke-color": "#38bdf8", "circle-stroke-width": 3 } });

  m.on("click", "stops", (e) => {
    const i = e.features[0].properties.i, d = S.day, plan = d.plans[p.plan];
    const over = Math.max(plan.arrival[i], d.stops.tw_start[i]) - d.stops.tw_end[i];
    const err = Math.round((plan.arrival[i] - plan.promised[i]) / 60);
    new maplibregl.Popup({ closeButton: false }).setLngLat(e.lngLat).setHTML(
      `<b>Order #${i + 1}</b> · slot ${hhmm(d.stops.tw_start[i])}–${hhmm(d.stops.tw_end[i])}<br>` +
      `Promised ETA <b>${hhmm(plan.promised[i])}</b> · real arrival <b>${hhmm(plan.arrival[i])}</b><br>` +
      `<span style="color:${Math.abs(err) <= 5 ? "#4ade80" : "#f59e0b"}">ETA ${err >= 0 ? "+" : ""}${err} min</span>` +
      (over > 0 ? ` · <span style="color:#f43f5e">${Math.round(over / 60)} min late</span>` : " · on time ✓")).addTo(m);
  });
  m.on("mouseenter", "stops", () => (m.getCanvas().style.cursor = "pointer"));
  m.on("mouseleave", "stops", () => (m.getCanvas().style.cursor = ""));
}

function setPlan(p, plan) {
  p.plan = plan;
  p.card.classList.toggle("rotaist", plan === "rotaist");
  drawTimeline();
  if (!p.ready) return;
  const P = S.day.plans[plan];
  p.map.getSource("routes").setData({ type: "FeatureCollection", features: P.vans.map((v, k) => ({
    type: "Feature", geometry: { type: "LineString", coordinates: v.coords }, properties: { color: VAN[k % VAN.length] } })) });
  const k = P.kpi;
  p.card.querySelector(".eod").innerHTML = `End of day: <b>${k.on_time_pct.toFixed(1)}%</b> on time · ` +
    `<b>${k.duty_hours.toFixed(1)} h</b> driving · ETA <b>±${k.eta_mae_min.toFixed(1)} min</b>`;
  updatePane(p);
}

function updatePane(p) {
  if (!p.ready) return;
  const d = S.day, P = d.plans[p.plan], t = S.t;
  const vans = [], done = [];
  P.vans.forEach((v, k) => {
    const st = vanState(v, t), color = VAN[k % VAN.length];
    vans.push({ type: "Feature", geometry: { type: "Point", coordinates: st.pos }, properties: { color } });
    if (st.done.length > 1) done.push({ type: "Feature", geometry: { type: "LineString", coordinates: st.done }, properties: { color } });
  });
  p.map.getSource("vans").setData({ type: "FeatureCollection", features: vans });
  p.map.getSource("done").setData({ type: "FeatureCollection", features: done });
  p.map.getSource("stops").setData({ type: "FeatureCollection", features: d.stops.lon.map((lon, i) => {
    const a = P.arrival[i];
    const s = a > t ? "wait" : Math.max(a, d.stops.tw_start[i]) > d.stops.tw_end[i] ? "late" : "ok";
    return { type: "Feature", geometry: { type: "Point", coordinates: [lon, d.stops.lat[i]] }, properties: { i, s } };
  }) });
  const st = planStats(P, t), q = (k) => p.card.querySelector(`[data-k=${k}]`);
  q("done").textContent = `${st.done}/${d.orders}`;
  q("late").textContent = st.late;
  q("late").className = "v " + (st.late ? "bad" : "good");
  const ot = st.done ? (100 * (st.done - st.late)) / st.done : null;
  q("ot").textContent = ot == null ? "–" : `${ot.toFixed(1)}%`;
  q("ot").className = "v " + (ot == null ? "" : ot >= 99 ? "good" : ot < 95 ? "bad" : "");
  q("duty").textContent = st.duty.toFixed(1);
  p.card.querySelector(".bar i").style.width = `${(100 * st.done) / d.orders}%`;
}

function refreshRoads() {
  const h = Math.min(23, Math.floor(S.t / 3600)) + (S.src === "forecast" ? 100 : 0);
  if (h === S.hour) return;
  const fc = roadsFC();
  let all = true;
  for (const p of S.panes) { if (p.ready) p.map.getSource("roads").setData(fc); else all = false; }
  if (all) S.hour = h;
}

function refresh() {
  $("clock").textContent = hhmm(S.t);
  $("time").value = S.t;
  refreshRoads();
  S.panes.forEach(updatePane);
  drawTimeline();
}
window.refresh = refresh;

function buildPanes() {
  const keep = S.panes.map((p) => p.plan);
  S.panes.forEach((p) => p.map.remove());
  $("maps").innerHTML = "";
  $("maps").classList.toggle("one", S.view === 1);
  const plans = S.view === 2 ? [keep.length === 1 && keep[0] !== "rotaist" ? keep[0] : "ortools_average", "rotaist"]
    : [keep[keep.length - 1] || "rotaist"];
  S.hour = -1;
  S.panes = plans.map(makePane);
}

/* ================================================================ replay: timeline */

const TL = { padL: 44, padR: 170, padT: 14, padB: 22 };

function drawTimeline() {
  const cv = $("timeline");
  if (!S.day || !cv.clientWidth) return;
  const dpr = devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
  if (cv.width !== W * dpr || cv.height !== H * dpr) { cv.width = W * dpr; cv.height = H * dpr; }
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);
  const narrow = W < 600, padR = narrow ? 12 : TL.padR;
  const x0 = TL.padL, x1 = W - padR, y0 = TL.padT, y1 = H - TL.padB;
  const X = (t) => x0 + ((t - T0) / (T1 - T0)) * (x1 - x0);
  g.font = "11px Inter, sans-serif";

  g.strokeStyle = "#1c2538"; g.fillStyle = "#8b97ad"; g.lineWidth = 1; g.textAlign = "center";
  for (let h = 8; h <= 22; h += 2) {
    const x = X(h * 3600);
    g.beginPath(); g.moveTo(x, y0); g.lineTo(x, y1); g.stroke();
    g.fillText(`${h}:00`, x, H - 6);
  }

  // network speed (% of free flow): actual as a filled area, forecast dashed
  const net = (src) => {
    const pts = [];
    const mean = (h) => { const r = S.day.traffic[src][h]; return r.reduce((a, b) => a + b, 0) / r.length; };
    for (let h = 8; h < 22; h++) pts.push([h * 3600 + 1800, mean(h)]);
    return [[T0, mean(8)], ...pts, [T1, (mean(21) + mean(22)) / 2]];
  };
  const Ys = (r) => y1 - ((r - 0.45) / (1.0 - 0.45)) * (y1 - y0);
  const act = net("ratio"), fc = net("forecast");
  const grad = g.createLinearGradient(0, y0, 0, y1);
  grad.addColorStop(0, "rgba(34,197,94,0.22)"); grad.addColorStop(1, "rgba(239,68,68,0.30)");
  g.beginPath(); g.moveTo(X(act[0][0]), y1);
  act.forEach(([t, r]) => g.lineTo(X(t), Ys(r)));
  g.lineTo(X(act[act.length - 1][0]), y1); g.closePath(); g.fillStyle = grad; g.fill();
  g.beginPath(); act.forEach(([t, r], i) => (i ? g.lineTo(X(t), Ys(r)) : g.moveTo(X(t), Ys(r))));
  g.strokeStyle = "rgba(226,232,240,0.55)"; g.lineWidth = 1.5; g.stroke();
  g.setLineDash([4, 4]); g.beginPath(); fc.forEach(([t, r], i) => (i ? g.lineTo(X(t), Ys(r)) : g.moveTo(X(t), Ys(r))));
  g.strokeStyle = "rgba(56,189,248,0.7)"; g.stroke(); g.setLineDash([]);
  g.fillStyle = "#8b97ad"; g.textAlign = "right";
  [0.5, 0.75, 1].forEach((r) => g.fillText(`${Math.round(r * 100)}%`, x0 - 6, Ys(r) + 4));
  g.save(); g.translate(10, (y0 + y1) / 2); g.rotate(-Math.PI / 2); g.textAlign = "center";
  g.fillText("street speed", 0, 0); g.restore();

  // cumulative late deliveries for the plans on screen
  const series = S.panes.map((p) => {
    const P = S.day.plans[p.plan], d = S.day;
    const lates = P.arrival.map((a, i) => (Math.max(a, d.stops.tw_start[i]) > d.stops.tw_end[i] ? a : null))
      .filter((a) => a != null).sort((a, b) => a - b);
    return { plan: p.plan, lates };
  });
  const maxLate = Math.max(5, ...series.map((s) => s.lates.length));
  const Yl = (n) => y1 - (n / maxLate) * (y1 - y0) * 0.92;
  series.forEach((s, si) => {
    g.beginPath(); g.moveTo(X(T0), Yl(0));
    let n = 0;
    for (const a of s.lates) { g.lineTo(X(a), Yl(n)); n++; g.lineTo(X(a), Yl(n)); }
    g.lineTo(X(T1), Yl(n));
    g.strokeStyle = COLOR[s.plan]; g.lineWidth = s.plan === "rotaist" ? 3 : 2.2; g.stroke();
    const shown = s.lates.filter((a) => a <= S.t).length;
    g.fillStyle = COLOR[s.plan]; g.font = "600 12px Inter, sans-serif";
    if (narrow) { g.textAlign = "left"; g.fillText(`${SHORT[s.plan]}: ${shown} late`, x0 + 6 + si * 150, y0 + 10); }
    else { g.textAlign = "left"; g.fillText(`${SHORT[s.plan]}: ${shown} late`, x1 + 14, y0 + 14 + si * 20); }
    g.font = "11px Inter, sans-serif";
  });
  if (!narrow) {
    g.fillStyle = "#8b97ad"; g.textAlign = "left";
    g.fillText("late deliveries, cumulative", x1 + 14, y0 + 14 + series.length * 20 + 2);
    g.setLineDash([4, 4]); g.strokeStyle = "rgba(56,189,248,0.7)"; g.beginPath();
    g.moveTo(x1 + 14, y1 - 22); g.lineTo(x1 + 32, y1 - 22); g.stroke(); g.setLineDash([]);
    g.fillText("forecast speed", x1 + 38, y1 - 18);
    g.fillStyle = "rgba(226,232,240,0.55)"; g.fillRect(x1 + 14, y1 - 6, 18, 2);
    g.fillStyle = "#8b97ad"; g.fillText("actual speed", x1 + 38, y1 - 2);
  }

  const cx = X(S.t);
  g.strokeStyle = "#fff"; g.lineWidth = 1.5; g.beginPath(); g.moveTo(cx, y0 - 4); g.lineTo(cx, y1); g.stroke();
  g.fillStyle = "#fff"; g.beginPath(); g.arc(cx, y0 - 4, 3.5, 0, 7); g.fill();
}

function setupTimelineScrub() {
  const cv = $("timeline");
  const toT = (e) => {
    const r = cv.getBoundingClientRect(), padR = r.width < 600 ? 12 : TL.padR;
    const f = (e.clientX - r.left - TL.padL) / (r.width - TL.padL - padR);
    S.t = Math.round(T0 + Math.min(1, Math.max(0, f)) * (T1 - T0));
    refresh();
  };
  let down = false;
  cv.addEventListener("pointerdown", (e) => { down = true; cv.setPointerCapture(e.pointerId); toT(e); });
  cv.addEventListener("pointermove", (e) => down && toT(e));
  cv.addEventListener("pointerup", () => (down = false));
  new ResizeObserver(drawTimeline).observe(cv);
}

/* ================================================================ playback */

let last = null;
function tick(ts) {
  if (S.playing) {
    if (last != null) {
      S.t += ((ts - last) / 1000) * Number($("speed").value);
      if (S.t >= T1) { S.t = T1; setPlaying(false); }
      refresh();
    }
    last = ts;
  } else last = null;
  requestAnimationFrame(tick);
}
function setPlaying(on) {
  S.playing = on;
  if (on && S.t >= T1) S.t = T0;
  $("play").textContent = on ? "❚❚" : "▶";
}

async function loadDay(date) {
  S.day = await getJSON(`data/day_${date}.json`);
  S.hour = -1;
  for (const p of S.panes) {
    if (!p.ready) continue;
    p.map.getSource("depot").setData({ type: "Point", coordinates: S.day.depot });
    setPlan(p, p.plan);
  }
  refresh();
}

/* ================================================================ results */

function countUp(el, to, { dec = 0, prefix = "", suffix = "", ms = 1400 } = {}) {
  const t0 = performance.now();
  const step = (t) => {
    const f = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - f, 3);
    el.textContent = `${prefix}${(to * e).toFixed(dec)}${suffix}`;
    if (f < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function renderHero(s) {
  const R = s.backtest["5"], base = R.ortools_average, rot = R.rotaist;
  const saved = base.duty_hours - rot.duty_hours;
  $("heroHours").textContent = `${saved.toFixed(1)} fewer driver hours a day.`;
  const stats = [
    { to: saved, dec: 1, prefix: "−", suffix: " h", cls: "good", l: "driver time per day",
      s: `${rot.duty_hours.toFixed(1)} h vs ${base.duty_hours.toFixed(1)} h, 5 vans` },
    { to: rot.on_time_pct, dec: 1, suffix: "%", cls: "acc", l: "delivered on time",
      s: `vs ${base.on_time_pct.toFixed(1)}% OR-Tools · ${R.ortools_freeflow.on_time_pct.toFixed(0)}% if traffic is ignored` },
    { to: rot.eta_mae_min, dec: 1, prefix: "±", suffix: " min", cls: "acc", l: "ETA promise error",
      s: `vs ±${R.ortools_freeflow.eta_mae_min.toFixed(0)} min for a free-flow plan` },
    { to: s.forecast.model.mae_kmh, dec: 1, suffix: " km/h", cls: "", l: "day-ahead speed error",
      s: `vs ${s.forecast.daily_mean.mae_kmh.toFixed(1)} km/h for a flat daily average` },
  ];
  $("heroStats").innerHTML = stats.map((x, i) =>
    `<div class="stat"><div class="n ${x.cls}" id="hs${i}">0</div><div class="l">${x.l}</div><div class="s">${x.s}</div></div>`).join("");
  stats.forEach((x, i) => countUp($(`hs${i}`), x.to, x));
}

function renderInsights(s) {
  const B = s.backtest, R = B["5"];
  const saves = ["5", "6"].map((f) => 100 * (1 - B[f].rotaist.duty_hours / B[f].ortools_average.duty_hours));
  const cards = [
    { c: "#f43f5e", num: `${R.ortools_freeflow.on_time_pct.toFixed(0)}%`, h: "Ignoring traffic breaks promises",
      p: `A plan built on free-flow speeds promises 100% on time and delivers ${R.ortools_freeflow.on_time_pct.toFixed(0)}%. ` +
         `Its ETAs run ${R.ortools_freeflow.eta_bias_min.toFixed(0)} minutes late on average.` },
    { c: "#f59e0b", num: `${R.alns_average.on_time_pct.toFixed(1)}%`, h: "A smarter solver alone isn't enough",
      p: `Our own ALNS on a static matrix squeezes out hours — and exactly the slack the evening rush needed. ` +
         `Punctuality drops and ETA error nearly doubles (±${R.alns_average.eta_mae_min.toFixed(1)} min).` },
    { c: "#38bdf8", num: `−${Math.round(saves[0])}–${Math.round(saves[1])}%`, h: "Forecast + optimiser: hours saved safely",
      p: `With tomorrow's traffic in the loop, Rotaist is as punctual as OR-Tools or better and needs ` +
         `${Math.round(saves[0])}–${Math.round(saves[1])}% fewer driver hours with 5–6 vans — close to an oracle that knows the real traffic.` },
  ];
  $("insights").innerHTML = cards.map((x) =>
    `<div class="ins" style="--c:${x.c}"><div class="num">${x.num}</div><h4>${x.h}</h4><p>${x.p}</p></div>`).join("");
  $("lead").innerHTML = `${s.backtest_days} held-out weekdays of January 2025 × 3 fleet sizes. Every day, ${s.orders} orders
    from a Kozyatağı micro-hub with 2-hour delivery slots. Every planner plans the evening before; every plan is then driven
    on that day's real İBB traffic. Same orders, same vans, same objective — only the solver and what it knows about traffic change.`;
}

function chartDefaults() {
  Chart.defaults.color = "#8b97ad";
  Chart.defaults.borderColor = "#1c2538";
  Chart.defaults.font.family = "Inter, system-ui, sans-serif";
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.boxWidth = 8;
  Chart.defaults.plugins.tooltip.backgroundColor = "#0b1120";
  Chart.defaults.plugins.tooltip.borderColor = "#1c2538";
  Chart.defaults.plugins.tooltip.borderWidth = 1;
  Chart.defaults.maintainAspectRatio = false;
}

let scatter = null;
function renderScatter(s, fleet) {
  const methods = ["ortools_freeflow", "ortools_average", "alns_average", "rotaist", "oracle"];
  const rows = s.per_day.filter((r) => String(r.vans) === fleet);
  const data = {
    datasets: methods.map((m) => ({
      label: SHORT[m], backgroundColor: COLOR[m] + (m === "oracle" ? "00" : "cc"), borderColor: COLOR[m],
      borderWidth: m === "oracle" ? 1.5 : 0, pointRadius: m === "rotaist" ? 6 : 4.5, pointHoverRadius: 8,
      data: rows.map((r) => ({ x: r[m].duty_hours, y: Math.max(r[m].late_minutes, 0.5), date: r.date, ot: r[m].on_time_pct })),
    })),
  };
  if (scatter) { scatter.data = data; scatter.update(); return; }
  scatter = new Chart($("chScatter"), {
    type: "scatter", data,
    options: {
      scales: {
        x: { title: { display: true, text: "driver hours per day   (← fewer is better)" } },
        y: { type: "logarithmic", reverse: true, min: 0.35,
          title: { display: true, text: "late minutes per day, log scale   (↑ fewer is better)" },
          afterBuildTicks: (ax) => { ax.ticks = [0.5, 1, 10, 100, 1000, 5000].filter((v) => v <= ax.max).map((v) => ({ value: v })); },
          ticks: { callback: (v) => (v <= 0.5 ? "0" : v) } },
      },
      plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label} · ${fmtDate(c.raw.date)}: ${c.raw.x.toFixed(1)} h, ` +
        `${c.raw.y <= 0.5 ? 0 : Math.round(c.raw.y)} late min, ${c.raw.ot.toFixed(1)}% on time` } } },
    },
  });
}

function renderEta(s) {
  const H = s.eta_hist, edges = H.edges, F = H["5"];
  const mids = edges.slice(0, -1).map((e, i) => (e + edges[i + 1]) / 2);
  new Chart($("chEta"), {
    type: "line",
    data: { datasets: ["ortools_freeflow", "ortools_average", "rotaist"].map((m) => ({
      label: SHORT[m], data: F[m].map((y, i) => ({ x: mids[i], y })), borderColor: COLOR[m], backgroundColor: COLOR[m] + "33",
      fill: true, tension: 0.35, pointRadius: 0, borderWidth: m === "rotaist" ? 2.5 : 1.6 })) },
    options: {
      interaction: { mode: "nearest", axis: "x", intersect: false },
      scales: {
        x: { type: "linear", min: -10, max: 60, ticks: { callback: (v) => (v === 60 ? "60+" : v) }, title: { display: true, text: "minutes later than promised   (0 = spot on)" } },
        y: { title: { display: true, text: "% of orders" } },
      },
      plugins: { tooltip: { callbacks: { title: (c) => `${c[0].parsed.x - 1} to ${c[0].parsed.x + 1} min`,
        label: (c) => `${c.dataset.label}: ${c.parsed.y.toFixed(1)}% of orders` } } },
    },
  });
}

const barLabels = {
  id: "barLabels",
  afterDatasetsDraw(chart) {
    const g = chart.ctx;
    chart.data.datasets.forEach((ds, di) => {
      if (!ds.labels || !chart.isDatasetVisible(di)) return;
      chart.getDatasetMeta(di).data.forEach((bar, i) => {
        g.save(); g.fillStyle = "#38bdf8"; g.font = "700 12px Inter, sans-serif"; g.textAlign = "center";
        g.fillText(ds.labels[i], bar.x, bar.y - 6); g.restore();
      });
    });
  },
};

function renderFleet(s) {
  const fleets = Object.keys(s.backtest);
  const methods = ["ortools_average", "alns_average", "rotaist", "oracle"];
  new Chart($("chFleet"), {
    type: "bar", plugins: [barLabels],
    data: { labels: fleets.map((f) => `${f} vans`), datasets: methods.map((m) => ({
      label: SHORT[m], backgroundColor: COLOR[m], borderRadius: 4,
      data: fleets.map((f) => s.backtest[f][m].duty_hours),
      labels: m === "rotaist" ? fleets.map((f) =>
        `−${(100 * (1 - s.backtest[f].rotaist.duty_hours / s.backtest[f].ortools_average.duty_hours)).toFixed(0)}%`) : null,
    })) },
    options: {
      scales: { y: { min: 28, title: { display: true, text: "driver hours per day" } } },
      plugins: { tooltip: { callbacks: { label: (c) => {
        const r = s.backtest[fleets[c.dataIndex]][methods[c.datasetIndex]];
        return `${c.dataset.label}: ${r.duty_hours.toFixed(1)} h · ${r.on_time_pct.toFixed(1)}% on time`;
      } } } },
    },
  });
}

function heatColor(r) {
  const stops = [[0.55, [185, 28, 28]], [0.7, [249, 115, 22]], [0.82, [250, 204, 21]], [0.95, [21, 128, 61]], [1.05, [8, 47, 73]]];
  if (r <= stops[0][0]) return `rgb(${stops[0][1]})`;
  for (let i = 1; i < stops.length; i++) {
    if (r <= stops[i][0]) {
      const [a, ca] = stops[i - 1], [b, cb] = stops[i], f = (r - a) / (b - a);
      return `rgb(${ca.map((c, k) => Math.round(c + (cb[k] - c) * f))})`;
    }
  }
  return `rgb(${stops[stops.length - 1][1]})`;
}

function renderHeat(tr) {
  const cv = $("heat"), tip = $("heatTip");
  const L = 40, R = 8, T = 24, B = 24;
  const draw = () => {
    const dpr = devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
    cv.width = W * dpr; cv.height = H * dpr;
    const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const n = tr.dates.length, cw = (W - L - R) / n, ch = (H - T - B) / 24;
    tr.heat.forEach((day, d) => day.forEach((r, h) => {
      g.fillStyle = r == null ? "#1c2538" : heatColor(r);
      g.fillRect(L + d * cw, T + h * ch, Math.ceil(cw), Math.ceil(ch));
    }));
    g.font = "11px Inter, sans-serif"; g.fillStyle = "#8b97ad"; g.textAlign = "right";
    [0, 6, 12, 18, 23].forEach((h) => g.fillText(`${h}:00`, L - 6, T + (h + 0.75) * ch));
    g.textAlign = "left";
    tr.dates.forEach((dt, d) => {
      if (dt.endsWith("-01")) {
        g.fillStyle = "#8b97ad"; g.fillText(fmtDate(dt, { month: "short", year: "numeric" }), L + d * cw + 3, H - 7);
        g.fillStyle = "#070a12"; g.fillRect(L + d * cw - 0.5, T, 1.5, H - T - B);
      }
    });
    const xs = L + tr.test_start * cw;
    g.strokeStyle = "#fff"; g.setLineDash([5, 4]); g.lineWidth = 1.5;
    g.beginPath(); g.moveTo(xs, T - 8); g.lineTo(xs, H - B); g.stroke(); g.setLineDash([]);
    g.fillStyle = "#e6e9f0"; g.font = "600 11px Inter, sans-serif";
    g.textAlign = "right"; g.fillText("← training: Oct–Dec 2024", xs - 8, T - 9);
    g.textAlign = "left"; g.fillText("test: January 2025 →", xs + 8, T - 9);
    cv._geo = { n, cw, ch };
  };
  draw();
  new ResizeObserver(draw).observe(cv);
  cv.addEventListener("mousemove", (e) => {
    const r = cv.getBoundingClientRect(), { cw, ch, n } = cv._geo;
    const d = Math.floor((e.clientX - r.left - L) / cw), h = Math.floor((e.clientY - r.top - T) / ch);
    if (d < 0 || d >= n || h < 0 || h > 23) { tip.style.display = "none"; return; }
    const v = tr.heat[d][h];
    tip.innerHTML = `<b>${fmtDate(tr.dates[d], { weekday: "short", day: "numeric", month: "short", year: "numeric" })}</b> · ${h}:00<br>` +
      `streets at <b>${v == null ? "–" : Math.round(v * 100)}%</b> of free-flow speed${tr.holidays.includes(tr.dates[d]) ? " · holiday" : ""}`;
    tip.style.display = "block";
    tip.style.left = `${Math.min(e.clientX - r.left + 12, r.width - 240)}px`;
    tip.style.top = `${e.clientY - r.top + 12}px`;
  });
  cv.addEventListener("mouseleave", () => (tip.style.display = "none"));
}

let fcChart = null;
function renderForecast(tr) {
  const days = Object.keys(tr.curves);
  $("fcDay").innerHTML = days.map((d) => `<option value="${d}">${fmtDate(d)}</option>`).join("");
  const pick = days.includes("2025-01-15") ? "2025-01-15" : days[0];
  $("fcDay").value = pick;
  const SER = [["actual", "What really happened", "#f8fafc", 3, []], ["model", "Rotaist forecast", "#38bdf8", 2.5, []],
    ["hour_of_week", "Hour-of-week profile", "#a78bfa", 1.5, [5, 4]], ["daily_mean", "Flat daily average", "#64748b", 1.5, [2, 3]]];
  const draw = (d) => {
    const c = tr.curves[d];
    const data = { labels: [...Array(24).keys()].map((h) => `${h}:00`), datasets: SER.map(([k, l, col, w, dash]) => ({
      label: l, data: c[k], borderColor: col, backgroundColor: col, borderWidth: w, borderDash: dash, pointRadius: 0, tension: 0.35 })) };
    if (fcChart) { fcChart.data = data; fcChart.update(); return; }
    fcChart = new Chart($("chFc"), { type: "line", data,
      options: { interaction: { mode: "index", intersect: false },
        scales: { y: { title: { display: true, text: "average street speed, km/h" } } } } });
  };
  draw(pick);
  $("fcDay").addEventListener("change", (e) => draw(e.target.value));
}

function renderDays(s) {
  const rows = s.per_day.filter((r) => r.vans === 5).sort((a, b) => a.date.localeCompare(b.date));
  new Chart($("chDays"), {
    type: "line",
    data: { labels: rows.map((r) => fmtDate(r.date, { day: "numeric", month: "short" })),
      datasets: ["ortools_average", "alns_average", "rotaist"].map((m) => ({
        label: SHORT[m], data: rows.map((r) => r[m].on_time_pct), borderColor: COLOR[m], backgroundColor: COLOR[m],
        borderWidth: m === "rotaist" ? 2.5 : 1.5, pointRadius: m === "rotaist" ? 3.5 : 2.5, tension: 0.25 })) },
    options: { interaction: { mode: "index", intersect: false },
      scales: { y: { max: 100, title: { display: true, text: "% on time" } } } },
  });
}

/* ================================================================ boot */

(async function main() {
  chartDefaults();
  const [days, roads] = await Promise.all([getJSON("data/days.json"), getJSON("data/roads.json")]);
  S.roads = roads;
  const note = ["rough day for static plans", "typical day"];
  $("day").innerHTML = days.map((d, i) => `<option value="${d}">${fmtDate(d)} — ${note[i] || ""}</option>`).join("");
  S.day = await getJSON(`data/day_${days[0]}.json`);
  buildPanes();
  setupTimelineScrub();
  refresh();

  $("day").addEventListener("change", (e) => loadDay(e.target.value));
  $("time").addEventListener("input", (e) => { S.t = Number(e.target.value); refresh(); });
  $("play").addEventListener("click", () => setPlaying(!S.playing));
  document.querySelectorAll("#viewSeg button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("#viewSeg button").forEach((x) => x.classList.toggle("on", x === b));
    S.view = Number(b.dataset.v); buildPanes(); refresh();
  }));
  document.querySelectorAll("#srcSeg button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("#srcSeg button").forEach((x) => x.classList.toggle("on", x === b));
    S.src = b.dataset.s; refresh();
  }));
  requestAnimationFrame(tick);

  const [s, tr] = await Promise.all([getJSON("data/summary.json"), getJSON("data/traffic.json")]);
  renderHero(s);
  renderInsights(s);
  const fleets = Object.keys(s.backtest);
  $("fleetSeg").innerHTML = fleets.map((f) => `<button data-f="${f}" class="${f === "5" ? "on" : ""}">${f} vans</button>`).join("");
  $("fleetSeg").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    $("fleetSeg").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    renderScatter(s, b.dataset.f);
  }));
  renderScatter(s, "5");
  renderEta(s);
  renderFleet(s);
  renderHeat(tr);
  renderForecast(tr);
  renderDays(s);
})();

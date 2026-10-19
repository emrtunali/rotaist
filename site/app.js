/* Rotaist web app: replay a real January 2025 day and the back-test results. */
"use strict";

const VAN_COLORS = ["#38bdf8", "#a78bfa", "#f472b6", "#facc15", "#34d399", "#fb923c"];
const PLAN_ORDER = ["ortools_freeflow", "ortools_average", "rotaist"];
const LABEL = {
  ortools_freeflow: "OR-Tools · free-flow matrix",
  ortools_average: "OR-Tools · typical daytime matrix",
  alns_average: "Rotaist solver · typical matrix",
  rotaist: "Rotaist · forecast, time-dependent",
  oracle: "Oracle · perfect knowledge",
};
const SHORT = {
  ortools_freeflow: "OR-Tools free-flow", ortools_average: "OR-Tools typical",
  alns_average: "ALNS typical", rotaist: "Rotaist", oracle: "Oracle",
};

const state = { day: null, plan: "rotaist", t: 9 * 3600, playing: false, src: "ratio", traffic: true };
const $ = (id) => document.getElementById(id);
const hhmm = (s) => `${String(Math.floor(s / 3600)).padStart(2, "0")}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;

const map = new maplibregl.Map({
  container: "mapview",
  style: "https://tiles.openfreemap.org/styles/dark",
  center: [29.07, 41.0], zoom: 11.6, attributionControl: { compact: true },
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
new ResizeObserver(() => map.resize()).observe($("mapview"));
// keep the study area visible beside the floating panel on wide screens
const fitArea = () => map.fitBounds([[29.005, 40.955], [29.2, 41.07]],
  { padding: innerWidth > 760 ? { left: 410, right: 30, top: 30, bottom: 30 } : 10, duration: 0 });
map.once("load", fitArea);

async function getJSON(url) { const r = await fetch(url); if (!r.ok) throw new Error(url); return r.json(); }

/* ---------------------------------------------------------------- map layers */

function trafficFeatures() {
  const d = state.day, h = Math.min(23, Math.floor(state.t / 3600));
  const src = d.traffic[state.src];
  return {
    type: "FeatureCollection",
    features: d.traffic.lon.map((lon, i) => ({
      type: "Feature", geometry: { type: "Point", coordinates: [lon, d.traffic.lat[i]] },
      properties: { r: src[h][i] },
    })),
  };
}

function routeFeatures() {
  const p = state.day.plans[state.plan];
  return {
    type: "FeatureCollection",
    features: p.vans.map((v, k) => ({
      type: "Feature", geometry: { type: "LineString", coordinates: v.coords },
      properties: { color: VAN_COLORS[k % VAN_COLORS.length] },
    })),
  };
}

function posAt(v, t) {
  const T = v.times;
  if (t <= T[0]) return v.coords[0];
  if (t >= T[T.length - 1]) return v.coords[v.coords.length - 1];
  let lo = 0, hi = T.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (T[m] <= t) lo = m; else hi = m; }
  const f = T[hi] > T[lo] ? (t - T[lo]) / (T[hi] - T[lo]) : 0;
  const a = v.coords[lo], b = v.coords[hi];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
}

function vanFeatures() {
  const p = state.day.plans[state.plan];
  return {
    type: "FeatureCollection",
    features: p.vans.map((v, k) => ({
      type: "Feature", geometry: { type: "Point", coordinates: posAt(v, state.t) },
      properties: { color: VAN_COLORS[k % VAN_COLORS.length] },
    })),
  };
}

function stopFeatures() {
  const d = state.day, p = d.plans[state.plan], t = state.t;
  return {
    type: "FeatureCollection",
    features: d.stops.lon.map((lon, i) => {
      const arr = p.arrival[i], end = d.stops.tw_end[i];
      const status = arr > t ? "wait" : Math.max(arr, d.stops.tw_start[i]) > end ? "late" : "ok";
      return {
        type: "Feature", geometry: { type: "Point", coordinates: [lon, d.stops.lat[i]] },
        properties: { i, status },
      };
    }),
  };
}

function setupLayers() {
  map.addSource("traffic", { type: "geojson", data: trafficFeatures() });
  map.addLayer({
    id: "traffic", type: "circle", source: "traffic",
    paint: {
      "circle-radius": ["interpolate", ["exponential", 2], ["zoom"], 10, 7, 13, 50, 15, 200],
      "circle-blur": 1,
      "circle-opacity": ["interpolate", ["linear"], ["get", "r"], 0.3, 0.5, 0.7, 0.25, 0.9, 0.0],
      "circle-color": ["interpolate", ["linear"], ["get", "r"], 0.3, "#dc2626", 0.55, "#f59e0b", 0.85, "#1e293b"],
    },
  });
  map.addSource("routes", { type: "geojson", data: routeFeatures() });
  map.addLayer({
    id: "routes", type: "line", source: "routes",
    layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": ["get", "color"], "line-width": 2.2, "line-opacity": 0.55 },
  });
  map.addSource("stops", { type: "geojson", data: stopFeatures() });
  map.addLayer({
    id: "stops", type: "circle", source: "stops",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 2.6, 15, 6],
      "circle-color": ["match", ["get", "status"], "ok", "#22c55e", "late", "#ef4444", "#64748b"],
      "circle-stroke-color": "#0b0f17", "circle-stroke-width": 0.6,
    },
  });
  map.addSource("vans", { type: "geojson", data: vanFeatures() });
  map.addLayer({
    id: "vans-halo", type: "circle", source: "vans",
    paint: { "circle-radius": 11, "circle-color": ["get", "color"], "circle-opacity": 0.25 },
  });
  map.addLayer({
    id: "vans", type: "circle", source: "vans",
    paint: { "circle-radius": 5.5, "circle-color": ["get", "color"], "circle-stroke-color": "#fff",
      "circle-stroke-width": 1.5 },
  });
  map.addSource("depot", { type: "geojson", data: { type: "Point", coordinates: state.day.depot } });
  map.addLayer({
    id: "depot", type: "circle", source: "depot",
    paint: { "circle-radius": 8, "circle-color": "#0b0f17", "circle-stroke-color": "#38bdf8", "circle-stroke-width": 3 },
  });

  map.on("click", "stops", (e) => {
    const i = e.features[0].properties.i, d = state.day, p = d.plans[state.plan];
    const late = Math.max(p.arrival[i], d.stops.tw_start[i]) - d.stops.tw_end[i];
    new maplibregl.Popup({ closeButton: false }).setLngLat(e.lngLat).setHTML(
      `<b>Order #${i + 1}</b><br>Slot ${hhmm(d.stops.tw_start[i])}–${hhmm(d.stops.tw_end[i])}<br>` +
      `Promised ETA ${hhmm(p.promised[i])}<br>Real arrival ${hhmm(p.arrival[i])}` +
      (late > 0 ? ` <span style="color:#ef4444">(${Math.round(late / 60)} min late)</span>` : " ✓")).addTo(map);
  });
  map.on("mouseenter", "stops", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "stops", () => (map.getCanvas().style.cursor = ""));
}

/* ---------------------------------------------------------------- panel */

function renderPlans() {
  const d = state.day;
  $("plans").innerHTML = PLAN_ORDER.map((m) => {
    const k = d.plans[m].kpi;
    return `<button data-m="${m}" class="${m === state.plan ? "on" : ""}">${LABEL[m]}
      <small>${k.on_time_pct.toFixed(1)}% on time · ${Math.round(k.late_minutes)} late min · ETA ±${k.eta_mae_min.toFixed(1)} min</small></button>`;
  }).join("");
  $("plans").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    state.plan = b.dataset.m; renderPlans(); renderKPI();
    map.getSource("routes").setData(routeFeatures()); refresh();
  }));
}

function renderKPI() {
  const d = state.day;
  const rows = PLAN_ORDER.map((m) => {
    const k = d.plans[m].kpi;
    return `<tr class="${m === state.plan ? "on" : ""}"><td>${SHORT[m]}</td>
      <td class="${k.on_time_pct >= 99 ? "good" : k.on_time_pct < 95 ? "bad" : ""}">${k.on_time_pct.toFixed(1)}%</td>
      <td>${Math.round(k.late_minutes)}</td><td>${k.duty_hours.toFixed(1)}</td><td>±${k.eta_mae_min.toFixed(1)}</td></tr>`;
  }).join("");
  $("kpi").innerHTML = `<tr><th>Plan (real traffic)</th><th>On time</th><th>Late min</th><th>Duty h</th><th>ETA err</th></tr>${rows}`;
}

function renderLive() {
  const d = state.day, p = d.plans[state.plan], t = state.t;
  let done = 0, late = 0;
  p.arrival.forEach((a, i) => {
    if (a <= t) { done++; if (Math.max(a, d.stops.tw_start[i]) > d.stops.tw_end[i]) late++; }
  });
  const h = Math.min(23, Math.floor(t / 3600));
  const r = d.traffic.ratio[h].filter((x) => x != null);
  const avg = r.reduce((s, x) => s + x, 0) / r.length;
  $("live").innerHTML = `<b>${done}</b>/${d.orders} delivered · <b style="color:${late ? "#ef4444" : "#22c55e"}">${late}</b> late<br>` +
    `Network speed now: <b>${Math.round(avg * 100)}%</b> of free flow`;
}

function refresh() {
  if (!state.day || !map.getSource("vans")) return;
  $("clock").textContent = hhmm(state.t);
  $("time").value = state.t;
  map.getSource("vans").setData(vanFeatures());
  map.getSource("stops").setData(stopFeatures());
  const h = Math.floor(state.t / 3600);
  if (h !== state.lastHour || state.srcChanged) {
    map.getSource("traffic").setData(trafficFeatures());
    state.lastHour = h; state.srcChanged = false;
  }
  renderLive();
}

let last = null;
function tick(ts) {
  if (state.playing) {
    if (last != null) {
      state.t += ((ts - last) / 1000) * Number($("speed").value);
      if (state.t >= 22 * 3600) { state.t = 22 * 3600; state.playing = false; $("play").textContent = "▶"; }
      refresh();
    }
    last = ts;
  } else last = null;
  requestAnimationFrame(tick);
}

async function loadDay(date) {
  state.day = await getJSON(`data/day_${date}.json`);
  $("dayinfo").textContent = `${state.day.orders} orders · ${state.day.vans} vans`;
  renderPlans(); renderKPI();
  if (map.getSource("routes")) {
    map.getSource("routes").setData(routeFeatures());
    map.getSource("depot").setData({ type: "Point", coordinates: state.day.depot });
    state.srcChanged = true; refresh();
  }
}

/* ---------------------------------------------------------------- results */

function chartDefaults() {
  Chart.defaults.color = "#94a3b8";
  Chart.defaults.borderColor = "#1f2937";
  Chart.defaults.font.family = "Inter, system-ui, sans-serif";
}

const METHOD_COLOR = {
  ortools_freeflow: "#64748b", ortools_average: "#94a3b8", alns_average: "#f59e0b",
  rotaist: "#38bdf8", oracle: "#22c55e",
};

function renderResults(s) {
  chartDefaults();
  const fleets = Object.keys(s.backtest);
  const methods = Object.keys(LABEL).filter((m) => s.backtest[fleets[0]][m]);
  const mid = fleets.includes("5") ? "5" : fleets[0];
  const R = s.backtest[mid];
  const fc = s.forecast;

  $("lead").innerHTML = `${s.backtest_days} held-out weekdays (January 2025), ${s.orders} orders a day from a
    Kadıköy micro-hub. Every planner plans the evening before; every plan is then driven on that day's real
    İBB traffic. Same orders, same vans, same objective — only the solver and what it knows about traffic change.`;

  const card = (big, label, sub, good) =>
    `<div class="card"><div class="big ${good ? "good" : ""}">${big}</div><div>${label}</div><small>${sub}</small></div>`;
  const saved = 100 * (1 - R.rotaist.duty_hours / R.ortools_average.duty_hours);
  const w = s.wins_vs_ortools_average;
  $("cards").innerHTML = [
    card(`${R.rotaist.on_time_pct.toFixed(1)}%`, "on time with Rotaist",
      `vs ${R.ortools_average.on_time_pct.toFixed(1)}% OR-Tools (typical matrix), ${mid} vans`, true),
    card(`−${saved.toFixed(0)}%`, "driver hours for the same orders",
      `${R.rotaist.duty_hours.toFixed(1)} h vs ${R.ortools_average.duty_hours.toFixed(1)} h per day`, true),
    card(`±${R.rotaist.eta_mae_min.toFixed(1)} min`, "ETA promise error",
      `vs ±${R.ortools_average.eta_mae_min.toFixed(1)} min static, ±${R.ortools_freeflow.eta_mae_min.toFixed(0)} min free-flow`, true),
    card(`${fc.model.mae_kmh.toFixed(2)} km/h`, "day-ahead speed error",
      `vs ${fc.daily_mean.mae_kmh.toFixed(1)} km/h flat average · ≥ as punctual in ${w.a_better_or_equal}/${w.cases} runs`, false),
  ].join("");

  new Chart($("chOnTime"), {
    type: "bar",
    data: {
      labels: fleets.map((f) => `${f} vans`),
      datasets: methods.map((m) => ({
        label: SHORT[m], backgroundColor: METHOD_COLOR[m],
        data: fleets.map((f) => s.backtest[f][m].on_time_pct),
      })),
    },
    options: { responsive: true, maintainAspectRatio: false,
      scales: { y: { min: Math.max(0, Math.floor(Math.min(...fleets.flatMap((f) => methods.map((m) => s.backtest[f][m].on_time_pct))) / 10) * 10), max: 100, title: { display: true, text: "% on time" } } } },
  });
  new Chart($("chEta"), {
    type: "bar",
    data: {
      labels: methods.map((m) => SHORT[m]),
      datasets: [{ label: `ETA MAE, ${mid} vans`, data: methods.map((m) => R[m].eta_mae_min),
        backgroundColor: methods.map((m) => METHOD_COLOR[m]) }],
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
      scales: { y: { title: { display: true, text: "minutes" } } } },
  });
  const hours = [...Array(24).keys()].map((h) => `${h}:00`);
  const FC = { free_flow: ["No traffic", "#475569"], daily_mean: ["Flat daily mean", "#94a3b8"],
    last_week: ["Last week", "#f59e0b"], hour_of_week: ["Hour-of-week", "#a78bfa"], model: ["Rotaist model", "#38bdf8"] };
  new Chart($("chFc"), {
    type: "line",
    data: { labels: hours, datasets: Object.entries(FC).map(([k, [l, c]]) => ({
      label: l, data: fc[k].by_hour, borderColor: c, backgroundColor: c, pointRadius: 0, tension: 0.3,
      borderWidth: k === "model" ? 3 : 1.5 })) },
    options: { responsive: true, maintainAspectRatio: false,
      scales: { y: { title: { display: true, text: "km/h" } } } },
  });
  const days = s.per_day.filter((r) => String(r.vans) === mid).sort((a, b) => a.date.localeCompare(b.date));
  new Chart($("chDays"), {
    type: "bar",
    data: { labels: days.map((r) => r.date.slice(5)), datasets: ["ortools_average", "alns_average", "rotaist"].map((m) => ({
      label: SHORT[m], backgroundColor: METHOD_COLOR[m], data: days.map((r) => r[m].late_minutes) })) },
    options: { responsive: true, maintainAspectRatio: false,
      scales: { y: { title: { display: true, text: "late minutes" } } } },
  });
}

/* ---------------------------------------------------------------- boot */

(async function main() {
  const days = await getJSON("data/days.json");
  $("day").innerHTML = days.map((d) => `<option>${d}</option>`).join("");
  $("day").addEventListener("change", (e) => loadDay(e.target.value));
  await loadDay(days[0]);
  const ready = () => { setupLayers(); refresh(); };
  if (map.loaded()) ready(); else map.on("load", ready);

  $("time").addEventListener("input", (e) => { state.t = Number(e.target.value); refresh(); });
  $("play").addEventListener("click", () => {
    state.playing = !state.playing;
    if (state.playing && state.t >= 22 * 3600) state.t = 8 * 3600;
    $("play").textContent = state.playing ? "❚❚" : "▶";
  });
  $("showTraffic").addEventListener("change", (e) =>
    map.setLayoutProperty("traffic", "visibility", e.target.checked ? "visible" : "none"));
  document.querySelectorAll("input[name=tsrc]").forEach((r) => r.addEventListener("change", (e) => {
    state.src = e.target.value; state.srcChanged = true; refresh();
  }));
  requestAnimationFrame(tick);
  renderResults(await getJSON("data/summary.json"));
})();

/* KhetDrishti frontend — plain JS, no build step.
   Sections: state & helpers · status bar · map & search · form · field report · charts · ask (SSE) · voice · monitor */

// ============================================================ state & helpers
const state = { lat: null, lon: null, marker: null, history: [], charts: {}, recording: null, busy: false };
const $ = (id) => document.getElementById(id);
const LANG_BCP47 = { hi: "hi-IN", mr: "mr-IN", te: "te-IN", ta: "ta-IN", kn: "kn-IN", bn: "bn-IN", gu: "gu-IN", pa: "pa-IN", ml: "ml-IN", en: "en-IN" };

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtDate(iso, opts = { day: "numeric", month: "short" }) {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? iso + "T00:00:00" : iso);
  return isNaN(d) ? iso : d.toLocaleDateString("en-IN", opts);
}
function fmtTime(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
function v(x, unit = "") { return x === null || x === undefined ? '<span class="unavail">n/a</span>' : `${esc(x)}${unit}`; }
// Plain-language version of a provider error, keeping the technical code in brackets for transparency.
function friendlyErr(e) {
  if (!e) return "Unavailable right now.";
  const s = String(e);
  if (/HTTP 5\d\d/.test(s)) return `Provider is temporarily down (${s.match(/HTTP 5\d\d/)[0]}). Nothing is estimated in its place.`;
  if (/HTTP 429/.test(s)) return "Provider is rate-limiting requests right now (HTTP 429). Try again in a minute.";
  if (/timed out|Timeout/i.test(s)) return "Provider took too long to answer. It will be retried on the next request.";
  if (/Connect/i.test(s)) return "Could not connect to the provider right now.";
  if (/simulated outage/i.test(s)) return "Simulated outage (resilience demo).";
  return s;
}

async function api(path, opts = {}, timeoutMs = 60000) {
  // Every request has a deadline so the UI can never hang forever on a stuck connection.
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(path, { headers: { "Content-Type": "application/json" }, signal: ctrl.signal, ...opts });
  } catch (err) {
    if (err.name === "AbortError") throw new Error(`The server took longer than ${timeoutMs / 1000} s. Please try again.`);
    throw new Error(navigator.onLine ? "Cannot reach the KhetDrishti server." : "You are offline — check the internet connection.");
  } finally { clearTimeout(timer); }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j); } catch (_) {}
    throw new Error(msg);
  }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res;
}

function plotPayload() {
  if (state.lat === null) throw new Error("Drop a pin on your field first (click the map).");
  return { lat: state.lat, lon: state.lon, crop: $("crop").value, sowing_date: $("sowing").value || null };
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".tabpane").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  if (name === "monitor") loadMonitor();
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

// ============================================================ status bar
async function loadStatus() {
  try {
    const s = await api("/api/status");
    const dots = Object.entries(s.sources).map(([k, ok]) =>
      `<span title="${esc((s.source_details || {})[k] || "")}"><i class="dot ${ok ? "ok" : "bad"}"></i>${esc(k)}</span>`);
    if (!s.config.llm) dots.push('<span><i class="dot bad"></i>LLM key missing</span>');
    if (!s.config.mandi_key) dots.push('<span title="Using data.gov.in public sample key (10 rows/call)"><i class="dot warn"></i>mandi sample key</span>');
    $("status").innerHTML = dots.join("");
    showOutages(s.simulated_outages || {});
  } catch (_) {
    $("status").innerHTML = '<span><i class="dot bad"></i>server unreachable</span>';
  }
}

// ============================================================ map & search
const map = L.map("map", { zoomControl: true }).setView([22.5, 79], 5);
const satellite = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  { maxZoom: 19, attribution: "Imagery © Esri, Maxar, Earthstar Geographics" }).addTo(map);
const labels = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
  { maxZoom: 19 }).addTo(map);
const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap contributors" });
L.control.layers({ Satellite: satellite, Map: osm }, { Labels: labels }, { position: "topright" }).addTo(map);
let plotSquare = null;

function setPin(lat, lon, zoom) {
  state.lat = +(+lat).toFixed(5);
  state.lon = +(+lon).toFixed(5);
  if (state.marker) state.marker.setLatLng([lat, lon]); else state.marker = L.marker([lat, lon]).addTo(map);
  // The 80 m x 80 m square the satellite statistics are computed over.
  const dLat = 40 / 111320, dLon = 40 / (111320 * Math.cos(lat * Math.PI / 180));
  const b = [[lat - dLat, lon - dLon], [lat + dLat, lon + dLon]];
  if (plotSquare) plotSquare.setBounds(b); else plotSquare = L.rectangle(b, { color: "#facc15", weight: 2, fill: false }).addTo(map);
  if (zoom) map.setView([lat, lon], zoom);
  $("pinInfo").innerHTML = `Pin: <b>${state.lat}, ${state.lon}</b> · yellow square = 80 m plot analysed by satellite`;
}
map.on("click", (e) => setPin(e.latlng.lat, e.latlng.lng));

let searchTimer = null;
$("placeSearch").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  if (q.length < 3) { $("searchResults").hidden = true; return; }
  searchTimer = setTimeout(async () => {
    try {
      const res = await api(`/api/geocode?q=${encodeURIComponent(q)}`);
      $("searchResults").innerHTML = res.length
        ? res.map((r) => `<li data-lat="${r.lat}" data-lon="${r.lon}">${esc(r.name)}</li>`).join("")
        : '<li class="muted">No places found in India</li>';
      $("searchResults").hidden = false;
    } catch (err) {
      $("searchResults").innerHTML = `<li class="muted">${esc(err.message)}</li>`;
      $("searchResults").hidden = false;
    }
  }, 450);
});
$("searchResults").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-lat]");
  if (!li) return;
  map.setView([+li.dataset.lat, +li.dataset.lon], 14);
  $("searchResults").hidden = true;
  $("pinInfo").textContent = "Now click on the exact field to drop the pin.";
});

document.querySelectorAll(".examples .link").forEach((b) => b.addEventListener("click", () => {
  setPin(+b.dataset.lat, +b.dataset.lon, 16);
  $("crop").value = b.dataset.crop;
  $("sowing").value = b.dataset.sow;
}));

// ============================================================ form
async function loadCrops() {
  const crops = await api("/api/crops");
  $("crop").innerHTML = crops.map((c) => `<option value="${c.id}">${esc(c.names.en)}${c.names.hi ? " · " + esc(c.names.hi) : ""}</option>`).join("");
}
$("sowing").max = new Date().toISOString().slice(0, 10);

$("plotForm").addEventListener("submit", (e) => { e.preventDefault(); runReport(); });
$("saveBtn").addEventListener("click", async () => {
  try {
    const p = plotPayload();
    const saved = await api("/api/plots", { method: "POST", body: JSON.stringify({ ...p, language: $("language").value }) });
    $("saveBtn").textContent = "Saved ✓";
    setTimeout(() => ($("saveBtn").textContent = "Save & monitor"), 2000);
    if (document.querySelector(".tab.active").dataset.tab === "monitor") loadMonitor();
    return saved;
  } catch (err) { alert(err.message); }
});

// ============================================================ field report
const LOAD_STEPS = ["Locating plot (OpenStreetMap)", "Sentinel-2 satellite NDVI", "16-day weather & soil moisture",
  "10-year rainfall baseline (ERA5)", "Soil texture (SoilGrids)", "Live mandi prices (Agmarknet)", "Risk engine"];

async function runReport() {
  let payload;
  try { payload = plotPayload(); } catch (err) { alert(err.message); return; }
  switchTab("report");
  $("reportEmpty").hidden = true; $("report").hidden = true; $("reportLoading").hidden = false;
  $("loadSteps").innerHTML = LOAD_STEPS.map((s) => `<li>${s}</li>`).join("");
  const items = [...$("loadSteps").children];
  let i = 0;
  const tick = setInterval(() => { if (i < items.length - 1) items[i++].classList.add("done"); }, 900);
  $("analyseBtn").disabled = true;
  try {
    const rep = await api("/api/report", { method: "POST", body: JSON.stringify(payload) }, 90000);
    renderReport(rep);
  } catch (err) {
    $("reportEmpty").hidden = false;
    $("reportEmpty").innerHTML = `<h2>Could not analyse this plot</h2><p>${esc(err.message)}</p>
      <button class="btn primary" id="retryReport" style="flex:0 0 auto">Try again</button>`;
    $("retryReport").addEventListener("click", runReport);
  } finally {
    clearInterval(tick);
    $("reportLoading").hidden = true;
    $("analyseBtn").disabled = false;
  }
}

function renderReport(r) {
  $("report").hidden = false;
  const loc = r.location || {};
  $("rPlace").textContent = [loc.village, loc.district, loc.state].filter(Boolean).join(", ") || `${r.plot.lat}, ${r.plot.lon}`;
  const cropSel = $("crop").selectedOptions[0]?.textContent || r.plot.crop;
  $("rMeta").textContent = `${cropSel} · sown ${r.plot.sowing_date ? fmtDate(r.plot.sowing_date, { day: "numeric", month: "short", year: "numeric" }) : "date not given"} · generated ${fmtTime(r.generated_at)}`;
  const st = r.stage || {};
  $("rStage").textContent = st.known ? `Day ${st.days_after_sowing} · ${st.stage_label}` : "Stage unknown (add sowing date)";

  // Risks
  $("risks").innerHTML = (r.risks || []).length
    ? r.risks.map((k) => `
      <div class="risk ${k.severity}">
        <h4>${esc(k.title)} <span class="sev">${k.severity}</span></h4>
        <p>${esc(k.detail)}</p>
        <p class="act">→ ${esc(k.action)}</p>
        <p class="ev">${k.evidence.map((e) => `${esc(e.label)}: <b>${esc(e.value)}${e.unit ? " " + esc(e.unit) : ""}</b> <i>(${esc(e.source)})</i>`).join(" · ")}</p>
      </div>`).join("")
    : '<p class="muted">No risks detected for the coming week.</p>';
  const errs = r.errors || {};
  if (Object.keys(errs).length) {
    $("risks").insertAdjacentHTML("beforeend", `<div class="risk"><h4>Some data unavailable <span class="sev">info</span></h4>
      <p class="ev">${Object.entries(errs).map(([k, e]) => `<b>${esc(k)}</b>: ${esc(friendlyErr(e))}`).join("<br>")}</p>
      <p class="ev">Nothing is estimated in its place — affected checks are skipped.</p></div>`);
  }

  renderNdvi(r.satellite, errs.satellite);
  renderWeather(r.weather, errs.weather);
  renderWater(r.water, r.weather);
  renderMarket(r.market, errs.market);
  renderSoil(r.soil, r.rainfall, errs);

  const srcs = r.sources || [];
  $("srcCount").textContent = srcs.length;
  $("sourceList").innerHTML = srcs.map((s) => `<li>${esc(s.source)} <span class="mode ${esc(s.mode)}">${esc(s.mode)}</span>
     <span class="muted">${fmtTime(s.fetched_at)}</span>${s.note ? ` — <i>${esc(s.note)}</i>` : ""}</li>`).join("");
}

// ============================================================ charts
function destroyChart(key) { if (state.charts[key]) { state.charts[key].destroy(); delete state.charts[key]; } }

function renderNdvi(sat, err) {
  destroyChart("ndvi");
  if (!sat) {
    $("ndviBadge").textContent = "unavailable"; $("ndviBadge").className = "chip warn";
    $("ndviNote").textContent = friendlyErr(err);
    return;
  }
  $("ndviBadge").textContent = `NDVI ${sat.latest_ndvi} · ${sat.health_class}`;
  $("ndviBadge").className = "chip" + (sat.latest_ndvi < 0.3 ? " warn" : "");
  const DAY = 86400000;
  const cur = sat.series.map((s) => ({ x: new Date(s.date).getTime(), y: s.ndvi }));
  const ly = (sat.last_year_series || []).map((s) => ({ x: new Date(s.date).getTime() + 365 * DAY, y: s.ndvi }));
  state.charts.ndvi = new Chart($("ndviChart"), {
    type: "line",
    data: { datasets: [
      { label: "This season", data: cur, borderColor: "#1f6f43", backgroundColor: "#1f6f43", tension: .3, pointRadius: 3 },
      { label: "Same period last year", data: ly, borderColor: "#9aa59f", backgroundColor: "#9aa59f", borderDash: [5, 4], tension: .3, pointRadius: 2 },
    ] },
    options: {
      maintainAspectRatio: false, parsing: false,
      scales: {
        x: { type: "linear", ticks: { callback: (val) => fmtDate(new Date(val).toISOString().slice(0, 10)), maxTicksLimit: 6 } },
        y: { min: 0, max: 1, title: { display: true, text: "NDVI" } },
      },
      plugins: { tooltip: { callbacks: { title: (it) => fmtDate(new Date(it[0].parsed.x).toISOString().slice(0, 10), { day: "numeric", month: "short", year: "numeric" }) } } },
    },
  });
  const parts = [`${sat.series.length} cloud-free passes in 120 days`, `latest ${fmtDate(sat.latest_date)} (${sat.days_since_latest} days ago)`];
  if (sat.change_vs_last_year_pct !== undefined) parts.push(`${sat.change_vs_last_year_pct > 0 ? "+" : ""}${sat.change_vs_last_year_pct}% vs last year`);
  parts.push(`${sat.plot_size_m} m plot · ${sat.provider}`);
  $("ndviNote").innerHTML = esc(parts.join(" · ")) + (sat.interpretation ? `<br><b>Note:</b> ${esc(sat.interpretation)}` : "");
  if (sat.interpretation) { $("ndviBadge").textContent = `NDVI ${sat.latest_ndvi} · crop not yet visible`; $("ndviBadge").className = "chip warn"; }
}

function renderWeather(w, err) {
  destroyChart("wx");
  if (!w) {
    $("wxBadge").textContent = "unavailable"; $("wxBadge").className = "chip warn";
    $("wxNote").textContent = friendlyErr(err);
    return;
  }
  const days = w.days;
  const total = days.slice(0, 7).reduce((a, d) => a + (d.rain_mm || 0), 0);
  $("wxBadge").textContent = `${w.provider} · ${total.toFixed(1)} mm in 7 days`;
  $("wxBadge").className = "chip";
  state.charts.wx = new Chart($("wxChart"), {
    data: {
      labels: days.map((d) => fmtDate(d.date)),
      datasets: [
        { type: "bar", label: "Rain (mm)", data: days.map((d) => d.rain_mm), backgroundColor: "#60a5fa", yAxisID: "y1" },
        { type: "line", label: "Max °C", data: days.map((d) => d.tmax), borderColor: "#dc2626", backgroundColor: "#dc2626", pointRadius: 2, tension: .3, yAxisID: "y" },
        { type: "line", label: "Min °C", data: days.map((d) => d.tmin), borderColor: "#f59e0b", backgroundColor: "#f59e0b", pointRadius: 2, tension: .3, yAxisID: "y" },
      ],
    },
    options: {
      maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      scales: { y: { position: "left", title: { display: true, text: "°C" } }, y1: { position: "right", beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: "mm" } } },
    },
  });
  $("wxNote").textContent = `ET0: ${w.et0_method}`;
}

const REC_TEXT = {
  irrigate_now: ["Irrigate now", "high"], irrigate_on_due_date: ["Irrigation due soon", "medium"],
  wait_rain_expected: ["Hold — rain expected", "low"], no_irrigation_needed: ["No irrigation needed this week", "low"],
  soil_moisture_unavailable: ["Soil moisture unavailable", "medium"],
  past_season: ["Crop past its season — harvest", "medium"],
};
function renderWater(wb, w) {
  if (!wb) { $("waterCard").innerHTML = '<h3>Water balance</h3><p class="unavail">Needs weather data — unavailable.</p>'; return; }
  const [txt, sev] = REC_TEXT[wb.recommendation] || [wb.recommendation, "low"];
  $("waterCard").innerHTML = `
    <h3>Irrigation (FAO-56 water balance)</h3>
    <div class="big ${sev}">${esc(txt)}${wb.suggested_irrigation_mm ? ` · ~${esc(wb.suggested_irrigation_mm)} mm` : ""}${wb.recommendation === "irrigate_on_due_date" ? ` on ${fmtDate(wb.irrigation_due_date)}` : ""}</div>
    <dl class="kv">
      <dt>Available soil water (0–27 cm)</dt><dd>${wb.at_field_capacity ? `Full <span class="muted small">(${esc(wb.root_zone_moisture_m3m3)} ≥ field capacity ${esc(wb.field_capacity_m3m3)} m³/m³)</span>` : v(wb.available_water_pct, "%")}</dd>
      <dt>Crop water use, next ${wb.horizon_days} days</dt><dd>${v(wb.crop_water_use_mm, " mm")}</dd>
      <dt>Forecast rain / effective</dt><dd>${v(wb.rain_forecast_mm)} / ${v(wb.effective_rain_mm, " mm")}</dd>
      <dt>Net balance</dt><dd>${v(wb.net_balance_mm, " mm")}</dd>
      <dt>Soil (FC / WP)</dt><dd>${esc(wb.soil_constants?.texture)} (${v(wb.field_capacity_m3m3 ?? wb.soil_constants?.fc)} / ${v(wb.wilting_point_m3m3 ?? wb.soil_constants?.wp)})</dd>
    </dl>
    <p class="muted small">${esc([wb.context_note, wb.stage_note, wb.assumption, wb.stage_assumption].filter(Boolean).join(" · ") || "Soil moisture: Open-Meteo land-surface model; Kc: FAO-56 Table 12.")}</p>`;
}

function renderMarket(m, err) {
  if (!m) { $("marketCard").innerHTML = `<h3>Mandi prices</h3><p class="unavail">${esc(friendlyErr(err))}</p>`; return; }
  const rows = (m.nearest_markets.length ? m.nearest_markets : m.home_district_markets).slice(0, 5);
  const trend = (k) => m.trend[k]
    ? `${m.trend[k].change_pct > 0 ? "▲" : "▼"} ${Math.abs(m.trend[k].change_pct)}% <span class="muted small">(${m.trend[k].markets_compared} mkts)</span>`
    : '<span class="unavail">too few markets</span>';
  $("marketCard").innerHTML = `
    <h3>Mandi prices — ${esc(m.commodity)}</h3>
    <div class="big">Rs ${esc(m.state_median_modal)}<span class="muted small"> /quintal · ${esc(m.state)} median · ${fmtDate(m.arrival_date)}</span></div>
    <dl class="kv"><dt>vs 7 days ago</dt><dd>${trend("7d")}</dd><dt>vs 30 days ago</dt><dd>${trend("30d")}</dd>
      <dt>Best modal price</dt><dd>Rs ${esc(m.highest_modal_market.modal)} · ${esc(m.highest_modal_market.market)}</dd></dl>
    <table><thead><tr><th>Market</th><th class="num">Modal</th><th class="num">km</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td>${esc(r.market)}<br><span class="muted small">${esc(r.district)}</span></td><td class="num">${esc(r.modal)}</td><td class="num">${r.distance_km ?? "—"}</td></tr>`).join("")}
    </tbody></table>
    <p class="muted small">${esc(m.markets_reporting)} markets reported on ${fmtDate(m.arrival_date)} · Agmarknet via data.gov.in
      ${(m.trend_notes || []).length ? `<br>Trend: ${esc(m.trend_notes.join("; "))}` : ""}</p>`;
}

function renderSoil(s, rf, errs) {
  const soil = s ? `
      <dl class="kv"><dt>Texture</dt><dd>${esc(s.texture)}</dd><dt>Sand / silt / clay</dt><dd>${s.sand_pct} / ${s.silt_pct} / ${s.clay_pct} %</dd>
      <dt>pH</dt><dd>${v(s.ph)}</dd><dt>Organic carbon</dt><dd>${v(s.organic_carbon_g_per_kg, " g/kg")}</dd></dl>
      <p class="muted small">${esc(s.note || s.resolution)}</p>`
    : `<p class="unavail">${esc(friendlyErr(errs.soil))}</p>`;
  const rain = rf ? `
      <dl class="kv"><dt>Rain ${fmtDate(rf.window_start)}–${fmtDate(rf.window_end)}</dt><dd>${esc(rf.rain_mm)} mm</dd>
      <dt>${rf.baseline_years}-year normal</dt><dd>${esc(rf.normal_mm)} mm</dd>
      <dt>Departure</dt><dd>${rf.departure_pct === null ? "—" : (rf.departure_pct > 0 ? "+" : "") + rf.departure_pct + "%"} · ${esc(rf.imd_category)}</dd></dl>`
    : `<p class="unavail">${esc(friendlyErr(errs.rainfall))}</p>`;
  $("soilCard").innerHTML = `<h3>Soil (SoilGrids)</h3>${soil}<h3 style="margin-top:10px">Rainfall vs normal (ERA5)</h3>${rain}`;
}

// ============================================================ ask (Server-Sent Events)
document.querySelectorAll("#suggestions .chip-btn").forEach((b) => b.addEventListener("click", () => { $("question").value = b.textContent; ask(); }));
$("askForm").addEventListener("submit", (e) => { e.preventDefault(); ask(); });

function renderTags(text) {
  return esc(text).replace(/\[([^\]]{1,40})\]/g, '<span class="tag">$1</span>');
}
function addMsg(cls, html) {
  $("chatEmpty")?.remove();
  const div = document.createElement("div");
  div.className = `msg ${cls}`;
  div.innerHTML = html;
  $("chatLog").appendChild(div);
  $("chatLog").scrollTop = $("chatLog").scrollHeight;
  return div;
}
function trace(kind, label, detail = "") {
  const li = document.createElement("li");
  li.innerHTML = `<span class="k ${kind}">${esc(label)}</span>${detail ? `<span class="s">${esc(detail)}</span>` : ""}`;
  $("trace").appendChild(li);
  $("trace").parentElement.scrollTop = $("trace").parentElement.scrollHeight;
}

async function ask() {
  const question = $("question").value.trim();
  if (!question || state.busy) return;
  let plot;
  try { plot = plotPayload(); } catch (err) { alert(err.message); return; }
  const language = $("language").value;
  state.busy = true; $("sendBtn").disabled = true;
  addMsg("user", esc(question));
  $("question").value = "";
  $("trace").innerHTML = "";
  const pending = addMsg("bot", '<span class="muted">Thinking…</span>');
  const t0 = performance.now();
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 150000);
  state.finished = false;

  try {
    const res = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, signal: ctrl.signal,
      body: JSON.stringify({ question, plot, language, history: state.history.slice(-4) }) });
    if (!res.ok) { const j = await res.json().catch(() => ({})); throw new Error(typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? res.statusText)); }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        const line = chunk.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        let ev;
        try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }   // skip a corrupted event, keep the stream
        handleEvent(ev, pending, question, language);
      }
    }
    if (!state.finished) throw new Error("The connection dropped before the answer arrived.");
  } catch (err) {
    const msg = err.name === "AbortError" ? "No answer within 150 s — the AI service is busy." : err.message;
    pending.className = "msg err";
    pending.innerHTML = `${esc(msg)} <button class="play" id="retryAsk">Retry</button>`;
    pending.querySelector("#retryAsk").addEventListener("click", () => { pending.remove(); $("question").value = question; ask(); });
  } finally {
    clearTimeout(timer);
    trace("ver", `done in ${((performance.now() - t0) / 1000).toFixed(1)} s`);
    state.busy = false; $("sendBtn").disabled = false;
  }
}

function handleEvent(ev, pending, question, language) {
  switch (ev.type) {
    case "status": trace("think", "•", ev.message); break;
    case "thinking": trace("think", `reasoning (${ev.model})`, ev.message); break;
    case "tool_call": trace("call", `→ ${ev.name}(${ev.args && ev.args !== "{}" ? ev.args : ""})`, `step ${ev.step}`); break;
    case "tool_result":
      trace(ev.ok ? "ok" : "fail", `${ev.ok ? "✓" : "✗"} ${ev.name} · ${ev.ms} ms`,
        [ev.preview, ...(ev.sources || [])].filter(Boolean).join("\n"));
      break;
    case "self_correct": trace("fix", "⟲ self-correction", ev.message); break;
    case "verify":
      trace(ev.passed ? "ver" : "fail", `fact-check #${ev.attempt}: ${ev.passed ? "PASSED" : "FAILED"}`,
        `${ev.numbers_checked} numbers checked` + (ev.unsupported_numbers.length ? ` · unsupported: ${ev.unsupported_numbers.join(", ")}` : "")
        + (ev.dose_violations.length ? ` · unverified doses: ${ev.dose_violations.join("; ")}` : ""));
      break;
    case "answer": {
      state.finished = true;
      const verifiedHtml = ev.verified
        ? `<span class="verified">✓ ${ev.numbers_checked} numbers verified against live data</span>`
        : '<span class="unverified">⚠ partially verified — unverifiable figures removed</span>';
      pending.innerHTML = `${renderTags(ev.text)}
        <div class="meta">${verifiedHtml} · ${ev.tool_calls} tool calls · ${(ev.latency_ms / 1000).toFixed(1)} s · ${esc(ev.model || "")}
          <button class="play">▶ Listen</button></div>
        ${ev.language !== "en" ? `<details><summary>English</summary>${renderTags(ev.text_en)}</details>` : ""}
        <details><summary>Sources (${ev.sources.length})</summary><ul>${ev.sources.map((s) => `<li>${esc(s.source)} <span class="mode ${esc(s.mode)}">${esc(s.mode)}</span> ${fmtTime(s.fetched_at)}</li>`).join("")}</ul></details>`;
      pending.querySelector(".play").addEventListener("click", () => speak(ev.text, ev.language));
      state.history.push({ role: "user", content: question }, { role: "assistant", content: ev.text_en });
      speak(ev.text, ev.language);
      break;
    }
    case "error": state.finished = true; pending.className = "msg err"; pending.textContent = ev.message; trace("fail", "error", ev.message); break;
    default: break;
  }
}

// ============================================================ voice: speech out
let currentAudio = null;
async function speak(text, language) {
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  speechSynthesis.cancel();
  try {
    const res = await fetch("/api/tts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, language }) });
    if (!res.ok) throw new Error("tts");
    currentAudio = new Audio(URL.createObjectURL(await res.blob()));
    await currentAudio.play();
  } catch (_) {
    // Fallback: the browser's own voice.
    const u = new SpeechSynthesisUtterance(text.replace(/\[[^\]]{1,40}\]/g, ""));
    u.lang = LANG_BCP47[language] || "en-IN";
    speechSynthesis.speak(u);
  }
}

// ============================================================ voice: speech in
$("micBtn").addEventListener("click", async () => {
  if (state.recording) { state.recording.stop(); return; }
  if (!navigator.mediaDevices?.getUserMedia) { alert("This browser cannot record audio. Please type your question."); return; }
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch (_) { alert("Microphone permission denied. Please allow it or type your question."); return; }
  const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
  const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  const chunks = [];
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  rec.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    state.recording = null;
    $("micBtn").classList.remove("rec");
    const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
    const fd = new FormData();
    fd.append("audio", blob, "speech.webm");
    fd.append("language", $("language").value);
    $("question").value = "Transcribing…";
    try {
      const r = await fetch("/api/transcribe", { method: "POST", body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "transcription failed");
      $("question").value = j.text;
      trace("ok", `✓ speech recognised (${j.language})`, j.text);
      ask();
    } catch (err) {
      $("question").value = "";
      addMsg("err", esc(err.message));
    }
  };
  rec.start();
  state.recording = rec;
  $("micBtn").classList.add("rec");
  setTimeout(() => state.recording === rec && rec.stop(), 30000);   // hard stop after 30 s
});

// ============================================================ monitor
async function loadMonitor() {
  try {
    const [plots, alerts] = await Promise.all([api("/api/plots"), api("/api/alerts")]);
    $("plotList").innerHTML = plots.length ? plots.map((p) => `
      <div class="plot-item"><div><b>${esc(p.name)}</b> · ${esc(p.crop)}${p.sowing_date ? ` · sown ${fmtDate(p.sowing_date)}` : ""}
        <div class="muted small">${p.lat}, ${p.lon} · ${esc(p.language)}</div></div>
        <div><button class="btn" data-open="${p.id}">Open</button> <button class="btn" data-del="${p.id}">Remove</button></div></div>`).join("")
      : '<p class="muted">No plots saved yet. Use “Save & monitor” on the left.</p>';
    $("plotList").querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      await api(`/api/plots/${b.dataset.del}`, { method: "DELETE" }); loadMonitor();
    }));
    $("plotList").querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => {
      const p = plots.find((x) => x.id === +b.dataset.open);
      setPin(p.lat, p.lon, 16); $("crop").value = p.crop; $("sowing").value = p.sowing_date || ""; $("language").value = p.language || "hi";
      runReport();
    }));
    $("alertList").innerHTML = alerts.length ? alerts.map((a) => `
      <div class="alert-item ${a.severity}"><h4>${esc(a.title)} <span class="muted small">· ${esc(a.name)} · ${fmtDate(a.for_date)}</span></h4>
        <p>${esc(a.detail)}</p><p><b>→ ${esc(a.action)}</b></p>
        <p class="muted small">${a.notified ? "Sent on Telegram ✓" : "Not pushed (Telegram not configured)"}</p></div>`).join("")
      : '<p class="muted">No alerts yet.</p>';
  } catch (err) {
    $("plotList").innerHTML = `<p class="unavail">${esc(err.message)}</p>`;
  }
}
$("runMonitor").addEventListener("click", async () => {
  $("runMonitor").disabled = true;
  $("monitorResult").textContent = "Checking every saved plot against live data…";
  try {
    const s = await api("/api/monitor/run", { method: "POST" });
    $("monitorResult").textContent = `Checked ${s.plots} plot(s) · ${s.new_alerts} new alert(s) · ${s.notified} pushed to Telegram`
      + (s.errors.length ? ` · errors: ${s.errors.join("; ")}` : "");
    loadMonitor();
  } catch (err) {
    $("monitorResult").textContent = `Monitor failed: ${err.message}`;
  } finally { $("runMonitor").disabled = false; }
});

// ============================================================ resilience lab & connectivity
let labTargets = {};
function showOutages(active) {
  const names = Object.keys(active).map((k) => labTargets[k] || k);
  $("outageBanner").hidden = !names.length;
  $("outageBanner").textContent = names.length ? `Resilience demo: simulated outage of ${names.join(", ")} — fallbacks active` : "";
  $("labState").textContent = names.length
    ? `Active: ${Object.entries(active).map(([k, s]) => `${labTargets[k] || k} (${Math.ceil(s / 60)} min left)`).join(", ")}`
    : "All providers live.";
}
async function loadLab() {
  try {
    const s = await api("/api/resilience");
    labTargets = s.targets;
    $("labTargets").innerHTML = Object.entries(s.targets)
      .map(([k, label]) => `<label><input type="checkbox" value="${esc(k)}"> ${esc(label)}</label>`).join("");
    showOutages(s.active);
  } catch (_) { /* lab is optional */ }
}
$("labStart").addEventListener("click", async () => {
  const targets = [...document.querySelectorAll("#labTargets input:checked")].map((i) => i.value);
  if (!targets.length) { alert("Tick at least one provider."); return; }
  const s = await api("/api/resilience/outage", { method: "POST", body: JSON.stringify({ targets, minutes: 5 }) });
  showOutages(s.active);
});
$("labStop").addEventListener("click", async () => {
  await api("/api/resilience/outage", { method: "DELETE" });
  document.querySelectorAll("#labTargets input").forEach((i) => (i.checked = false));
  showOutages({});
});
window.addEventListener("offline", () => ($("offline").hidden = false));
window.addEventListener("online", () => { $("offline").hidden = true; loadStatus(); });
$("offline").hidden = navigator.onLine;

// ============================================================ boot
// Deep link, e.g. /?lat=20.065&lon=74.13&crop=onion&sow=2026-09-15&lang=mr&tab=ask — opens a plot directly.
async function boot() {
  try { await loadCrops(); } catch (e) { alert(`Could not load crops: ${e.message}`); return; }
  const q = new URLSearchParams(location.search);
  if (q.has("lat") && q.has("lon")) {
    setPin(+q.get("lat"), +q.get("lon"), 16);
    if (q.get("crop")) $("crop").value = q.get("crop");
    if (q.get("sow")) $("sowing").value = q.get("sow");
    if (q.get("lang")) $("language").value = q.get("lang");
    if (q.get("tab") === "ask") switchTab("ask"); else runReport();
  }
}
boot();
loadLab();
loadStatus();
setInterval(loadStatus, 120000);

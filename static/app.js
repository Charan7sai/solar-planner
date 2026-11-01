const $ = (id) => document.getElementById(id);
const inr = (n) => "₹" + Math.round(n).toLocaleString("en-IN");
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// ---- Map: satellite imagery + roof drawing ----
const map = L.map("map").setView([20.6, 78.9], 5);
L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
  maxZoom: 20, maxNativeZoom: 19,
  attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
}).addTo(map);
L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", {
  maxZoom: 20, maxNativeZoom: 19,
}).addTo(map);

const roofs = new L.FeatureGroup().addTo(map);
map.addControl(new L.Control.Draw({
  edit: { featureGroup: roofs },
  draw: {
    polygon: { allowIntersection: false, shapeOptions: { color: "#f5a13a", weight: 2 } },
    rectangle: { shapeOptions: { color: "#f5a13a", weight: 2 } },
    polyline: false, circle: false, circlemarker: false, marker: false,
  },
}));

function updateArea() {
  let total = 0;
  roofs.eachLayer((layer) => { total += L.GeometryUtil.geodesicArea(layer.getLatLngs()[0]); });
  if (total > 0) $("area").value = total.toFixed(1);
}
map.on(L.Draw.Event.CREATED, (e) => { roofs.addLayer(e.layer); updateArea(); });
map.on(L.Draw.Event.EDITED, updateArea);
map.on(L.Draw.Event.DELETED, updateArea);

// ---- Address search (OpenStreetMap Nominatim) ----
$("search").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("q").value.trim();
  if (!q) return;
  setStatus("Searching…");
  try {
    const url = `https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(q)}`;
    const [hit] = await (await fetch(url)).json();
    if (!hit) return setStatus("Address not found. Try a nearby landmark.", true);
    map.setView([+hit.lat, +hit.lon], 19);
    setStatus("Now trace your roof with the polygon tool.");
  } catch {
    setStatus("Search failed. Pan the map manually.", true);
  }
});

// ---- Estimate ----
function setStatus(msg, isError = false) {
  $("status").textContent = msg;
  $("status").classList.toggle("error", isError);
}

function roofCenter() {
  if (roofs.getLayers().length) return roofs.getBounds().getCenter();
  return map.getCenter();
}

$("inputs").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!roofs.getLayers().length && map.getZoom() < 10) {
    return setStatus("Find your location on the map first.", true);
  }
  const { lat, lng } = roofCenter();
  const body = {
    lat, lon: lng,
    roof_area_m2: +$("area").value,
    monthly_units: +$("units").value,
    tariff: +$("tariff").value,
    export_rate: +$("export").value,
    cost_per_kw: +$("cost").value,
  };
  if ($("kwp").value) body.kwp = +$("kwp").value;

  $("go").disabled = true;
  setStatus("Fetching 20 years of NASA solar data…");
  try {
    const res = await fetch("api/estimate", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Invalid input");
    render(data, body);
    setStatus("");
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    $("go").disabled = false;
  }
});

// ---- Results ----
const charts = {};
function chart(id, config) {
  charts[id]?.destroy();
  charts[id] = new Chart($(id), config);
}

function render(d, input) {
  const { sizing: s, generation: g, finance: f } = d;
  const cards = [
    ["Recommended system", `${d.kwp} kW`, `${s.panels} × 550 W panels · limited by ${s.limited_by}`],
    ["Net cost after subsidy", inr(f.net_cost), `${inr(f.gross_cost)} − ${inr(f.subsidy)} subsidy`],
    ["Payback", f.payback_years ? `${f.payback_years} yrs` : "> 25 yrs", "Simple payback incl. maintenance", true],
    ["25-year net savings", inr(f.lifetime_net_savings), `Year 1: ${inr(f.first_year_savings)}`, true],
    ["Annual generation", `${g.annual_p50.toLocaleString("en-IN")} kWh`, `P90 (bad year): ${g.annual_p90.toLocaleString("en-IN")} kWh`],
    ["Solar yield here", `${g.specific_yield} kWh/kWp`, `Avg sunlight ${g.ghi_mean} kWh/m²/day`],
    ["CO₂ avoided", `${f.co2_tonnes_per_year} t/yr`, "vs. India grid average"],
  ];
  $("cards").innerHTML = cards.map(([label, value, sub, hi]) =>
    `<div class="card${hi ? " highlight" : ""}"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`
  ).join("");
  $("results").hidden = false;

  const css = getComputedStyle(document.documentElement);
  const accent = css.getPropertyValue("--accent").trim();
  const muted = css.getPropertyValue("--muted").trim();
  const good = css.getPropertyValue("--good").trim();
  Chart.defaults.color = muted;
  Chart.defaults.borderColor = css.getPropertyValue("--border").trim();

  chart("genChart", {
    type: "bar",
    data: {
      labels: MONTHS,
      datasets: [
        { label: "Solar (kWh, median year)", data: g.monthly_p50, backgroundColor: accent, borderRadius: 4 },
        { label: "Your use (kWh)", data: MONTHS.map(() => input.monthly_units), type: "line", borderColor: muted, pointRadius: 0, borderDash: [4, 4] },
      ],
    },
    options: { plugins: { legend: { position: "bottom" } } },
  });

  chart("cashChart", {
    type: "line",
    data: {
      labels: f.yearly.map((y) => y.year),
      datasets: [{
        label: "Cumulative ₹", data: f.yearly.map((y) => y.cumulative),
        borderColor: good, backgroundColor: good + "22", fill: true, pointRadius: 0, tension: 0.2,
      }],
    },
    options: {
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => inr(c.parsed.y) } } },
      scales: { x: { title: { display: true, text: "Year" } }, y: { ticks: { callback: (v) => inr(v) } } },
    },
  });

  chart("yearChart", {
    type: "bar",
    data: {
      labels: g.years,
      datasets: [{ label: "Annual kWh", data: g.annual_by_year, backgroundColor: accent + "aa", borderRadius: 3 }],
    },
    options: { plugins: { legend: { display: false } } },
  });
}

// Planner page: find a building, outline the roof, run the estimate and explain the results.
(function () {
  const $ = (id) => document.getElementById(id);
  const inr = (n) => (n < 0 ? "-" : "") + "₹" + Math.abs(Math.round(n)).toLocaleString("en-IN");
  const num = (n) => Math.round(n).toLocaleString("en-IN");
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December"];
  const MIN_DETECT_ZOOM = 17;
  const NOMINATIM = "https://nominatim.openstreetmap.org/search";
  const track = window.track || function () {};

  // ---------- Messages ----------

  // Messages pop up as notifications (see notify in site.js). The first argument names the
  // source, so a new message from the same step replaces the previous one instead of stacking.
  function showAlert(source, type, title, body, actions, autoHide) {
    window.notify(source, type, title, body, actions, autoHide);
  }

  function clearAlert(source) { window.notify.clear(source); }

  function setBusy(button, busy, busyLabel) {
    if (busy) {
      button.dataset.label = button.innerHTML;
      button.setAttribute("aria-busy", "true");
      button.disabled = true;
      button.replaceChildren();
      const spinner = document.createElement("span");
      spinner.className = "spinner";
      spinner.setAttribute("aria-hidden", "true");
      button.append(spinner, document.createTextNode(busyLabel));
    } else if (button.dataset.label !== undefined) {
      button.innerHTML = button.dataset.label;
      delete button.dataset.label;
      button.removeAttribute("aria-busy");
      button.disabled = false;
    }
  }

  function fieldError(input, message) {
    const error = $(input.id + "-error");
    input.setAttribute("aria-invalid", message ? "true" : "false");
    if (error) {
      error.textContent = message || "";
      error.hidden = !message;
    }
  }

  // ---------- Map ----------

  const map = L.map("map", { zoomControl: true }).setView([22.5, 79.0], 5);
  const roofs = new L.FeatureGroup().addTo(map);
  const shapeStyle = { color: "#f0903f", weight: 3, fillOpacity: 0.25 };
  const drawControl = new L.Control.Draw({
    position: "topright",
    edit: { featureGroup: roofs },
    draw: {
      polygon: { allowIntersection: false, showArea: false, shapeOptions: shapeStyle },
      rectangle: { shapeOptions: shapeStyle },
      polyline: false, circle: false, circlemarker: false, marker: false,
    },
  });
  map.addControl(drawControl);

  let imageryKey = null;
  let locationMarker = null;

  (async function addBaseLayers() {
    try {
      const res = await fetch("/api/config");
      if (res.ok) imageryKey = (await res.json()).arcgis_api_key;
    } catch (e) { imageryKey = null; }

    if (imageryKey) {
      const base = "https://ibasemaps-api.arcgis.com/arcgis/rest/services";
      // Outside big cities Esri often lacks zoom-19 tiles; the fallback layer upscales the nearest lower zoom
      L.tileLayer.fallback(base + "/World_Imagery/MapServer/tile/{z}/{y}/{x}?token=" + imageryKey, {
        maxZoom: 20, maxNativeZoom: 19,
        attribution: "Powered by Esri | Imagery © Esri, Maxar, Earthstar Geographics",
      }).addTo(map);
      L.tileLayer(base + "/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}?token=" + imageryKey, {
        maxZoom: 20, maxNativeZoom: 19,
      }).addTo(map);
      updateImageryDate();
    } else {
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19, attribution: "© OpenStreetMap contributors",
      }).addTo(map);
      $("detect-btn").disabled = true;
      $("detect-btn").title = "Satellite imagery is not set up on this server";
      showAlert("map-alert", "warning", "Satellite imagery is unavailable. ",
        "You can still trace your roof on the street map with Trace roof, or type the area in step 3.");
    }
  })();

  function updateZoomIndicator() {
    const zoom = map.getZoom();
    const indicator = $("zoom-indicator");
    const tooFar = zoom < MIN_DETECT_ZOOM;
    indicator.textContent = tooFar
      ? "Zoom " + zoom + ". Zoom in to " + MIN_DETECT_ZOOM + " or closer to detect roofs."
      : "Zoom " + zoom + ". Close enough to detect roofs.";
    indicator.classList.toggle("too-far", tooFar);
  }
  map.on("zoomend", updateZoomIndicator);
  updateZoomIndicator();

  // ---------- Imagery capture date ----------

  let imageryTimer = null;
  let imageryRequest = 0;
  let lastImagery = null;
  const OLD_IMAGERY_MS = 548 * 24 * 3600 * 1000;

  function formatDate(iso) {
    return new Date(iso + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
  }

  function updateImageryDate() {
    const line = $("imagery-date");
    clearTimeout(imageryTimer);
    if (!imageryKey || map.getZoom() < 14) { line.textContent = ""; lastImagery = null; return; }
    imageryTimer = setTimeout(async () => {
      const request = ++imageryRequest;
      const c = map.getCenter();
      line.textContent = "Checking when this imagery was captured";
      try {
        const res = await fetch("/api/imagery-date?lat=" + c.lat.toFixed(5) + "&lon=" + c.lng.toFixed(5));
        const d = await res.json();
        if (request !== imageryRequest) return;  // the map moved again meanwhile
        lastImagery = d.found ? d : null;
        // Imagery older than about 18 months often misses buildings finished since then
        const old = d.found && Date.now() - new Date(d.captured + "T00:00:00") > OLD_IMAGERY_MS;
        line.classList.toggle("old", Boolean(old));
        line.textContent = d.found
          ? "Satellite imagery here was captured on " + formatDate(d.captured) +
            (d.resolution_m ? ", with about " + Math.round(d.resolution_m * 100) + " cm detail." : ".") +
            (old ? " Buildings finished after that will not appear. If yours is missing, type its roof area in step 3." : "")
          : "The capture date of this imagery is not available.";
      } catch (e) {
        if (request === imageryRequest) line.textContent = "";
      }
    }, 600);
  }
  map.on("moveend", updateImageryDate);

  function updateArea() {
    let total = 0;
    roofs.eachLayer((layer) => { total += L.GeometryUtil.geodesicArea(layer.getLatLngs()[0]); });
    $("area").value = total > 0 ? total.toFixed(1) : "";
    if (total > 0) fieldError($("area"), "");
  }

  map.on(L.Draw.Event.CREATED, (e) => {
    roofs.addLayer(e.layer);
    updateArea();
    showAlert("map-alert", "success", "Roof added. ",
      "Total roof area is now " + $("area").value + " m². Trace another roof to add it, or continue to step 3.", null, 8000);
  });
  map.on(L.Draw.Event.EDITED, () => {
    updateArea();
    showAlert("map-alert", "success", "Outline updated. ", "Roof area is now " + ($("area").value || "0") + " m².", null, 8000);
  });
  map.on(L.Draw.Event.DELETED, () => {
    updateArea();
    showAlert("map-alert", "info", "Roof removed. ", $("area").value ? "Roof area is now " + $("area").value + " m²." : "No roofs are outlined.", null, 8000);
  });
  map.on(L.Draw.Event.DRAWSTART, () => setDetecting(false));

  // ---------- Step 1: search by pincode and street ----------

  const PINCODE = /^[1-9][0-9]{5}$/;

  function wait(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

  async function nominatim(params) {
    const url = new URL(NOMINATIM);
    Object.entries({ format: "jsonv2", limit: "5", countrycodes: "in", ...params })
      .forEach(([key, value]) => url.searchParams.set(key, value));
    const res = await fetch(url, { headers: { "Accept-Language": "en" } });
    if (res.status === 429) throw new Error("rate");
    if (!res.ok) throw new Error("http");
    return res.json();
  }

  // Most specific query first; each fallback is broader. Nominatim allows one request per second.
  function searchPlan(f) {
    const plan = [];
    const street = [f.house, f.street].filter(Boolean).join(" ");
    if (street) plan.push({ precise: true, params: { street: street, city: f.city || f.locality, postalcode: f.pincode, country: "India" } });
    if (f.locality) plan.push({ precise: false, params: { q: [f.locality, f.city, f.pincode, "India"].filter(Boolean).join(", ") } });
    plan.push({ precise: false, params: { postalcode: f.pincode, country: "India" } });
    plan.push({ precise: false, params: { q: f.pincode + ", India" } });
    return plan;
  }

  // Nominatim's boxes for pincodes are often 10 km wide, so centre on the point instead:
  // zoom 15 shows a pincode area, 18 a street, 19 a single building.
  function flyToResult(result, precise) {
    const [south, north, west, east] = result.boundingbox.map(Number);
    const span = Math.max(north - south, east - west);
    if (!precise && span < 0.02) {
      map.fitBounds([[south, west], [north, east]], { maxZoom: 16 });
      return;
    }
    const building = ["building", "house", "amenity", "office", "shop"].includes(result.addresstype);
    map.setView([Number(result.lat), Number(result.lon)], precise ? (building ? 19 : 18) : 15);
  }

  function renderMatches(results, precise) {
    const list = $("search-results");
    list.replaceChildren();
    if (results.length < 2) { list.hidden = true; return; }
    const note = document.createElement("p");
    note.textContent = "Not the right place? Pick another match:";
    list.append(note);
    results.forEach((result, i) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = result.display_name;
      if (i === 0) button.setAttribute("aria-current", "true");
      button.addEventListener("click", () => {
        list.querySelectorAll("button").forEach((b) => b.removeAttribute("aria-current"));
        button.setAttribute("aria-current", "true");
        flyToResult(result, precise);
      });
      list.append(button);
    });
    list.hidden = false;
  }

  $("pincode").addEventListener("input", (e) => {
    e.target.value = e.target.value.replace(/\D/g, "").slice(0, 6);
    if (e.target.getAttribute("aria-invalid") === "true" && PINCODE.test(e.target.value)) fieldError(e.target, "");
  });

  $("search-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fields = {
      pincode: $("pincode").value.trim(),
      house: $("house").value.trim(),
      street: $("street").value.trim(),
      locality: $("locality").value.trim(),
      city: $("city").value.trim(),
    };
    if (!PINCODE.test(fields.pincode)) {
      fieldError($("pincode"), fields.pincode ? "A pincode has 6 digits and does not start with 0." : "Enter your 6 digit pincode.");
      $("pincode").focus();
      return;
    }
    fieldError($("pincode"), "");
    clearAlert("search-alert");
    $("search-results").hidden = true;
    const button = $("search-btn");
    setBusy(button, true, "Searching");

    try {
      let found = null;
      const plan = searchPlan(fields);
      for (let i = 0; i < plan.length; i++) {
        if (i > 0) await wait(1100);
        const results = await nominatim(plan[i].params);
        if (results.length) { found = { results: results, precise: plan[i].precise }; break; }
      }
      if (!found) {
        showAlert("search-alert", "error", "We couldn't find that pincode. ",
          "Check the digits, or add your area and city under \"Add street details\". You can also use your current location or move the map yourself.");
        return;
      }
      flyToResult(found.results[0], found.precise);
      renderMatches(found.results, found.precise);
      track("search", { precise: found.precise });
      const hadStreet = Boolean(fields.house || fields.street);
      showAlert("search-alert", "success", found.precise ? "Street found. " : "Area found. ",
        found.precise
          ? "Address data can be off by a few buildings, so look for your roof nearby, then press Detect roof and click it."
          : (hadStreet ? "We couldn't match the street, so this shows the wider area. " : "") +
            "Zoom in and move the map to your building, then press Detect roof and click your roof.");
    } catch (err) {
      showAlert("search-alert", "error", "Search is not responding. ",
        err.message === "rate"
          ? "Too many searches in a short time. Wait a few seconds and try again."
          : "Check your internet connection and try again, or use your current location.");
    } finally {
      setBusy(button, false);
    }
  });

  // ---------- Step 1: current location ----------

  $("locate-btn").addEventListener("click", () => {
    if (!("geolocation" in navigator)) {
      showAlert("search-alert", "error", "Your browser can't share its location. ", "Search by pincode instead.");
      return;
    }
    if (!window.isSecureContext) {
      showAlert("search-alert", "error", "Location needs a secure connection. ", "Open this site with https:// and try again, or search by pincode.");
      return;
    }
    const button = $("locate-btn");
    setBusy(button, true, "Finding your location");
    clearAlert("search-alert");

    navigator.geolocation.getCurrentPosition((pos) => {
      setBusy(button, false);
      const { latitude, longitude, accuracy } = pos.coords;
      const here = [latitude, longitude];
      if (locationMarker) locationMarker.remove();
      locationMarker = L.layerGroup([
        L.circle(here, { radius: accuracy, color: "#2563eb", weight: 1, fillOpacity: 0.08, interactive: false }),
        L.circleMarker(here, { radius: 7, color: "#ffffff", weight: 2, fillColor: "#2563eb", fillOpacity: 1, interactive: false }),
      ]).addTo(map);
      map.setView(here, 19);
      track("location_used", { accurate: accuracy <= 30 });
      const metres = Math.round(accuracy);
      if (accuracy <= 30) {
        showAlert("search-alert", "success", "Showing your current location. ",
          "Your device reported it to within " + metres + " m. If you are at home, your roof is at or next to the blue dot. Press Detect roof and click it.");
      } else {
        showAlert("search-alert", "warning", "Your location is approximate. ",
          "Your device reported it to within " + metres + " m, which can be several buildings away. The blue circle shows the possible area. Move the map to your building before detecting the roof. Turning on GPS or precise location usually helps.");
      }
    }, (err) => {
      setBusy(button, false);
      const messages = {
        1: ["Location access is blocked. ", "Allow location for this site from the icon in your browser's address bar, then press the button again. Or search by pincode."],
        2: ["Your location could not be found. ", "Turn on location or GPS on your device and try again, or search by pincode."],
        3: ["Finding your location took too long. ", "Try again outdoors or near a window, or search by pincode."],
      };
      const [title, body] = messages[err.code] || messages[2];
      showAlert("search-alert", "error", title, body);
    }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
  });

  // ---------- Step 2: roof detection and tracing ----------

  let detecting = false;
  let detectBusy = false;

  function setDetecting(on) {
    detecting = on;
    $("detect-btn").setAttribute("aria-pressed", String(on));
    $("detect-btn").textContent = on ? "Detecting: click a roof" : "Detect roof";
    $("map").classList.toggle("picking", on);
  }

  function startTracing() {
    setDetecting(false);
    new L.Draw.Polygon(map, drawControl.options.draw.polygon).enable();
    showAlert("map-alert", "info", "Tracing. ", "Click each corner of your roof, then click the first corner again to finish. Press Esc to cancel.");
  }

  $("detect-btn").addEventListener("click", () => {
    setDetecting(!detecting);
    if (detecting) {
      const zoom = map.getZoom();
      if (zoom < MIN_DETECT_ZOOM) {
        showAlert("map-alert", "info", "Detection is on. ",
          "First zoom in to level " + MIN_DETECT_ZOOM + " or closer (you are at " + zoom + "), then click the middle of your roof.");
      } else {
        showAlert("map-alert", "info", "Detection is on. ", "Click the middle of your roof.", null, 8000);
      }
    } else {
      clearAlert("map-alert");
    }
  });
  $("trace-btn").addEventListener("click", startTracing);
  $("clear-btn").addEventListener("click", () => {
    if (!roofs.getLayers().length) {
      showAlert("map-alert", "info", "Nothing to clear. ", "No roofs are outlined yet.");
      return;
    }
    roofs.clearLayers();
    updateArea();
    showAlert("map-alert", "info", "All roofs cleared. ", "Detect or trace your roof again.", null, 8000);
  });

  const traceAction = { label: "Trace roof instead", onClick: startTracing };

  map.on("click", async (e) => {
    if (!detecting || detectBusy) return;
    const zoom = map.getZoom();
    if (zoom < MIN_DETECT_ZOOM) {
      showAlert("map-alert", "error", "Zoom in closer to detect a roof. ",
        "You are at zoom level " + zoom + ". Detection needs level " + MIN_DETECT_ZOOM + " or closer so that single roofs are visible. Use the + button, pinch or scroll to zoom in, or jump straight there.",
        [{ label: "Zoom in to this spot", onClick: () => { map.setView(e.latlng, 19); clearAlert("map-alert"); } }]);
      return;
    }

    detectBusy = true;
    $("map-loading").hidden = false;
    clearAlert("map-alert");
    try {
      const res = await fetch("/api/detect-roof", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lat: e.latlng.lat, lon: e.latlng.lng }),
      });
      const data = await res.json().catch(() => ({}));

      if (res.status === 404) {
        track("roof_detect_error", { reason: "no_imagery" });
        showAlert("map-alert", "error", "No sharp satellite imagery here. ",
          "The imagery for this spot is too coarse for the model. Trace the roof yourself: press Trace roof, click each corner, then click the first corner again.", [traceAction]);
        return;
      }
      if (res.status === 503) {
        showAlert("map-alert", "error", "Roof detection is switched off on this server. ", "Trace the roof yourself instead.", [traceAction]);
        return;
      }
      if (!res.ok) {
        track("roof_detect_error", { reason: "server" });
        showAlert("map-alert", "error", "Couldn't load satellite imagery. ",
          "This is usually temporary. Wait a moment and click the roof again, or trace it yourself.", [traceAction]);
        return;
      }
      if (!data.found) {
        track("roof_not_found");
        showAlert("map-alert", "warning", "No roof found at that spot. ",
          "Click nearer the middle of the roof. Roofs that are very dark, very small or covered by trees are hard for the model to see, so trace those yourself.", [traceAction]);
        return;
      }

      const ring = data.polygon.coordinates[0].slice(0, -1).map(([lon, lat]) => [lat, lon]);
      roofs.addLayer(L.polygon(ring, shapeStyle));
      updateArea();
      track("roof_detected", { low_res: data.imagery_zoom < 19 });
      const lowRes = data.imagery_zoom < 19;
      showAlert("map-alert", lowRes ? "warning" : "success",
        "Roof outlined: " + data.area_m2 + " m². ",
        (lowRes ? "Only lower resolution imagery exists here, so check the outline carefully. " : "") +
        "If it is off, use the edit button on the map to drag the corners. Click another roof to add it, or continue to step 3.",
        null, lowRes ? 0 : 8000);
    } catch (err) {
      showAlert("map-alert", "error", "Couldn't reach the server. ", "Check your internet connection and try again.");
    } finally {
      detectBusy = false;
      $("map-loading").hidden = true;
    }
  });

  // ---------- Step 3: estimate ----------

  const FIELD_RULES = {
    area: { label: "roof area", required: true },
    units: { label: "monthly use", required: true },
    tariff: { label: "tariff", required: true },
    export: { label: "export rate", required: true },
    cost: { label: "install cost", required: true },
    kwp: { label: "system size", required: false },
  };

  function validateField(id) {
    const input = $(id);
    const rule = FIELD_RULES[id];
    const raw = input.value.trim();
    if (!raw) {
      if (!rule.required) { fieldError(input, ""); return true; }
      fieldError(input, id === "area"
        ? "Outline your roof on the map, or type its area."
        : "Enter your " + rule.label + ".");
      return false;
    }
    const value = Number(raw);
    const min = Number(input.min), max = Number(input.max);
    if (!Number.isFinite(value) || value < min || value > max) {
      fieldError(input, "Enter a number from " + num(min) + " to " + num(max) + ".");
      return false;
    }
    fieldError(input, "");
    return true;
  }

  Object.keys(FIELD_RULES).forEach((id) => {
    $(id).addEventListener("blur", () => { if ($(id).value.trim() || $(id).getAttribute("aria-invalid") === "true") validateField(id); });
  });

  function estimateLocation() {
    if (roofs.getLayers().length) return roofs.getBounds().getCenter();
    if (map.getZoom() >= 10) return map.getCenter();
    return null;
  }

  $("estimate-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    clearAlert("estimate-alert");
    const invalid = Object.keys(FIELD_RULES).filter((id) => !validateField(id));
    if (invalid.length) {
      $(invalid[0]).focus();
      showAlert("estimate-alert", "error", "Some details need fixing. ", "Check the highlighted fields above.");
      return;
    }
    const where = estimateLocation();
    if (!where) {
      showAlert("estimate-alert", "error", "We need to know where the roof is. ",
        "Search for your pincode or use your location in step 1, so the estimate uses the weather at your roof.");
      return;
    }

    const body = {
      lat: where.lat, lon: where.lng,
      roof_area_m2: Number($("area").value),
      monthly_units: Number($("units").value),
      tariff: Number($("tariff").value),
      export_rate: Number($("export").value),
      cost_per_kw: Number($("cost").value),
    };
    if ($("kwp").value.trim()) body.kwp = Number($("kwp").value);

    const button = $("estimate-btn");
    setBusy(button, true, "Loading 20 years of weather data");
    try {
      const res = await fetch("/api/estimate", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = typeof data.detail === "string" ? data.detail : "";
        showAlert("estimate-alert", "error", "The estimate could not be calculated. ",
          res.status === 502
            ? "NASA's weather service did not respond. Please try again in a minute."
            : (detail || "Check your details and try again."));
        return;
      }
      lastResult = { data: data, input: body };
      renderResults(data, body);
      track("estimate", { kwp: data.kwp, limited_by: data.sizing.limited_by });
      showAlert("estimate-alert", "success", "Estimate ready. ", "Scroll down to see your results.");
      $("results").scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      $("results-title").focus({ preventScroll: true });
    } catch (err) {
      showAlert("estimate-alert", "error", "Couldn't reach the server. ", "Check your internet connection and try again.");
    } finally {
      setBusy(button, false);
    }
  });

  $("print-btn").addEventListener("click", () => window.print());

  // ---------- Results ----------

  let lastResult = null;
  const charts = {};

  function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  let printing = false;

  function setChart(id, config) {
    if (charts[id]) charts[id].destroy();
    // A chart drawn for printing must be complete at once, not animating up from empty
    if (printing) config.options = Object.assign({}, config.options, { animation: false });
    charts[id] = new Chart($(id), config);
  }

  // Snapshot every chart into an image that the print stylesheet shows in place of the canvas
  function snapshotCharts() {
    Object.values(charts).forEach((chart) => {
      const box = chart.canvas.parentNode;
      let img = box.querySelector(".chart-print");
      if (!img) {
        img = document.createElement("img");
        img.className = "chart-print";
        img.alt = chart.canvas.getAttribute("aria-label") || "";
        box.append(img);
      }
      img.src = chart.toBase64Image("image/png", 1);
    });
  }

  function insightList(id, items) {
    const list = $(id);
    list.replaceChildren();
    items.forEach(([value, label]) => {
      const li = document.createElement("li");
      const strong = document.createElement("strong");
      strong.textContent = value;
      li.append(strong, document.createTextNode(label));
      list.append(li);
    });
  }

  function statCard(label, value, unit, sub, positive) {
    const card = document.createElement("div");
    card.className = "stat" + (positive ? " positive" : "");
    const labelEl = document.createElement("div");
    labelEl.className = "label";
    labelEl.textContent = label;
    const valueEl = document.createElement("div");
    valueEl.className = "value";
    valueEl.textContent = value;
    if (unit) {
      const unitEl = document.createElement("span");
      unitEl.className = "unit";
      unitEl.textContent = " " + unit;
      valueEl.append(unitEl);
    }
    const subEl = document.createElement("div");
    subEl.className = "sub";
    subEl.textContent = sub;
    card.append(labelEl, valueEl, subEl);
    return card;
  }

  // Draws the payback year as a labelled vertical line on the savings chart
  const paybackLine = {
    id: "paybackLine",
    afterDatasetsDraw(chart, args, opts) {
      if (opts.year == null) return;
      const { ctx, chartArea, scales } = chart;
      const x = scales.x.getPixelForValue(opts.year);
      if (x < chartArea.left || x > chartArea.right) return;
      ctx.save();
      ctx.strokeStyle = opts.color;
      ctx.setLineDash([6, 4]);
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(x, chartArea.top);
      ctx.lineTo(x, chartArea.bottom);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = opts.color;
      ctx.font = "600 12px system-ui, sans-serif";
      ctx.textAlign = x > chartArea.right - 120 ? "right" : "left";
      ctx.fillText("Pays back in year " + opts.year.toFixed(1), x + (ctx.textAlign === "left" ? 6 : -6), chartArea.top + 14);
      ctx.restore();
    },
  };

  function renderResults(d, input) {
    const s = d.sizing, g = d.generation, f = d.finance;
    $("results").hidden = false;

    // Summary cards
    const stats = $("stats");
    stats.replaceChildren(
      statCard("Recommended system", String(d.kwp), "kW", s.panels + " panels of 550 W, limited by your " + s.limited_by),
      statCard("Cost after subsidy", inr(f.net_cost), "", inr(f.gross_cost) + " minus " + inr(f.subsidy) + " subsidy"),
      statCard("Payback period", f.payback_years ? String(f.payback_years) : "25+", "years", "Including upkeep costs", true),
      statCard("Savings over 25 years", inr(f.lifetime_net_savings), "", "After paying for the system", true),
      statCard("Yearly generation", num(g.annual_p50), "units", "Poor year: " + num(g.annual_p90) + " units"),
      statCard("Sunlight here", num(g.specific_yield), "units per kW", "Average " + g.ghi_mean + " kWh/m² of sunlight a day"),
      statCard("CO₂ avoided", String(f.co2_tonnes_per_year), "tonnes a year", "Compared with grid power"),
    );

    // Print summary
    const roofCount = roofs.getLayers().length;
    $("print-summary").innerHTML = "";
    const summary = document.createElement("p");
    summary.textContent = "Location " + input.lat.toFixed(5) + ", " + input.lon.toFixed(5) +
      ". Roof area " + input.roof_area_m2 + " m²" + (roofCount ? " from " + roofCount + " outlined roof" + (roofCount > 1 ? "s" : "") : "") +
      ". Monthly use " + num(input.monthly_units) + " units at " + inr(input.tariff) + " per unit, export rate " + inr(input.export_rate) +
      ", install cost " + inr(input.cost_per_kw) + " per kW." +
      (lastImagery ? " Satellite imagery captured " + formatDate(lastImagery.captured) + "." : "") +
      " Generated " + new Date().toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) + ".";
    $("print-summary").append(summary);

    drawCharts(d, input);
  }

  function drawCharts(d, input) {
    const g = d.generation, f = d.finance;
    const accent = cssVar("--accent"), muted = cssVar("--muted"), good = cssVar("--good");
    const text = cssVar("--text"), border = cssVar("--border");
    Chart.defaults.color = muted;
    Chart.defaults.borderColor = border;
    Chart.defaults.font.family = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
    Chart.defaults.font.size = 13;
    const legend = { position: "bottom", labels: { color: text, boxWidth: 14, padding: 16 } };

    // 1. Monthly generation vs use
    const use = input.monthly_units;
    setChart("gen-chart", {
      type: "bar",
      data: {
        labels: MONTHS,
        datasets: [
          { label: "Solar generation (units)", data: g.monthly_p50, backgroundColor: accent, borderRadius: 4, order: 2 },
          { label: "Your monthly use (units)", data: MONTHS.map(() => use), type: "line", borderColor: text,
            borderDash: [6, 5], borderWidth: 2, pointRadius: 0, order: 1 },
        ],
      },
      options: {
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: legend, tooltip: { callbacks: { label: (c) => c.dataset.label + ": " + num(c.parsed.y) } } },
        scales: {
          y: { beginAtZero: true, title: { display: true, text: "Units (kWh) per month" } },
          x: { title: { display: true, text: "Month" } },
        },
      },
    });
    const gen = g.monthly_p50;
    const best = gen.indexOf(Math.max(...gen)), worst = gen.indexOf(Math.min(...gen));
    const covered = gen.filter((v) => v >= use).length;
    const totalGen = gen.reduce((a, b) => a + b, 0), totalUse = use * 12;
    const selfCovered = gen.reduce((a, v) => a + Math.min(v, use), 0);
    insightList("gen-insights", [
      [MONTH_NAMES[best], "Highest generation: " + num(gen[best]) + " units"],
      [MONTH_NAMES[worst], "Lowest generation: " + num(gen[worst]) + " units"],
      [covered + " of 12 months", "Solar covers all of your use"],
      [Math.round((selfCovered / totalUse) * 100) + "%", "Of your yearly use supplied directly by solar"],
      [num(Math.max(totalGen - selfCovered, 0)) + " units", "Exported to the grid in a year"],
    ]);

    // 2. Cumulative savings
    const points = [{ x: 0, y: -f.net_cost }].concat(f.yearly.map((y) => ({ x: y.year, y: y.cumulative })));
    setChart("cash-chart", {
      type: "line",
      data: {
        datasets: [{
          label: "Cumulative savings (₹)", data: points, borderColor: good,
          backgroundColor: good + "26", fill: { target: { value: 0 } }, pointRadius: 0, pointHitRadius: 8, tension: 0.2, borderWidth: 2.5,
        }],
      },
      options: {
        maintainAspectRatio: false,
        interaction: { mode: "nearest", axis: "x", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { title: (items) => "Year " + items[0].parsed.x, label: (c) => inr(c.parsed.y) } },
          paybackLine: { year: f.payback_years, color: text },
        },
        scales: {
          x: { type: "linear", min: 0, max: 25, ticks: { stepSize: 5 }, title: { display: true, text: "Years after installation" } },
          y: {
            title: { display: true, text: "Rupees" },
            ticks: { callback: (v) => (Math.abs(v) >= 100000 ? (v < 0 ? "-" : "") + "₹" + (Math.abs(v) / 100000).toFixed(1) + " L" : inr(v)) },
            grid: { color: (c) => (c.tick.value === 0 ? muted : border) },
          },
        },
      },
      plugins: [paybackLine],
    });
    const y1 = f.yearly[0].savings, y25 = f.yearly[f.yearly.length - 1].savings;
    insightList("cash-insights", [
      [f.payback_years ? "Year " + f.payback_years : "Not within 25 years", "The system has paid for itself"],
      [inr(f.net_cost), "Paid up front after the subsidy"],
      [inr(y1), "Saved in the first year"],
      [inr(y25), "Saved in year 25, as tariffs rise"],
      [inr(f.lifetime_net_savings), "Total profit after 25 years"],
    ]);

    // 3. Year-to-year variability
    setChart("year-chart", {
      type: "bar",
      data: {
        labels: g.years,
        datasets: [
          { label: "Generation that year (units)", data: g.annual_by_year, backgroundColor: accent + "b3", borderRadius: 3, order: 3 },
          { label: "Typical year, P50", data: g.years.map(() => g.annual_p50), type: "line", borderColor: text, borderWidth: 2, pointRadius: 0, order: 1 },
          { label: "Poor year, P90", data: g.years.map(() => g.annual_p90), type: "line", borderColor: muted, borderDash: [6, 5], borderWidth: 2, pointRadius: 0, order: 2 },
        ],
      },
      options: {
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: legend, tooltip: { callbacks: { label: (c) => c.dataset.label + ": " + num(c.parsed.y) } } },
        scales: {
          y: { beginAtZero: false, title: { display: true, text: "Units (kWh) per year" } },
          x: { title: { display: true, text: "Weather year" } },
        },
      },
    });
    const annual = g.annual_by_year;
    const hi = annual.indexOf(Math.max(...annual)), lo = annual.indexOf(Math.min(...annual));
    insightList("year-insights", [
      [String(g.years[hi]), "Best year: " + num(annual[hi]) + " units"],
      [String(g.years[lo]), "Weakest year: " + num(annual[lo]) + " units"],
      [Math.round(((annual[hi] - annual[lo]) / g.annual_p50) * 100) + "%", "Gap between the best and weakest year"],
      [num(g.annual_p90) + " units", "A safe yearly figure to plan with (P90)"],
    ]);
  }

  // Redraw charts in the new colours when the theme changes
  document.addEventListener("themechange", () => { if (lastResult) drawCharts(lastResult.data, lastResult.input); });

  // Charts are images, so print them in light-theme colours even when the page is dark
  let themeBeforePrint = null;
  // Browsers that still print their own header show the page title, and it names the saved PDF
  const pageTitle = document.title;
  window.addEventListener("beforeprint", () => { document.title = "Rooftop Solar Planner"; });
  window.addEventListener("afterprint", () => { document.title = pageTitle; });

  window.addEventListener("beforeprint", () => {
    if (!lastResult) return;
    themeBeforePrint = document.documentElement.dataset.theme || "";
    document.documentElement.dataset.theme = "light";
    printing = true;
    drawCharts(lastResult.data, lastResult.input);
    printing = false;
    snapshotCharts();
  });
  window.addEventListener("afterprint", () => {
    if (themeBeforePrint === null) return;
    if (themeBeforePrint) document.documentElement.dataset.theme = themeBeforePrint;
    else delete document.documentElement.dataset.theme;
    themeBeforePrint = null;
    if (lastResult) drawCharts(lastResult.data, lastResult.input);
  });
})();

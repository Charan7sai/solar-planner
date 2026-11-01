---
title: Rooftop Solar Planner
colorFrom: yellow
colorTo: green
sdk: docker
app_port: 7860
---

# Rooftop Solar Planner

Should *my* house go solar? **Click your roof on the satellite map and a deep-learning model outlines it**, then enter your electricity bill to get a system size, cost after the **PM Surya Ghar** subsidy, payback period and 25-year savings. Generation comes from **20 years of NASA POWER satellite data** for your exact coordinates.

## How it works

```
Click on roof ─▶ zoom-19 imagery (512 px) ─▶ ResNet-34 U-Net, INT8 ONNX on CPU
                                                  │
                     watershed split of touching roofs ─▶ roof polygon + area (m²)
                                                          │   (editable; manual tracing also works)
NASA POWER (2004–2023 monthly irradiance + temperature) ──┤
                                                          ▼
                       PV model: kWp × GHI × days × PR(temperature)
                                                          │
             ┌────────────────────────────────────────────┼──────────────────────────┐
             ▼                                            ▼                          ▼
   Sizing (roof vs. consumption)        P50 / P90 annual generation        25-yr cash flow
                                       (spread over 20 real weather years)  (net metering, subsidy,
                                                                            degradation, tariff escalation)
```

- **Sizing:** 10 m² of shadow-free roof per kWp, 75% of the roof usable, capped at what your consumption needs.
- **Generation:** a temperature-derated performance ratio (−0.4%/°C above 25 °C cell temperature). The system is simulated against every historical year, so you get both a typical (P50) and a bad-year (P90) figure, not a single guess.
- **Finance:** month-by-month net metering (self-used units save your tariff, surplus earns the export rate), 0.5%/yr panel degradation, 3%/yr tariff escalation, 1%/yr maintenance.

## Roof detection

The model ([training and evaluation](ml/README.md)) was trained on 21k aerial tiles from
15 countries and runs on CPU as a 24.7 MB INT8 ONNX file in about 0.3 s per click.
Measured per building on 300 held-out roofs, with the user simulated clicking the roof centre:

| Post-processing (chosen on validation) | Mean IoU | Roofs with IoU > 0.5 | Median area error |
|---|---|---|---|
| Plain connected components | 0.326 | 32% | 39% |
| **Watershed split of touching roofs** | **0.378** | **40%** | **21%** |

On Esri imagery of Indian cities it separates individual roofs well in mixed neighbourhoods
(Banjara Hills, Jayanagar, Kothrud, Anna Nagar), but misses many roofs in very dense,
uniformly grey flat-concrete areas (e.g. KPHB). You can always edit the outline or trace it yourself.

![Model output on Indian neighbourhoods](ml/runs/india_samples.jpg)
*Model output (yellow) on six Indian neighbourhoods. Imagery © Esri, Maxar, Earthstar Geographics.*

## Run locally

Satellite imagery and roof detection need a free
[ArcGIS Location Platform](https://location.arcgis.com) API key (public application, **Basemaps**
privileges only). Without a key the app falls back to OpenStreetMap and manual tracing.

```bash
pip install -r requirements.txt
cp .env.example .env          # then paste your key after ARCGIS_API_KEY=
uvicorn app.main:app --reload
# open http://localhost:8000
```

```bash
pip install pytest && python -m pytest
```

## Deploy free on Hugging Face Spaces

1. Create a new Space, choose **Docker**, then **Blank**.
2. Push this folder to the Space's git repo. The YAML header above configures it.
3. The app is live at `https://<user>-<space>.hf.space`.

## Pages

| Path | What it is |
|---|---|
| `/` | Landing page: how it works, accuracy and limits, FAQ, contact |
| `/planner/` | The planner: pincode or current-location search, roof detection, estimate with charts |
| anything else | Custom 404 page |

Light and dark themes (remembered per browser), a print stylesheet for saving the estimate as a PDF,
and no third-party analytics.

### Campaign tracking (UTM)

Add UTM parameters to links you share, for example
`https://<your-site>/?utm_source=linkedin&utm_medium=social&utm_campaign=launch`.
The parameters are kept for the browser tab and sent with anonymous usage events
(`page_view`, `search`, `location_used`, `roof_detected`, `estimate`, ...) to `POST /api/track`,
which writes one JSON line per event to the server log. Events never include coordinates or addresses.

## API

`POST /api/detect-roof` returns the roof under a point as GeoJSON:

```json
{ "lat": 17.415234, "lon": 78.434525 }
→ { "found": true, "area_m2": 252.4, "confidence": 0.93,
    "polygon": { "type": "Polygon", "coordinates": [[[78.4344, 17.4153], …]] } }
```

`POST /api/estimate`

```json
{ "lat": 17.385, "lon": 78.486, "roof_area_m2": 60, "monthly_units": 300,
  "tariff": 7, "export_rate": 3, "cost_per_kw": 60000 }
```

## Roadmap

- [x] **Roof segmentation baseline:** U-Net on 21k aerial tiles, 0.308 test IoU on unseen locations ([details](ml/README.md))
- [x] **Pretrained model + CPU export:** ResNet-34 U-Net, 0.321 test IoU, INT8 ONNX (24.7 MB, <0.3 s per area)
- [x] **Drop-a-pin roof detection:** click a roof to get an editable outline and area (0.378 instance IoU, 21% median area error)
- [ ] **More diverse training data** for dense flat-roof neighbourhoods (KPHB, Peru-style)
- [ ] **Shading model:** estimate obstruction losses from building heights
- [ ] **Learned yield correction:** train on real inverter data to correct the physics model
- [ ] State-wise tariff slabs and DISCOM net-metering rules
- [ ] Shareable PDF report

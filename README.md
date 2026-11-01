---
title: Rooftop Solar Planner
emoji: ☀️
colorFrom: yellow
colorTo: green
sdk: docker
app_port: 7860
---

# ☀️ Rooftop Solar Planner

Should *my* house go solar? Find your roof on satellite imagery, trace it, enter your electricity bill, and get a system size, cost after the **PM Surya Ghar** subsidy, payback period and 25-year savings. Generation comes from **20 years of NASA POWER satellite data** for your exact coordinates.

## How it works

```
Address ─▶ Esri satellite map ─▶ user traces roof ─▶ usable area (m²)
                                                          │
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

## Run locally

```bash
pip install -r requirements.txt
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

## API

`POST /api/estimate`

```json
{ "lat": 17.385, "lon": 78.486, "roof_area_m2": 60, "monthly_units": 300,
  "tariff": 7, "export_rate": 3, "cost_per_kw": 60000 }
```

## Roadmap

- [x] **Roof segmentation baseline:** U-Net on 21k aerial tiles, 0.308 test IoU on unseen locations ([details](ml/README.md))
- [x] **Pretrained model + CPU export:** ResNet-34 U-Net, 0.321 test IoU, INT8 ONNX (24.7 MB, <0.3 s per area)
- [ ] **Drop-a-pin roof detection** in the app
- [ ] **Shading model:** estimate obstruction losses from building heights
- [ ] **Learned yield correction:** train on real inverter data to correct the physics model
- [ ] State-wise tariff slabs and DISCOM net-metering rules
- [ ] Shareable PDF report

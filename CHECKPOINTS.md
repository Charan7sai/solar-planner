# Checkpoints

Each checkpoint ends in a working, tested app and is pushed to GitHub as its own commit (tagged `cp-N`).

## ✅ CP1: Working planner (physics + finance)
- FastAPI backend, NASA POWER 20-year irradiance + temperature
- Temperature-derated PV model, P50 / P90 generation across historical years
- Sizing (roof vs. consumption), PM Surya Ghar subsidy, 25-year net-metering cash flow
- Satellite map with manual roof tracing, charts, unit tests, Dockerfile

## ✅ CP2: Roof segmentation dataset + baseline model
- HOT Building Segmentation data (21k train tiles, 15 countries; val/test from unseen locations)
- U-Net trained from scratch: **test IoU 0.308 / F1 0.471** (threshold tuned on validation)
- Per-country analysis: ~0.60 IoU on familiar imagery, 0.12 on Peru, so we need better generalisation
- Data pipeline, resumable training, evaluation and write-up in [`ml/`](ml/README.md)

## ✅ CP3: Better model + export
- ImageNet-pretrained ResNet-34 U-Net: **test IoU 0.321 / F1 0.486** vs 0.308 / 0.471 baseline (selected on validation)
- Learning-rate ablation; checkpoint and threshold chosen on validation only
- ONNX + static INT8 quantization: **4x smaller (24.7 MB), no accuracy loss, 289 ms per 512 px area on 2 CPU threads**
- Remaining gap: Peru-style rooftops (~0.12 IoU) need more diverse training data

## ✅ CP4: Drop-a-pin auto roof detection
- `/api/detect-roof`: zoom-19 ArcGIS imagery around the pin → INT8 ONNX model → watershed split → GeoJSON roof + area
- Frontend: Detect roof mode; outline is editable and feeds the estimate; OpenStreetMap fallback without a key
- Instance-level eval on 300 held-out buildings (method chosen on validation): **0.378 mean IoU, 21% median area error**
- Checked on Esri imagery of six Indian neighbourhoods: good in mixed areas, weak in dense flat-concrete blocks

## ✅ CP5: Deploy + CI
- GitHub Actions: tests, then build and smoke-test the Docker image on every push
- Live on Render with HTTPS, auto-deploy from `main`: https://solar-planner-33mq.onrender.com
- ArcGIS key stays on the server behind a tile proxy; security headers added
- Google Analytics after cookie consent; sitemap submitted to Search Console
- Tested in Chromium and WebKit on desktop and phone sizes; detection takes 4-5.5 s on the free plan
- Demo GIF in the README

## ✅ CP6: Accuracy + product polish
- Shading estimate from the height and distance of obstacles a visitor enters
- Net metering with a month's surplus banked and the year-end balance paid at the export rate
- Monthly bill before and after solar, using the flat rate from the visitor's bill
- Share link that keeps the location to about 1 km and leaves out the roof outline
- State tariff slabs left out: official schedules could not all be verified, so only the PM Surya Ghar subsidy, the same across India, is built in

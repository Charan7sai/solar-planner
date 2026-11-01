# Checkpoints

Each checkpoint ends in a working, tested app and is pushed to GitHub as its own commit (tagged `cp-N`).

## ✅ CP1: Working planner (physics + finance)
- FastAPI backend, NASA POWER 20-year irradiance + temperature
- Temperature-derated PV model, P50 / P90 generation across historical years
- Sizing (roof vs. consumption), PM Surya Ghar subsidy, 25-year net-metering cash flow
- Satellite map with manual roof tracing, charts, unit tests, Dockerfile

## ⬜ CP2: Roof segmentation dataset + baseline model
- Collect aerial rooftop images with masks (public building-footprint dataset + Indian city tiles)
- Train a U-Net baseline and report IoU / F1 on a held-out set
- Training script, data pipeline and results in `ml/`

## ⬜ CP3: Better model + export
- Fine-tune a pretrained SegFormer/U-Net encoder and compare with the baseline
- Quantize to ONNX for fast CPU inference (< 1 s per roof)

## ⬜ CP4: Drop-a-pin auto roof detection
- `/api/detect-roof`: fetch tiles around the pin, segment, return the roof polygon + area
- Frontend: click a building, see the detected outline, edit it if needed

## ⬜ CP5: Deploy + CI
- GitHub Actions: run tests on every push
- Deploy to Hugging Face Spaces, add live link + demo GIF to README

## ⬜ CP6: Accuracy + product polish
- Shading/obstruction loss estimate
- State-wise tariff slabs and net-metering rules
- Shareable PDF report, usage analytics

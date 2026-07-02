# Incoming Replay Assets Import Pipeline

This directory acts as the staging area for introducing new raw research assets (dashcam images, top-view PNGs, and predicted labels) to build or expand the dataset replay pack.

## Directory Workflow

1. Place the raw camera capture files inside this directory.
2. Provide the reference CSV with ground truth annotations.
3. Configure `source_map.example.json` under `demo_scenarios/` to establish the mapping between demo pack indices (`sample_001`, `sample_002`, etc.) and the original dataset IDs.
4. Run the package import script (or use Sentinel utility commands) to populate `dashcam.jpg`, `topview.png`, and compile `manifest.json` along with the camelCase validated `cached_prediction.json` files.

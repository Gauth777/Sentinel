# Indian-Road Dataset Replay Mode

## What this is

This directory contains curated Indian-road research samples for deterministic replay during the Sentinel hackathon demo.

**This is not a live camera feed.** Images are replayed from stored research assets in a fixed sequence.

## What this is not

- Not a live VLM inference pipeline.
- Not verified training data until explicitly reviewed.
- Not a replacement for real camera capture.

## Folder structure

```
demo_scenarios/
  manifest.json
  README.md
  sample_001/
    dashcam.jpg
    topview.png
    cached_prediction.json   (for Qwen phase tomorrow)
  sample_002/
    ...
```

## Manifest schema

```json
{
  "schemaVersion": "1.0",
  "mode": "dataset_replay",
  "loop": true,
  "samples": [
    {
      "sampleId": "sample_001",
      "sequenceIndex": 1,
      "title": "Urban arterial evening",
      "description": "Dense traffic on GST Road, Chennai",
      "dashcamPath": "sample_001/dashcam.jpg",
      "topviewPath": "sample_001/topview.png",
      "location": { "latitude": 12.9452, "longitude": 80.1506 },
      "headingDegrees": 8,
      "capturedAt": "2026-06-29T10:00:00Z",
      "tags": ["urban", "indian_road", "evening"],
      "expectedLabels": {
        "roadType": "urban_arterial",
        "trafficDensity": "high",
        "roadComplexity": "complex",
        "hazardPresence": "yes",
        "anticipatedRisk": "high",
        "recommendedAction": "slow_down"
      },
      "cachedPredictionPath": "sample_001/cached_prediction.json",
      "enabled": true
    }
  ]
}
```

## Allowed image types

- `.jpg`, `.jpeg`
- `.png`
- `.webp`

## Adding curated samples

1. Copy 5–6 research images into `sample_001/` through `sample_005/`.
2. Create `manifest.json` based on `manifest.example.json`.
3. Ensure `sequenceIndex` is unique and ordered for enabled samples.
4. Set `enabled: false` to exclude a sample without deleting it.

## Expected labels

`expectedLabels` represent research ground truth for evaluation.

They are **not** model predictions. They should not be confused with VLM output.

## cached_prediction.json

Cached predictions are the deterministic fallback when live Qwen inference is unavailable or fails.

- Cached files **must** contain `"validated": true`.
- `sampleId` in the cached file **must** match the manifest sample.
- `expectedLabels` are research ground truth for evaluation and are **never** used as a prediction fallback.
- Cached mode is displayed as **CACHED QWEN FALLBACK** in the replay console.

## Replay behaviour

- Samples replay in `sequenceIndex` order.
- After the last sample, the loop returns to sample 1.
- Reset returns to the first enabled sample.
- Replay state is independent of hazards, training samples, and managed media.

## Licensing and privacy

- Only include assets whose licensing permits repository use.
- Do not include identifiable faces or licence plates without consent.
- Blur or exclude sensitive content before adding.

## Hazard activation

- `hazardPresence=yes` can activate the existing Sentinel hazard workflow.
- Activation creates an observation and may create/update a hazard.
- Warning text generation and warning-event dispatch are separate:
  - `warningTextGenerated` indicates multilingual warning strings were created.
  - `warningEventCreated` indicates at least one warning was successfully recorded in the perception graph or Neo4j.
- Activation is idempotent by deterministic inference ID.

## Manifest reload

`POST /api/sentinel/demo-replay/reload` re-reads `manifest.json` and refreshes the sample list without restarting the server.

## Public repository note

A cloned public repository starts unconfigured because real research images and cached prediction files are intentionally ignored by `.gitignore`.

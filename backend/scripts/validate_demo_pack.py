#!/usr/bin/env python3
"""Validation script for Sentinel deterministic dataset replay demo pack.

Verifies:
  1. manifest.json existence, schema validity, and consistency constraints.
  2. Image files (dashcam/topview) existence and path safety.
  3. cached_prediction.json files validity and alignment with manifest samples.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Add backend directory to path to allow importing models
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from models.demo_replay import DemoReplayManifest
    from models.vision_inference import CachedPredictionFile
except ImportError as exc:
    print(f"Error: Failed to import Sentinel models. Ensure sys.path contains: {BACKEND_DIR}")
    print(f"ImportError details: {exc}")
    sys.exit(1)


def validate_demo_pack(scenario_dir: Path) -> bool:
    print(f"Starting validation of demo pack at: {scenario_dir.resolve()}")
    manifest_path = scenario_dir / "manifest.json"

    if not manifest_path.exists():
        print(f"FAIL: manifest.json not found at {manifest_path}")
        return False

    # 1. Parse and validate manifest.json
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
    except Exception as e:
        print(f"FAIL: Failed to parse manifest.json as JSON: {e}")
        return False

    try:
        manifest = DemoReplayManifest(**manifest_data)
        print("PASS: manifest.json conforms to DemoReplayManifest schema.")
    except Exception as e:
        print(f"FAIL: manifest.json validation failed: {e}")
        return False

    # 2. Validate loop and sample counts
    enabled_samples = manifest.enabled_samples()
    print(f"Info: Found {len(manifest.samples)} total samples ({len(enabled_samples)} enabled).")
    if len(enabled_samples) < 4:
        print(f"FAIL: Minimum of 4 operational scenarios/samples required. Found {len(enabled_samples)}.")
        return False

    # 3. Validate each sample
    success = True
    for sample in manifest.samples:
        print(f"\nValidating sample: {sample.sample_id} (Enabled: {sample.enabled})")

        # Verify dashcam image
        dashcam_path = scenario_dir / sample.dashcam_path
        # Path safety validation (no traversal)
        try:
            dashcam_path.resolve().relative_to(scenario_dir.resolve())
        except ValueError:
            print(f"  FAIL: dashcamPath traversal detected: {sample.dashcam_path}")
            success = False
            continue

        if not dashcam_path.exists():
            print(f"  FAIL: dashcam image not found: {dashcam_path}")
            success = False
        else:
            print(f"  PASS: dashcam image exists: {sample.dashcam_path}")

        # Verify topview image
        topview_path = scenario_dir / sample.topview_path
        try:
            topview_path.resolve().relative_to(scenario_dir.resolve())
        except ValueError:
            print(f"  FAIL: topviewPath traversal detected: {sample.topview_path}")
            success = False
            continue

        if not topview_path.exists():
            print(f"  FAIL: top-view image not found: {topview_path}")
            success = False
        else:
            print(f"  PASS: top-view image exists: {sample.topview_path}")

        # Verify cached prediction if configured
        if sample.cached_prediction_path:
            cached_path = scenario_dir / sample.cached_prediction_path
            try:
                cached_path.resolve().relative_to(scenario_dir.resolve())
            except ValueError:
                print(f"  FAIL: cachedPredictionPath traversal detected: {sample.cached_prediction_path}")
                success = False
                continue

            if not cached_path.exists():
                print(f"  FAIL: cached prediction file not found: {cached_path}")
                success = False
            else:
                try:
                    with open(cached_path, "r", encoding="utf-8") as f:
                        cached_data = json.load(f)
                    # Validate cached prediction schema
                    cached_file = CachedPredictionFile(**cached_data)

                    # Consistency validation
                    if cached_file.sample_id != sample.sample_id:
                        print(f"  FAIL: cached prediction sampleId mismatch: expected {sample.sample_id}, got {cached_file.sample_id}")
                        success = False
                    elif not cached_file.validated:
                        print("  FAIL: cached prediction field 'validated' must be true")
                        success = False
                    else:
                        print(f"  PASS: cached prediction is valid and matches sample ID: {sample.cached_prediction_path}")
                except Exception as e:
                    print(f"  FAIL: cached prediction file validation failed: {e}")
                    success = False
        else:
            print("  WARN: No cached prediction path configured for this sample.")

    print("\n--------------------------------------------------")
    if success:
        print("VERIFICATION SUCCESS: All checks passed successfully.")
        return True
    else:
        print("VERIFICATION FAILURE: Some checks failed. Check diagnostics above.")
        return False


if __name__ == "__main__":
    default_dir = BACKEND_DIR / "demo_scenarios"
    target_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else default_dir
    ok = validate_demo_pack(target_dir)
    sys.exit(0 if ok else 1)

#!/usr/bin/env python3
"""Validate the Sentinel deterministic dataset replay demo pack.

Checks:
1. manifest.json exists and conforms to DemoReplayManifest.
2. source_map.example.json maps every replay sample to its real dataset sample.
3. Dashcam and top-view files exist and remain inside demo_scenarios.
4. cached_prediction.json conforms to CachedPredictionFile.
5. Cached predictions exactly match the mapped qwen_both_* CSV values.
6. Manifest expectedLabels exactly match the mapped gt_* CSV values.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

# Allow imports from the backend directory.
BACKEND_DIR = Path(__file__).resolve().parents[1]

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from models.demo_replay import DemoReplayManifest
    from models.vision_inference import CachedPredictionFile
except ImportError as exc:
    print(
        "Error: Failed to import Sentinel models. "
        f"Ensure sys.path contains: {BACKEND_DIR}"
    )
    print(f"ImportError details: {exc}")
    sys.exit(1)


QWEN_PREDICTION_COLUMNS = {
    "roadType": "qwen_both_road_type",
    "trafficDensity": "qwen_both_traffic_density",
    "roadComplexity": "qwen_both_road_complexity",
    "hazardPresence": "qwen_both_hazard_presence",
    "anticipatedRisk": "qwen_both_anticipated_risk",
    "recommendedAction": "qwen_both_recommended_action",
}

GROUND_TRUTH_COLUMNS = {
    "roadType": "gt_road_type",
    "trafficDensity": "gt_traffic_density",
    "roadComplexity": "gt_road_complexity",
    "hazardPresence": "gt_hazard_presence",
    "anticipatedRisk": "gt_anticipated_risk",
    "recommendedAction": "gt_recommended_action",
}

REQUIRED_QWEN_COLUMNS = {
    "sample_id",
    *QWEN_PREDICTION_COLUMNS.values(),
    *GROUND_TRUTH_COLUMNS.values(),
}


def resolve_safe_path(
    scenario_dir: Path,
    relative_path: str,
    label: str,
) -> Path | None:
    """Resolve a path and reject paths outside the scenario directory."""
    target = scenario_dir / relative_path

    try:
        target.resolve().relative_to(scenario_dir.resolve())
    except ValueError:
        print(f"  FAIL: {label} traversal detected: {relative_path}")
        return None

    return target


def load_source_map(source_map_path: Path) -> dict[str, str]:
    """Load and validate the local-to-source sample mapping."""
    with open(source_map_path, "r", encoding="utf-8") as file:
        source_map_data: Any = json.load(file)

    if not isinstance(source_map_data, dict):
        raise ValueError("source map must be a JSON object")

    source_map: dict[str, str] = {}

    for local_id, source_id in source_map_data.items():
        if not isinstance(local_id, str) or not isinstance(source_id, str):
            raise ValueError(
                "source map keys and values must both be strings"
            )

        local_id = local_id.strip()
        source_id = source_id.strip()

        if not local_id or not source_id:
            raise ValueError(
                "source map keys and values must not be empty"
            )

        source_map[local_id] = source_id

    return source_map


def load_qwen_rows(qwen_csv_path: Path) -> dict[str, dict[str, str]]:
    """Load Qwen CSV rows indexed by original sample_id."""
    rows: dict[str, dict[str, str]] = {}

    with open(
        qwen_csv_path,
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        reader = csv.DictReader(file)

        if reader.fieldnames is None:
            raise ValueError("Qwen CSV has no header row")

        available_columns = {
            column.strip()
            for column in reader.fieldnames
            if column is not None
        }

        missing_columns = REQUIRED_QWEN_COLUMNS - available_columns

        if missing_columns:
            missing_text = ", ".join(sorted(missing_columns))
            raise ValueError(
                f"Qwen CSV is missing required columns: {missing_text}"
            )

        for row_number, row in enumerate(reader, start=2):
            sample_id = (row.get("sample_id") or "").strip()

            if not sample_id:
                raise ValueError(
                    f"Qwen CSV row {row_number} has no sample_id"
                )

            if sample_id in rows:
                raise ValueError(
                    f"Duplicate Qwen CSV sample_id: {sample_id}"
                )

            rows[sample_id] = {
                key: (value or "").strip()
                for key, value in row.items()
                if key is not None
            }

    if not rows:
        raise ValueError("Qwen CSV contains no data rows")

    return rows


def values_from_row(
    row: dict[str, str],
    column_mapping: dict[str, str],
) -> dict[str, str]:
    """Convert selected CSV columns into API-style field names."""
    return {
        output_field: row[csv_column].strip()
        for output_field, csv_column in column_mapping.items()
    }


def validate_demo_pack(scenario_dir: Path) -> bool:
    """Validate the complete Sentinel replay demo pack."""
    scenario_dir = scenario_dir.resolve()

    print(f"Starting validation of demo pack at: {scenario_dir}")

    manifest_path = scenario_dir / "manifest.json"
    source_map_path = scenario_dir / "source_map.example.json"
    qwen_csv_path = scenario_dir / "qwen_complete_all_six_fields.csv"

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    if not manifest_path.exists():
        print(f"FAIL: manifest.json not found at {manifest_path}")
        return False

    try:
        with open(manifest_path, "r", encoding="utf-8") as file:
            manifest_data = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: Failed to parse manifest.json: {exc}")
        return False

    try:
        manifest = DemoReplayManifest(**manifest_data)
    except Exception as exc:
        print(f"FAIL: manifest.json validation failed: {exc}")
        return False

    print("PASS: manifest.json conforms to DemoReplayManifest schema.")

    # ------------------------------------------------------------------
    # Source map
    # ------------------------------------------------------------------

    if not source_map_path.exists():
        print(
            "FAIL: source_map.example.json not found at "
            f"{source_map_path}"
        )
        return False

    try:
        source_map = load_source_map(source_map_path)
    except Exception as exc:
        print(f"FAIL: Could not load source map: {exc}")
        return False

    print(
        f"PASS: Loaded source mapping for "
        f"{len(source_map)} local samples."
    )

    # ------------------------------------------------------------------
    # Qwen results
    # ------------------------------------------------------------------

    if not qwen_csv_path.exists():
        print(
            "FAIL: Qwen prediction CSV not found at "
            f"{qwen_csv_path}"
        )
        return False

    try:
        qwen_rows = load_qwen_rows(qwen_csv_path)
    except Exception as exc:
        print(f"FAIL: Could not load Qwen CSV: {exc}")
        return False

    print(f"PASS: Loaded {len(qwen_rows)} genuine Qwen result rows.")

    # ------------------------------------------------------------------
    # Pack-level checks
    # ------------------------------------------------------------------

    enabled_samples = manifest.enabled_samples()

    print(
        f"Info: Found {len(manifest.samples)} total samples "
        f"({len(enabled_samples)} enabled)."
    )

    if len(enabled_samples) < 4:
        print(
            "FAIL: Minimum of 4 operational scenarios required. "
            f"Found {len(enabled_samples)}."
        )
        return False

    manifest_sample_ids = {
        sample.sample_id
        for sample in manifest.samples
    }

    missing_source_mappings = sorted(
        sample_id
        for sample_id in manifest_sample_ids
        if sample_id not in source_map
    )

    if missing_source_mappings:
        print(
            "FAIL: Source mappings are missing for: "
            + ", ".join(missing_source_mappings)
        )
        return False

    extra_source_mappings = sorted(
        sample_id
        for sample_id in source_map
        if sample_id not in manifest_sample_ids
    )

    if extra_source_mappings:
        print(
            "WARN: Source map contains entries not present in the manifest: "
            + ", ".join(extra_source_mappings)
        )

    # ------------------------------------------------------------------
    # Per-sample checks
    # ------------------------------------------------------------------

    success = True

    for sample in manifest.samples:
        print(
            f"\nValidating sample: {sample.sample_id} "
            f"(Enabled: {sample.enabled})"
        )

        # --------------------------------------------------------------
        # Source provenance
        # --------------------------------------------------------------

        source_sample_id = source_map.get(sample.sample_id)

        if not source_sample_id:
            print(
                f"  FAIL: No source mapping exists for "
                f"{sample.sample_id}"
            )
            success = False
            continue

        qwen_row = qwen_rows.get(source_sample_id)

        if qwen_row is None:
            print(
                f"  FAIL: Source sample {source_sample_id} was not found "
                "in qwen_complete_all_six_fields.csv"
            )
            success = False
            continue

        print(
            f"  INFO: {sample.sample_id} maps to original "
            f"{source_sample_id}"
        )

        # --------------------------------------------------------------
        # Dashcam image
        # --------------------------------------------------------------

        dashcam_path = resolve_safe_path(
            scenario_dir,
            sample.dashcam_path,
            "dashcamPath",
        )

        if dashcam_path is None:
            success = False
        elif not dashcam_path.is_file():
            print(
                f"  FAIL: dashcam image not found: "
                f"{dashcam_path}"
            )
            success = False
        else:
            print(
                f"  PASS: dashcam image exists: "
                f"{sample.dashcam_path}"
            )

        # --------------------------------------------------------------
        # Top-view image
        # --------------------------------------------------------------

        topview_path = resolve_safe_path(
            scenario_dir,
            sample.topview_path,
            "topviewPath",
        )

        if topview_path is None:
            success = False
        elif not topview_path.is_file():
            print(
                f"  FAIL: top-view image not found: "
                f"{topview_path}"
            )
            success = False
        else:
            print(
                f"  PASS: top-view image exists: "
                f"{sample.topview_path}"
            )

        # --------------------------------------------------------------
        # Manifest ground truth
        # --------------------------------------------------------------

        expected_ground_truth = values_from_row(
            qwen_row,
            GROUND_TRUTH_COLUMNS,
        )

        if sample.expected_labels is None:
            print(
                f"  FAIL: Manifest expectedLabels are missing for "
                f"{sample.sample_id}"
            )
            success = False
        else:
            actual_ground_truth = (
                sample.expected_labels.model_dump(
                    by_alias=True,
                    mode="json",
                )
            )

            if actual_ground_truth != expected_ground_truth:
                print(
                    "  FAIL: Manifest ground truth does not match "
                    f"gt_* values for {source_sample_id}"
                )
                print(f"    Expected: {expected_ground_truth}")
                print(f"    Actual:   {actual_ground_truth}")
                success = False
            else:
                print(
                    "  PASS: Manifest ground truth matches "
                    f"{source_sample_id}"
                )

        # --------------------------------------------------------------
        # Cached prediction
        # --------------------------------------------------------------

        if not sample.cached_prediction_path:
            print(
                "  FAIL: No cached prediction path configured "
                "for this sample."
            )
            success = False
            continue

        cached_path = resolve_safe_path(
            scenario_dir,
            sample.cached_prediction_path,
            "cachedPredictionPath",
        )

        if cached_path is None:
            success = False
            continue

        if not cached_path.is_file():
            print(
                f"  FAIL: cached prediction file not found: "
                f"{cached_path}"
            )
            success = False
            continue

        try:
            with open(cached_path, "r", encoding="utf-8") as file:
                cached_data = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"  FAIL: cached prediction JSON could not be read: "
                f"{exc}"
            )
            success = False
            continue

        try:
            cached_file = CachedPredictionFile(**cached_data)
        except Exception as exc:
            print(
                f"  FAIL: cached prediction schema validation failed: "
                f"{exc}"
            )
            success = False
            continue

        if cached_file.sample_id != sample.sample_id:
            print(
                "  FAIL: cached prediction sampleId mismatch: "
                f"expected {sample.sample_id}, "
                f"got {cached_file.sample_id}"
            )
            success = False
            continue

        if cached_file.validated is not True:
            print(
                "  FAIL: cached prediction field "
                "'validated' must be true"
            )
            success = False
            continue

        print(
            "  PASS: cached prediction is valid and matches "
            f"sample ID: {sample.cached_prediction_path}"
        )

        expected_prediction = values_from_row(
            qwen_row,
            QWEN_PREDICTION_COLUMNS,
        )

        actual_prediction = cached_file.prediction.model_dump(
            by_alias=True,
            mode="json",
        )

        if actual_prediction != expected_prediction:
            print(
                "  FAIL: Cached prediction does not match genuine "
                f"Qwen fusion row {source_sample_id}"
            )
            print(f"    Expected: {expected_prediction}")
            print(f"    Actual:   {actual_prediction}")
            success = False
        else:
            print(
                "  PASS: Cache exactly matches Qwen fusion values "
                f"from {source_sample_id}"
            )

    # ------------------------------------------------------------------
    # Final result
    # ------------------------------------------------------------------

    print("\n--------------------------------------------------")

    if success:
        print(
            "VERIFICATION SUCCESS: All checks passed successfully."
        )
        return True

    print(
        "VERIFICATION FAILURE: Some checks failed. "
        "Check diagnostics above."
    )
    return False


if __name__ == "__main__":
    default_dir = BACKEND_DIR / "demo_scenarios"

    target_dir = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else default_dir
    )

    validation_passed = validate_demo_pack(target_dir)

    sys.exit(0 if validation_passed else 1)
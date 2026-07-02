#!/usr/bin/env python3
"""Smoke test for the Sentinel deterministic dataset replay demo pack.

Exercises:
1. Replay initialization.
2. Sample listing.
3. Current-sample retrieval.
4. Replay advancement.
5. Replay reset.
6. Genuine hazard-positive cached Qwen inference and activation.
7. Replay loop wrap-around.
8. Manifest reload.

The test writes all request results to demo_smoke_report.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Add backend directory to Python import path.
BACKEND_DIR = Path(__file__).resolve().parents[1]

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from fastapi.testclient import TestClient
    from server import app
except ImportError as exc:
    print(
        "Error: Failed to import FastAPI TestClient or Sentinel server app. "
        f"Details: {exc}"
    )
    sys.exit(1)


def response_data(response: Any) -> Any:
    """Read a JSON response safely for reporting."""
    try:
        return response.json()
    except Exception:
        return {
            "rawText": response.text,
        }


def run_smoke_test() -> dict[str, Any]:
    """Run the Sentinel replay smoke flow."""
    report: dict[str, Any] = {
        "summary": {
            "success": True,
            "totalSteps": 0,
            "failedSteps": 0,
        },
        "steps": [],
    }

    def log_step(
        name: str,
        passed: bool,
        details: dict[str, Any],
    ) -> None:
        report["summary"]["totalSteps"] += 1

        if not passed:
            report["summary"]["failedSteps"] += 1
            report["summary"]["success"] = False

        report["steps"].append(
            {
                "step": report["summary"]["totalSteps"],
                "name": name,
                "passed": passed,
                "details": details,
            }
        )

        status = "PASS" if passed else "FAIL"

        print(
            f"[{status}] Step "
            f"{report['summary']['totalSteps']}: {name}"
        )

    print("Starting Sentinel Replay Demo Pack Smoke Test...")

    # TestClient context executes the FastAPI lifespan startup and shutdown.
    with TestClient(app) as client:

        # --------------------------------------------------------------
        # Step 1: Replay service initialization
        # --------------------------------------------------------------

        try:
            response = client.get("/api/sentinel/demo-replay")
            data = response_data(response)

            passed = (
                response.status_code == 200
                and isinstance(data, dict)
                and data.get("status") == "ready"
                and data.get("sampleCount") == 5
                and data.get("currentIndex") == 0
                and data.get("currentSampleId") == "sample_001"
                and data.get("loop") is True
            )

            log_step(
                "GET replay initial status",
                passed,
                {
                    "statusCode": response.status_code,
                    "response": data,
                },
            )

        except Exception as exc:
            log_step(
                "GET replay initial status crashed",
                False,
                {
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                },
            )

        # --------------------------------------------------------------
        # Step 2: List the five configured replay samples
        # --------------------------------------------------------------

        try:
            response = client.get(
                "/api/sentinel/demo-replay/samples"
            )
            data = response_data(response)

            sample_ids = []

            if isinstance(data, list):
                sample_ids = [
                    sample.get("sampleId")
                    for sample in data
                    if isinstance(sample, dict)
                ]

            passed = (
                response.status_code == 200
                and isinstance(data, list)
                and len(data) == 5
                and sample_ids
                == [
                    "sample_001",
                    "sample_002",
                    "sample_003",
                    "sample_004",
                    "sample_005",
                ]
            )

            log_step(
                "GET replay sample list",
                passed,
                {
                    "statusCode": response.status_code,
                    "responseCount": (
                        len(data)
                        if isinstance(data, list)
                        else None
                    ),
                    "sampleIds": sample_ids,
                    "response": data,
                },
            )

        except Exception as exc:
            log_step(
                "GET replay sample list crashed",
                False,
                {
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                },
            )

        # --------------------------------------------------------------
        # Step 3: Confirm initial current sample
        # --------------------------------------------------------------

        try:
            response = client.get(
                "/api/sentinel/demo-replay/current"
            )
            data = response_data(response)

            sample = (
                data.get("sample", {})
                if isinstance(data, dict)
                else {}
            )

            passed = (
                response.status_code == 200
                and isinstance(data, dict)
                and sample.get("sampleId") == "sample_001"
                and data.get("currentIndex") == 0
                and data.get("sampleCount") == 5
                and data.get("hasNext") is True
            )

            log_step(
                "GET initial current replay sample",
                passed,
                {
                    "statusCode": response.status_code,
                    "response": data,
                },
            )

        except Exception as exc:
            log_step(
                "GET initial current replay sample crashed",
                False,
                {
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                },
            )

        # --------------------------------------------------------------
        # Step 4: Advance from sample_001 to sample_002
        # --------------------------------------------------------------

        try:
            response = client.post(
                "/api/sentinel/demo-replay/advance"
            )
            data = response_data(response)

            sample = (
                data.get("sample", {})
                if isinstance(data, dict)
                else {}
            )

            passed = (
                response.status_code == 200
                and isinstance(data, dict)
                and data.get("previousSampleId") == "sample_001"
                and sample.get("sampleId") == "sample_002"
                and data.get("currentIndex") == 1
                and data.get("sampleCount") == 5
                and data.get("looped") is False
            )

            log_step(
                "POST advance to sample_002",
                passed,
                {
                    "statusCode": response.status_code,
                    "response": data,
                },
            )

        except Exception as exc:
            log_step(
                "POST advance to sample_002 crashed",
                False,
                {
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                },
            )

        # --------------------------------------------------------------
        # Step 5: Reset replay pointer to sample_001
        # --------------------------------------------------------------

        try:
            response = client.post(
                "/api/sentinel/demo-replay/reset"
            )
            data = response_data(response)

            sample = (
                data.get("sample", {})
                if isinstance(data, dict)
                else {}
            )

            passed = (
                response.status_code == 200
                and isinstance(data, dict)
                and sample.get("sampleId") == "sample_001"
                and data.get("currentIndex") == 0
                and data.get("sampleCount") == 5
            )

            log_step(
                "POST reset replay pointer",
                passed,
                {
                    "statusCode": response.status_code,
                    "response": data,
                },
            )

        except Exception as exc:
            log_step(
                "POST reset replay pointer crashed",
                False,
                {
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                },
            )

        # --------------------------------------------------------------
        # Step 6: Genuine hazard-positive cached Qwen replay
        #
        # Local sample_005 maps to original dataset sample_041.
        # These prediction values must match the genuine qwen_both_*
        # values already verified by validate_demo_pack.py.
        # --------------------------------------------------------------

        try:
            response = client.post(
                (
                    "/api/sentinel/demo-replay/"
                    "samples/sample_005/infer"
                ),
                json={
                    "activate": True,
                },
            )

            data = response_data(response)

            expected_prediction = {
                "roadType": "junction",
                "trafficDensity": "medium",
                "roadComplexity": "moderate",
                "hazardPresence": "yes",
                "anticipatedRisk": "medium",
                "recommendedAction": "slow_down",
            }

            prediction = (
                data.get("prediction", {})
                if isinstance(data, dict)
                else {}
            )

            activation = (
                data.get("activation", {})
                if isinstance(data, dict)
                else {}
            )

            passed = (
                response.status_code == 200
                and isinstance(data, dict)
                and data.get("sampleId") == "sample_005"
                and data.get("inferenceMode") == "cached_qwen"
                and data.get("latencyMs") == 0
                and prediction == expected_prediction
                and activation.get("activated") is True
                and activation.get("reason") is None
                and bool(activation.get("observationId"))
                and bool(activation.get("hazardId"))
                and activation.get("warningTextGenerated") is True
            )

            log_step(
                (
                    "POST sample_005 cached inference and "
                    "hazard activation"
                ),
                passed,
                {
                    "statusCode": response.status_code,
                    "expectedPrediction": expected_prediction,
                    "actualPrediction": prediction,
                    "activation": activation,
                    "warningEventCreated": activation.get(
                        "warningEventCreated"
                    ),
                    "response": data,
                },
            )

        except Exception as exc:
            log_step(
                (
                    "POST sample_005 cached inference and "
                    "hazard activation crashed"
                ),
                False,
                {
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                },
            )

        # --------------------------------------------------------------
        # Step 7: Advance through all samples and verify loop-around
        # --------------------------------------------------------------

        try:
            intermediate_responses = []
            intermediate_success = True

            # Current pointer remains sample_001 because inference does not
            # change replay state.
            #
            # Advance four times:
            # sample_001 → 002 → 003 → 004 → 005
            for expected_id in (
                "sample_002",
                "sample_003",
                "sample_004",
                "sample_005",
            ):
                advance_response = client.post(
                    "/api/sentinel/demo-replay/advance"
                )
                advance_data = response_data(advance_response)

                intermediate_responses.append(advance_data)

                current_id = None

                if isinstance(advance_data, dict):
                    current_id = advance_data.get(
                        "sample",
                        {},
                    ).get("sampleId")

                if (
                    advance_response.status_code != 200
                    or current_id != expected_id
                ):
                    intermediate_success = False

            # One final advance should loop sample_005 → sample_001.
            response = client.post(
                "/api/sentinel/demo-replay/advance"
            )
            data = response_data(response)

            sample = (
                data.get("sample", {})
                if isinstance(data, dict)
                else {}
            )

            passed = (
                intermediate_success
                and response.status_code == 200
                and isinstance(data, dict)
                and data.get("previousSampleId") == "sample_005"
                and sample.get("sampleId") == "sample_001"
                and data.get("currentIndex") == 0
                and data.get("looped") is True
            )

            log_step(
                "POST advance and verify replay loop wrap-around",
                passed,
                {
                    "statusCode": response.status_code,
                    "intermediateResponses": intermediate_responses,
                    "response": data,
                },
            )

        except Exception as exc:
            log_step(
                "POST replay loop wrap-around crashed",
                False,
                {
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                },
            )

        # --------------------------------------------------------------
        # Step 8: Reload manifest and verify pointer reset
        # --------------------------------------------------------------

        try:
            # Move away from index zero before reloading.
            pre_reload_response = client.post(
                "/api/sentinel/demo-replay/advance"
            )
            pre_reload_data = response_data(
                pre_reload_response
            )

            response = client.post(
                "/api/sentinel/demo-replay/reload"
            )
            data = response_data(response)

            passed = (
                pre_reload_response.status_code == 200
                and response.status_code == 200
                and isinstance(data, dict)
                and data.get("status") == "ready"
                and data.get("sampleCount") == 5
                and data.get("currentIndex") == 0
                and data.get("currentSampleId") == "sample_001"
                and data.get("loop") is True
            )

            log_step(
                "POST reload manifest and reset replay state",
                passed,
                {
                    "preReloadResponse": pre_reload_data,
                    "statusCode": response.status_code,
                    "response": data,
                },
            )

        except Exception as exc:
            log_step(
                "POST reload manifest crashed",
                False,
                {
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                },
            )

    return report


if __name__ == "__main__":
    smoke_report = run_smoke_test()

    report_path = (
        BACKEND_DIR.parent
        / "demo_smoke_report.json"
    )

    with open(
        report_path,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            smoke_report,
            file,
            indent=2,
        )

    print(
        "\nSmoke test complete. "
        f"Report saved to: {report_path.resolve()}"
    )

    print(
        "Summary: "
        f"{smoke_report['summary']['totalSteps']} steps, "
        f"{smoke_report['summary']['failedSteps']} failures."
    )

    if not smoke_report["summary"]["success"]:
        sys.exit(1)

    sys.exit(0)
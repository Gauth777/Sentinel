#!/usr/bin/env python3
"""Smoke test script for Sentinel deterministic dataset replay demo pack.

Runs end-to-end endpoint tests using FastAPI TestClient, exercising advance,
reset, reload, infer, and looping behaviors, and writes results to demo_smoke_report.json.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Add backend directory to path
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from fastapi.testclient import TestClient
    from server import app
except ImportError as exc:
    print(f"Error: Failed to import FastAPI or Server app. Details: {exc}")
    sys.exit(1)


def run_smoke_test() -> dict:
    report = {
        "summary": {
            "success": True,
            "total_steps": 0,
            "failed_steps": 0
        },
        "steps": []
    }

    def log_step(name: str, passed: bool, details: dict):
        report["summary"]["total_steps"] += 1
        if not passed:
            report["summary"]["failed_steps"] += 1
            report["summary"]["success"] = False
        report["steps"].append({
            "step": report["summary"]["total_steps"],
            "name": name,
            "passed": passed,
            "details": details
        })
        status_str = "PASS" if passed else "FAIL"
        print(f"[{status_str}] Step {report['summary']['total_steps']}: {name}")

    print("Starting Sentinel Replay Demo Pack Smoke Test...")

    # Start FastAPI TestClient with lifespan context manager to run startup/shutdown events
    with TestClient(app) as client:
        # Step 1: Check Replay Status
        try:
            resp = client.get("/api/sentinel/demo-replay")
            passed = resp.status_code == 200
            data = resp.json() if passed else {}
            if passed:
                passed = (
                    data.get("status") == "ready" and
                    data.get("sampleCount") == 5 and
                    data.get("currentIndex") == 0 and
                    data.get("currentSampleId") == "sample_001"
                )
            log_step("GET /api/sentinel/demo-replay (initial status)", passed, {
                "statusCode": resp.status_code,
                "response": data
            })
        except Exception as e:
            log_step("GET /api/sentinel/demo-replay (initial status) crashed", False, {"error": str(e)})

        # Step 2: List Samples
        try:
            resp = client.get("/api/sentinel/demo-replay/samples")
            passed = resp.status_code == 200
            data = resp.json() if passed else []
            if passed:
                passed = len(data) == 5 and all("sampleId" in s for s in data)
            log_step("GET /api/sentinel/demo-replay/samples (list)", passed, {
                "statusCode": resp.status_code,
                "responseCount": len(data),
                "response": data
            })
        except Exception as e:
            log_step("GET /api/sentinel/demo-replay/samples crashed", False, {"error": str(e)})

        # Step 3: Get Current Sample
        try:
            resp = client.get("/api/sentinel/demo-replay/current")
            passed = resp.status_code == 200
            data = resp.json() if passed else {}
            if passed:
                passed = (
                    data.get("sample", {}).get("sampleId") == "sample_001" and
                    data.get("currentIndex") == 0 and
                    data.get("hasNext") is True
                )
            log_step("GET /api/sentinel/demo-replay/current (initial)", passed, {
                "statusCode": resp.status_code,
                "response": data
            })
        except Exception as e:
            log_step("GET /api/sentinel/demo-replay/current crashed", False, {"error": str(e)})

        # Step 4: Advance to sample_002
        try:
            resp = client.post("/api/sentinel/demo-replay/advance")
            passed = resp.status_code == 200
            data = resp.json() if passed else {}
            if passed:
                passed = (
                    data.get("previousSampleId") == "sample_001" and
                    data.get("sample", {}).get("sampleId") == "sample_002" and
                    data.get("currentIndex") == 1 and
                    data.get("looped") is False
                )
            log_step("POST /api/sentinel/demo-replay/advance (first)", passed, {
                "statusCode": resp.status_code,
                "response": data
            })
        except Exception as e:
            log_step("POST /api/sentinel/demo-replay/advance (first) crashed", False, {"error": str(e)})

        # Step 5: Reset back to sample_001
        try:
            resp = client.post("/api/sentinel/demo-replay/reset")
            passed = resp.status_code == 200
            data = resp.json() if passed else {}
            if passed:
                passed = (
                    data.get("sample", {}).get("sampleId") == "sample_001" and
                    data.get("currentIndex") == 0
                )
            log_step("POST /api/sentinel/demo-replay/reset", passed, {
                "statusCode": resp.status_code,
                "response": data
            })
        except Exception as e:
            log_step("POST /api/sentinel/demo-replay/reset crashed", False, {"error": str(e)})

        # Step 6: Test Inference cached fallback for sample_001
        try:
            resp = client.post("/api/sentinel/demo-replay/samples/sample_001/infer", json={"activate": True})
            passed = resp.status_code == 200
            data = resp.json() if passed else {}
            if passed:
                passed = (
                    data.get("sampleId") == "sample_001" and
                    data.get("inferenceMode") == "cached_qwen" and
                    data.get("latencyMs") == 0.0 and
                    "prediction" in data and
                    "activation" in data
                )
            log_step("POST /api/sentinel/demo-replay/samples/sample_001/infer (cached fallback)", passed, {
                "statusCode": resp.status_code,
                "response": data
            })
        except Exception as e:
            log_step("POST /api/sentinel/demo-replay/samples/sample_001/infer crashed", False, {"error": str(e)})

        # Step 7: Test Looping wrap-around (advance 5 times)
        try:
            # We are currently at index 0 (sample_001). Advance 4 times to reach index 4 (sample_005)
            for idx in range(1, 5):
                client.post("/api/sentinel/demo-replay/advance")
            
            # Now advance one more time to trigger loop wrap-around
            resp = client.post("/api/sentinel/demo-replay/advance")
            passed = resp.status_code == 200
            data = resp.json() if passed else {}
            if passed:
                passed = (
                    data.get("previousSampleId") == "sample_005" and
                    data.get("sample", {}).get("sampleId") == "sample_001" and
                    data.get("currentIndex") == 0 and
                    data.get("looped") is True
                )
            log_step("POST /api/sentinel/demo-replay/advance (loop wrap-around)", passed, {
                "statusCode": resp.status_code,
                "response": data
            })
        except Exception as e:
            log_step("POST /api/sentinel/demo-replay/advance (loop) crashed", False, {"error": str(e)})

        # Step 8: Test Reload Manifest
        try:
            # Advance to sample_002 first, so index is non-zero
            client.post("/api/sentinel/demo-replay/advance")
            
            # Reload
            resp = client.post("/api/sentinel/demo-replay/reload")
            passed = resp.status_code == 200
            data = resp.json() if passed else {}
            if passed:
                # Reload resets index to 0
                passed = (
                    data.get("status") == "ready" and
                    data.get("currentIndex") == 0 and
                    data.get("currentSampleId") == "sample_001"
                )
            log_step("POST /api/sentinel/demo-replay/reload", passed, {
                "statusCode": resp.status_code,
                "response": data
            })
        except Exception as e:
            log_step("POST /api/sentinel/demo-replay/reload crashed", False, {"error": str(e)})

    return report


if __name__ == "__main__":
    rep = run_smoke_test()
    report_path = BACKEND_DIR.parent / "demo_smoke_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    print(f"\nSmoke test complete. Report saved to: {report_path.resolve()}")
    if not rep["summary"]["success"]:
        sys.exit(1)
    sys.exit(0)

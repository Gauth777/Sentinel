import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
import json

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from models.training_samples import DatasetStatus, TrainingSampleCreate
from services.training_sample_service import TrainingSampleService, DuplicateError
from models.demo_replay import DemoReplaySample, DemoLocation, DemoExpectedLabels


@pytest.mark.anyio
async def test_seeding_in_memory_mode():
    # 1. Mock DemoReplayService
    demo_replay_service = MagicMock()
    
    # 5 enabled samples
    samples = []
    for i in range(1, 6):
        sample = DemoReplaySample(
            sample_id=f"sample_00{i}",
            sequence_index=i,
            title=f"Sample {i}",
            description=f"Description {i}",
            dashcam_path=f"sample_00{i}/dashcam.jpg",
            topview_path=f"sample_00{i}/topview.png",
            location=DemoLocation(latitude=12.0 + i, longitude=80.0 + i),
            heading_degrees=float(i * 10),
            captured_at=datetime.now(timezone.utc),
            tags=["tag"],
            expected_labels=DemoExpectedLabels(
                road_type="highway",
                traffic_density="high",
                road_complexity="simple",
                hazard_presence="yes",
                anticipated_risk="medium",
                recommended_action="slow_down"
            )
        )
        samples.append(sample)
        
    demo_replay_service.get_enabled_samples = AsyncMock(return_value=samples)
    
    # Returns valid cached prediction for all 5
    async def mock_get_cached_prediction(sample_id):
        return {
            "sampleId": sample_id,
            "model": "Qwen2.5-VL-7B-Instruct",
            "promptVersion": "v1",
            "generatedAt": "2026-07-06T10:00:00Z",
            "prediction": {
                "roadType": "highway",
                "trafficDensity": "high",
                "roadComplexity": "simple",
                "hazardPresence": "yes",
                "anticipatedRisk": "medium",
                "recommendedAction": "slow_down"
            },
            "runtimeHazard": {
                "hazardType": "pothole",
                "hazardDescription": "Pothole ahead",
                "confidence": 0.95
            },
            "validated": True
        }
    
    demo_replay_service.get_cached_prediction = AsyncMock(side_effect=mock_get_cached_prediction)
    
    # Initialize service in memory mode
    svc = TrainingSampleService(db=None, mongo_reachable=False)
    await svc.initialize()
    
    # Run seed
    await svc.seed_memory_mode(demo_replay_service)
    
    # Verify exactly 5 samples loaded
    stats = await svc.get_stats()
    assert stats.total == 5
    assert stats.pending == 5
    assert stats.verified == 0
    assert stats.rejected == 0
    
    # Verify deterministic IDs
    for i in range(1, 6):
        sample = await svc.get_sample(f"ts-replay-sample_00{i}")
        assert sample is not None
        assert sample.sample_id == f"ts-replay-sample_00{i}"
        
    # Idempotency check: second seed call remains at 5
    await svc.seed_memory_mode(demo_replay_service)
    stats2 = await svc.get_stats()
    assert stats2.total == 5


@pytest.mark.anyio
async def test_seeding_skips_malformed_cached_prediction():
    demo_replay_service = MagicMock()
    
    # 2 enabled samples
    samples = []
    for i in range(1, 3):
        sample = DemoReplaySample(
            sample_id=f"sample_00{i}",
            sequence_index=i,
            title=f"Sample {i}",
            description=f"Description {i}",
            dashcam_path=f"sample_00{i}/dashcam.jpg",
            topview_path=f"sample_00{i}/topview.png",
            location=DemoLocation(latitude=12.0 + i, longitude=80.0 + i),
            heading_degrees=float(i * 10),
            captured_at=datetime.now(timezone.utc),
            tags=["tag"]
        )
        samples.append(sample)
        
    demo_replay_service.get_enabled_samples = AsyncMock(return_value=samples)
    
    # Returns valid cached prediction for sample 1, and None (malformed) for sample 2
    async def mock_get_cached_prediction(sample_id):
        if sample_id == "sample_001":
            return {
                "sampleId": "sample_001",
                "model": "Qwen2.5-VL-7B-Instruct",
                "promptVersion": "v1",
                "generatedAt": "2026-07-06T10:00:00Z",
                "prediction": {
                    "roadType": "highway",
                    "trafficDensity": "high",
                    "roadComplexity": "simple",
                    "hazardPresence": "yes",
                    "anticipatedRisk": "medium",
                    "recommendedAction": "slow_down"
                },
                "validated": True
            }
        return None
        
    demo_replay_service.get_cached_prediction = AsyncMock(side_effect=mock_get_cached_prediction)
    
    svc = TrainingSampleService(db=None, mongo_reachable=False)
    await svc.initialize()
    
    await svc.seed_memory_mode(demo_replay_service)
    
    # Should only successfully seed sample_001
    stats = await svc.get_stats()
    assert stats.total == 1
    assert await svc.get_sample("ts-replay-sample_001") is not None
    assert await svc.get_sample("ts-replay-sample_002") is None


@pytest.mark.anyio
async def test_mongo_mode_is_not_seeded():
    demo_replay_service = MagicMock()
    
    # Setup mock Mongo db database collections
    mock_db = MagicMock()
    mock_coll = MagicMock()
    mock_coll.count_documents = AsyncMock(return_value=0)
    mock_db.__getitem__ = MagicMock(return_value=mock_coll)
    
    svc = TrainingSampleService(db=mock_db, mongo_reachable=True)
    
    # Run seed
    await svc.seed_memory_mode(demo_replay_service)
    
    # get_enabled_samples should not even have been called because it skips mongo mode
    assert not demo_replay_service.get_enabled_samples.called

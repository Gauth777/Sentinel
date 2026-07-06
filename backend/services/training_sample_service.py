"""TrainingSampleService — dataset lifecycle and persistence for Sentinel VLM engine.

Supports MongoDB (primary) and isolated in-memory fallback (demo/test).
Does not import FastAPI.
"""
import asyncio
import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from models.training_samples import (
    DatasetStatus,
    FeedbackEvent,
    FeedbackStatus,
    FeedbackStatusInput,
    TrainingFeedbackCreate,
    TrainingSample,
    TrainingSampleCreate,
    TrainingSampleListResponse,
    TrainingStatsResponse,
)

logger = logging.getLogger(__name__)


class DuplicateError(Exception):
    """Raised when a sampleId already exists."""

    pass


class ServiceError(Exception):
    """Raised for general service-level failures."""

    pass


class ConcurrencyError(ServiceError):
    """Raised when an optimistic-concurrency conflict occurs."""

    pass


def _get_nested(doc: dict, key: str) -> Any:
    """Resolve dotted keys such as model.name or final_verified_labels.road_type."""
    parts = key.split(".")
    val = doc
    for part in parts:
        if not isinstance(val, dict):
            return None
        val = val.get(part)
        if val is None:
            return None
    return val


class _InMemoryTrainingStore:
    """Thread-safe in-memory store with asyncio.Lock."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._samples: Dict[str, dict] = {}

    async def find_one(self, filter: dict, projection: Optional[dict] = None) -> Optional[dict]:
        async with self._lock:
            for doc in self._samples.values():
                match = True
                for k, v in filter.items():
                    if _get_nested(doc, k) != v:
                        match = False
                        break
                if match:
                    res = deepcopy(doc)
                    if projection and projection.get("_id") == 0:
                        res.pop("_id", None)
                    return res
            return None

    async def find(self, filter: Optional[dict] = None, projection: Optional[dict] = None) -> List[dict]:
        filter = filter or {}
        async with self._lock:
            results = []
            for doc in self._samples.values():
                match = True
                for k, v in filter.items():
                    if _get_nested(doc, k) != v:
                        match = False
                        break
                if match:
                    res = deepcopy(doc)
                    if projection and projection.get("_id") == 0:
                        res.pop("_id", None)
                    results.append(res)
            return results

    async def insert_one(self, document: dict) -> None:
        sample_id = document.get("sample_id")
        async with self._lock:
            if sample_id in self._samples:
                raise DuplicateError(f"sample_id '{sample_id}' already exists")
            self._samples[sample_id] = deepcopy(document)

    async def replace_one(self, filter: dict, replacement: dict, upsert: bool = False) -> bool:
        async with self._lock:
            for k, doc in self._samples.items():
                match = True
                for fk, fv in filter.items():
                    if _get_nested(doc, fk) != fv:
                        match = False
                        break
                if match:
                    self._samples[k] = deepcopy(replacement)
                    return True
            if upsert:
                sid = replacement.get("sample_id")
                if sid is not None:
                    self._samples[sid] = deepcopy(replacement)
                return True
            return False

    async def find_one_and_update(
        self, filter: dict, update_fn: Callable[[dict], dict]
    ) -> Optional[dict]:
        """Atomically read, modify, and write a document under the store lock."""
        async with self._lock:
            for k, doc in self._samples.items():
                match = True
                for fk, fv in filter.items():
                    if _get_nested(doc, fk) != fv:
                        match = False
                        break
                if match:
                    updated = update_fn(deepcopy(doc))
                    self._samples[k] = updated
                    return deepcopy(updated)
            return None

    async def count_documents(self, filter: Optional[dict] = None) -> int:
        filter = filter or {}
        async with self._lock:
            count = 0
            for doc in self._samples.values():
                match = True
                for k, v in filter.items():
                    if _get_nested(doc, k) != v:
                        match = False
                        break
                if match:
                    count += 1
            return count

    async def delete_many(self, filter: dict) -> int:
        async with self._lock:
            to_delete = []
            for k, doc in self._samples.items():
                match = True
                for fk, fv in filter.items():
                    if _get_nested(doc, fk) != fv:
                        match = False
                        break
                if match:
                    to_delete.append(k)
            for k in to_delete:
                del self._samples[k]
            return len(to_delete)


class TrainingSampleService:
    """Handles training-sample lifecycle: create, read, feedback, stats, export."""

    def __init__(self, db: Any, mongo_reachable: bool) -> None:
        self._db = db
        self._mongo_reachable = mongo_reachable
        self._mode: str = "mongo" if mongo_reachable else "memory"
        self._memory_store = _InMemoryTrainingStore()
        self._collection_name = "training_samples"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collection(self):
        if self._mode == "mongo":
            return self._db[self._collection_name]
        return self._memory_store

    async def _initialize_indexes(self) -> None:
        if self._mode != "mongo":
            return
        coll = self._collection()
        try:
            await coll.create_index("sample_id", unique=True)
        except Exception as e:
            logger.warning("TrainingSampleService: sample_id unique index failed: %s", type(e).__name__)
        for field in ["dataset_status", "hazard_id", "captured_at"]:
            try:
                await coll.create_index(field)
            except Exception as e:
                logger.warning("TrainingSampleService: %s index failed: %s", field, type(e).__name__)
        try:
            await coll.create_index([("captured_at", -1), ("created_at", -1)])
        except Exception as e:
            logger.warning("TrainingSampleService: compound index failed: %s", type(e).__name__)

    async def initialize(self) -> None:
        await self._initialize_indexes()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _remove_id(doc: dict) -> dict:
        doc.pop("_id", None)
        return doc

    @staticmethod
    def _prediction_to_dict(pred: Any) -> dict:
        if isinstance(pred, dict):
            return dict(pred)
        return pred.model_dump(by_alias=False)

    @staticmethod
    def _merge_final_labels(original: dict, corrections: Optional[Dict[str, Any]]) -> dict:
        merged = {k: v for k, v in original.items() if k in {
            "road_type", "traffic_density", "road_complexity",
            "hazard_presence", "anticipated_risk", "recommended_action",
        }}
        if corrections:
            for k, v in corrections.items():
                if v is not None and k in merged:
                    merged[k] = v
        return merged

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create_sample(self, data: TrainingSampleCreate) -> TrainingSample:
        now = self._now()
        pred_dict = data.prediction.model_dump(by_alias=False)
        doc = {
            "schema_version": data.schema_version,
            "sample_id": data.sample_id,
            "observation_id": data.observation_id,
            "hazard_id": data.hazard_id,
            "source_vehicle_id": data.source_vehicle_id,
            "captured_at": data.captured_at,
            "context": data.context.model_dump(by_alias=False),
            "media": data.media.model_dump(by_alias=False),
            "model": data.model.model_dump(by_alias=False),
            "prediction": pred_dict,
            "original_prediction": pred_dict,
            "final_verified_labels": None,
            "provenance": data.provenance.model_dump(by_alias=False),
            "quality": data.quality.model_dump(by_alias=False) if data.quality is not None else None,
            "dataset_status": DatasetStatus.pending.value,
            "feedback_status": FeedbackStatus.pending.value,
            "feedback_history": [],
            "created_at": now,
            "updated_at": now,
            "revision": 1,
        }

        coll = self._collection()
        try:
            if self._mode == "mongo":
                await coll.insert_one(doc)
            else:
                await coll.insert_one(doc)
        except DuplicateError:
            raise
        except Exception as e:
            if self._mode == "mongo":
                if "duplicate" in str(e).lower() or "E11000" in str(e):
                    raise DuplicateError(f"sample_id '{data.sample_id}' already exists")
                logger.error("MongoDB insert failed: %s", type(e).__name__)
                raise ServiceError(f"Database operation failed: {type(e).__name__}")
            raise

        return TrainingSample(**self._remove_id(deepcopy(doc)))

    async def get_sample(self, sample_id: str) -> Optional[TrainingSample]:
        coll = self._collection()
        try:
            if self._mode == "mongo":
                doc = await coll.find_one({"sample_id": sample_id}, {"_id": 0})
            else:
                doc = await coll.find_one({"sample_id": sample_id}, {"_id": 0})
        except Exception as e:
            logger.error("TrainingSampleService get_sample failed: %s", type(e).__name__)
            raise ServiceError(f"Database operation failed: {type(e).__name__}")
        if doc is None:
            return None
        return TrainingSample(**self._remove_id(doc))

    async def list_samples(
        self,
        status: Optional[DatasetStatus] = None,
        feedback_status: Optional[FeedbackStatus] = None,
        hazard_id: Optional[str] = None,
        source_vehicle_id: Optional[str] = None,
        model_name: Optional[str] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> TrainingSampleListResponse:
        query: dict = {}
        if status is not None:
            query["dataset_status"] = status.value
        if feedback_status is not None:
            query["feedback_status"] = feedback_status.value
        if hazard_id is not None:
            query["hazard_id"] = hazard_id
        if source_vehicle_id is not None:
            query["source_vehicle_id"] = source_vehicle_id
        if model_name is not None:
            query["model.name"] = model_name

        coll = self._collection()
        try:
            if self._mode == "mongo":
                cursor = coll.find(query, {"_id": 0}).sort(
                    [("captured_at", -1), ("created_at", -1)]
                ).skip(skip).limit(limit)
                docs = await cursor.to_list(length=limit)
            else:
                docs = await coll.find(query, {"_id": 0})
                docs.sort(
                    key=lambda d: (d.get("captured_at", datetime.min), d.get("created_at", datetime.min)),
                    reverse=True,
                )
                docs = docs[skip : skip + limit]
        except Exception as e:
            logger.error("TrainingSampleService list_samples failed: %s", type(e).__name__)
            raise ServiceError(f"Database operation failed: {type(e).__name__}")

        items = [TrainingSample(**self._remove_id(deepcopy(d))) for d in docs]
        return TrainingSampleListResponse(
            items=items,
            count=len(items),
            limit=limit,
            skip=skip,
            mode=self._mode,  # type: ignore[arg-type]
        )

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    async def submit_feedback(
        self, sample_id: str, feedback: TrainingFeedbackCreate
    ) -> Optional[TrainingSample]:
        coll = self._collection()
        now = self._now()
        submitted_at = feedback.submitted_at or now

        corrections_dict = None
        if feedback.corrected_labels is not None:
            corrections_dict = feedback.corrected_labels.to_correction_dict()

        event = {
            "status": feedback.status.value,
            "corrected_labels": corrections_dict,
            "submitted_by": feedback.submitted_by,
            "submitted_at": submitted_at,
            "note": feedback.note,
        }

        # Determine lifecycle changes
        if feedback.status == FeedbackStatusInput.confirmed:
            new_dataset_status = DatasetStatus.verified.value
            new_feedback_status = FeedbackStatus.confirmed.value
        elif feedback.status == FeedbackStatusInput.corrected:
            new_dataset_status = DatasetStatus.verified.value
            new_feedback_status = FeedbackStatus.corrected.value
        else:  # rejected
            new_dataset_status = DatasetStatus.rejected.value
            new_feedback_status = FeedbackStatus.rejected.value

        # For memory mode: perform the entire read-modify-write under one lock
        if self._mode == "memory":
            def _update(doc: dict) -> dict:
                if feedback.status == FeedbackStatusInput.confirmed:
                    doc["final_verified_labels"] = self._merge_final_labels(doc["original_prediction"], {})
                elif feedback.status == FeedbackStatusInput.corrected:
                    doc["final_verified_labels"] = self._merge_final_labels(doc["original_prediction"], corrections_dict)
                else:
                    doc["final_verified_labels"] = None

                doc["dataset_status"] = new_dataset_status
                doc["feedback_status"] = new_feedback_status
                history = doc.get("feedback_history", [])
                history.append(event)
                doc["feedback_history"] = history
                doc["revision"] = doc.get("revision", 1) + 1
                doc["updated_at"] = now
                return doc

            try:
                updated_doc = await coll.find_one_and_update({"sample_id": sample_id}, _update)
            except Exception as e:
                logger.error("TrainingSampleService memory feedback failed: %s", type(e).__name__)
                raise ServiceError(f"Database operation failed: {type(e).__name__}")
            if updated_doc is None:
                return None
            return TrainingSample(**self._remove_id(updated_doc))

        # For Mongo mode: use atomic find_one_and_update with $inc, $push, $set
        if self._mode == "mongo":
            from pymongo import ReturnDocument

            # Build $set fields
            set_fields: dict = {
                "dataset_status": new_dataset_status,
                "feedback_status": new_feedback_status,
                "updated_at": now,
            }
            if feedback.status == FeedbackStatusInput.confirmed:
                # We need the original_prediction from the doc to build final_verified_labels
                # Because we can't reference fields in $set, we fetch first then compute
                # But to keep atomic, we can do a two-step: fetch + atomic update with expected revision
                # However, for simplicity and correctness, we'll use the two-step with revision guard
                pass

            # For Mongo, we'll do a fetch-compute-update with revision check for safety
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    doc = await coll.find_one({"sample_id": sample_id}, {"_id": 0})
                    if doc is None:
                        return None

                    expected_revision = doc.get("revision", 1)

                    if feedback.status == FeedbackStatusInput.confirmed:
                        set_fields["final_verified_labels"] = self._merge_final_labels(doc["original_prediction"], {})
                    elif feedback.status == FeedbackStatusInput.corrected:
                        set_fields["final_verified_labels"] = self._merge_final_labels(doc["original_prediction"], corrections_dict)
                    else:
                        set_fields["final_verified_labels"] = None

                    result = await coll.find_one_and_update(
                        {"sample_id": sample_id, "revision": expected_revision},
                        {
                            "$set": set_fields,
                            "$inc": {"revision": 1},
                            "$push": {"feedback_history": event},
                        },
                        return_document=ReturnDocument.AFTER,
                    )
                    if result is not None:
                        return TrainingSample(**self._remove_id(result))
                    # Revision conflict — retry
                except Exception as e:
                    logger.error("TrainingSampleService Mongo feedback failed: %s", type(e).__name__)
                    raise ServiceError(f"Database operation failed: {type(e).__name__}")

            raise ConcurrencyError(f"Unable to apply feedback to {sample_id} after {max_retries} attempts due to revision conflict")

        return None

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def get_stats(self) -> TrainingStatsResponse:
        coll = self._collection()

        try:
            if self._mode == "mongo":
                total = await coll.count_documents({})
                pending = await coll.count_documents({"dataset_status": DatasetStatus.pending.value})
                verified = await coll.count_documents({"dataset_status": DatasetStatus.verified.value})
                rejected = await coll.count_documents({"dataset_status": DatasetStatus.rejected.value})
                confirmed = await coll.count_documents({"feedback_status": FeedbackStatus.confirmed.value})
                corrected = await coll.count_documents({"feedback_status": FeedbackStatus.corrected.value})
                exportable = await coll.count_documents({
                    "dataset_status": DatasetStatus.verified.value,
                })
            else:
                total = await coll.count_documents()
                pending = await coll.count_documents({"dataset_status": DatasetStatus.pending.value})
                verified = await coll.count_documents({"dataset_status": DatasetStatus.verified.value})
                rejected = await coll.count_documents({"dataset_status": DatasetStatus.rejected.value})
                confirmed = await coll.count_documents({"feedback_status": FeedbackStatus.confirmed.value})
                corrected = await coll.count_documents({"feedback_status": FeedbackStatus.corrected.value})
                exportable = await coll.count_documents({"dataset_status": DatasetStatus.verified.value})
        except Exception as e:
            logger.error("TrainingSampleService get_stats failed: %s", type(e).__name__)
            raise ServiceError(f"Database operation failed: {type(e).__name__}")

        # Distribution counts
        by_road_type: Dict[str, int] = {}
        by_hazard_presence: Dict[str, int] = {}
        by_recommended_action: Dict[str, int] = {}

        try:
            if self._mode == "mongo":
                all_docs = await coll.find({"dataset_status": DatasetStatus.verified.value}, {"_id": 0}).to_list(10000)
            else:
                all_docs = await coll.find({"dataset_status": DatasetStatus.verified.value}, {"_id": 0})
        except Exception as e:
            logger.error("TrainingSampleService stats distribution failed: %s", type(e).__name__)
            all_docs = []

        for d in all_docs:
            labels = d.get("final_verified_labels") or d.get("original_prediction") or {}
            if not labels:
                continue
            rt = labels.get("road_type")
            if rt:
                by_road_type[rt] = by_road_type.get(rt, 0) + 1
            hp = labels.get("hazard_presence")
            if hp:
                by_hazard_presence[hp] = by_hazard_presence.get(hp, 0) + 1
            ra = labels.get("recommended_action")
            if ra:
                by_recommended_action[ra] = by_recommended_action.get(ra, 0) + 1

        return TrainingStatsResponse(
            mode=self._mode,  # type: ignore[arg-type]
            total=total,
            pending=pending,
            verified=verified,
            rejected=rejected,
            confirmed=confirmed,
            corrected=corrected,
            exportable=exportable,
            by_road_type=by_road_type,
            by_hazard_presence=by_hazard_presence,
            by_recommended_action=by_recommended_action,
        )

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    async def export_verified(
        self,
        model_name: Optional[str] = None,
        road_type: Optional[str] = None,
        hazard_presence: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[dict]:
        query: dict = {"dataset_status": DatasetStatus.verified.value}
        if model_name is not None:
            query["model.name"] = model_name
        if road_type is not None:
            query["final_verified_labels.road_type"] = road_type
        if hazard_presence is not None:
            query["final_verified_labels.hazard_presence"] = hazard_presence

        coll = self._collection()
        try:
            if self._mode == "mongo":
                cursor = coll.find(query, {"_id": 0}).sort(
                    [("captured_at", -1), ("created_at", -1)]
                )
                if limit:
                    cursor = cursor.limit(limit)
                docs = await cursor.to_list(length=limit or 10000)
            else:
                docs = await coll.find(query, {"_id": 0})
                docs.sort(
                    key=lambda d: (d.get("captured_at", datetime.min), d.get("created_at", datetime.min)),
                    reverse=True,
                )
                if limit:
                    docs = docs[:limit]
        except Exception as e:
            logger.error("TrainingSampleService export failed: %s", type(e).__name__)
            raise ServiceError(f"Database operation failed: {type(e).__name__}")

        export_lines = []
        for doc in docs:
            clean = self._remove_id(deepcopy(doc))
            sample = TrainingSample(**clean)
            export_dict = sample.to_export_dict()
            for key in ["captured_at", "created_at", "updated_at"]:
                if key in export_dict and isinstance(export_dict[key], datetime):
                    export_dict[key] = export_dict[key].isoformat().replace("+00:00", "Z")
            for evt in export_dict.get("feedback_history", []):
                if isinstance(evt.get("submitted_at"), datetime):
                    evt["submitted_at"] = evt["submitted_at"].isoformat().replace("+00:00", "Z")
            if export_dict.get("final_verified_labels"):
                export_dict["verified_at"] = export_dict.get("updated_at")
            export_lines.append(export_dict)

        return export_lines

    async def seed_memory_mode(self, demo_replay_service: Any) -> None:
        """Seed memory database with the five curated replay samples.

        Runs only in memory mode. Idempotent. Malformed samples are skipped and logged.
        """
        if self._mode != "memory":
            logger.info("TrainingSampleService: skip seeding because mode is %s", self._mode)
            return

        import json
        from models.training_samples import (
            Context,
            GeoLocation,
            TelemetrySource,
            Media,
            MediaType,
            StorageMode,
            ModelInfo,
            InferenceMode as TrainingInferenceMode,
            PredictionLabels,
            Provenance,
            ProvenanceSource,
            QualityMetadata,
            PrivacyStatus
        )

        try:
            enabled_samples = await demo_replay_service.get_enabled_samples()
        except Exception as e:
            logger.error("Failed to retrieve enabled samples for seeding: %s", e)
            return

        for sample in enabled_samples:
            sample_id = f"ts-replay-{sample.sample_id}"

            # Idempotency check
            try:
                existing = await self.get_sample(sample_id)
                if existing is not None:
                    continue
            except Exception:
                pass

            # Fetch and validate cached prediction
            cached_pred = await demo_replay_service.get_cached_prediction(sample.sample_id)
            if cached_pred is None:
                logger.warning("Skipping training sample seed for %s: cached prediction missing or malformed", sample.sample_id)
                continue

            try:
                lat = sample.location.latitude if sample.location else 0.0
                lon = sample.location.longitude if sample.location else 0.0
                captured_at = sample.captured_at or datetime.now(timezone.utc)

                context = Context(
                    location=GeoLocation(latitude=lat, longitude=lon),
                    heading_degrees=sample.heading_degrees,
                    speed_kmh=None,
                    road_name=None,
                    route_direction=None,
                    telemetry_source=TelemetrySource.demo
                )

                media = Media(
                    type=MediaType.image,
                    uri=f"demo://{sample.dashcam_path}",
                    mime_type="image/jpeg",
                    storage_mode=StorageMode.demo_uri
                )

                model_info = ModelInfo(
                    provider="Qwen",
                    name=cached_pred.get("model", "Qwen2.5-VL-7B-Instruct"),
                    version="2.5",
                    prompt_version=cached_pred.get("promptVersion", "v1"),
                    inference_id=f"inf-replay-{sample.sample_id}",
                    inference_mode=TrainingInferenceMode.demo
                )

                pred_data = cached_pred.get("prediction", {})
                prediction = PredictionLabels(
                    road_type=pred_data.get("roadType"),
                    traffic_density=pred_data.get("trafficDensity"),
                    road_complexity=pred_data.get("roadComplexity"),
                    hazard_presence=pred_data.get("hazardPresence"),
                    anticipated_risk=pred_data.get("anticipatedRisk"),
                    recommended_action=pred_data.get("recommendedAction"),
                    confidence=cached_pred.get("runtimeHazard", {}).get("confidence") if cached_pred.get("runtimeHazard") else None,
                    raw_response=cached_pred.get("rawResponse")
                )

                provenance = Provenance(
                    source=ProvenanceSource.demo,
                    session_id=None,
                    device_id=None
                )

                notes = []
                if sample.topview_path:
                    notes.append(f"topview: demo://{sample.topview_path}")
                if sample.expected_labels:
                    expected_dict = sample.expected_labels.model_dump(by_alias=True)
                    notes.append(f"expected_labels: {json.dumps(expected_dict)}")

                quality = QualityMetadata(
                    privacy_status=PrivacyStatus.not_reviewed,
                    unusable_reason=None,
                    notes=notes if notes else None
                )

                payload = TrainingSampleCreate(
                    sample_id=sample_id,
                    source_vehicle_id="v-replay-observer",
                    captured_at=captured_at,
                    context=context,
                    media=media,
                    model=model_info,
                    prediction=prediction,
                    provenance=provenance,
                    quality=quality
                )

                await self.create_sample(payload)
                logger.info("Successfully seeded training sample: %s", sample_id)

            except Exception as e:
                logger.warning("Failed to seed training sample %s: %s", sample.sample_id, e)
                continue

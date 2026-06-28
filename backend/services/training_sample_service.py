"""TrainingSampleService — dataset lifecycle and persistence for Sentinel VLM engine.

Supports MongoDB (primary) and isolated in-memory fallback (demo/test).
Does not import FastAPI.
"""
import asyncio
import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from models.training_samples import (
    DatasetStatus,
    FeedbackEvent,
    FeedbackStatus,
    FinalVerifiedLabels,
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
                    if doc.get(k) != v:
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
                    if doc.get(k) != v:
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
                    if doc.get(fk) != fv:
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

    async def count_documents(self, filter: Optional[dict] = None) -> int:
        filter = filter or {}
        async with self._lock:
            count = 0
            for doc in self._samples.values():
                match = True
                for k, v in filter.items():
                    if doc.get(k) != v:
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
                    if doc.get(fk) != fv:
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
    def _merge_final_labels(original: dict, corrections: Optional[dict]) -> dict:
        merged = dict(original)
        if corrections:
            for k, v in corrections.items():
                if v is not None:
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
            "quality": data.quality.model_dump(by_alias=False) if data.quality else None,
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
                # Check for duplicate key error
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
                # Sort newest first by captured_at, then created_at
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

        # Fetch current document
        try:
            if self._mode == "mongo":
                doc = await coll.find_one({"sample_id": sample_id}, {"_id": 0})
            else:
                doc = await coll.find_one({"sample_id": sample_id}, {"_id": 0})
        except Exception as e:
            logger.error("TrainingSampleService feedback fetch failed: %s", type(e).__name__)
            raise ServiceError(f"Database operation failed: {type(e).__name__}")

        if doc is None:
            return None

        now = self._now()
        submitted_at = feedback.submitted_at or now

        event = {
            "status": feedback.status.value,
            "corrected_labels": feedback.corrected_labels,
            "submitted_by": feedback.submitted_by,
            "submitted_at": submitted_at,
            "note": feedback.note,
        }

        # Apply lifecycle rules
        if feedback.status == FeedbackStatus.confirmed:
            doc["dataset_status"] = DatasetStatus.verified.value
            doc["feedback_status"] = FeedbackStatus.confirmed.value
            doc["final_verified_labels"] = deepcopy(doc["original_prediction"])
        elif feedback.status == FeedbackStatus.corrected:
            doc["dataset_status"] = DatasetStatus.verified.value
            doc["feedback_status"] = FeedbackStatus.corrected.value
            merged = self._merge_final_labels(doc["original_prediction"], feedback.corrected_labels)
            # Strip extra fields to ensure only canonical labels remain
            canonical_keys = {
                "road_type",
                "traffic_density",
                "road_complexity",
                "hazard_presence",
                "anticipated_risk",
                "recommended_action",
            }
            doc["final_verified_labels"] = {k: merged[k] for k in canonical_keys if k in merged}
        elif feedback.status == FeedbackStatus.rejected:
            doc["dataset_status"] = DatasetStatus.rejected.value
            doc["feedback_status"] = FeedbackStatus.rejected.value
            doc["final_verified_labels"] = None

        # Append feedback event and bump revision
        history = doc.get("feedback_history", [])
        history.append(event)
        doc["feedback_history"] = history
        doc["revision"] = doc.get("revision", 1) + 1
        doc["updated_at"] = now

        # Persist
        try:
            if self._mode == "mongo":
                await coll.replace_one({"sample_id": sample_id}, doc)
            else:
                await coll.replace_one({"sample_id": sample_id}, doc)
        except Exception as e:
            logger.error("TrainingSampleService feedback persist failed: %s", type(e).__name__)
            raise ServiceError(f"Database operation failed: {type(e).__name__}")

        return TrainingSample(**self._remove_id(deepcopy(doc)))

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
            # Reconstruct with deterministic ordering using Pydantic
            sample = TrainingSample(**clean)
            export_dict = sample.to_export_dict()
            # Flatten datetime fields to ISO strings for JSONL
            for key in ["captured_at", "created_at", "updated_at"]:
                if key in export_dict and isinstance(export_dict[key], datetime):
                    export_dict[key] = export_dict[key].isoformat().replace("+00:00", "Z")
            # Also flatten nested datetime fields in feedback_history
            for evt in export_dict.get("feedback_history", []):
                if isinstance(evt.get("submitted_at"), datetime):
                    evt["submitted_at"] = evt["submitted_at"].isoformat().replace("+00:00", "Z")
            # Add verified_at if available
            if export_dict.get("final_verified_labels"):
                export_dict["verified_at"] = export_dict.get("updated_at")
            export_lines.append(export_dict)

        return export_lines

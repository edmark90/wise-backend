import asyncio
import logging
from fastapi import APIRouter, File, UploadFile, HTTPException, status, Depends
from pydantic import BaseModel
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

from apps.database import get_db
from apps.utils.image_processor import (
    MAX_BYTES,
    prepare_inference_package,
    save_waste_image,
    validate_image_metadata,
)
from apps.services.model_service import model_service, class_key
from apps.services.storage_service import delete_stored_image
from apps.utils.jwt import get_optional_current_user
from apps.models.user import User
from apps.models.waste_record import WasteRecord

logger = logging.getLogger("waste_classifier.router_prediction")

router = APIRouter(prefix="/api/v1", tags=["Prediction"])


class PredictionResponse(BaseModel):
    success: bool
    prediction: str
    confidence: float
    probabilities: Dict[str, float]
    processing_time_ms: float
    record_id: Optional[int] = None
    image_url: Optional[str] = None
    confidence_level: Optional[str] = None
    is_low_confidence: Optional[bool] = None
    confidence_margin: Optional[float] = None
    warning: Optional[str] = None
    image_quality: Optional[Dict[str, Any]] = None
    views_evaluated: Optional[int] = None


async def _read_bounded_upload(upload: UploadFile, max_bytes: int) -> bytes:
    """Read an UploadFile up to max_bytes + 1 so oversized uploads fail fast without exhausting RAM."""
    chunks: list[bytes] = []
    total = 0
    chunk_size = 256 * 1024
    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=getattr(status, "HTTP_413_CONTENT_TOO_LARGE", 413),
                detail=f"File size exceeds maximum allowed size of {max_bytes // (1024 * 1024)}MB.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/predict",
    response_model=PredictionResponse,
    status_code=status.HTTP_200_OK,
    summary="Classify Waste Image",
    description="Accepts an image via multipart upload, validates format/size, normalizes EXIF/sRGB/alpha, runs multi-view EfficientNetB0 waste classification inference, then stores the photo + result into waste_records for the admin dashboard.",
)
async def predict_waste_image(
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    """Waste Image Prediction Endpoint."""
    if not image or not image.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No image file provided in multipart payload.",
        )

    try:
        try:
            contents = await _read_bounded_upload(image, MAX_BYTES)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to read uploaded file stream: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Could not read uploaded image file stream.",
            )

        content_type = image.content_type or "application/octet-stream"
        validate_image_metadata(
            content_type=content_type,
            file_size=len(contents),
            image_bytes=contents,
        )

        # Offload Pillow decode, EXIF/ICC/alpha normalization, and ROI cropping to a worker thread
        package = await asyncio.to_thread(prepare_inference_package, contents)

        # Run async thread-safe multi-view model prediction
        prediction_result = await model_service.predict_pipeline_async(package)

        # Persist the photo + result for the admin dashboard (fail-open)
        stored_path = None
        try:
            stored_path = await asyncio.to_thread(save_waste_image, contents, image.filename)
            short_class = class_key(prediction_result["prediction"])
            record = WasteRecord(
                user_id=current_user.id if current_user else None,
                image_url=stored_path,
                waste_type=short_class,
                disposal_category=short_class,
                confidence=prediction_result["confidence"],
                is_flagged=1 if prediction_result.get("is_low_confidence") else 0,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            prediction_result["record_id"] = record.id
            prediction_result["image_url"] = stored_path
            logger.info(
                f"Saved waste record #{record.id} for prediction '{short_class}' ({record.confidence}%, flagged={record.is_flagged})"
            )
        except Exception as persist_err:
            db.rollback()
            if stored_path:
                await asyncio.to_thread(_remove_stored_file, stored_path)
            logger.warning(f"Failed to persist waste record (prediction still returned): {persist_err}")

        return prediction_result

    finally:
        await image.close()


def _remove_stored_file(stored_path: str) -> None:
    """Best-effort removal of a waste image when its DB insert failed."""
    delete_stored_image(stored_path)

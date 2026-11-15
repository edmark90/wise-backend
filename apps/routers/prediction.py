import os
import logging
from fastapi import APIRouter, File, UploadFile, HTTPException, status, Depends
from pydantic import BaseModel
from typing import Dict, Optional
from sqlalchemy.orm import Session

from apps.database import get_db
from apps.utils.image_processor import validate_image_metadata, process_image_bytes, save_waste_image
from apps.services.model_service import model_service, class_key
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


@router.post(
    "/predict",
    response_model=PredictionResponse,
    status_code=status.HTTP_200_OK,
    summary="Classify Waste Image",
    description="Accepts an image via multipart upload, validates format/size, preprocesses it, runs MobileNetV2 waste classification inference, then stores the photo + result into waste_records for the admin dashboard."
)
async def predict_waste_image(
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    """
    Waste Image Prediction Endpoint.

    - Accepts: multipart/form-data field 'image'
    - Returns: JSON object containing predicted waste category, confidence %, probability breakdown, and execution time.
    - Side effect (fail-open): the photo is saved to uploads/waste and a row is
      inserted into waste_records so the admin Waste Classification page gets
      live data. If persistence fails the prediction is still returned.
    """
    if not image or not image.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No image file provided in multipart payload."
        )

    # 1. Read raw image content into memory
    try:
        contents = await image.read()
    except Exception as e:
        logger.error(f"Failed to read uploaded file stream: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not read uploaded image file stream."
        )

    # 2. Validate MIME type & file size limit (sniffs bytes when MIME is generic)
    content_type = image.content_type or "application/octet-stream"
    file_size = len(contents)

    validate_image_metadata(content_type=content_type, file_size=file_size, image_bytes=contents)

    try:
        # 3. Preprocess image into 4D tensor (1, 224, 224, 3)
        tensor_batch = process_image_bytes(contents)

        # 4. Run async thread-safe model prediction
        prediction_result = await model_service.predict_async(tensor_batch)

        # 5. Persist the photo + result for the admin dashboard (fail-open)
        stored_path = None
        try:
            stored_path = save_waste_image(contents, image.filename)
            short_class = class_key(prediction_result["prediction"])
            record = WasteRecord(
                user_id=current_user.id if current_user else None,
                image_url=stored_path,
                waste_type=short_class,
                disposal_category=short_class,
                confidence=prediction_result["confidence"],
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            prediction_result["record_id"] = record.id
            prediction_result["image_url"] = stored_path
            logger.info(f"Saved waste record #{record.id} for prediction '{short_class}' ({record.confidence}%)")
        except Exception as persist_err:
            db.rollback()
            # Don't keep orphan image files when the DB insert failed.
            if stored_path:
                _remove_stored_file(stored_path)
            logger.warning(f"Failed to persist waste record (prediction still returned): {persist_err}")

        return prediction_result

    finally:
        await image.close()


def _remove_stored_file(stored_path: str) -> None:
    """Best-effort removal of a waste image when its DB insert failed."""
    try:
        full = os.path.normpath(os.path.join(os.getcwd(), stored_path))
        if os.path.isfile(full):
            os.remove(full)
    except OSError:
        pass
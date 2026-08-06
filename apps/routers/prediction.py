import os
import shutil
import logging
import tempfile
from fastapi import APIRouter, File, UploadFile, HTTPException, status
from pydantic import BaseModel
from typing import Dict

from apps.utils.image_processor import validate_image_metadata, process_image_bytes
from apps.services.model_service import model_service

logger = logging.getLogger("waste_classifier.router_prediction")

router = APIRouter(prefix="/api/v1", tags=["Prediction"])


class PredictionResponse(BaseModel):
    success: bool
    prediction: str
    confidence: float
    probabilities: Dict[str, float]
    processing_time_ms: float


@router.post(
    "/predict",
    response_model=PredictionResponse,
    status_code=status.HTTP_200_OK,
    summary="Classify Waste Image",
    description="Accepts an image via multipart upload, validates format/size, preprocesses it, and runs MobileNetV2 waste classification inference."
)
async def predict_waste_image(image: UploadFile = File(...)):
    """
    Waste Image Prediction Endpoint.
    
    - Accepts: multipart/form-data field 'image'
    - Returns: JSON object containing predicted waste category, confidence %, probability breakdown, and execution time.
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

    # 3. Create temporary file on disk if required (guarantees automatic cleanup in finally block)
    temp_file_path = None
    try:
        # Create temp file in system upload scratch directory
        temp_dir = os.path.join(os.getcwd(), "uploads")
        os.makedirs(temp_dir, exist_ok=True)
        
        with tempfile.NamedTemporaryFile(delete=False, dir=temp_dir, suffix=os.path.splitext(image.filename)[1]) as tmp:
            tmp.write(contents)
            temp_file_path = tmp.name

        # 4. Preprocess image into 4D tensor (1, 224, 224, 3)
        tensor_batch = process_image_bytes(contents)

        # 5. Run async thread-safe model prediction
        prediction_result = await model_service.predict_async(tensor_batch)

        return prediction_result

    finally:
        # 6. Delete temporary file after prediction process finishes
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception as cleanup_err:
                logger.warning(f"Could not remove temp file {temp_file_path}: {cleanup_err}")
        
        await image.close()

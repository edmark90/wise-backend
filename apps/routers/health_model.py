from fastapi import APIRouter, status
from typing import Dict, Any
from apps.services.model_service import model_service
from apps.database import test_connection

router = APIRouter(tags=["Health & Model Info"])


@router.get(
    "/health",
    status_code=status.HTTP_200_OK,
    summary="Backend & Model Health Status",
    description="Check operational status of FastAPI backend server, database connection, and AI Model state."
)
def get_health_status() -> Dict[str, Any]:
    db_ok = False
    try:
        db_ok = test_connection()
    except Exception:
        db_ok = False

    model_info = model_service.get_info()

    return {
        "status": "healthy",
        "database": "connected" if db_ok else "disconnected",
        "model": model_info["status"],
        "model_file": model_info["model_file"]
    }


@router.get(
    "/model/info",
    status_code=status.HTTP_200_OK,
    summary="Waste Classifier Model Information",
    description="Retrieves classification model metadata, input shape requirements, labels, and TensorFlow version."
)
def get_model_information() -> Dict[str, Any]:
    return model_service.get_info()

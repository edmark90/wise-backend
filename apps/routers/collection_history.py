from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
from apps.database import get_db
from apps.schemas.collection_history import CollectionHistoryResponse, CollectionHistoryListResponse
from apps.services.collection_history_service import get_collection_history
from apps.utils.jwt import get_current_admin
from apps.model.user import User

router = APIRouter()

@router.get("/", response_model=CollectionHistoryListResponse)
def list_collection_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    search: Optional[str] = None,
    personnel_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get collection history with search and filters."""
    skip = (page - 1) * page_size
    result = get_collection_history(
        db,
        skip=skip,
        limit=page_size,
        search=search,
        personnel_id=personnel_id,
        start_date=start_date,
        end_date=end_date
    )
    
    return {
        "history": result["history"],
        "total": result["total"],
        "page": page,
        "page_size": page_size
    }

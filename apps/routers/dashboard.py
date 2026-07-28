from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from apps.database import get_db
from apps.schemas.dashboard import DashboardStats
from apps.services.dashboard_service import get_dashboard_stats
from apps.utils.jwt import get_current_admin
from apps.model.user import User

router = APIRouter()

@router.get("/stats", response_model=DashboardStats)
def get_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get dashboard statistics."""
    return get_dashboard_stats(db)

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from apps.database import get_db
from apps.schemas.dashboard import DashboardStats
from apps.services.dashboard import get_dashboard_stats, get_monthly_dashboard_stats
from apps.utils.jwt import get_current_admin
from apps.models.user import User

router = APIRouter()

@router.get("/stats", response_model=DashboardStats)
def get_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get dashboard statistics."""
    return get_dashboard_stats(db)

@router.get("/monthly")
def get_monthly_dashboard(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Month-scoped stats: daily trend + per-class breakdown for charts."""
    return get_monthly_dashboard_stats(db, year, month)

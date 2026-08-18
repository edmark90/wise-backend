"""Report export endpoints for the admin Reports page.

GET /api/reports/export?format=excel|pdf&year=&month=&types=waste,schedule,users
GET /api/reports/summary?year=&month=
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from apps.database import get_db
from apps.models.collection_schedule import CollectionSchedule
from apps.models.user import User
from apps.models.waste_record import WasteRecord
from apps.services.reports import (
    REPORT_TYPES,
    build_excel,
    build_pdf,
    period_label,
    resolve_period,
    schedule_stats,
    waste_stats,
)
from apps.utils.jwt import get_current_admin

router = APIRouter()


@router.get("/export")
def export_report(
    format: str = Query("excel", pattern="^(excel|pdf)$"),
    year: int = Query(..., ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    types: str = Query("waste,schedule,users"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """Download a single report file (Excel or PDF) for the chosen period.

    `types` is a comma-separated subset of:
    waste, schedule, users. All are included by default.
    """
    selected = [t.strip() for t in types.split(",") if t.strip() in REPORT_TYPES]
    if not selected:
        raise HTTPException(status_code=400, detail="No valid report types selected.")

    if format == "excel":
        content = build_excel(db, year, month, selected)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ext = "xlsx"
    else:
        content = build_pdf(db, year, month, selected)
        media_type = "application/pdf"
        ext = "pdf"

    label = period_label(year, month)
    filename = f"WISE_Report_{label.replace(' ', '_')}.{ext}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(iter([content]), media_type=media_type, headers=headers)


@router.get("/summary")
def report_summary(
    year: int = Query(..., ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """Quick counts per report type for the requested period (summary chips)."""
    start, end = resolve_period(year, month)
    waste = waste_stats(db, start, end)
    schedule = schedule_stats(db, start, end)

    users_total = (
        db.query(func.count(User.id))
        .filter(User.created_at >= start, User.created_at <= end)
        .scalar() or 0
    )

    return {
        "period": period_label(year, month),
        "waste_records": waste["total_records"],
        "waste_avg_confidence": waste["avg_confidence"],
        "waste_low_confidence": waste["low_confidence"],
        "waste_most_common_type": waste["most_common_type"],
        "waste_per_class": waste["per_class"],
        "collection_schedules": schedule["total_routes"],
        "routes_completed": schedule["completed"],
        "routes_cancelled": schedule["cancelled"],
        "routes_delayed": schedule["delayed"],
        "routes_upcoming": schedule["upcoming"],
        "completion_rate": schedule["completion_rate"],
        "users": users_total,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
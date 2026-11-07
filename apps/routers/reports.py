"""Report export, summary, preview, and leaderboard endpoints for the admin Reports page.

GET /api/reports/export?format=excel|pdf&year=&month=&types=...
GET /api/reports/summary?year=&month=
GET /api/reports/preview?type=&year=&month=
GET /api/reports/barangay-leaderboard?year=&month=
"""
import io
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from apps.database import get_db
from apps.models.user import User
from apps.schemas.leaderboard import BarangayLeaderboardResponse
from apps.services.reports import (
    EXCEL_HEADERS,
    REPORT_TYPES,
    TYPE_LABELS,
    _data_for,
    build_excel,
    build_pdf,
    get_barangay_recycling_leaderboard,
    get_report_summary_data,
    period_label,
    resolve_period,
)
from apps.utils.jwt import get_current_admin

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/export")
def export_report(
    format: str = Query("excel", pattern="^(excel|pdf)$"),
    year: int = Query(..., ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    types: str = Query("barangay_performance,waste_trends,collection_calendar,notification_analytics,schedule"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """Download an administrative report file (Excel or PDF) for the selected period.

    `types` is a comma-separated list of selected report types.
    """
    selected = [t.strip() for t in types.split(",") if t.strip() in REPORT_TYPES]
    if not selected:
        raise HTTPException(status_code=400, detail="No valid report types selected.")

    try:
        if format == "excel":
            content = build_excel(db, year, month, selected)
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ext = "xlsx"
        else:
            content = build_pdf(db, year, month, selected)
            media_type = "application/pdf"
            ext = "pdf"
    except Exception as e:
        logger.exception("Failed to generate report export: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to generate {format.upper()} report: {str(e)}")

    label = period_label(year, month)
    clean_label = label.replace(" ", "_")
    filename = f"WISE_Report_{clean_label}.{ext}"

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Access-Control-Expose-Headers": "Content-Disposition",
    }
    return StreamingResponse(io.BytesIO(content), media_type=media_type, headers=headers)


@router.get("/summary")
def report_summary(
    year: int = Query(..., ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """Authoritative summary metrics for the selected period.

    Guaranteed identical to the figures exported in Excel and PDF.
    """
    summary = get_report_summary_data(db, year, month)

    return {
        "period": summary["period"],
        "total_collections": summary["total_collections"],
        "completed_collections": summary["completed_collections"],
        "delayed_collections": summary["delayed_collections"],
        "cancelled_collections": summary["cancelled_collections"],
        "upcoming_collections": summary["upcoming_collections"],
        "completion_rate": summary["completion_rate"],
        "waste_classifications": summary["waste_classifications"],
        "recycling_rate": summary["recycling_rate"],
        "recyclable_count": summary["recyclable_count"],
        "avg_confidence": summary["avg_confidence"],
        "low_confidence_count": summary["low_confidence_count"],
        "top_barangay": summary["top_barangay"],
        "total_barangays": summary["total_barangays"],
        "efficiency_score": summary["efficiency_score"],
        "users": summary["new_users"],
        "generated_at": summary["generated_at"],
        # Backward compatibility aliases
        "waste_records": summary["waste_classifications"],
        "waste_avg_confidence": summary["avg_confidence"],
        "waste_low_confidence": summary["low_confidence_count"],
        "waste_most_common_type": summary["most_common_type"],
        "waste_per_class": summary["per_class"],
        "collection_schedules": summary["total_collections"],
        "routes_completed": summary["completed_collections"],
        "routes_cancelled": summary["cancelled_collections"],
        "routes_delayed": summary["delayed_collections"],
        "routes_upcoming": summary["upcoming_collections"],
    }


@router.get("/preview")
def report_preview(
    type: str = Query("barangay_performance"),
    year: int = Query(..., ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """Structured report table data for live interactive preview on the Reports page."""
    if type not in REPORT_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid report type: '{type}'")

    start_ph, end_ph = resolve_period(year, month)
    data = _data_for(db, start_ph, end_ph, [type])
    rows = data.get(type, [])
    headers = EXCEL_HEADERS.get(type, [])
    label = TYPE_LABELS.get(type, type.replace("_", " ").title())

    return {
        "type": type,
        "title": label,
        "period": period_label(year, month),
        "headers": headers,
        "rows": rows,
        "total_records": len(rows),
    }


@router.get("/barangay-leaderboard", response_model=BarangayLeaderboardResponse)
def get_leaderboard(
    year: int | None = Query(None, ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """Barangay Recycling & AI Classification Leaderboard for Civic Incentives and City Council Grants."""
    return get_barangay_recycling_leaderboard(db, year, month)

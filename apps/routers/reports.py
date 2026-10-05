import io
"""Report export and summary endpoints for the admin Reports page.

GET /api/reports/export?format=excel|pdf&year=&month=&types=barangay_performance,waste_trends,...
GET /api/reports/summary?year=&month=
"""
from datetime import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from apps.database import get_db
from apps.models.collection_schedule import CollectionSchedule
from apps.models.user import User
from apps.models.waste_record import WasteRecord
from apps.services.collection_schedule import derive_schedule_status
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
    """Quick summary metrics for the selected period."""
    start, end = resolve_period(year, month)
    waste = waste_stats(db, start, end)
    schedule = schedule_stats(db, start, end)

    total_collections = schedule["total_routes"]
    completed_collections = schedule["completed"]
    delayed_collections = schedule["delayed"]
    completion_rate = schedule["completion_rate"]

    waste_classifications = waste["total_records"]
    avg_confidence = waste["avg_confidence"]

    # Calculate recycling rate (recyclable waste / total classified waste)
    recycling_rate = 0.0
    if waste["per_class"]:
        recyclable_count = 0
        total_count = sum(waste["per_class"].values())
        for waste_type, count in waste["per_class"].items():
            wt_lower = (waste_type or "").lower()
            if any(term in wt_lower for term in ["recyclable", "plastic", "paper", "cardboard", "metal", "glass", "can"]):
                recyclable_count += count
        if total_count > 0:
            recycling_rate = round(recyclable_count / total_count * 100, 1)

    # Barangay performance aggregation
    barangay_stats = {}
    schedules = (
        db.query(CollectionSchedule)
        .filter(CollectionSchedule.collection_date >= start.date(), CollectionSchedule.collection_date <= end.date())
        .all()
    )

    for schedule_item in schedules:
        barangay = schedule_item.barangay or "Unknown"
        if barangay not in barangay_stats:
            barangay_stats[barangay] = {"total": 0, "completed": 0}
        barangay_stats[barangay]["total"] += 1
        status = derive_schedule_status(schedule_item)
        if status == "Completed":
            barangay_stats[barangay]["completed"] += 1

    top_barangay = "-"
    best_rate = -1
    total_barangays = len(barangay_stats)
    efficiency_scores = []

    for barangay, stats in barangay_stats.items():
        if stats["total"] > 0:
            efficiency = round(stats["completed"] / stats["total"] * 100, 1)
            efficiency_scores.append(efficiency)
            if efficiency > best_rate:
                best_rate = efficiency
                top_barangay = barangay

    avg_efficiency = round(sum(efficiency_scores) / len(efficiency_scores), 1) if efficiency_scores else 0.0

    return {
        "period": period_label(year, month),
        "total_collections": total_collections,
        "completed_collections": completed_collections,
        "delayed_collections": delayed_collections,
        "completion_rate": completion_rate,
        "waste_classifications": waste_classifications,
        "recycling_rate": recycling_rate,
        "avg_confidence": avg_confidence,
        "top_barangay": top_barangay,
        "total_barangays": total_barangays,
        "efficiency_score": avg_efficiency,
        # Backward compatibility
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
        "users": (
            db.query(func.count(User.id))
            .filter(User.created_at >= start, User.created_at <= end)
            .scalar() or 0
        ),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

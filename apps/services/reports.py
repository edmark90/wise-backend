"""Report generation service (Excel + PDF).

Curated 5-Report System for WISE:
  1. Barangay Performance (per-barangay collection statistics & AI metrics)
  2. Waste Classification Summary (breakdown by waste type, category, AI confidence & flags)
  3. Collection Operations Calendar (day-by-day operational logs and completion rates)
  4. Notification Analytics (broadcast delivery counts and citizen read engagement)
  5. Collection Schedule Records (full exportable operational schedule with routes & personnel)

Legacy aliases supported for backward compatibility:
  - waste, users, route_efficiency, environmental_impact, comparative_performance, peak_analysis
"""
import calendar
import io
import os
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from apps.models.collection_schedule import CollectionSchedule
from apps.models.notification import Notification
from apps.models.notification_read import NotificationRead
from apps.models.user import User
from apps.models.waste_record import WasteRecord
from apps.services.collection_schedule import derive_schedule_status
from apps.utils.ph_time import ph_now

REPORT_TYPES = [
    "barangay_performance",
    "waste_trends",
    "collection_calendar",
    "notification_analytics",
    "schedule",
    # Backward compatibility aliases
    "notification_effectiveness",
    "waste",
    "users",
    "route_efficiency",
    "environmental_impact",
    "comparative_performance",
    "peak_analysis",
]

TYPE_LABELS = {
    "barangay_performance": "Barangay Performance Summary",
    "waste_trends": "Waste Classification Summary",
    "collection_calendar": "Collection Operations Calendar",
    "notification_analytics": "Notification Analytics & Engagement",
    "schedule": "Collection Schedule Records",
    # Legacy
    "notification_effectiveness": "Notification Analytics & Engagement",
    "waste": "Waste Classification Records",
    "users": "User Registration Records",
    "route_efficiency": "Route Efficiency (Legacy)",
    "environmental_impact": "Environmental Impact (Legacy)",
    "comparative_performance": "Comparative Performance (Legacy)",
    "peak_analysis": "Peak Analysis (Legacy)",
}

TYPE_SHEET_NAMES = {
    "barangay_performance": "Barangay Performance",
    "waste_trends": "Waste Classification",
    "collection_calendar": "Collection Calendar",
    "notification_analytics": "Notification Analytics",
    "schedule": "Collection Schedule",
    # Legacy
    "notification_effectiveness": "Notification Analytics",
    "waste": "Waste Records",
    "users": "Users",
    "route_efficiency": "Route Efficiency",
    "environmental_impact": "Environmental Impact",
    "comparative_performance": "Comparative Performance",
    "peak_analysis": "Peak Analysis",
}

LOW_CONFIDENCE_THRESHOLD = 50.0

# Excel styling constants
HEADER_FILL = PatternFill(start_color="1E40AF", end_color="1E40AF", fill_type="solid")
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
BODY_FONT = Font(size=10)
THIN = Side(style="thin", color="CBD5E1")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def resolve_period(year: int, month: int | None) -> tuple[datetime, datetime]:
    """Return (start, end) naive datetimes for the requested period."""
    if month:
        last_day = calendar.monthrange(year, month)[1]
        start = datetime(year, month, 1, 0, 0, 0)
        end = datetime(year, month, last_day, 23, 59, 59)
    else:
        start = datetime(year, 1, 1, 0, 0, 0)
        end = datetime(year, 12, 31, 23, 59, 59)
    return start, end


def period_label(year: int, month: int | None) -> str:
    if month:
        return datetime(year, month, 1).strftime("%B %Y")
    return str(year)


# ---------------------------------------------------------------------------
# Data gathering helpers (5 Core Reports + Legacy)
# ---------------------------------------------------------------------------

def _barangay_performance_rows(db: Session, start: datetime, end: datetime):
    """1. Barangay Performance: Routes, completion rate, waste count, AI accuracy."""
    barangay_stats = {}

    schedules = (
        db.query(CollectionSchedule)
        .filter(CollectionSchedule.collection_date >= start.date(), CollectionSchedule.collection_date <= end.date())
        .all()
    )

    for schedule in schedules:
        barangay = schedule.barangay or "Unknown"
        if barangay not in barangay_stats:
            barangay_stats[barangay] = {
                "total_routes": 0, "completed": 0, "delayed": 0, "cancelled": 0,
                "waste_count": 0, "confidence_sum": 0.0, "confidence_count": 0,
            }

        status = derive_schedule_status(schedule)
        barangay_stats[barangay]["total_routes"] += 1
        if status == "Completed":
            barangay_stats[barangay]["completed"] += 1
        elif status == "Delayed":
            barangay_stats[barangay]["delayed"] += 1
        elif status == "Cancelled":
            barangay_stats[barangay]["cancelled"] += 1

    # Get waste records joined with user barangay
    waste_records = (
        db.query(WasteRecord, User)
        .join(User, WasteRecord.user_id == User.id, isouter=True)
        .filter(WasteRecord.classified_at >= start, WasteRecord.classified_at <= end)
        .all()
    )

    for waste, user in waste_records:
        barangay = (user.barangay if user else None) or "Unknown"
        if barangay not in barangay_stats:
            barangay_stats[barangay] = {
                "total_routes": 0, "completed": 0, "delayed": 0, "cancelled": 0,
                "waste_count": 0, "confidence_sum": 0.0, "confidence_count": 0,
            }
        barangay_stats[barangay]["waste_count"] += 1
        if waste.confidence is not None:
            barangay_stats[barangay]["confidence_sum"] += float(waste.confidence)
            barangay_stats[barangay]["confidence_count"] += 1

    rows = []
    for barangay, stats in sorted(barangay_stats.items()):
        completion_rate = round(stats["completed"] / stats["total_routes"] * 100, 1) if stats["total_routes"] > 0 else 0.0
        avg_confidence = round(stats["confidence_sum"] / stats["confidence_count"], 1) if stats["confidence_count"] > 0 else 0.0

        rows.append([
            barangay,
            stats["total_routes"],
            stats["completed"],
            stats["delayed"],
            stats["cancelled"],
            f"{completion_rate}%",
            stats["waste_count"],
            f"{avg_confidence}%" if avg_confidence > 0 else "-",
        ])

    return rows


def _waste_classification_rows(db: Session, start: datetime, end: datetime):
    """2. Waste Classification Summary: Material types, counts, percentage, avg confidence, flags."""
    waste_records = (
        db.query(
            WasteRecord.waste_type,
            WasteRecord.disposal_category,
            func.count(WasteRecord.id).label('count'),
            func.avg(WasteRecord.confidence).label('avg_conf'),
            func.sum(WasteRecord.is_flagged).label('flagged_cnt'),
        )
        .filter(WasteRecord.classified_at >= start, WasteRecord.classified_at <= end)
        .group_by(WasteRecord.waste_type, WasteRecord.disposal_category)
        .all()
    )

    total_submissions = sum(r.count for r in waste_records)

    rows = []
    for r in sorted(waste_records, key=lambda x: x.count, reverse=True):
        pct = round(r.count / total_submissions * 100, 1) if total_submissions > 0 else 0.0
        avg_conf_str = f"{round(float(r.avg_conf), 1)}%" if r.avg_conf is not None else "-"
        flagged_count = int(r.flagged_cnt or 0)

        rows.append([
            r.waste_type or "-",
            r.disposal_category or "-",
            r.count,
            f"{pct}%",
            avg_conf_str,
            flagged_count,
        ])

    return rows


def _collection_calendar_rows(db: Session, start: datetime, end: datetime):
    """3. Collection Operations Calendar: Day-by-day operations and completion."""
    calendar_data = {}

    schedules = (
        db.query(CollectionSchedule)
        .filter(CollectionSchedule.collection_date >= start.date(), CollectionSchedule.collection_date <= end.date())
        .order_by(CollectionSchedule.collection_date.asc())
        .all()
    )

    for schedule in schedules:
        if not schedule.collection_date:
            continue

        date_key = schedule.collection_date.strftime("%Y-%m-%d")
        if date_key not in calendar_data:
            calendar_data[date_key] = {
                "date": schedule.collection_date.strftime("%Y-%m-%d"),
                "day_of_week": schedule.collection_date.strftime("%A"),
                "total_scheduled": 0, "completed": 0, "delayed": 0, "cancelled": 0,
                "barangays": set(),
            }

        calendar_data[date_key]["total_scheduled"] += 1
        if schedule.barangay:
            calendar_data[date_key]["barangays"].add(schedule.barangay)

        status = derive_schedule_status(schedule)
        if status == "Completed":
            calendar_data[date_key]["completed"] += 1
        elif status == "Delayed":
            calendar_data[date_key]["delayed"] += 1
        elif status == "Cancelled":
            calendar_data[date_key]["cancelled"] += 1

    rows = []
    for date_key in sorted(calendar_data.keys()):
        data = calendar_data[date_key]
        barangay_list = ", ".join(sorted(data["barangays"])) if data["barangays"] else "-"
        completion_rate = round(data["completed"] / data["total_scheduled"] * 100, 1) if data["total_scheduled"] > 0 else 0.0
        status_label = "Complete" if data["completed"] == data["total_scheduled"] and data["total_scheduled"] > 0 else "Incomplete"

        rows.append([
            data["date"],
            data["day_of_week"],
            data["total_scheduled"],
            data["completed"],
            data["delayed"],
            data["cancelled"],
            f"{completion_rate}%",
            barangay_list,
            status_label,
        ])

    return rows


def _notification_analytics_rows(db: Session, start: datetime, end: datetime):
    """4. Notification Analytics: Delivery and citizen read engagement per type."""
    notifications = (
        db.query(Notification)
        .filter(Notification.created_at >= start, Notification.created_at <= end)
        .all()
    )

    type_breakdown = {}
    for n in notifications:
        ntype = n.notification_type or "General Announcement"
        if ntype not in type_breakdown:
            type_breakdown[ntype] = {"sent": 0, "read": 0, "priorities": set()}
        type_breakdown[ntype]["sent"] += 1
        if n.priority:
            type_breakdown[ntype]["priorities"].add(n.priority)

    notification_ids = [n.id for n in notifications]
    read_by_notif = {}
    if notification_ids:
        read_records = (
            db.query(NotificationRead.notification_id, func.count(NotificationRead.id).label('cnt'))
            .filter(NotificationRead.notification_id.in_(notification_ids))
            .group_by(NotificationRead.notification_id)
            .all()
        )
        for rec in read_records:
            read_by_notif[rec.notification_id] = rec.cnt

    for n in notifications:
        ntype = n.notification_type or "General Announcement"
        if n.id in read_by_notif:
            type_breakdown[ntype]["read"] += read_by_notif[n.id]

    rows = []
    for ntype, counts in sorted(type_breakdown.items()):
        read_rate = round(counts["read"] / max(counts["sent"], 1) * 100, 1) if counts["sent"] > 0 else 0.0
        channels = ", ".join(sorted(counts["priorities"])) if counts["priorities"] else "Normal"

        rows.append([
            ntype,
            counts["sent"],
            counts["read"],
            f"{read_rate}%",
            channels,
        ])

    return rows


def _schedule_rows(db: Session, start: datetime, end: datetime):
    """5. Collection Schedule Records: Full operational schedule log."""
    rows = (
        db.query(CollectionSchedule)
        .filter(CollectionSchedule.collection_date >= start.date(), CollectionSchedule.collection_date <= end.date())
        .order_by(CollectionSchedule.collection_date.asc(), CollectionSchedule.collection_time.asc())
        .all()
    )
    result = []
    for r in rows:
        bgy = r.barangay or "-"
        zn = r.zone or ""
        starting = r.starting_point or zn or (f"{bgy} Center" if bgy != "-" else "-")

        if r.route_name:
            rt_name = r.route_name
        elif starting and starting != "-" and starting.lower() != bgy.lower() and starting.lower() != f"{bgy.lower()} center":
            rt_name = f"{bgy} - {starting} Route"
        elif bgy != "-":
            rt_name = f"{bgy} Route"
        else:
            rt_name = "-"

        status_val = derive_schedule_status(r)
        if r.remarks:
            rem = r.remarks
        elif status_val == "Completed":
            rem = "Collection Completed Successfully"
        elif status_val == "Delayed":
            rem = "Collection Delayed"
        elif status_val == "Cancelled":
            rem = "Collection Cancelled"
        else:
            rem = "Regular Scheduled Collection"

        result.append([
            r.collection_date.strftime("%Y-%m-%d") if r.collection_date else "-",
            r.collection_time.strftime("%I:%M %p") if r.collection_time else "-",
            bgy,
            zn or "-",
            rt_name,
            starting,
            r.assigned_personnel or "-",
            status_val,
            rem,
        ])
    return result


def _waste_rows(db: Session, start: datetime, end: datetime):
    """Legacy: Individual waste records."""
    rows = (
        db.query(
            WasteRecord.classified_at,
            User.fullname,
            User.barangay,
            User.zone,
            WasteRecord.waste_type,
            WasteRecord.disposal_category,
            WasteRecord.confidence,
            WasteRecord.is_flagged,
        )
        .join(User, WasteRecord.user_id == User.id, isouter=True)
        .filter(WasteRecord.classified_at >= start, WasteRecord.classified_at <= end)
        .order_by(WasteRecord.classified_at.asc())
        .all()
    )
    return [
        [
            r.classified_at.strftime("%Y-%m-%d %H:%M") if r.classified_at else "-",
            r.fullname or "Anonymous",
            f"{r.barangay or ''}{', ' + r.zone if r.zone else ''}".strip(" ,") or "-",
            r.waste_type or "-",
            r.disposal_category or "-",
            f"{float(r.confidence):.1f}%" if r.confidence is not None else "-",
            "Flagged" if r.is_flagged else "Verified",
        ]
        for r in rows
    ]


def _user_rows(db: Session, start: datetime, end: datetime):
    """Legacy: Users list."""
    rows = (
        db.query(User)
        .filter(User.created_at >= start, User.created_at <= end)
        .order_by(User.created_at.asc())
        .all()
    )
    return [
        [
            r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "-",
            r.fullname or "-",
            r.email or "-",
            r.phone or "-",
            (r.role or "citizen").capitalize(),
            r.barangay or "-",
            r.zone or "-",
            "Active" if r.is_active else "Inactive",
        ]
        for r in rows
    ]


def _data_for(db: Session, start: datetime, end: datetime, types: list[str]):
    """Gather report data for all selected types."""
    data = {}
    for t in types:
        if t == "barangay_performance":
            data[t] = _barangay_performance_rows(db, start, end)
        elif t in ("waste_trends", "environmental_impact"):
            data[t] = _waste_classification_rows(db, start, end)
        elif t == "collection_calendar":
            data[t] = _collection_calendar_rows(db, start, end)
        elif t in ("notification_analytics", "notification_effectiveness"):
            data[t] = _notification_analytics_rows(db, start, end)
        elif t == "schedule":
            data[t] = _schedule_rows(db, start, end)
        elif t == "waste":
            data[t] = _waste_rows(db, start, end)
        elif t == "users":
            data[t] = _user_rows(db, start, end)
        elif t == "route_efficiency":
            data[t] = _barangay_performance_rows(db, start, end)
        elif t in ("comparative_performance", "peak_analysis"):
            data[t] = _collection_calendar_rows(db, start, end)
    return data


# ---------------------------------------------------------------------------
# Aggregate stats
# ---------------------------------------------------------------------------

def waste_stats(db: Session, start: datetime, end: datetime) -> dict:
    """Summary analytics for waste records in the period."""
    rows = (
        db.query(
            WasteRecord.waste_type,
            func.count(WasteRecord.id).label("cnt"),
            func.avg(WasteRecord.confidence).label("avg_conf"),
        )
        .filter(WasteRecord.classified_at >= start, WasteRecord.classified_at <= end)
        .group_by(WasteRecord.waste_type)
        .all()
    )

    total = sum(int(r.cnt) for r in rows)
    total_records = (
        db.query(func.count(WasteRecord.id))
        .filter(WasteRecord.classified_at >= start, WasteRecord.classified_at <= end)
        .scalar() or 0
    )
    avg_conf = None
    conf_counts = []
    for r in rows:
        if r.avg_conf is not None:
            conf_counts.append(float(r.avg_conf) * int(r.cnt))
    if conf_counts and total > 0:
        avg_conf = round(sum(conf_counts) / total, 1)

    low_conf = (
        db.query(func.count(WasteRecord.id))
        .filter(
            WasteRecord.classified_at >= start,
            WasteRecord.classified_at <= end,
            WasteRecord.confidence.isnot(None),
            WasteRecord.confidence < LOW_CONFIDENCE_THRESHOLD,
        )
        .scalar() or 0
    )

    per_class = {}
    most_common_type = None
    most_common_count = 0
    for r in rows:
        per_class[r.waste_type] = int(r.cnt)
        if int(r.cnt) > most_common_count:
            most_common_count = int(r.cnt)
            most_common_type = r.waste_type

    return {
        "total_records": total_records,
        "total_classified": total,
        "avg_confidence": avg_conf,
        "low_confidence": low_conf,
        "per_class": per_class,
        "most_common_type": most_common_type,
        "most_common_count": most_common_count,
    }


def schedule_stats(db: Session, start: datetime, end: datetime) -> dict:
    """Route completion analytics for the period."""
    rows = (
        db.query(CollectionSchedule)
        .filter(CollectionSchedule.collection_date >= start.date(), CollectionSchedule.collection_date <= end.date())
        .all()
    )

    counts = {}
    for r in rows:
        status = derive_schedule_status(r)
        counts[status] = counts.get(status, 0) + 1

    total = sum(counts.values())
    completed = counts.get("Completed", 0)
    cancelled = counts.get("Cancelled", 0)
    delayed = counts.get("Delayed", 0)
    upcoming = counts.get("Upcoming", 0)
    completion_rate = round(completed / total * 100, 1) if total else 0.0

    return {
        "total_routes": total,
        "completed": completed,
        "cancelled": cancelled,
        "delayed": delayed,
        "upcoming": upcoming,
        "completion_rate": completion_rate,
        "status_breakdown": counts,
    }


# ---------------------------------------------------------------------------
# Excel (.xlsx) Headers
# ---------------------------------------------------------------------------

EXCEL_HEADERS = {
    "barangay_performance": ["Barangay", "Total Routes", "Completed", "Delayed", "Cancelled", "Completion Rate", "Waste Records", "Avg Confidence"],
    "waste_trends": ["Waste Type", "Disposal Category", "Total Submissions", "% of Total", "Avg AI Confidence", "Flagged Items"],
    "collection_calendar": ["Date", "Day of Week", "Total Scheduled", "Completed", "Delayed", "Cancelled", "Completion Rate", "Barangays Covered", "Status"],
    "notification_analytics": ["Notification Type", "Total Broadcasts", "Citizen Reads", "Read Engagement %", "Active Channels"],
    "schedule": ["Date", "Time", "Barangay", "Zone", "Route Name", "Starting Point", "Assigned Personnel", "Status", "Remarks"],
    # Legacy
    "notification_effectiveness": ["Notification Type", "Total Broadcasts", "Citizen Reads", "Read Engagement %", "Active Channels"],
    "waste": ["Date & Time", "Citizen", "Barangay / Zone", "Waste Type", "Classification", "Confidence", "Status"],
    "users": ["Registered On", "Full Name", "Email", "Phone", "Role", "Barangay", "Zone", "Status"],
    "route_efficiency": ["Barangay", "Total Routes", "Completed", "Delayed", "Cancelled", "Completion Rate", "Waste Records", "Avg Confidence"],
    "environmental_impact": ["Waste Type", "Disposal Category", "Total Submissions", "% of Total", "Avg AI Confidence", "Flagged Items"],
    "comparative_performance": ["Date", "Day of Week", "Total Scheduled", "Completed", "Delayed", "Cancelled", "Completion Rate", "Barangays Covered", "Status"],
    "peak_analysis": ["Date", "Day of Week", "Total Scheduled", "Completed", "Delayed", "Cancelled", "Completion Rate", "Barangays Covered", "Status"],
}


def build_excel(
    db: Session,
    year: int,
    month: int | None,
    types: list[str],
    generated_at: datetime | None = None,
) -> bytes:
    """Generate a single .xlsx workbook with a Summary sheet + one sheet per type."""
    generated_at = generated_at or ph_now()
    start, end = resolve_period(year, month)
    label = period_label(year, month)
    data = _data_for(db, start, end, types)
    waste = waste_stats(db, start, end)
    schedule = schedule_stats(db, start, end)

    wb = Workbook()

    # ---- Summary sheet -------------------------------------------------
    ws = wb.active
    ws.title = "Summary"
    ws.sheet_view.showGridLines = True

    title_cell = ws.cell(row=1, column=1, value="WISE SYSTEM — Executive Report")
    title_cell.font = Font(bold=True, size=16, color="1E293B")
    period_cell = ws.cell(row=2, column=1, value=f"Reporting Period: {label}")
    period_cell.font = Font(size=12, color="2563EB", bold=True)

    current_row = 4
    sections = []

    # Overall Collection Performance
    perf_rows = [
        ("Total Routes Scheduled", schedule["total_routes"]),
        ("Routes Completed", schedule["completed"]),
        ("Routes Delayed", schedule["delayed"]),
        ("Routes Cancelled", schedule["cancelled"]),
        ("Overall Completion Rate", f"{schedule['completion_rate']}%"),
    ]
    sections.append(("Collection Performance", perf_rows))

    # Waste Classification Summary
    w_rows = [
        ("Total Classifications Recorded", waste["total_records"]),
        ("Average AI Accuracy", f"{waste['avg_confidence']}%" if waste["avg_confidence"] is not None else "-"),
        ("Low Accuracy Count (<50%)", waste["low_confidence"]),
        ("Most Common Waste Type", waste["most_common_type"] or "-"),
    ]
    if waste["per_class"]:
        w_rows.append(("Waste Types Breakdown:", ""))
        for cls, cnt in sorted(waste["per_class"].items()):
            w_rows.append((f"  • {cls}", cnt))
    sections.append(("Waste Classification Summary", w_rows))

    # Barangay Performance Summary
    barangay_data = data.get("barangay_performance", [])
    if not barangay_data and "barangay_performance" not in types:
        barangay_data = _barangay_performance_rows(db, start, end)

    top_barangay = "-"
    best_rate = -1
    for row_item in barangay_data:
        try:
            rate_val = float(str(row_item[5]).replace("%", ""))
            if rate_val > best_rate and row_item[1] > 0:
                best_rate = rate_val
                top_barangay = row_item[0]
        except (ValueError, IndexError):
            pass

    total_barangays = len(barangay_data)
    efficiencies = []
    for row_item in barangay_data:
        try:
            eff_str = str(row_item[5]).replace("%", "")
            if eff_str and eff_str != "-":
                efficiencies.append(float(eff_str))
        except (ValueError, IndexError):
            pass
    avg_efficiency = round(sum(efficiencies) / len(efficiencies), 1) if efficiencies else 0.0

    b_rows = [
        ("Top Performing Barangay", top_barangay),
        ("Total Active Barangays", total_barangays),
        ("Average Barangay Efficiency", f"{avg_efficiency}%"),
    ]
    sections.append(("Barangay Overview", b_rows))

    for section_name, summary_rows in sections:
        sec_cell = ws.cell(row=current_row, column=1, value=section_name)
        sec_cell.font = Font(bold=True, size=11, color="1E40AF")
        current_row += 1
        for key, val in summary_rows:
            c1 = ws.cell(row=current_row, column=1, value=key)
            c2 = ws.cell(row=current_row, column=2, value=val)
            c1.font = BODY_FONT
            c2.font = BODY_FONT
            c1.border = BORDER
            c2.border = BORDER
            current_row += 1
        current_row += 1

    gen_row = current_row + 1
    ws.cell(row=gen_row, column=1, value=f"Generated: {generated_at.strftime('%Y-%m-%d %H:%M')} (Philippine Standard Time)").font = Font(
        size=9, color="94A3B8", italic=True
    )

    ws.column_dimensions["A"].width = 36
    ws.column_dimensions["B"].width = 18

    # ---- Per-type sheets ------------------------------------------------
    for t in types:
        if t not in EXCEL_HEADERS:
            continue
        headers = EXCEL_HEADERS[t]
        type_rows = data.get(t, [])
        sheet_name = TYPE_SHEET_NAMES.get(t, t[:31])
        ws = wb.create_sheet(sheet_name)
        ws.sheet_view.showGridLines = True

        for c, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=c, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.border = BORDER
            cell.alignment = Alignment(vertical="center", horizontal="center")

        if not type_rows:
            cell = ws.cell(row=2, column=1, value="No records found for the selected period.")
            cell.font = Font(size=10, color="94A3B8", italic=True)
            ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))
        else:
            for r_idx, row_item in enumerate(type_rows, start=2):
                for c_idx, val in enumerate(row_item, start=1):
                    if c_idx > len(headers):
                        break
                    cell = ws.cell(row=r_idx, column=c_idx, value=val)
                    cell.font = BODY_FONT
                    cell.border = BORDER
                    cell.alignment = Alignment(vertical="center")

        widths = [22, 22, 18, 16, 16, 14, 14, 18, 20]
        for c in range(1, len(headers) + 1):
            ws.column_dimensions[get_column_letter(c)].width = widths[c - 1] if c - 1 < len(widths) else 16

        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(len(type_rows) + 1, 2)}"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PDF (Landscape A4 with NumberedCanvas for 'Page X of Y')
# ---------------------------------------------------------------------------

_NAVY = colors.HexColor("#0F1B2D")
_BLUE = colors.HexColor("#2563EB")
_MUTED = colors.HexColor("#64748B")
_BORDER_COLOR = colors.HexColor("#CBD5E1")
_HEADER_BG = colors.HexColor("#1E40AF")
_ALT_ROW = colors.HexColor("#F8FAFC")
_WHITE = colors.white
_LOGO_PATH = os.path.join(os.path.dirname(__file__), "..", "assets", "wise-logo.png")

_PAGE = landscape(A4)
_PAGE_W = _PAGE[0]
_PAGE_H = _PAGE[1]
_LEFT_M = 12 * mm
_RIGHT_M = 12 * mm
_TOP_M = 32 * mm
_BOT_M = 16 * mm
_CONTENT_W = _PAGE_W - _LEFT_M - _RIGHT_M

_TITLE_STYLE = ParagraphStyle(
    "PDFTitle", fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=_NAVY,
    alignment=TA_CENTER, spaceAfter=2,
)
_PERIOD_STYLE = ParagraphStyle(
    "PDFPeriod", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=_BLUE,
    alignment=TA_CENTER, spaceAfter=8,
)
_SECTION_STYLE = ParagraphStyle(
    "PDFSection", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=_NAVY,
    spaceBefore=6, spaceAfter=4,
)
_CARD_VALUE_STYLE = ParagraphStyle(
    "PDFCardValue", fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=_NAVY,
    alignment=TA_CENTER,
)
_CARD_LABEL_STYLE = ParagraphStyle(
    "PDFCardLabel", fontName="Helvetica", fontSize=7, leading=9, textColor=_MUTED,
    alignment=TA_CENTER,
)
_TH_STYLE = ParagraphStyle(
    "PDFTH", fontName="Helvetica-Bold", fontSize=7.5, leading=9.5, textColor=_WHITE,
    alignment=TA_CENTER,
)
_TD_STYLE = ParagraphStyle(
    "PDFTD", fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=_NAVY,
)
_EMPTY_STYLE = ParagraphStyle(
    "PDFEmpty", fontName="Helvetica-Oblique", fontSize=8.5, leading=11, textColor=_MUTED,
    alignment=TA_CENTER, spaceBefore=4, spaceAfter=4,
)

PDF_HEADERS = {
    "barangay_performance": ["Barangay", "Total Routes", "Completed", "Delayed", "Cancelled", "Completion %", "Waste Records", "Avg Confidence"],
    "waste_trends": ["Waste Type", "Disposal Category", "Total Submissions", "% of Total", "Avg AI Confidence", "Flagged Items"],
    "collection_calendar": ["Date", "Day", "Scheduled", "Completed", "Delayed", "Cancelled", "Completion %", "Barangays Covered", "Status"],
    "notification_analytics": ["Notification Type", "Total Broadcasts", "Citizen Reads", "Read Engagement %", "Active Channels"],
    "schedule": ["Date", "Time", "Barangay", "Zone", "Route Name", "Starting Point", "Personnel", "Status", "Remarks"],
    # Legacy
    "notification_effectiveness": ["Notification Type", "Total Broadcasts", "Citizen Reads", "Read Engagement %", "Active Channels"],
    "waste": ["Date & Time", "Citizen", "Barangay / Zone", "Waste Type", "Category", "Confidence", "Status"],
    "users": ["Registered On", "Full Name", "Email", "Phone", "Role", "Barangay", "Zone", "Status"],
    "route_efficiency": ["Barangay", "Total Routes", "Completed", "Delayed", "Cancelled", "Completion %", "Waste Records", "Avg Confidence"],
    "environmental_impact": ["Waste Type", "Disposal Category", "Total Submissions", "% of Total", "Avg AI Confidence", "Flagged Items"],
    "comparative_performance": ["Date", "Day", "Scheduled", "Completed", "Delayed", "Cancelled", "Completion %", "Barangays Covered", "Status"],
    "peak_analysis": ["Date", "Day", "Scheduled", "Completed", "Delayed", "Cancelled", "Completion %", "Barangays Covered", "Status"],
}


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to dynamically compute and draw total page count ('Page X of Y')."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []
        self.report_label = ""
        self.generated_at = None

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, total_pages: int):
        self.saveState()

        # ---- Header band ----
        band_h = 24 * mm
        self.setFillColor(_NAVY)
        self.rect(0, _PAGE_H - band_h, _PAGE_W, band_h, stroke=0, fill=1)

        # Accent line under band
        self.setFillColor(_BLUE)
        self.rect(0, _PAGE_H - band_h - 1.2 * mm, _PAGE_W, 1.2 * mm, stroke=0, fill=1)

        # Logo
        if os.path.exists(_LOGO_PATH):
            try:
                target_h = band_h - 6 * mm
                self.drawImage(
                    _LOGO_PATH, _LEFT_M, _PAGE_H - band_h + (band_h - target_h) / 2,
                    width=target_h, height=target_h,
                    mask="auto", preserveAspectRatio=True,
                )
            except Exception:
                pass

        # Header titles
        self.setFillColor(_WHITE)
        self.setFont("Helvetica-Bold", 14)
        self.drawRightString(_PAGE_W - _RIGHT_M, _PAGE_H - 9 * mm, "CITY OF MUNTINLUPA")
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#93C5FD"))
        self.drawRightString(_PAGE_W - _RIGHT_M, _PAGE_H - 15 * mm, "Waste Information System for the Environment (WISE)")

        # ---- Footer ----
        self.setFont("Helvetica", 7)
        self.setFillColor(colors.HexColor("#64748B"))

        # Footer divider line
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(_LEFT_M, 11 * mm, _PAGE_W - _RIGHT_M, 11 * mm)

        # Left: report identity
        self.drawString(_LEFT_M, 7 * mm, f"WISE SYSTEM — {self.report_label} Report")

        # Center: timestamp
        gen_str = self.generated_at.strftime("%B %d, %Y at %I:%M %p") if self.generated_at else ""
        self.drawCentredString(_PAGE_W / 2, 7 * mm, f"Official Report · Generated {gen_str} (PST)")

        # Right: Page X of Y
        self.drawRightString(_PAGE_W - _RIGHT_M, 7 * mm, f"Page {self._pageNumber} of {total_pages}")

        self.restoreState()


def _stat_card(value: str, label: str, bg_hex: str) -> Table:
    bg = colors.HexColor(bg_hex)
    card = Table(
        [[Paragraph(str(value), _CARD_VALUE_STYLE)], [Paragraph(label, _CARD_LABEL_STYLE)]],
        colWidths=[(_CONTENT_W / 5) - 3 * mm],
        rowHeights=[7.5 * mm, 4.5 * mm],
    )
    card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("ROUNDEDCORNERS", [2.5, 2.5, 2.5, 2.5]),
    ]))
    return card


def _build_pdf_table(headers: list[str], rows: list[list], col_widths: list[float] | None = None) -> Table:
    header_cells = [Paragraph(h, _TH_STYLE) for h in headers]
    data_rows = [header_cells]

    if not rows:
        empty_cell = Paragraph("No records found for the selected period.", _EMPTY_STYLE)
        empty_row = [empty_cell] + [""] * (len(headers) - 1)
        data_rows.append(empty_row)
    else:
        for row in rows:
            cells = []
            for i, val in enumerate(row):
                if i >= len(headers):
                    break
                cells.append(Paragraph(str(val) if val is not None else "-", _TD_STYLE))
            while len(cells) < len(headers):
                cells.append(Paragraph("-", _TD_STYLE))
            data_rows.append(cells)

    if col_widths is None:
        num_cols = len(headers)
        col_widths = [_CONTENT_W / num_cols] * num_cols

    table = Table(data_rows, colWidths=col_widths, repeatRows=1)

    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), _WHITE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, _BORDER_COLOR),
        ("LINEBELOW", (0, 0), (-1, 0), 1, _BLUE),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]

    if rows:
        for i in range(1, len(data_rows)):
            if i % 2 == 0:
                style_cmds.append(("BACKGROUND", (0, i), (-1, i), _ALT_ROW))
    else:
        style_cmds.append(("SPAN", (0, 1), (-1, 1)))
        style_cmds.append(("ALIGN", (0, 1), (-1, 1), "CENTER"))

    table.setStyle(TableStyle(style_cmds))
    return table


def _get_col_widths(report_type: str) -> list[float]:
    widths = {
        "barangay_performance": [42, 22, 20, 20, 20, 24, 24, 25],
        "waste_trends": [45, 45, 30, 25, 30, 22],
        "collection_calendar": [24, 24, 22, 22, 20, 20, 22, 45, 20],
        "notification_analytics": [65, 30, 30, 35, 35],
        "schedule": [22, 20, 26, 18, 30, 28, 30, 20, 24],
        # Legacy
        "notification_effectiveness": [65, 30, 30, 35, 35],
        "waste": [32, 34, 38, 32, 28, 22, 18],
        "users": [30, 34, 40, 26, 20, 26, 20, 18],
        "route_efficiency": [42, 22, 20, 20, 20, 24, 24, 25],
        "environmental_impact": [45, 45, 30, 25, 30, 22],
        "comparative_performance": [24, 24, 22, 22, 20, 20, 22, 45, 20],
        "peak_analysis": [24, 24, 22, 22, 20, 20, 22, 45, 20],
    }

    mm_widths = widths.get(report_type, None)
    if mm_widths is None:
        return None

    total = sum(mm_widths)
    content_mm = _CONTENT_W / mm
    scale = content_mm / total
    return [w * scale * mm for w in mm_widths]


def build_pdf(
    db: Session,
    year: int,
    month: int | None,
    types: list[str],
    generated_at: datetime | None = None,
) -> bytes:
    """Generate a professional landscape A4 PDF report with NumberedCanvas."""
    generated_at = generated_at or ph_now()
    start, end = resolve_period(year, month)
    label = period_label(year, month)

    data = _data_for(db, start, end, types)
    waste = waste_stats(db, start, end)
    schedule = schedule_stats(db, start, end)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=_PAGE,
        leftMargin=_LEFT_M,
        rightMargin=_RIGHT_M,
        topMargin=_TOP_M,
        bottomMargin=_BOT_M,
        title=f"WISE Report — {label}",
        author="WISE System",
    )

    story = []

    # ---- Executive Title ----
    story.append(Paragraph("Waste Management & Collection Report", _TITLE_STYLE))
    story.append(Paragraph(f"Reporting Period: {label}", _PERIOD_STYLE))

    # ---- Summary Performance Cards ----
    story.append(Paragraph("Collection & Waste Analytics Summary", _SECTION_STYLE))
    avg_conf_str = f"{waste['avg_confidence']}%" if waste["avg_confidence"] is not None else "-"
    stat_cards = [
        _stat_card(str(schedule["total_routes"]), "Total Routes", "#DBEAFE"),
        _stat_card(str(schedule["completed"]), "Completed Routes", "#D1FAE5"),
        _stat_card(f"{schedule['completion_rate']}%", "Completion Rate", "#E0E7FF"),
        _stat_card(str(waste["total_records"]), "Waste Records", "#FEF3C7"),
        _stat_card(avg_conf_str, "Avg AI Confidence", "#EDE9FE"),
    ]
    card_table = Table([stat_cards], colWidths=[_CONTENT_W / 5] * 5, rowHeights=[13 * mm])
    card_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(card_table)
    story.append(Spacer(1, 8))

    # ---- Section Data Tables ----
    for idx, t in enumerate(types):
        if t not in PDF_HEADERS:
            continue

        headers = PDF_HEADERS[t]
        type_rows = data.get(t, [])
        type_label = TYPE_LABELS.get(t, t.replace("_", " ").title())
        col_widths = _get_col_widths(t)

        if idx > 0 or len(types) > 1:
            story.append(PageBreak())

        sec_title_style = ParagraphStyle(
            f"PDFSecTitle_{t}", fontName="Helvetica-Bold", fontSize=13, leading=16,
            textColor=_NAVY, spaceBefore=0, spaceAfter=2,
        )
        story.append(Paragraph(type_label, sec_title_style))

        count_text = f"{len(type_rows)} record{'s' if len(type_rows) != 1 else ''}" if type_rows else "0 records"
        sec_sub_style = ParagraphStyle(
            f"PDFSecSub_{t}", fontName="Helvetica", fontSize=8, leading=10,
            textColor=_MUTED, spaceAfter=6,
        )
        story.append(Paragraph(f"{count_text} · Period: {label}", sec_sub_style))

        table = _build_pdf_table(headers, type_rows, col_widths)
        story.append(table)

    def canvas_maker(*args, **kwargs):
        c = NumberedCanvas(*args, **kwargs)
        c.report_label = label
        c.generated_at = generated_at
        return c

    doc.build(story, canvasmaker=canvas_maker)
    return buf.getvalue()

"""Report generation service (Excel + PDF) for the WISE System.

Official 5 Core Administrative Reports:
  1. barangay_performance: Per-barangay route counts, completion rates, waste scans & AI accuracy.
  2. waste_trends: Waste classification summary by type, disposal category, AI confidence & flags.
  3. collection_calendar: Day-by-day operational logs of scheduled, completed, and active collection routes.
  4. notification_analytics: Broadcast delivery numbers, citizen read counts, and engagement rates.
  5. schedule: Full operational collection schedule records with routes, zones, times, and personnel.

Legacy aliases supported for backward compatibility:
  - notification_effectiveness, waste, users, route_efficiency, environmental_impact,
    comparative_performance, peak_analysis.
"""
import calendar
import io
import math
import os
from datetime import date, datetime, time, timedelta
from xml.sax.saxutils import escape as _xml_escape

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
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from apps.config import AI_LOW_CONFIDENCE_THRESHOLD
from apps.models.collection_schedule import CollectionSchedule
from apps.models.notification import Notification
from apps.models.notification_read import NotificationRead
from apps.models.user import User
from apps.models.waste_record import WasteRecord
from apps.services.collection_schedule import derive_schedule_status
from apps.utils.ph_time import ph_now, ph_today

# ---------------------------------------------------------------------------
# Constants & Taxonomy
# ---------------------------------------------------------------------------

OFFICIAL_MUNTINLUPA_BARANGAYS = [
    "Alabang",
    "Bayanan",
    "Buli",
    "Cupang",
    "New Alabang Village",
    "Poblacion",
    "Putatan",
    "Sucat",
    "Tunasan",
]

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
    # Legacy aliases
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
    # Legacy aliases
    "notification_effectiveness": "Notification Analytics",
    "waste": "Waste Records",
    "users": "Users",
    "route_efficiency": "Route Efficiency",
    "environmental_impact": "Environmental Impact",
    "comparative_performance": "Comparative Performance",
    "peak_analysis": "Peak Analysis",
}

LOW_CONFIDENCE_THRESHOLD = float(AI_LOW_CONFIDENCE_THRESHOLD)

FLAGGED_AUDIT_HEADERS = [
    "Date & Time (PST)",
    "Citizen / Barangay",
    "Predicted Type",
    "Confidence",
    "Flag Reason",
    "Review Status",
]

FLAGGED_EXPLANATION_TEXT = (
    f"What 'Flagged' Means: A waste classification record is marked as Flagged (is_flagged = 1) when either: "
    f"(1) Auto-Flagged Low Confidence (< {LOW_CONFIDENCE_THRESHOLD:.0f}%) — the AI prediction confidence fell below "
    f"the {LOW_CONFIDENCE_THRESHOLD:.1f}% reliability threshold due to blurry, dark, or ambiguous photos; or "
    "(2) Manual Admin Flag — an administrator marked the scan on the Waste Classification Monitoring page for visual "
    "review or dataset retraining. Verified / Normal records met or exceeded the confidence threshold without issues."
)

# ---------------------------------------------------------------------------
# Period & Timezone Helpers
# ---------------------------------------------------------------------------

def resolve_period(year: int, month: int | None) -> tuple[datetime, datetime]:
    """Return (start, end) naive datetimes representing the period in Manila wall-clock time."""
    if month:
        last_day = calendar.monthrange(year, month)[1]
        start = datetime(year, month, 1, 0, 0, 0)
        end = datetime(year, month, last_day, 23, 59, 59)
    else:
        start = datetime(year, 1, 1, 0, 0, 0)
        end = datetime(year, 12, 31, 23, 59, 59)
    return start, end


def ph_to_utc_range(start_ph: datetime, end_ph: datetime) -> tuple[datetime, datetime]:
    """Convert Manila naive bounds (UTC+8) to UTC naive bounds for querying UTC columns.

    In MySQL/TiDB, columns with server_default=func.now() (classified_at, created_at,
    read_at) are stored in UTC. Shifting the period bounds by -8 hours ensures records
    logged during the Manila calendar period are captured accurately.
    """
    return start_ph - timedelta(hours=8), end_ph - timedelta(hours=8)


def period_label(year: int, month: int | None) -> str:
    """Human-readable period title, e.g. 'November 2026' or '2026'."""
    if month:
        return datetime(year, month, 1).strftime("%B %Y")
    return str(year)


def normalize_barangay(raw: str | None) -> str | None:
    """Normalize a barangay name to its official Muntinlupa designation."""
    if not raw:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    lower = cleaned.lower()
    if "ayala alabang" in lower or "new alabang" in lower:
        return "New Alabang Village"
    for official in OFFICIAL_MUNTINLUPA_BARANGAYS:
        if official.lower() == lower:
            return official
    for official in OFFICIAL_MUNTINLUPA_BARANGAYS:
        if official.lower() in lower or lower in official.lower():
            return official
    return cleaned


def _get_schedule_covered_barangays(schedule: CollectionSchedule) -> list[str]:
    """Resolve all distinct official or recognized barangays serviced by a route."""
    covered = []
    if hasattr(schedule, "stops") and schedule.stops:
        for stop in schedule.stops:
            nb = normalize_barangay(stop.barangay)
            if nb and nb not in covered:
                covered.append(nb)
    if not covered and schedule.barangay:
        nb = normalize_barangay(schedule.barangay)
        if nb:
            covered.append(nb)
    return covered or [schedule.barangay or "Unknown"]

# ---------------------------------------------------------------------------
# Authoritative Shared Summary Data
# ---------------------------------------------------------------------------

def get_report_summary_data(db: Session, year: int, month: int | None) -> dict:
    """Authoritative summary KPI calculations for the selected period.

    Used identically by:
      - GET /api/reports/summary (web dashboard chips)
      - build_excel() (Summary sheet)
      - build_pdf() (Page 1 Executive cards)
    """
    start_ph, end_ph = resolve_period(year, month)
    start_utc, end_utc = ph_to_utc_range(start_ph, end_ph)

    # 1. Collection Schedule KPIs
    schedules = (
        db.query(CollectionSchedule)
        .options(joinedload(CollectionSchedule.stops))
        .filter(
            CollectionSchedule.collection_date >= start_ph.date(),
            CollectionSchedule.collection_date <= end_ph.date(),
        )
        .all()
    )

    total_collections = len(schedules)
    completed_collections = 0
    delayed_collections = 0
    cancelled_collections = 0
    upcoming_collections = 0

    barangay_route_stats: dict[str, dict] = {}
    for b in OFFICIAL_MUNTINLUPA_BARANGAYS:
        barangay_route_stats[b] = {"total": 0, "completed": 0, "delayed": 0, "cancelled": 0}

    for s in schedules:
        status = derive_schedule_status(s)
        if status == "Completed":
            completed_collections += 1
        elif status == "Delayed":
            delayed_collections += 1
        elif status == "Cancelled":
            cancelled_collections += 1
        else:
            upcoming_collections += 1

        covered = _get_schedule_covered_barangays(s)
        for b in covered:
            if b not in barangay_route_stats:
                barangay_route_stats[b] = {"total": 0, "completed": 0, "delayed": 0, "cancelled": 0}
            barangay_route_stats[b]["total"] += 1
            if status == "Completed":
                barangay_route_stats[b]["completed"] += 1
            elif status == "Delayed":
                barangay_route_stats[b]["delayed"] += 1
            elif status == "Cancelled":
                barangay_route_stats[b]["cancelled"] += 1

    completion_rate = (
        round(completed_collections / total_collections * 100, 1)
        if total_collections > 0
        else 0.0
    )

    # Active barangays and top barangay determination
    active_barangay_counts = [b for b, st in barangay_route_stats.items() if st["total"] > 0]
    total_active_barangays = len(active_barangay_counts)

    top_barangay = "—"
    best_score = (-1.0, -1)  # (completion_rate, completed_count)
    efficiency_list = []

    for b in active_barangay_counts:
        st = barangay_route_stats[b]
        eff = round(st["completed"] / st["total"] * 100, 1) if st["total"] > 0 else 0.0
        efficiency_list.append(eff)
        score = (eff, st["completed"])
        if score > best_score:
            best_score = score
            top_barangay = b

    avg_efficiency = (
        round(sum(efficiency_list) / len(efficiency_list), 1)
        if efficiency_list
        else 0.0
    )

    # 2. Waste Classification KPIs (queried via UTC bounds)
    waste_rows = (
        db.query(
            WasteRecord.waste_type,
            func.count(WasteRecord.id).label("cnt"),
            func.avg(WasteRecord.confidence).label("avg_conf"),
        )
        .filter(
            WasteRecord.classified_at >= start_utc,
            WasteRecord.classified_at <= end_utc,
        )
        .group_by(WasteRecord.waste_type)
        .all()
    )

    total_waste_records = sum(int(r.cnt) for r in waste_rows)
    per_class = {r.waste_type: int(r.cnt) for r in waste_rows}

    # Recyclable rate
    recyclable_count = 0
    for w_type, cnt in per_class.items():
        wt_lower = (w_type or "").lower()
        if "recycl" in wt_lower or wt_lower in ("plastic", "paper", "cardboard", "metal", "glass", "can", "bottle"):
            recyclable_count += cnt

    recycling_rate = (
        round(recyclable_count / total_waste_records * 100, 1)
        if total_waste_records > 0
        else 0.0
    )

    # Confidence calculation
    conf_weighted_sum = sum(float(r.avg_conf) * int(r.cnt) for r in waste_rows if r.avg_conf is not None)
    avg_confidence = (
        round(conf_weighted_sum / total_waste_records, 1)
        if total_waste_records > 0
        else None
    )

    low_confidence_count = (
        db.query(func.count(WasteRecord.id))
        .filter(
            WasteRecord.classified_at >= start_utc,
            WasteRecord.classified_at <= end_utc,
            WasteRecord.confidence.isnot(None),
            WasteRecord.confidence < LOW_CONFIDENCE_THRESHOLD,
        )
        .scalar()
        or 0
    )

    flagged_count = (
        db.query(func.count(WasteRecord.id))
        .filter(
            WasteRecord.classified_at >= start_utc,
            WasteRecord.classified_at <= end_utc,
            WasteRecord.is_flagged == 1,
        )
        .scalar()
        or 0
    )
    low_confidence_flagged_count = (
        db.query(func.count(WasteRecord.id))
        .filter(
            WasteRecord.classified_at >= start_utc,
            WasteRecord.classified_at <= end_utc,
            WasteRecord.is_flagged == 1,
            WasteRecord.confidence.isnot(None),
            WasteRecord.confidence < LOW_CONFIDENCE_THRESHOLD,
        )
        .scalar()
        or 0
    )
    manual_flagged_count = max(flagged_count - low_confidence_flagged_count, 0)
    verified_count = max(total_waste_records - flagged_count, 0)
    flagged_rate = (
        round(flagged_count / total_waste_records * 100, 1)
        if total_waste_records > 0
        else 0.0
    )

    most_common_type = None
    most_common_count = 0
    if per_class:
        top_pair = max(per_class.items(), key=lambda x: x[1])
        most_common_type, most_common_count = top_pair

    # 3. User registrations in period
    new_users = (
        db.query(func.count(User.id))
        .filter(User.created_at >= start_utc, User.created_at <= end_utc)
        .scalar()
        or 0
    )

    return {
        "period": period_label(year, month),
        "total_collections": total_collections,
        "completed_collections": completed_collections,
        "delayed_collections": delayed_collections,
        "cancelled_collections": cancelled_collections,
        "upcoming_collections": upcoming_collections,
        "completion_rate": completion_rate,
        "waste_classifications": total_waste_records,
        "verified_count": verified_count,
        "flagged_count": flagged_count,
        "flagged_rate": flagged_rate,
        "low_confidence_flagged_count": low_confidence_flagged_count,
        "manual_flagged_count": manual_flagged_count,
        "recycling_rate": recycling_rate,
        "recyclable_count": recyclable_count,
        "avg_confidence": avg_confidence,
        "low_confidence_count": low_confidence_count,
        "most_common_type": most_common_type,
        "most_common_count": most_common_count,
        "per_class": per_class,
        "top_barangay": top_barangay,
        "total_barangays": total_active_barangays,
        "efficiency_score": avg_efficiency,
        "new_users": new_users,
        "generated_at": ph_now().strftime("%Y-%m-%d %H:%M"),
    }

# ---------------------------------------------------------------------------
# Data Gathering Helpers for the 5 Core Reports
# ---------------------------------------------------------------------------

def _barangay_performance_rows(db: Session, start_ph: datetime, end_ph: datetime) -> list[list]:
    """1. Barangay Performance: Routes, completion rate, waste count, AI accuracy."""
    start_utc, end_utc = ph_to_utc_range(start_ph, end_ph)

    stats: dict[str, dict] = {}
    for b in OFFICIAL_MUNTINLUPA_BARANGAYS:
        stats[b] = {
            "total_routes": 0, "completed": 0, "delayed": 0, "cancelled": 0,
            "waste_count": 0, "conf_sum": 0.0, "conf_count": 0,
        }

    # Load schedules with stops
    schedules = (
        db.query(CollectionSchedule)
        .options(joinedload(CollectionSchedule.stops))
        .filter(
            CollectionSchedule.collection_date >= start_ph.date(),
            CollectionSchedule.collection_date <= end_ph.date(),
        )
        .all()
    )

    for schedule in schedules:
        status = derive_schedule_status(schedule)
        covered = _get_schedule_covered_barangays(schedule)
        for b in covered:
            if b not in stats:
                stats[b] = {
                    "total_routes": 0, "completed": 0, "delayed": 0, "cancelled": 0,
                    "waste_count": 0, "conf_sum": 0.0, "conf_count": 0,
                }
            stats[b]["total_routes"] += 1
            if status == "Completed":
                stats[b]["completed"] += 1
            elif status == "Delayed":
                stats[b]["delayed"] += 1
            elif status == "Cancelled":
                stats[b]["cancelled"] += 1

    # Waste records joined with user
    waste_records = (
        db.query(WasteRecord.confidence, User.barangay)
        .join(User, WasteRecord.user_id == User.id, isouter=True)
        .filter(
            WasteRecord.classified_at >= start_utc,
            WasteRecord.classified_at <= end_utc,
        )
        .all()
    )

    for conf, raw_bgy in waste_records:
        b = normalize_barangay(raw_bgy) or "Unassigned"
        if b not in stats:
            stats[b] = {
                "total_routes": 0, "completed": 0, "delayed": 0, "cancelled": 0,
                "waste_count": 0, "conf_sum": 0.0, "conf_count": 0,
            }
        stats[b]["waste_count"] += 1
        if conf is not None:
            stats[b]["conf_sum"] += float(conf)
            stats[b]["conf_count"] += 1

    # Filter: include official barangays, and non-official only if they have data
    ordered_bgys = list(OFFICIAL_MUNTINLUPA_BARANGAYS)
    extra_bgys = sorted(b for b in stats if b not in ordered_bgys and (stats[b]["total_routes"] > 0 or stats[b]["waste_count"] > 0))
    ordered_bgys.extend(extra_bgys)

    rows = []
    for b in ordered_bgys:
        s = stats.get(b, {
            "total_routes": 0, "completed": 0, "delayed": 0, "cancelled": 0,
            "waste_count": 0, "conf_sum": 0.0, "conf_count": 0,
        })
        comp_rate = (
            round(s["completed"] / s["total_routes"] * 100, 1)
            if s["total_routes"] > 0
            else 0.0
        )
        avg_conf = (
            round(s["conf_sum"] / s["conf_count"], 1)
            if s["conf_count"] > 0
            else None
        )

        rows.append([
            b,
            s["total_routes"],
            s["completed"],
            s["delayed"],
            s["cancelled"],
            f"{comp_rate:.1f}%",
            s["waste_count"],
            f"{avg_conf:.1f}%" if avg_conf is not None else "—",
        ])

    return rows


def _waste_classification_rows(db: Session, start_ph: datetime, end_ph: datetime) -> list[list]:
    """2. Waste Classification Summary: Material types, counts, percentage, avg confidence, flags."""
    start_utc, end_utc = ph_to_utc_range(start_ph, end_ph)

    waste_records = (
        db.query(
            WasteRecord.waste_type,
            WasteRecord.disposal_category,
            func.count(WasteRecord.id).label("count"),
            func.avg(WasteRecord.confidence).label("avg_conf"),
            func.sum(WasteRecord.is_flagged).label("flagged_cnt"),
        )
        .filter(
            WasteRecord.classified_at >= start_utc,
            WasteRecord.classified_at <= end_utc,
        )
        .group_by(WasteRecord.waste_type, WasteRecord.disposal_category)
        .all()
    )

    total_submissions = sum(int(r.count) for r in waste_records)

    rows = []
    for r in sorted(waste_records, key=lambda x: int(x.count), reverse=True):
        count_val = int(r.count)
        pct = round(count_val / total_submissions * 100, 1) if total_submissions > 0 else 0.0
        avg_conf_str = f"{round(float(r.avg_conf), 1):.1f}%" if r.avg_conf is not None else "—"
        flagged_count = int(r.flagged_cnt or 0)

        rows.append([
            r.waste_type or "—",
            r.disposal_category or "—",
            count_val,
            f"{pct:.1f}%",
            avg_conf_str,
            flagged_count,
        ])

    return rows


def _collection_calendar_rows(db: Session, start_ph: datetime, end_ph: datetime) -> list[list]:
    """3. Collection Operations Calendar: Day-by-day operations, coverage, and status."""
    today = ph_today()

    schedules = (
        db.query(CollectionSchedule)
        .options(joinedload(CollectionSchedule.stops))
        .filter(
            CollectionSchedule.collection_date >= start_ph.date(),
            CollectionSchedule.collection_date <= end_ph.date(),
        )
        .order_by(CollectionSchedule.collection_date.asc())
        .all()
    )

    calendar_data: dict[str, dict] = {}

    for schedule in schedules:
        if not schedule.collection_date:
            continue

        date_key = schedule.collection_date.strftime("%Y-%m-%d")
        if date_key not in calendar_data:
            calendar_data[date_key] = {
                "date": date_key,
                "date_obj": schedule.collection_date,
                "day_of_week": schedule.collection_date.strftime("%A"),
                "total_scheduled": 0, "completed": 0, "delayed": 0, "cancelled": 0,
                "barangays": set(),
            }

        calendar_data[date_key]["total_scheduled"] += 1
        for b in _get_schedule_covered_barangays(schedule):
            calendar_data[date_key]["barangays"].add(b)

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
        c_date = data["date_obj"]
        total = data["total_scheduled"]
        comp = data["completed"]
        del_cnt = data["delayed"]
        canc = data["cancelled"]

        comp_rate = round(comp / total * 100, 1) if total > 0 else 0.0
        barangay_list = ", ".join(sorted(data["barangays"])) if data["barangays"] else "—"

        # Honest operational status label
        if c_date > today:
            status_label = "Scheduled"
        elif c_date == today:
            status_label = "In Progress" if comp < total else "Completed"
        else:
            if comp == total and total > 0:
                status_label = "Completed"
            elif canc == total and total > 0:
                status_label = "Cancelled"
            elif del_cnt > 0:
                status_label = "Delayed"
            elif comp > 0:
                status_label = "Partially Completed"
            else:
                status_label = "Pending"

        rows.append([
            data["date"],
            data["day_of_week"],
            total,
            comp,
            del_cnt,
            canc,
            f"{comp_rate:.1f}%",
            barangay_list,
            status_label,
        ])

    return rows


def _notification_analytics_rows(db: Session, start_ph: datetime, end_ph: datetime) -> list[list]:
    """4. Notification Analytics: Total broadcasts, unique citizen reads, and engagement."""
    start_utc, end_utc = ph_to_utc_range(start_ph, end_ph)

    notifications = (
        db.query(Notification)
        .filter(
            Notification.created_at >= start_utc,
            Notification.created_at <= end_utc,
        )
        .all()
    )

    type_breakdown: dict[str, dict] = {}
    for n in notifications:
        ntype = n.notification_type or "General Announcement"
        if ntype not in type_breakdown:
            type_breakdown[ntype] = {
                "sent": 0,
                "read": 0,
                "broadcast_ids": set(),
                "read_broadcast_ids": set(),
                "priorities": set(),
            }
        type_breakdown[ntype]["sent"] += 1
        type_breakdown[ntype]["broadcast_ids"].add(n.id)
        if n.priority:
            type_breakdown[ntype]["priorities"].add(n.priority)

    notif_ids = [n.id for n in notifications]
    if notif_ids:
        read_records = (
            db.query(
                NotificationRead.notification_id,
                func.count(NotificationRead.id).label("cnt"),
            )
            .filter(NotificationRead.notification_id.in_(notif_ids))
            .group_by(NotificationRead.notification_id)
            .all()
        )
        for nid, cnt in read_records:
            for ntype, data in type_breakdown.items():
                if nid in data["broadcast_ids"]:
                    data["read"] += int(cnt)
                    data["read_broadcast_ids"].add(nid)

    rows = []
    for ntype, data in sorted(type_breakdown.items()):
        total_sent = data["sent"]
        read_count = data["read"]
        # Engagement rate: percentage of broadcasts that were read by citizens
        broadcasts_read = len(data["read_broadcast_ids"])
        engagement_rate = (
            round(broadcasts_read / total_sent * 100, 1)
            if total_sent > 0
            else 0.0
        )
        channels = ", ".join(sorted(data["priorities"])) if data["priorities"] else "Normal"

        rows.append([
            ntype,
            total_sent,
            read_count,
            broadcasts_read,
            f"{engagement_rate:.1f}%",
            channels,
        ])

    return rows


def _schedule_rows(db: Session, start_ph: datetime, end_ph: datetime) -> list[list]:
    """5. Collection Schedule Records: Full operational schedule log with all stops."""
    schedules = (
        db.query(CollectionSchedule)
        .options(joinedload(CollectionSchedule.stops))
        .filter(
            CollectionSchedule.collection_date >= start_ph.date(),
            CollectionSchedule.collection_date <= end_ph.date(),
        )
        .order_by(
            CollectionSchedule.collection_date.asc(),
            CollectionSchedule.collection_time.asc(),
        )
        .all()
    )

    collector_ids = {
        r.assigned_collector_id
        for r in schedules
        if getattr(r, "assigned_collector_id", None)
    }
    collector_name_by_id: dict[int, str] = {}
    if collector_ids:
        for u in db.query(User).filter(User.id.in_(collector_ids)).all():
            collector_name_by_id[u.id] = u.fullname or u.email or f"Collector #{u.id}"

    result = []
    for r in schedules:
        covered = _get_schedule_covered_barangays(r)
        bgy_display = ", ".join(covered) if covered else (r.barangay or "—")
        zn = r.zone or "—"
        starting = r.starting_point or zn

        assigned_name = (
            r.assigned_personnel
            or collector_name_by_id.get(getattr(r, "assigned_collector_id", None))
            or "—"
        )

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
            r.collection_date.strftime("%Y-%m-%d") if r.collection_date else "—",
            r.collection_time.strftime("%I:%M %p") if r.collection_time else "—",
            bgy_display,
            zn,
            r.route_name or f"{bgy_display} Route",
            starting or "—",
            assigned_name,
            status_val,
            rem,
        ])

    return result


def _collector_pdf_report_data(db: Session, start_ph: datetime, end_ph: datetime) -> dict:
    """Gather Collector Roster & Operational Status Summary and Assigned Route Verification Log for PDF export."""
    collectors = (
        db.query(User)
        .filter(func.lower(User.role) == "collector")
        .order_by(User.fullname.asc())
        .all()
    )

    schedules = (
        db.query(CollectionSchedule)
        .options(joinedload(CollectionSchedule.stops))
        .filter(
            CollectionSchedule.collection_date >= start_ph.date(),
            CollectionSchedule.collection_date <= end_ph.date(),
        )
        .order_by(
            CollectionSchedule.collection_date.asc(),
            CollectionSchedule.collection_time.asc(),
        )
        .all()
    )

    roster_stats: dict[str, dict] = {}
    id_to_key: dict[int, str] = {}
    name_to_key: dict[str, str] = {}

    for c in collectors:
        key = f"user_{c.id}"
        c_name = (c.fullname or c.email or f"Collector #{c.id}").strip()
        contact_parts = [p for p in (c.email, c.phone) if p]
        contact_str = " / ".join(contact_parts) if contact_parts else "—"
        id_to_key[c.id] = key
        name_to_key[c_name.lower()] = key
        roster_stats[key] = {
            "name": c_name,
            "contact": contact_str,
            "home_barangay": c.barangay or "Citywide",
            "is_active": bool(c.is_active),
            "barangays": set([c.barangay] if c.barangay else []),
            "total_routes": 0,
            "completed": 0,
            "in_progress": 0,
            "upcoming": 0,
            "delayed": 0,
            "cancelled": 0,
            "total_stops": 0,
            "confirmed_stops": 0,
        }

    route_log_rows: list[list] = []

    for s in schedules:
        cid = getattr(s, "assigned_collector_id", None)
        raw_personnel = (s.assigned_personnel or "").strip()

        matched_key = None
        if cid and cid in id_to_key:
            matched_key = id_to_key[cid]
        elif raw_personnel and raw_personnel.lower() in name_to_key:
            matched_key = name_to_key[raw_personnel.lower()]
        elif raw_personnel:
            matched_key = f"team_{raw_personnel.lower()}"
            if matched_key not in roster_stats:
                roster_stats[matched_key] = {
                    "name": raw_personnel,
                    "contact": "Dispatch Team / Crew",
                    "home_barangay": s.barangay or "Citywide",
                    "is_active": True,
                    "barangays": set(),
                    "total_routes": 0,
                    "completed": 0,
                    "in_progress": 0,
                    "upcoming": 0,
                    "delayed": 0,
                    "cancelled": 0,
                    "total_stops": 0,
                    "confirmed_stops": 0,
                }

        covered = _get_schedule_covered_barangays(s)
        bgy_display = ", ".join(covered) if covered else (s.barangay or "—")
        r_status = derive_schedule_status(s)

        stops = list(s.stops or [])
        active_stops = [st for st in stops if (st.status or "") != "Cancelled"]
        confirmed_cnt = sum(1 for st in active_stops if (st.status or "") in ("Arrived", "Completed"))
        delayed_stop_cnt = sum(1 for st in stops if (st.status or "") == "Delayed")
        cancelled_stop_cnt = sum(1 for st in stops if (st.status or "") == "Cancelled")

        if matched_key and matched_key in roster_stats:
            st_entry = roster_stats[matched_key]
            st_entry["total_routes"] += 1
            for b in covered:
                if b:
                    st_entry["barangays"].add(b)
            if r_status == "Completed":
                st_entry["completed"] += 1
            elif r_status == "In Progress":
                st_entry["in_progress"] += 1
            elif r_status == "Delayed":
                st_entry["delayed"] += 1
            elif r_status == "Cancelled":
                st_entry["cancelled"] += 1
            else:
                st_entry["upcoming"] += 1
            st_entry["total_stops"] += len(active_stops)
            st_entry["confirmed_stops"] += confirmed_cnt
            assigned_display = st_entry["name"]
        else:
            assigned_display = "Unassigned"

        if active_stops:
            stop_progress_str = f"{confirmed_cnt}/{len(active_stops)} Confirmed"
            if delayed_stop_cnt > 0:
                stop_progress_str += f" ({delayed_stop_cnt} Delayed)"
        elif stops and cancelled_stop_cnt == len(stops):
            stop_progress_str = f"0/{len(stops)} (Cancelled)"
        else:
            stop_progress_str = "Direct Route"

        # Build concise field verification remarks from stop reasons or route remarks
        stop_notes = []
        for st in stops:
            street = (getattr(st, "street", None) or "").strip()
            barangay = (getattr(st, "barangay", None) or "").strip()
            if street and barangay:
                stop_label = f"{street}, {barangay}"
            elif street or barangay:
                stop_label = street or barangay
            else:
                seq = getattr(st, "sequence", None)
                stop_label = f"Stop #{seq}" if seq is not None else "Stop"

            if getattr(st, "delay_reason", None):
                stop_notes.append(f"{stop_label}: Delayed ({st.delay_reason})")
            elif getattr(st, "cancellation_reason", None):
                stop_notes.append(f"{stop_label}: Cancelled ({st.cancellation_reason})")
        if stop_notes:
            field_remarks = "; ".join(stop_notes[:2])
        elif s.remarks:
            field_remarks = s.remarks
        elif r_status == "Completed":
            field_remarks = "All stops confirmed arrived — Route Completed"
        elif r_status == "In Progress":
            field_remarks = "Collector actively servicing stops"
        elif r_status == "Delayed":
            field_remarks = "Collection delayed on route"
        elif r_status == "Cancelled":
            field_remarks = "Collection route cancelled"
        else:
            field_remarks = "Scheduled — Awaiting collection day"

        dt_str = s.collection_date.strftime("%Y-%m-%d") if s.collection_date else "—"
        if s.collection_time:
            dt_str += f" {s.collection_time.strftime('%I:%M %p')}"

        route_log_rows.append([
            dt_str,
            assigned_display,
            s.route_name or f"{bgy_display} Route",
            bgy_display,
            stop_progress_str,
            r_status,
            field_remarks,
        ])

    roster_rows: list[list] = []
    for key, st in roster_stats.items():
        tot = st["total_routes"]
        comp = st["completed"]
        in_prog = st["in_progress"]
        upcom = st["upcoming"]
        dly = st["delayed"]
        canc = st["cancelled"]
        comp_rate = round(comp / tot * 100, 1) if tot > 0 else 0.0

        bgy_cov = ", ".join(sorted(st["barangays"])) if st["barangays"] else st["home_barangay"]
        stops_str = f"{st['confirmed_stops']}/{st['total_stops']} Stops" if st["total_stops"] > 0 else "0 Stops"

        if not st["is_active"]:
            col_status = "Inactive"
        elif tot == 0:
            col_status = "Standby (Unassigned)"
        elif in_prog > 0:
            col_status = "In Progress"
        elif dly > 0:
            col_status = "Delayed"
        elif comp == tot and tot > 0:
            col_status = "Completed"
        elif comp > 0 and upcom > 0:
            col_status = "In Progress"
        elif canc == tot and tot > 0:
            col_status = "Cancelled"
        else:
            col_status = "Scheduled"

        roster_rows.append([
            st["name"],
            st["contact"],
            bgy_cov,
            tot,
            comp,
            f"{dly} / {canc}",
            stops_str,
            f"{comp_rate:.1f}%",
            col_status,
        ])

    return {
        "roster_headers": [
            "Collector / Crew",
            "Contact / Account",
            "Barangay Coverage",
            "Assigned Routes",
            "Completed",
            "Delayed / Canc.",
            "Confirmed Stops",
            "Completion Rate",
            "Collector Status",
        ],
        "roster_rows": roster_rows,
        "route_log_headers": [
            "Date & Time",
            "Assigned Collector",
            "Route Name",
            "Barangays Serviced",
            "Stop Confirmation",
            "Route Status",
            "Field Verification & Remarks",
        ],
        "route_log_rows": route_log_rows,
    }


def _legacy_waste_rows(db: Session, start_ph: datetime, end_ph: datetime) -> list[list]:
    """Legacy alias: Individual raw waste records."""
    start_utc, end_utc = ph_to_utc_range(start_ph, end_ph)
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
        .filter(
            WasteRecord.classified_at >= start_utc,
            WasteRecord.classified_at <= end_utc,
        )
        .order_by(WasteRecord.classified_at.asc())
        .all()
    )
    return [
        [
            (r.classified_at + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") if r.classified_at else "—",
            r.fullname or "Anonymous",
            f"{r.barangay or ''}{', ' + r.zone if r.zone else ''}".strip(" ,") or "—",
            r.waste_type or "—",
            r.disposal_category or "—",
            f"{float(r.confidence):.1f}%" if r.confidence is not None else "—",
            "Flagged" if r.is_flagged else "Verified",
        ]
        for r in rows
    ]


def _legacy_user_rows(db: Session, start_ph: datetime, end_ph: datetime) -> list[list]:
    """Legacy alias: Users registered in the period."""
    start_utc, end_utc = ph_to_utc_range(start_ph, end_ph)
    rows = (
        db.query(User)
        .filter(User.created_at >= start_utc, User.created_at <= end_utc)
        .order_by(User.created_at.asc())
        .all()
    )
    return [
        [
            (r.created_at + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") if r.created_at else "—",
            r.fullname or "—",
            r.email or "—",
            r.phone or "—",
            (r.role or "citizen").capitalize(),
            r.barangay or "—",
            r.zone or "—",
            "Active" if r.is_active else "Inactive",
        ]
        for r in rows
    ]


def _data_for(db: Session, start_ph: datetime, end_ph: datetime, types: list[str]) -> dict[str, list[list]]:
    """Gather rows for all requested report types."""
    data = {}
    for t in types:
        if t == "barangay_performance":
            data[t] = _barangay_performance_rows(db, start_ph, end_ph)
        elif t in ("waste_trends", "environmental_impact"):
            data[t] = _waste_classification_rows(db, start_ph, end_ph)
        elif t in ("collection_calendar", "comparative_performance", "peak_analysis"):
            data[t] = _collection_calendar_rows(db, start_ph, end_ph)
        elif t in ("notification_analytics", "notification_effectiveness"):
            data[t] = _notification_analytics_rows(db, start_ph, end_ph)
        elif t in ("schedule", "route_efficiency"):
            data[t] = _schedule_rows(db, start_ph, end_ph)
        elif t == "waste":
            data[t] = _legacy_waste_rows(db, start_ph, end_ph)
        elif t == "users":
            data[t] = _legacy_user_rows(db, start_ph, end_ph)
    return data


def _flagged_waste_audit_rows(db: Session, start_ph: datetime, end_ph: datetime) -> list[list]:
    """Return flagged waste classification records for the period with explicit flag reasons."""
    start_utc, end_utc = ph_to_utc_range(start_ph, end_ph)
    records = (
        db.query(
            WasteRecord.classified_at,
            User.fullname,
            User.barangay,
            WasteRecord.waste_type,
            WasteRecord.confidence,
        )
        .join(User, WasteRecord.user_id == User.id, isouter=True)
        .filter(
            WasteRecord.classified_at >= start_utc,
            WasteRecord.classified_at <= end_utc,
            WasteRecord.is_flagged == 1,
        )
        .order_by(WasteRecord.classified_at.desc())
        .all()
    )

    audit_rows = []
    for r in records:
        dt_str = (r.classified_at + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") if r.classified_at else "—"
        citizen_name = (r.fullname or "Citizen Scanner").strip()
        brgy = normalize_barangay(r.barangay) or (r.barangay or "Unassigned")
        conf_val = float(r.confidence) if r.confidence is not None else None
        conf_str = f"{conf_val:.1f}%" if conf_val is not None else "—"
        if conf_val is not None and conf_val < LOW_CONFIDENCE_THRESHOLD:
            reason = f"Low AI Confidence (< {LOW_CONFIDENCE_THRESHOLD:.0f}%)"
        else:
            reason = "Manual Admin Flag"
        audit_rows.append([
            dt_str,
            f"{citizen_name} ({brgy})",
            r.waste_type or "—",
            conf_str,
            reason,
            "Flagged for Review",
        ])
    return audit_rows


def get_executive_narrative(summary: dict) -> str:
    """Build a concise English narrative brief summarizing all core KPIs for the period."""
    period = summary["period"]
    total_routes = summary["total_collections"]
    comp_routes = summary["completed_collections"]
    dly_routes = summary["delayed_collections"]
    canc_routes = summary["cancelled_collections"]
    comp_rate = summary["completion_rate"]
    scans = summary["waste_classifications"]
    verified = summary.get("verified_count", scans)
    flagged = summary.get("flagged_count", 0)
    flagged_rate = summary.get("flagged_rate", 0.0)
    recyc_rate = summary["recycling_rate"]
    avg_conf = f"{summary['avg_confidence']:.1f}%" if summary["avg_confidence"] is not None else "N/A"
    top_brgy = summary.get("top_barangay") or "—"

    if total_routes == 0 and scans == 0:
        return (
            f"No collection schedules or AI waste scans were recorded for {period}. "
            "Verify scheduled routes or citizen scanning activity to populate metrics."
        )

    route_sentence = (
        f"During {period}, {total_routes:,} collection route(s) were scheduled with a "
        f"{comp_rate:.1f}% completion rate ({comp_routes:,} completed, {dly_routes:,} delayed, "
        f"{canc_routes:,} cancelled)."
        if total_routes > 0
        else f"No collection routes were scheduled in {period}."
    )
    scan_sentence = (
        f" Citizens recorded {scans:,} AI waste classification(s) at {avg_conf} average confidence "
        f"and {recyc_rate:.1f}% recyclable share; {verified:,} passed verification while {flagged:,} "
        f"({flagged_rate:.1f}%) were flagged for review."
        if scans > 0
        else " No citizen AI waste scans were recorded in this period."
    )
    brgy_sentence = f" Top-performing barangay: {top_brgy}." if top_brgy != "—" else ""
    return f"{route_sentence}{scan_sentence}{brgy_sentence}"


def get_section_narratives(
    report_type: str,
    label: str,
    summary: dict,
    rows: list[list],
    collector_pdf: dict | None = None,
) -> dict[str, str]:
    """Return English 'about', 'insight', and optional 'legend' strings for each report section."""
    canonical = {
        "environmental_impact": "waste_trends",
        "notification_effectiveness": "notification_analytics",
        "route_efficiency": "schedule",
        "comparative_performance": "collection_calendar",
        "peak_analysis": "collection_calendar",
    }.get(report_type, report_type)

    if canonical == "barangay_performance":
        about = (
            "Evaluates municipal waste collection reliability and citizen AI scanning activity across Muntinlupa's "
            "barangays. Compares scheduled vs. completed, delayed, and cancelled routes alongside waste scan volume "
            "and average AI model confidence per barangay."
        )
        active = [r for r in rows if (r[1] or 0) > 0 or (r[6] or 0) > 0]
        tot_routes = sum(int(r[1] or 0) for r in rows)
        tot_comp = sum(int(r[2] or 0) for r in rows)
        tot_scans = sum(int(r[6] or 0) for r in rows)
        rate = round(tot_comp / tot_routes * 100, 1) if tot_routes > 0 else 0.0
        insight = (
            f"{len(active)} barangay(s) recorded route dispatches or waste scans in {label}, totaling "
            f"{tot_routes:,} route(s) ({rate:.1f}% completed) and {tot_scans:,} waste scan(s). "
            f"Top service barangay: {summary.get('top_barangay') or '—'}."
            if active
            else f"No barangay route dispatches or AI waste scans were recorded for {label}."
        )
        return {"about": about, "insight": insight, "legend": ""}

    if canonical == "waste_trends":
        about = (
            "Summarizes all waste items classified by the MobileNetV2 AI scanner, grouped by waste type and disposal "
            "category. Tracks submission counts, share of total volume (%), average confidence (%), and flagged items "
            "requiring administrator review."
        )
        total_scans = summary.get("waste_classifications", 0)
        verified = summary.get("verified_count", 0)
        flagged = summary.get("flagged_count", 0)
        flagged_rate = summary.get("flagged_rate", 0.0)
        low_flg = summary.get("low_confidence_flagged_count", 0)
        man_flg = summary.get("manual_flagged_count", 0)
        top_type = summary.get("most_common_type") or "—"
        top_cnt = summary.get("most_common_count", 0)
        if total_scans > 0:
            verified_pct = round(verified / total_scans * 100, 1)
            insight = (
                f"Out of {total_scans:,} AI waste classification(s) in {label}, {verified:,} ({verified_pct:.1f}%) "
                f"were verified normally and {flagged:,} ({flagged_rate:.1f}%) were flagged for review "
                f"({low_flg:,} auto-flagged below {LOW_CONFIDENCE_THRESHOLD:.0f}% confidence; {man_flg:,} manually flagged). "
                f"Most common waste type: {top_type} ({top_cnt:,} scans)."
            )
        else:
            insight = f"No AI waste classification scans were recorded for {label}."
        return {"about": about, "insight": insight, "legend": FLAGGED_EXPLANATION_TEXT}

    if canonical == "collection_calendar":
        about = (
            "Provides a day-by-day chronological log of collection dispatch operations, tracking daily scheduled "
            "volume, completion/delay/cancellation counts, completion rates, serviced barangays, and daily status."
        )
        if rows:
            tot_sched = sum(int(r[2] or 0) for r in rows)
            done_days = sum(1 for r in rows if r[8] == "Completed")
            dly_days = sum(1 for r in rows if r[8] == "Delayed" or int(r[4] or 0) > 0)
            insight = (
                f"Operations spanned {len(rows)} active calendar day(s) in {label} with {tot_sched:,} scheduled "
                f"route dispatch(es): {done_days} day(s) fully completed and {dly_days} day(s) with recorded delays."
            )
        else:
            insight = f"No scheduled collection dates were recorded in the calendar for {label}."
        return {"about": about, "insight": insight, "legend": ""}

    if canonical == "notification_analytics":
        about = (
            "Measures municipal broadcast reach and citizen engagement across notification categories (Route Updates, "
            "Announcements, Reminders, Emergency Alerts). Engagement % reflects the share of sent broadcasts opened "
            "by at least one citizen."
        )
        if rows:
            sent = sum(int(r[1] or 0) for r in rows)
            reads = sum(int(r[2] or 0) for r in rows)
            b_read = sum(int(r[3] or 0) for r in rows)
            eng = round(b_read / sent * 100, 1) if sent > 0 else 0.0
            insight = (
                f"{sent:,} municipal broadcast(s) were sent across {len(rows)} category(ies) in {label}, generating "
                f"{reads:,} total citizen read(s) across {b_read:,} read broadcast(s) ({eng:.1f}% engagement rate)."
            )
        else:
            insight = f"No notification broadcasts were sent during {label}."
        return {"about": about, "insight": insight, "legend": ""}

    if canonical == "schedule":
        about = (
            "Detailed operational ledger of individual waste collection schedules, documenting date, dispatch time, "
            "serviced barangays, street/zone stops, route name, starting point, assigned personnel, final status, and remarks."
        )
        if rows:
            comp = sum(1 for r in rows if r[7] == "Completed")
            dly = sum(1 for r in rows if r[7] == "Delayed")
            canc = sum(1 for r in rows if r[7] == "Cancelled")
            upcom = max(len(rows) - comp - dly - canc, 0)
            insight = (
                f"Contains {len(rows):,} individual schedule record(s) for {label}: {comp:,} Completed, "
                f"{dly:,} Delayed, {canc:,} Cancelled, and {upcom:,} Upcoming / In Progress."
            )
        else:
            insight = f"No collection schedule records were logged for {label}."
        return {"about": about, "insight": insight, "legend": ""}

    if canonical == "collector_operations":
        about = (
            "Documents field collector accountability and route execution. Table 6A summarizes each collector's "
            "assigned workload, stop confirmations, and current status; Table 6B logs stop-by-stop arrival verification "
            "and delay or cancellation reasons."
        )
        roster_rows = (collector_pdf or {}).get("roster_rows", [])
        log_rows = (collector_pdf or {}).get("route_log_rows", [])
        insight = (
            f"Tracks {len(roster_rows):,} collector/crew profile(s) across {len(log_rows):,} route assignment(s) "
            f"in {label} with verified stop arrival telemetry."
            if (roster_rows or log_rows)
            else f"No collector roster or assigned route telemetry was recorded for {label}."
        )
        return {"about": about, "insight": insight, "legend": ""}

    if canonical == "waste":
        about = "Individual raw AI waste classification submission records with timestamps, citizen details, and confidence scores."
        insight = f"Lists {len(rows):,} raw classification record(s) for {label}."
        return {"about": about, "insight": insight, "legend": FLAGGED_EXPLANATION_TEXT}

    if canonical == "users":
        about = "Registered citizen, collector, and administrator accounts created during the selected reporting period."
        insight = f"Lists {len(rows):,} user registration(s) recorded during {label}."
        return {"about": about, "insight": insight, "legend": ""}

    return {"about": "Verified municipal report data.", "insight": f"{len(rows):,} record(s) for {label}.", "legend": ""}

# ---------------------------------------------------------------------------
# Excel Export (.xlsx) with OpenPyXL
# ---------------------------------------------------------------------------

EXCEL_HEADERS = {
    "barangay_performance": [
        "Barangay", "Total Routes", "Completed", "Delayed", "Cancelled",
        "Completion Rate", "Waste Records", "Avg AI Confidence"
    ],
    "waste_trends": [
        "Waste Type", "Disposal Category", "Total Submissions", "% of Total",
        "Avg AI Confidence", "Flagged Items"
    ],
    "collection_calendar": [
        "Date", "Day of Week", "Total Scheduled", "Completed", "Delayed",
        "Cancelled", "Completion Rate", "Barangays Covered", "Operational Status"
    ],
    "notification_analytics": [
        "Notification Type", "Total Broadcasts", "Citizen Reads",
        "Broadcasts Read", "Read Engagement %", "Priority Level"
    ],
    "schedule": [
        "Date", "Time", "Barangays Serviced", "Zone / Streets", "Route Name",
        "Starting Point", "Assigned Personnel", "Status", "Remarks"
    ],
    # Legacy aliases
    "notification_effectiveness": [
        "Notification Type", "Total Broadcasts", "Citizen Reads",
        "Broadcasts Read", "Read Engagement %", "Priority Level"
    ],
    "waste": ["Date & Time (PST)", "Citizen", "Barangay / Zone", "Waste Type", "Category", "Confidence", "Status"],
    "users": ["Registered On (PST)", "Full Name", "Email", "Phone", "Role", "Barangay", "Zone", "Status"],
    "route_efficiency": [
        "Date", "Time", "Barangays Serviced", "Zone / Streets", "Route Name",
        "Starting Point", "Assigned Personnel", "Status", "Remarks"
    ],
    "environmental_impact": [
        "Waste Type", "Disposal Category", "Total Submissions", "% of Total",
        "Avg AI Confidence", "Flagged Items"
    ],
    "comparative_performance": [
        "Date", "Day of Week", "Total Scheduled", "Completed", "Delayed",
        "Cancelled", "Completion Rate", "Barangays Covered", "Operational Status"
    ],
    "peak_analysis": [
        "Date", "Day of Week", "Total Scheduled", "Completed", "Delayed",
        "Cancelled", "Completion Rate", "Barangays Covered", "Operational Status"
    ],
}

def _calc_wrapped_height(text: str, chars_per_line: int = 95, line_h: float = 14.5, min_h: float = 24.0) -> float:
    """Estimate a safe Excel row height for wrapped narrative blocks."""
    if not text:
        return min_h
    lines = sum(max(1, math.ceil(len(part) / max(chars_per_line, 25))) for part in str(text).splitlines())
    return max(min_h, lines * line_h + 10.0)


def _apply_excel_print_setup(ws) -> None:
    """Configure clean landscape fit-to-width printing for Excel sheets."""
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0


_STATUS_COLORS_HEX = {
    "Completed": "047857",
    "Complete": "047857",
    "Verified": "047857",
    "Active": "047857",
    "High Engagement": "047857",
    "Delayed": "B45309",
    "Partially Completed": "B45309",
    "Moderate Engagement": "B45309",
    "Cancelled": "B91C1C",
    "Flagged": "B91C1C",
    "Flagged for Review": "B91C1C",
    "Low Engagement": "B91C1C",
    "Inactive": "64748B",
    "Scheduled": "1D4ED8",
    "In Progress": "1D4ED8",
}


def build_excel(
    db: Session,
    year: int,
    month: int | None,
    types: list[str],
    generated_at: datetime | None = None,
) -> bytes:
    """Generate a publication-grade .xlsx workbook with Executive Summary + annotated Data Sheets."""
    generated_at = generated_at or ph_now()
    start_ph, end_ph = resolve_period(year, month)
    summary = get_report_summary_data(db, year, month)
    data = _data_for(db, start_ph, end_ph, types)
    label = summary["period"]
    needs_flagged_audit = any(t in ("waste_trends", "environmental_impact") for t in types)
    flagged_rows = _flagged_waste_audit_rows(db, start_ph, end_ph) if needs_flagged_audit else []

    wb = Workbook()

    # Styling definitions
    NAVY_FILL = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    BLUE_FILL = PatternFill(start_color="1E40AF", end_color="1E40AF", fill_type="solid")
    SUB_BANNER_FILL = PatternFill(start_color="DBEAFE", end_color="DBEAFE", fill_type="solid")
    CALLOUT_BLUE_FILL = PatternFill(start_color="EFF6FF", end_color="EFF6FF", fill_type="solid")
    CALLOUT_AMBER_FILL = PatternFill(start_color="FFFBEB", end_color="FFFBEB", fill_type="solid")
    SECTION_FILL = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    ALT_FILL = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    TOTAL_FILL = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    AUDIT_HDR_FILL = PatternFill(start_color="92400E", end_color="92400E", fill_type="solid")

    BANNER_FONT = Font(bold=True, size=13, color="FFFFFF", name="Calibri")
    SUB_BANNER_FONT = Font(bold=True, size=10.5, color="1E3A8A", name="Calibri")
    TITLE_FONT = Font(bold=True, size=15, color="0F172A", name="Calibri")
    SUBTITLE_FONT = Font(bold=True, size=11, color="2563EB", name="Calibri")
    SECTION_FONT = Font(bold=True, size=11, color="1E3A8A", name="Calibri")
    HEADER_FONT = Font(bold=True, size=10.5, color="FFFFFF", name="Calibri")
    BODY_FONT = Font(size=10, color="0F172A", name="Calibri")
    BODY_BOLD = Font(bold=True, size=10, color="0F172A", name="Calibri")
    CALLOUT_FONT = Font(size=10, color="1E293B", name="Calibri")
    AMBER_FONT = Font(size=9.5, color="78350F", name="Calibri")
    MUTED_FONT = Font(italic=True, size=9.5, color="64748B", name="Calibri")

    THIN_SIDE = Side(style="thin", color="CBD5E1")
    BLUE_ACCENT_SIDE = Side(style="thick", color="1E40AF")
    AMBER_ACCENT_SIDE = Side(style="thick", color="D97706")
    DOUBLE_BOTTOM_SIDE = Side(style="double", color="1E40AF")
    DATA_BORDER = Border(left=THIN_SIDE, right=THIN_SIDE, top=THIN_SIDE, bottom=THIN_SIDE)
    CALLOUT_BLUE_BORDER = Border(left=BLUE_ACCENT_SIDE, right=THIN_SIDE, top=THIN_SIDE, bottom=THIN_SIDE)
    CALLOUT_AMBER_BORDER = Border(left=AMBER_ACCENT_SIDE, right=THIN_SIDE, top=THIN_SIDE, bottom=THIN_SIDE)
    TOTAL_BORDER = Border(top=Side(style="medium", color="1E40AF"), bottom=DOUBLE_BOTTOM_SIDE, left=THIN_SIDE, right=THIN_SIDE)

    ALIGN_LEFT_WRAP = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")
    ALIGN_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # -----------------------------------------------------------------------
    # Sheet 1: Executive Summary
    # -----------------------------------------------------------------------
    ws_sum = wb.active
    ws_sum.title = "Executive Summary"
    ws_sum.views.sheetView[0].showGridLines = True
    _apply_excel_print_setup(ws_sum)

    ws_sum.merge_cells("A1:C1")
    ws_sum.row_dimensions[1].height = 28
    c = ws_sum.cell(row=1, column=1, value="CITY GOVERNMENT OF MUNTINLUPA — WISE SYSTEM")
    c.font = BANNER_FONT
    c.fill = NAVY_FILL
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)

    ws_sum.merge_cells("A2:C2")
    ws_sum.row_dimensions[2].height = 24
    c = ws_sum.cell(row=2, column=1, value="Official Administrative Waste & Operations Report")
    c.font = TITLE_FONT
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)

    ws_sum.merge_cells("A3:C3")
    ws_sum.row_dimensions[3].height = 20
    c = ws_sum.cell(row=3, column=1, value=f"Reporting Period: {label} · Executive Overview & Key Performance Indicators")
    c.font = SUBTITLE_FONT
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)

    exec_brief = f"Executive Summary Brief: {get_executive_narrative(summary)}"
    ws_sum.merge_cells("A5:C5")
    ws_sum.row_dimensions[5].height = _calc_wrapped_height(exec_brief, chars_per_line=105, min_h=34.0)
    brief_cell = ws_sum.cell(row=5, column=1, value=exec_brief)
    brief_cell.font = CALLOUT_FONT
    brief_cell.alignment = ALIGN_LEFT_WRAP
    for col_i in range(1, 4):
        cell_b = ws_sum.cell(row=5, column=col_i)
        cell_b.fill = CALLOUT_BLUE_FILL
        cell_b.border = Border(
            left=BLUE_ACCENT_SIDE if col_i == 1 else None,
            right=THIN_SIDE if col_i == 3 else None,
            top=THIN_SIDE,
            bottom=THIN_SIDE,
        )

    curr_row = 7
    summary_sections = [
        (
            "Overall Collection Route Performance",
            [
                ("Total Routes Scheduled", summary["total_collections"], "#,##0", "Total scheduled waste collection trips during the period."),
                ("Routes Completed Successfully", summary["completed_collections"], "#,##0", "Trips confirmed completed across assigned barangay stops."),
                ("Routes Delayed / Issues", summary["delayed_collections"], "#,##0", "Trips delayed by traffic, weather, or operational setbacks."),
                ("Routes Cancelled", summary["cancelled_collections"], "#,##0", "Scheduled trips cancelled prior to or during dispatch."),
                ("Routes Upcoming / Pending", summary["upcoming_collections"], "#,##0", "Scheduled trips awaiting future dispatch dates."),
                ("Overall Fleet Completion Rate", summary["completion_rate"] / 100.0, "0.0%", "Share of total scheduled routes completed successfully."),
            ],
        ),
        (
            "Waste Classification & Flagged Quality Metrics",
            [
                ("Total AI Waste Classifications", summary["waste_classifications"], "#,##0", "Total waste photos scanned and categorized by the AI model."),
                ("Verified Scans (Passed Quality Check)", summary["verified_count"], "#,##0", "Scans meeting confidence threshold (>=60%) without flags."),
                ("Flagged Classifications (Needs Review)", summary["flagged_count"], "#,##0", "Total scans marked is_flagged=1 (low confidence or admin flag)."),
                (f"  - Auto-Flagged (<{LOW_CONFIDENCE_THRESHOLD:.0f}% AI Confidence)", summary["low_confidence_flagged_count"], "#,##0", "Flagged automatically due to low model confidence (<60%)."),
                ("  - Manually Flagged by Admin", summary["manual_flagged_count"], "#,##0", "Flagged manually by an admin for visual audit or retraining."),
                ("Flagged Classification Rate", summary["flagged_rate"] / 100.0, "0.0%", "Percentage of total AI scans requiring review."),
                ("Properly Sorted Recyclables Count", summary["recyclable_count"], "#,##0", "Classifications identified as Recyclable materials."),
                ("Citywide Recycling / Diversion Rate", summary["recycling_rate"] / 100.0, "0.0%", "Share of total classified scans that are recyclable."),
                ("Average AI Confidence Accuracy", (summary["avg_confidence"] / 100.0 if summary["avg_confidence"] is not None else None), "0.0%", "Mean confidence score across all classified waste photos."),
                ("Most Predominant Waste Type", summary["most_common_type"] or "—", None, "Highest-volume waste material classified in the period."),
            ],
        ),
        (
            "Barangay Operational Overview",
            [
                ("Top Performing Service Barangay", summary["top_barangay"], None, "Barangay with highest route completion rate and volume."),
                ("Total Active Service Barangays", summary["total_barangays"], "#,##0", "Number of barangays with scheduled routes in the period."),
                ("Average Barangay Completion Rate", summary["efficiency_score"] / 100.0, "0.0%", "Mean completion efficiency across active barangays."),
                ("New Citizen Registrations in Period", summary["new_users"], "#,##0", "New mobile/web user accounts created during this period."),
            ],
        ),
    ]

    for sec_title, items in summary_sections:
        ws_sum.row_dimensions[curr_row].height = 24
        for col_i, h_lbl in enumerate((sec_title, "Metric Value", "Description / Definition"), start=1):
            hc = ws_sum.cell(row=curr_row, column=col_i, value=h_lbl)
            hc.font = HEADER_FONT
            hc.fill = BLUE_FILL
            hc.border = DATA_BORDER
            hc.alignment = ALIGN_CENTER if col_i == 2 else ALIGN_LEFT_WRAP
        curr_row += 1

        for idx_item, (m_label, val, num_fmt, desc) in enumerate(items):
            ws_sum.row_dimensions[curr_row].height = 21
            c1 = ws_sum.cell(row=curr_row, column=1, value=m_label)
            c2 = ws_sum.cell(row=curr_row, column=2, value=val)
            c3 = ws_sum.cell(row=curr_row, column=3, value=desc)
            for cell_item in (c1, c2, c3):
                cell_item.border = DATA_BORDER
                if idx_item % 2 == 1:
                    cell_item.fill = ALT_FILL
            c1.font = BODY_FONT
            c2.font = BODY_BOLD
            c3.font = Font(size=9.5, color="475569", name="Calibri")
            c1.alignment = ALIGN_LEFT_WRAP
            c2.alignment = ALIGN_RIGHT if isinstance(val, (int, float)) else ALIGN_CENTER
            c3.alignment = ALIGN_LEFT_WRAP
            if num_fmt and isinstance(val, (int, float)):
                c2.number_format = num_fmt
            curr_row += 1
        curr_row += 1

    # Flagged explanation callout on Executive Summary
    ws_sum.merge_cells(start_row=curr_row, start_column=1, end_row=curr_row, end_column=3)
    ws_sum.row_dimensions[curr_row].height = _calc_wrapped_height(FLAGGED_EXPLANATION_TEXT, chars_per_line=105, min_h=34.0)
    flg_note = ws_sum.cell(row=curr_row, column=1, value=FLAGGED_EXPLANATION_TEXT)
    flg_note.font = AMBER_FONT
    flg_note.alignment = ALIGN_LEFT_WRAP
    for col_i in range(1, 4):
        cb = ws_sum.cell(row=curr_row, column=col_i)
        cb.fill = CALLOUT_AMBER_FILL
        cb.border = Border(
            left=AMBER_ACCENT_SIDE if col_i == 1 else None,
            right=THIN_SIDE if col_i == 3 else None,
            top=THIN_SIDE,
            bottom=THIN_SIDE,
        )
    curr_row += 2

    gen_cell = ws_sum.cell(
        row=curr_row,
        column=1,
        value=f"Report Generated: {generated_at.strftime('%Y-%m-%d %H:%M')} (PST) · Official Document · City of Muntinlupa WISE",
    )
    gen_cell.font = MUTED_FONT
    ws_sum.column_dimensions["A"].width = 42
    ws_sum.column_dimensions["B"].width = 20
    ws_sum.column_dimensions["C"].width = 58

    # -----------------------------------------------------------------------
    # Data Sheets for Checked Report Types
    # -----------------------------------------------------------------------
    first_data_row = 7
    for t in types:
        if t not in EXCEL_HEADERS:
            continue
        headers = EXCEL_HEADERS[t]
        type_rows = data.get(t, [])
        num_cols = len(headers)
        last_col_let = get_column_letter(num_cols)
        sheet_title = TYPE_SHEET_NAMES.get(t, t[:31])
        type_label = TYPE_LABELS.get(t, sheet_title)
        narrative = get_section_narratives(t, label, summary, type_rows)

        ws = wb.create_sheet(title=sheet_title)
        ws.views.sheetView[0].showGridLines = True
        _apply_excel_print_setup(ws)

        # Row 1: Municipal Banner
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=num_cols)
        ws.row_dimensions[1].height = 26
        b1 = ws.cell(row=1, column=1, value=f"CITY GOVERNMENT OF MUNTINLUPA · WISE SYSTEM — {type_label.upper()}")
        b1.font = BANNER_FONT
        b1.fill = NAVY_FILL
        b1.alignment = Alignment(horizontal="left", vertical="center", indent=1)

        # Row 2: Period & Record Count Subtitle
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=num_cols)
        ws.row_dimensions[2].height = 21
        b2 = ws.cell(
            row=2,
            column=1,
            value=f"Reporting Period: {label} · {len(type_rows):,} Record(s) · Generated {generated_at.strftime('%Y-%m-%d %H:%M')} (PST)",
        )
        b2.font = SUB_BANNER_FONT
        b2.fill = SUB_BANNER_FILL
        b2.alignment = Alignment(horizontal="left", vertical="center", indent=1)

        # Row 3: About This Report
        about_txt = f"About This Report: {narrative['about']}"
        ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=num_cols)
        ws.row_dimensions[3].height = _calc_wrapped_height(about_txt, chars_per_line=max(80, num_cols * 15), min_h=28.0)
        b3 = ws.cell(row=3, column=1, value=about_txt)
        b3.font = CALLOUT_FONT
        b3.alignment = ALIGN_LEFT_WRAP
        for col_i in range(1, num_cols + 1):
            c_box = ws.cell(row=3, column=col_i)
            c_box.fill = CALLOUT_BLUE_FILL
            c_box.border = Border(
                left=BLUE_ACCENT_SIDE if col_i == 1 else None,
                right=THIN_SIDE if col_i == num_cols else None,
                top=THIN_SIDE,
                bottom=THIN_SIDE,
            )

        # Row 4: Period Findings
        insight_txt = f"Period Findings: {narrative['insight']}"
        ws.merge_cells(start_row=4, start_column=1, end_row=4, end_column=num_cols)
        ws.row_dimensions[4].height = _calc_wrapped_height(insight_txt, chars_per_line=max(80, num_cols * 15), min_h=26.0)
        b4 = ws.cell(row=4, column=1, value=insight_txt)
        b4.font = BODY_BOLD if type_rows else MUTED_FONT
        b4.alignment = ALIGN_LEFT_WRAP
        for col_i in range(1, num_cols + 1):
            c_box = ws.cell(row=4, column=col_i)
            c_box.fill = ALT_FILL
            c_box.border = Border(
                left=BLUE_ACCENT_SIDE if col_i == 1 else None,
                right=THIN_SIDE if col_i == num_cols else None,
                top=THIN_SIDE,
                bottom=THIN_SIDE,
            )

        # Row 5: Blank spacer
        ws.row_dimensions[5].height = 8

        # Row 6: Column Headers
        ws.row_dimensions[6].height = 28
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=6, column=col_idx, value=h)
            cell.font = HEADER_FONT
            cell.fill = BLUE_FILL
            cell.border = DATA_BORDER
            cell.alignment = ALIGN_CENTER

        last_data_row = first_data_row
        tot_row = first_data_row

        if not type_rows:
            empty_cell = ws.cell(row=first_data_row, column=1, value="No records found for the selected period.")
            empty_cell.font = MUTED_FONT
            empty_cell.alignment = ALIGN_CENTER
            ws.merge_cells(start_row=first_data_row, start_column=1, end_row=first_data_row, end_column=num_cols)
            ws.row_dimensions[first_data_row].height = 24
            for col_i in range(1, num_cols + 1):
                ws.cell(row=first_data_row, column=col_i).border = DATA_BORDER
        else:
            for idx_r, r_data in enumerate(type_rows):
                row_idx = first_data_row + idx_r
                max_cell_chars = max((len(str(v)) for v in r_data if v is not None), default=12)
                ws.row_dimensions[row_idx].height = max(21.0, min(54.0, math.ceil(max_cell_chars / 38) * 14.0 + 6.0))
                for col_idx, val in enumerate(r_data, start=1):
                    if col_idx > num_cols:
                        break
                    cell = ws.cell(row=row_idx, column=col_idx)
                    cell.border = DATA_BORDER
                    cell.font = BODY_FONT

                    if idx_r % 2 == 1:
                        cell.fill = ALT_FILL

                    if isinstance(val, (int, float)):
                        cell.value = val
                        cell.alignment = ALIGN_RIGHT
                        cell.number_format = "#,##0"
                    elif isinstance(val, str) and val.endswith("%"):
                        try:
                            f_val = float(val.replace("%", "").strip()) / 100.0
                            cell.value = f_val
                            cell.alignment = ALIGN_RIGHT
                            cell.number_format = "0.0%"
                        except ValueError:
                            cell.value = val
                            cell.alignment = ALIGN_RIGHT
                    elif isinstance(val, str) and (val.startswith("20") and len(val) == 10):
                        cell.value = val
                        cell.alignment = ALIGN_CENTER
                    elif isinstance(val, str) and val in _STATUS_COLORS_HEX:
                        cell.value = val
                        cell.alignment = ALIGN_CENTER
                        cell.font = Font(bold=True, size=10, color=_STATUS_COLORS_HEX[val], name="Calibri")
                    else:
                        cell.value = val if val is not None else "—"
                        cell.alignment = ALIGN_LEFT_WRAP

            last_data_row = first_data_row + len(type_rows) - 1
            tot_row = last_data_row + 1

            def _style_total_cell(row_i: int, col_i: int, val, fmt: str | None = None, align=ALIGN_RIGHT):
                tc = ws.cell(row=row_i, column=col_i, value=val)
                tc.font = BODY_BOLD
                tc.fill = TOTAL_FILL
                tc.border = TOTAL_BORDER
                tc.alignment = align
                if fmt:
                    tc.number_format = fmt
                return tc

            if t == "barangay_performance":
                ws.row_dimensions[tot_row].height = 24
                _style_total_cell(tot_row, 1, "City Total / Fleet Average", align=ALIGN_LEFT_WRAP)
                for c_idx in (2, 3, 4, 5, 7):
                    col_let = get_column_letter(c_idx)
                    _style_total_cell(tot_row, c_idx, f"=SUM({col_let}{first_data_row}:{col_let}{last_data_row})", "#,##0")
                _style_total_cell(tot_row, 6, f"=IFERROR(C{tot_row}/B{tot_row},0)", "0.0%")
                _style_total_cell(tot_row, 8, f'=IFERROR(AVERAGE(H{first_data_row}:H{last_data_row}),"—")', "0.0%")

            elif t in ("waste_trends", "environmental_impact"):
                ws.row_dimensions[tot_row].height = 24
                _style_total_cell(tot_row, 1, "Total Classified", align=ALIGN_LEFT_WRAP)
                _style_total_cell(tot_row, 2, "All Categories", align=ALIGN_LEFT_WRAP)
                _style_total_cell(tot_row, 3, f"=SUM(C{first_data_row}:C{last_data_row})", "#,##0")
                _style_total_cell(tot_row, 4, f"=SUM(D{first_data_row}:D{last_data_row})", "0.0%")
                _style_total_cell(tot_row, 5, f'=IFERROR(AVERAGE(E{first_data_row}:E{last_data_row}),"—")', "0.0%")
                _style_total_cell(tot_row, 6, f"=SUM(F{first_data_row}:F{last_data_row})", "#,##0")

            elif t in ("collection_calendar", "comparative_performance", "peak_analysis"):
                ws.row_dimensions[tot_row].height = 24
                _style_total_cell(tot_row, 1, "Period Total", align=ALIGN_LEFT_WRAP)
                _style_total_cell(tot_row, 2, f"{len(type_rows)} Day(s)", align=ALIGN_LEFT_WRAP)
                for c_idx in (3, 4, 5, 6):
                    col_let = get_column_letter(c_idx)
                    _style_total_cell(tot_row, c_idx, f"=SUM({col_let}{first_data_row}:{col_let}{last_data_row})", "#,##0")
                _style_total_cell(tot_row, 7, f"=IFERROR(D{tot_row}/C{tot_row},0)", "0.0%")
                _style_total_cell(tot_row, 8, "—", align=ALIGN_CENTER)
                _style_total_cell(tot_row, 9, "—", align=ALIGN_CENTER)

            elif t in ("notification_analytics", "notification_effectiveness"):
                ws.row_dimensions[tot_row].height = 24
                _style_total_cell(tot_row, 1, "Total Broadcasts & Reads", align=ALIGN_LEFT_WRAP)
                for c_idx in (2, 3, 4):
                    col_let = get_column_letter(c_idx)
                    _style_total_cell(tot_row, c_idx, f"=SUM({col_let}{first_data_row}:{col_let}{last_data_row})", "#,##0")
                _style_total_cell(tot_row, 5, f"=IFERROR(D{tot_row}/B{tot_row},0)", "0.0%")
                _style_total_cell(tot_row, 6, "—", align=ALIGN_CENTER)

        # Waste Classification extra block: Flagged Meaning + Audit Log
        if t in ("waste_trends", "environmental_impact"):
            note_row = (tot_row + 2) if type_rows else (first_data_row + 2)
            ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=num_cols)
            ws.row_dimensions[note_row].height = _calc_wrapped_height(FLAGGED_EXPLANATION_TEXT, chars_per_line=105, min_h=38.0)
            fn_cell = ws.cell(row=note_row, column=1, value=FLAGGED_EXPLANATION_TEXT)
            fn_cell.font = AMBER_FONT
            fn_cell.alignment = ALIGN_LEFT_WRAP
            for col_i in range(1, num_cols + 1):
                cb = ws.cell(row=note_row, column=col_i)
                cb.fill = CALLOUT_AMBER_FILL
                cb.border = Border(
                    left=AMBER_ACCENT_SIDE if col_i == 1 else None,
                    right=THIN_SIDE if col_i == num_cols else None,
                    top=THIN_SIDE,
                    bottom=THIN_SIDE,
                )

            audit_title_row = note_row + 2
            ws.merge_cells(start_row=audit_title_row, start_column=1, end_row=audit_title_row, end_column=num_cols)
            ws.row_dimensions[audit_title_row].height = 22
            at_cell = ws.cell(
                row=audit_title_row,
                column=1,
                value=f"Flagged Classifications Audit Log ({len(flagged_rows)} record(s) requiring review)",
            )
            at_cell.font = SECTION_FONT
            at_cell.fill = SECTION_FILL
            at_cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)

            audit_hdr_row = audit_title_row + 1
            ws.row_dimensions[audit_hdr_row].height = 24
            for col_idx, ah in enumerate(FLAGGED_AUDIT_HEADERS, start=1):
                ac = ws.cell(row=audit_hdr_row, column=col_idx, value=ah)
                ac.font = HEADER_FONT
                ac.fill = AUDIT_HDR_FILL
                ac.border = DATA_BORDER
                ac.alignment = ALIGN_CENTER

            if not flagged_rows:
                ar = audit_hdr_row + 1
                ws.merge_cells(start_row=ar, start_column=1, end_row=ar, end_column=num_cols)
                ws.row_dimensions[ar].height = 22
                empty_a = ws.cell(
                    row=ar,
                    column=1,
                    value="Zero flagged classifications recorded in this period. All AI scans met quality checks.",
                )
                empty_a.font = Font(italic=True, size=9.5, color="047857", name="Calibri")
                empty_a.alignment = ALIGN_CENTER
                for col_i in range(1, num_cols + 1):
                    ws.cell(row=ar, column=col_i).border = DATA_BORDER
            else:
                for a_idx, a_row in enumerate(flagged_rows):
                    ar = audit_hdr_row + 1 + a_idx
                    ws.row_dimensions[ar].height = 21
                    for col_idx, val in enumerate(a_row, start=1):
                        ac = ws.cell(row=ar, column=col_idx)
                        ac.border = DATA_BORDER
                        ac.font = BODY_FONT
                        if a_idx % 2 == 1:
                            ac.fill = ALT_FILL
                        if isinstance(val, str) and val.endswith("%"):
                            try:
                                ac.value = float(val.replace("%", "").strip()) / 100.0
                                ac.alignment = ALIGN_RIGHT
                                ac.number_format = "0.0%"
                            except ValueError:
                                ac.value = val
                                ac.alignment = ALIGN_RIGHT
                        elif col_idx == 6:
                            ac.value = val
                            ac.alignment = ALIGN_CENTER
                            ac.font = Font(bold=True, size=10, color="B91C1C", name="Calibri")
                        elif col_idx == 1:
                            ac.value = val
                            ac.alignment = ALIGN_CENTER
                        else:
                            ac.value = val
                            ac.alignment = ALIGN_LEFT_WRAP

        # Auto-fit Column Widths cleanly (respecting wrap cap)
        for col_idx in range(1, num_cols + 1):
            col_letter = get_column_letter(col_idx)
            max_len = len(str(headers[col_idx - 1]))
            for r in type_rows:
                if col_idx - 1 < len(r):
                    val_str = str(r[col_idx - 1] if r[col_idx - 1] is not None else "")
                    max_len = max(max_len, len(val_str))
            if t in ("waste_trends", "environmental_impact") and col_idx - 1 < len(FLAGGED_AUDIT_HEADERS):
                max_len = max(max_len, len(FLAGGED_AUDIT_HEADERS[col_idx - 1]))
                for ar in flagged_rows:
                    if col_idx - 1 < len(ar):
                        max_len = max(max_len, len(str(ar[col_idx - 1] or "")))
            cap = 44 if col_idx in (3, 7, 8, 9) else 34
            ws.column_dimensions[col_letter].width = min(max(max_len + 4, 15), cap)

        # Freeze Panes (Header Row 6) & AutoFilter
        ws.freeze_panes = "A7"
        ws.auto_filter.ref = f"A6:{last_col_let}{max(last_data_row, 7)}"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

# ---------------------------------------------------------------------------
# PDF Generation with ReportLab (Landscape A4 + NumberedCanvas)
# ---------------------------------------------------------------------------

_PAGE = landscape(A4)
_PAGE_W = _PAGE[0]  # 841.89 pt
_PAGE_H = _PAGE[1]  # 595.28 pt

_LEFT_M = 14 * mm
_RIGHT_M = 14 * mm
_TOP_M = 28 * mm
_BOT_M = 16 * mm
_CONTENT_W = _PAGE_W - _LEFT_M - _RIGHT_M

_COLOR_NAVY = colors.HexColor("#0F172A")
_COLOR_BLUE = colors.HexColor("#1E40AF")
_COLOR_ACCENT = colors.HexColor("#2563EB")
_COLOR_MUTED = colors.HexColor("#64748B")
_COLOR_BORDER = colors.HexColor("#CBD5E1")
_COLOR_ALT_ROW = colors.HexColor("#F8FAFC")
_COLOR_WHITE = colors.white

_LOGO_PATH = os.path.join(os.path.dirname(__file__), "..", "assets", "wise-logo.png")

PDF_HEADERS = EXCEL_HEADERS

class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas providing persistent header banners, branding, and accurate 'Page X of Y' footers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []
        self.period_label = ""
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

        # ---- Top Header Banner ----
        band_h = 20 * mm
        self.setFillColor(_COLOR_NAVY)
        self.rect(0, _PAGE_H - band_h, _PAGE_W, band_h, stroke=0, fill=1)

        # Accent Line under Banner
        self.setFillColor(_COLOR_ACCENT)
        self.rect(0, _PAGE_H - band_h - 1.2 * mm, _PAGE_W, 1.2 * mm, stroke=0, fill=1)

        # Official Logo
        if os.path.exists(_LOGO_PATH):
            try:
                logo_size = 14 * mm
                self.drawImage(
                    _LOGO_PATH,
                    _LEFT_M,
                    _PAGE_H - band_h + (band_h - logo_size) / 2,
                    width=logo_size,
                    height=logo_size,
                    mask="auto",
                    preserveAspectRatio=True,
                )
            except Exception:
                pass

        # Left Branding Text beside Logo
        self.setFillColor(_COLOR_WHITE)
        self.setFont("Helvetica-Bold", 11)
        self.drawString(_LEFT_M + 18 * mm, _PAGE_H - 9 * mm, "WISE SYSTEM — WASTE MANAGEMENT & LOGISTICS")
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#93C5FD"))
        self.drawString(_LEFT_M + 18 * mm, _PAGE_H - 14 * mm, "City Government of Muntinlupa · Environmental Sanitation Center")

        # Right City Header
        self.setFillColor(_COLOR_WHITE)
        self.setFont("Helvetica-Bold", 12)
        self.drawRightString(_PAGE_W - _RIGHT_M, _PAGE_H - 9 * mm, "REPUBLIC OF THE PHILIPPINES")
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#93C5FD"))
        self.drawRightString(_PAGE_W - _RIGHT_M, _PAGE_H - 14 * mm, f"Administrative Report · {self.period_label}")

        # ---- Footer ----
        self.setFont("Helvetica", 7.5)
        self.setFillColor(_COLOR_MUTED)

        # Footer divider line
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.6)
        self.line(_LEFT_M, 11 * mm, _PAGE_W - _RIGHT_M, 11 * mm)

        # Left footer: System & Notice
        self.drawString(_LEFT_M, 6.5 * mm, "WISE System · Official Document · Confidential Municipal Administration Report")

        # Center footer: Timestamp
        gen_str = self.generated_at.strftime("%B %d, %Y at %I:%M %p") if self.generated_at else ""
        self.drawCentredString(_PAGE_W / 2, 6.5 * mm, f"Generated {gen_str} (PST)")

        # Right footer: Page count
        self.drawRightString(_PAGE_W - _RIGHT_M, 6.5 * mm, f"Page {self._pageNumber} of {total_pages}")

        self.restoreState()


def _get_pdf_col_widths(report_type: str) -> list[float]:
    """Calculate proportional column widths summing exactly to _CONTENT_W."""
    raw_proportions = {
        "barangay_performance": [36, 16, 16, 16, 16, 20, 20, 22],
        "waste_trends": [38, 38, 26, 20, 26, 18],
        "collection_calendar": [20, 18, 16, 16, 14, 14, 18, 48, 18],
        "notification_analytics": [44, 20, 20, 20, 22, 24],
        "schedule": [18, 16, 26, 20, 26, 24, 24, 16, 32],
        # Legacy
        "notification_effectiveness": [44, 20, 20, 20, 22, 24],
        "waste": [26, 28, 32, 26, 24, 18, 18],
        "users": [26, 28, 34, 22, 16, 22, 16, 16],
        "route_efficiency": [18, 16, 26, 20, 26, 24, 24, 16, 32],
        "environmental_impact": [38, 38, 26, 20, 26, 18],
        "comparative_performance": [20, 18, 16, 16, 14, 14, 18, 48, 18],
        "peak_analysis": [20, 18, 16, 16, 14, 14, 18, 48, 18],
    }
    props = raw_proportions.get(report_type)
    if not props:
        return None
    total_prop = sum(props)
    return [(p / total_prop) * _CONTENT_W for p in props]


def _build_pdf_callout_box(
    title: str,
    body: str,
    bg_hex: str,
    accent_hex: str,
    text_hex: str,
    styles: dict,
) -> Table:
    """Create a sleek left-accented narrative box for PDF reports."""
    title_p = Paragraph(
        f"<b>{_xml_escape(title)}</b>",
        ParagraphStyle(f"CallT_{accent_hex}", parent=styles["CalloutTitle"], textColor=colors.HexColor(accent_hex)),
    )
    body_p = Paragraph(
        _xml_escape(body),
        ParagraphStyle(f"CallB_{accent_hex}", parent=styles["CalloutBody"], textColor=colors.HexColor(text_hex)),
    )
    box = Table([[title_p], [body_p]], colWidths=[_CONTENT_W])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg_hex)),
        ("LINEBEFORE", (0, 0), (0, -1), 3.0, colors.HexColor(accent_hex)),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 1.5),
        ("TOPPADDING", (0, 1), (-1, 1), 1.5),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return box


def _build_pdf_section_banner(
    title: str,
    subtitle: str,
    about: str,
    insight: str,
    styles: dict,
) -> Table:
    """Create a structured section header card containing title, About This Report, and Period Findings."""
    head_p = Paragraph(
        f"<b>{_xml_escape(title)}</b> <font color='#64748B' size='8'>· {_xml_escape(subtitle)}</font>",
        styles["SecBannerTitle"],
    )
    about_p = Paragraph(
        f"<font color='#1E40AF'><b>About This Report:</b></font> {_xml_escape(about)}",
        styles["CalloutBody"],
    )
    insight_p = Paragraph(
        f"<font color='#0F172A'><b>Period Findings:</b></font> {_xml_escape(insight)}",
        styles["CalloutBody"],
    )
    card = Table([[head_p], [about_p], [insight_p]], colWidths=[_CONTENT_W])
    card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFF6FF")),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F8FAFC")),
        ("LINEBEFORE", (0, 0), (0, -1), 3.0, _COLOR_BLUE),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#BFDBFE")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#DBEAFE")),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ("TOPPADDING", (0, 1), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return card


def _pdf_totals_row(report_type: str, rows: list[list]) -> list[str] | None:
    """Compute a summary totals row for quantitative PDF tables matching Excel."""
    if not rows:
        return None
    if report_type == "barangay_performance":
        tot_routes = sum(int(r[1] or 0) for r in rows)
        tot_comp = sum(int(r[2] or 0) for r in rows)
        tot_dly = sum(int(r[3] or 0) for r in rows)
        tot_canc = sum(int(r[4] or 0) for r in rows)
        tot_waste = sum(int(r[6] or 0) for r in rows)
        rate = round(tot_comp / tot_routes * 100, 1) if tot_routes > 0 else 0.0
        confs = [float(str(r[7]).replace("%", "")) for r in rows if r[7] and str(r[7]).endswith("%")]
        avg_conf = f"{round(sum(confs) / len(confs), 1):.1f}%" if confs else "—"
        return ["City Total / Fleet Rate", str(tot_routes), str(tot_comp), str(tot_dly), str(tot_canc), f"{rate:.1f}%", str(tot_waste), avg_conf]
    if report_type in ("waste_trends", "environmental_impact"):
        tot_sub = sum(int(r[2] or 0) for r in rows)
        tot_flg = sum(int(r[5] or 0) for r in rows)
        confs = [float(str(r[4]).replace("%", "")) for r in rows if r[4] and str(r[4]).endswith("%")]
        avg_conf = f"{round(sum(confs) / len(confs), 1):.1f}%" if confs else "—"
        return ["Total Classified", "All Categories", str(tot_sub), "100.0%", avg_conf, str(tot_flg)]
    if report_type in ("collection_calendar", "comparative_performance", "peak_analysis"):
        tot_sched = sum(int(r[2] or 0) for r in rows)
        tot_comp = sum(int(r[3] or 0) for r in rows)
        tot_dly = sum(int(r[4] or 0) for r in rows)
        tot_canc = sum(int(r[5] or 0) for r in rows)
        rate = round(tot_comp / tot_sched * 100, 1) if tot_sched > 0 else 0.0
        return ["Period Total", f"{len(rows)} Day(s)", str(tot_sched), str(tot_comp), str(tot_dly), str(tot_canc), f"{rate:.1f}%", "—", "—"]
    if report_type in ("notification_analytics", "notification_effectiveness"):
        tot_sent = sum(int(r[1] or 0) for r in rows)
        tot_reads = sum(int(r[2] or 0) for r in rows)
        tot_bread = sum(int(r[3] or 0) for r in rows)
        eng = round(tot_bread / tot_sent * 100, 1) if tot_sent > 0 else 0.0
        return ["Total Broadcasts & Reads", str(tot_sent), str(tot_reads), str(tot_bread), f"{eng:.1f}%", "—"]
    return None


def _style_pdf_cell(val_str: str, styles: dict, align_override=None) -> Paragraph:
    """Format a PDF cell paragraph with semantic status colors when appropriate."""
    hex_col = _STATUS_COLORS_HEX.get(val_str)
    if hex_col:
        st = ParagraphStyle(
            f"ST_{hex_col}",
            parent=styles["TD_C"],
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(f"#{hex_col}"),
        )
        return Paragraph(_xml_escape(val_str), st)

    if align_override is not None:
        return Paragraph(_xml_escape(val_str), align_override)

    if val_str.endswith("%") or (val_str.isdigit() and len(val_str) <= 6):
        cell_style = styles["TD_R"]
    elif val_str.startswith("20") and len(val_str) == 10:
        cell_style = styles["TD_C"]
    else:
        cell_style = styles["TD_L"]
    return Paragraph(_xml_escape(val_str), cell_style)


def build_pdf(
    db: Session,
    year: int,
    month: int | None,
    types: list[str],
    generated_at: datetime | None = None,
) -> bytes:
    """Generate a clean, professional landscape A4 PDF report with narrative context and totals."""
    generated_at = generated_at or ph_now()
    start_ph, end_ph = resolve_period(year, month)
    summary = get_report_summary_data(db, year, month)
    data = _data_for(db, start_ph, end_ph, types)
    label = period_label(year, month)
    needs_flagged_audit = any(t in ("waste_trends", "environmental_impact") for t in types)
    flagged_rows = _flagged_waste_audit_rows(db, start_ph, end_ph) if needs_flagged_audit else []

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=_PAGE,
        leftMargin=_LEFT_M,
        rightMargin=_RIGHT_M,
        topMargin=_TOP_M,
        bottomMargin=_BOT_M,
        title=f"WISE System Report — {label}",
        author="City of Muntinlupa - WISE System",
    )

    styles = {
        "DocTitle": ParagraphStyle(
            "DocTitle", fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=_COLOR_NAVY, spaceAfter=2,
        ),
        "DocSub": ParagraphStyle(
            "DocSub", fontName="Helvetica-Bold", fontSize=9.5, leading=12, textColor=_COLOR_ACCENT, spaceAfter=8,
        ),
        "CardVal": ParagraphStyle(
            "CardVal", fontName="Helvetica-Bold", fontSize=11.5, leading=13.5, textColor=_COLOR_NAVY, alignment=TA_CENTER,
        ),
        "CardLbl": ParagraphStyle(
            "CardLbl", fontName="Helvetica", fontSize=6.8, leading=8.5, textColor=_COLOR_MUTED, alignment=TA_CENTER,
        ),
        "SecBannerTitle": ParagraphStyle(
            "SecBannerTitle", fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=_COLOR_NAVY,
        ),
        "CalloutTitle": ParagraphStyle(
            "CalloutTitle", fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=_COLOR_BLUE,
        ),
        "CalloutBody": ParagraphStyle(
            "CalloutBody", fontName="Helvetica", fontSize=7.8, leading=10.5, textColor=_COLOR_NAVY,
        ),
        "SecHeader": ParagraphStyle(
            "SecHeader", fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=_COLOR_NAVY, spaceBefore=4, spaceAfter=2,
        ),
        "SecSub": ParagraphStyle(
            "SecSub", fontName="Helvetica", fontSize=8, leading=10, textColor=_COLOR_MUTED, spaceAfter=5,
        ),
        "TH": ParagraphStyle(
            "TH", fontName="Helvetica-Bold", fontSize=7.5, leading=9.5, textColor=_COLOR_WHITE, alignment=TA_CENTER,
        ),
        "TD_L": ParagraphStyle(
            "TD_L", fontName="Helvetica", fontSize=7.2, leading=9, textColor=_COLOR_NAVY, alignment=TA_LEFT,
        ),
        "TD_R": ParagraphStyle(
            "TD_R", fontName="Helvetica", fontSize=7.2, leading=9, textColor=_COLOR_NAVY, alignment=TA_RIGHT,
        ),
        "TD_C": ParagraphStyle(
            "TD_C", fontName="Helvetica", fontSize=7.2, leading=9, textColor=_COLOR_NAVY, alignment=TA_CENTER,
        ),
        "TOT_L": ParagraphStyle(
            "TOT_L", fontName="Helvetica-Bold", fontSize=7.4, leading=9.5, textColor=_COLOR_NAVY, alignment=TA_LEFT,
        ),
        "TOT_R": ParagraphStyle(
            "TOT_R", fontName="Helvetica-Bold", fontSize=7.4, leading=9.5, textColor=_COLOR_NAVY, alignment=TA_RIGHT,
        ),
        "TOT_C": ParagraphStyle(
            "TOT_C", fontName="Helvetica-Bold", fontSize=7.4, leading=9.5, textColor=_COLOR_NAVY, alignment=TA_CENTER,
        ),
        "Empty": ParagraphStyle(
            "Empty", fontName="Helvetica-Oblique", fontSize=8.2, leading=11, textColor=_COLOR_MUTED, alignment=TA_CENTER, spaceBefore=5, spaceAfter=5,
        ),
    }

    story = []

    # 1. Executive Title Block
    story.append(Paragraph("City Waste Management & Operations Report", styles["DocTitle"]))
    story.append(Paragraph(f"Reporting Period: {label} · Executive Administrative Performance Overview", styles["DocSub"]))

    # 2. Executive Metric Cards (6 KPI Cards in a row)
    card_width = (_CONTENT_W / 6) - 2 * mm
    cards_data = [
        ("Total Routes", str(summary["total_collections"]), "#EFF6FF", "#1D4ED8"),
        ("Completed Routes", str(summary["completed_collections"]), "#ECFDF5", "#047857"),
        ("Completion Rate", f"{summary['completion_rate']:.1f}%", "#F5F3FF", "#6D28D9"),
        ("Total Waste Scans", str(summary["waste_classifications"]), "#ECFEFF", "#0E7490"),
        ("Flagged Scans", f"{summary['flagged_count']} ({summary['flagged_rate']:.1f}%)", "#FEF2F2", "#B91C1C"),
        ("Avg AI Accuracy", f"{summary['avg_confidence']:.1f}%" if summary["avg_confidence"] is not None else "—", "#FFFBEB", "#B45309"),
    ]

    card_cells = []
    for lbl, val, bg_color, text_color in cards_data:
        val_style = ParagraphStyle(f"CV_{lbl}", parent=styles["CardVal"], textColor=colors.HexColor(text_color))
        card_table = Table(
            [[Paragraph(val, val_style)], [Paragraph(lbl, styles["CardLbl"])]],
            colWidths=[card_width],
            rowHeights=[6.5 * mm, 4.5 * mm],
        )
        card_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg_color)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 1.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("ROUNDEDCORNERS", [3, 3, 3, 3]),
        ]))
        card_cells.append(card_table)

    cards_row = Table([card_cells], colWidths=[_CONTENT_W / 6] * 6, rowHeights=[12.5 * mm])
    cards_row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 1),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(cards_row)
    story.append(Spacer(1, 6))

    # Executive Narrative Brief Box
    story.append(
        _build_pdf_callout_box(
            f"Executive Narrative Brief — {label}",
            get_executive_narrative(summary),
            "#EFF6FF",
            "#1E40AF",
            "#0F172A",
            styles,
        )
    )
    story.append(Spacer(1, 10))

    # 3. Report Section Tables
    for idx, t in enumerate(types):
        if t not in PDF_HEADERS:
            continue

        headers = PDF_HEADERS[t]
        type_rows = data.get(t, [])
        type_label = TYPE_LABELS.get(t, t.replace("_", " ").title())
        col_widths = _get_pdf_col_widths(t)
        narrative = get_section_narratives(t, label, summary, type_rows)
        rec_count_str = f"{len(type_rows)} record{'s' if len(type_rows) != 1 else ''}" if type_rows else "No records"

        if idx > 0:
            story.append(PageBreak())

        banner = _build_pdf_section_banner(
            type_label,
            f"{rec_count_str} · {label}",
            narrative["about"],
            narrative["insight"],
            styles,
        )
        story.append(KeepTogether([banner, Spacer(1, 6)]))

        # Build Table Rows
        header_cells = [Paragraph(h, styles["TH"]) for h in headers]
        table_rows = [header_cells]

        if not type_rows:
            empty_cell = Paragraph("No records found for the selected period.", styles["Empty"])
            table_rows.append([empty_cell] + [""] * (len(headers) - 1))
        else:
            for row in type_rows:
                cells = []
                for c_idx, val in enumerate(row):
                    if c_idx >= len(headers):
                        break
                    val_str = str(val) if val is not None else "—"
                    cells.append(_style_pdf_cell(val_str, styles))
                while len(cells) < len(headers):
                    cells.append(Paragraph("—", styles["TD_C"]))
                table_rows.append(cells)

        totals_vals = _pdf_totals_row(t, type_rows) if type_rows else None
        has_totals = totals_vals is not None
        if has_totals:
            tot_cells = []
            for c_idx, val_str in enumerate(totals_vals):
                if val_str.endswith("%") or (val_str.isdigit() and len(val_str) <= 6):
                    st = styles["TOT_R"]
                elif val_str in ("—", "All Categories") or val_str.endswith("Day(s)"):
                    st = styles["TOT_C"] if val_str == "—" else styles["TOT_L"]
                else:
                    st = styles["TOT_L"]
                tot_cells.append(Paragraph(_xml_escape(val_str), st))
            table_rows.append(tot_cells)

        table = Table(table_rows, colWidths=col_widths, repeatRows=1)

        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), _COLOR_BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), _COLOR_WHITE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.4, _COLOR_BORDER),
            ("LINEBELOW", (0, 0), (-1, 0), 1.2, _COLOR_ACCENT),
            ("TOPPADDING", (0, 0), (-1, -1), 3.2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
            ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
        ]

        if type_rows:
            body_end = len(table_rows) - (1 if has_totals else 0)
            for r_i in range(1, body_end):
                if r_i % 2 == 0:
                    style_cmds.append(("BACKGROUND", (0, r_i), (-1, r_i), _COLOR_ALT_ROW))
            if has_totals:
                tot_r_idx = len(table_rows) - 1
                style_cmds.append(("BACKGROUND", (0, tot_r_idx), (-1, tot_r_idx), colors.HexColor("#F1F5F9")))
                style_cmds.append(("LINEABOVE", (0, tot_r_idx), (-1, tot_r_idx), 1.0, _COLOR_BLUE))
                style_cmds.append(("LINEBELOW", (0, tot_r_idx), (-1, tot_r_idx), 1.2, _COLOR_NAVY))
        else:
            style_cmds.append(("SPAN", (0, 1), (-1, 1)))
            style_cmds.append(("ALIGN", (0, 1), (-1, 1), "CENTER"))

        table.setStyle(TableStyle(style_cmds))
        story.append(table)
        story.append(Spacer(1, 8))

        # Waste Classification Flagged Meaning + Audit Log
        if t in ("waste_trends", "environmental_impact"):
            story.append(
                _build_pdf_callout_box(
                    "Understanding Flagged Waste Classifications",
                    FLAGGED_EXPLANATION_TEXT,
                    "#FFFBEB",
                    "#D97706",
                    "#78350F",
                    styles,
                )
            )
            story.append(Spacer(1, 7))
            story.append(
                Paragraph(
                    f"Flagged Classifications Audit Log ({len(flagged_rows)} record{'s' if len(flagged_rows) != 1 else ''})",
                    styles["SecHeader"],
                )
            )
            audit_props = [24, 34, 24, 16, 36, 22]
            audit_tot_p = sum(audit_props)
            audit_widths = [(p / audit_tot_p) * _CONTENT_W for p in audit_props]
            audit_table_rows = [[Paragraph(h, styles["TH"]) for h in FLAGGED_AUDIT_HEADERS]]
            if not flagged_rows:
                audit_table_rows.append(
                    [Paragraph("Zero flagged classifications recorded in this period. All AI scans met quality checks.", styles["Empty"])]
                    + [""] * (len(FLAGGED_AUDIT_HEADERS) - 1)
                )
            else:
                for a_row in flagged_rows:
                    a_cells = []
                    for c_i, val in enumerate(a_row):
                        v_s = str(val) if val is not None else "—"
                        if c_i == 0:
                            a_cells.append(_style_pdf_cell(v_s, styles, styles["TD_C"]))
                        elif c_i == 3:
                            a_cells.append(_style_pdf_cell(v_s, styles, styles["TD_R"]))
                        else:
                            a_cells.append(_style_pdf_cell(v_s, styles))
                    audit_table_rows.append(a_cells)

            audit_tbl = Table(audit_table_rows, colWidths=audit_widths, repeatRows=1)
            audit_cmds = [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#92400E")),
                ("TEXTCOLOR", (0, 0), (-1, 0), _COLOR_WHITE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.4, _COLOR_BORDER),
                ("LINEBELOW", (0, 0), (-1, 0), 1.1, colors.HexColor("#D97706")),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
            ]
            if flagged_rows:
                for r_i in range(1, len(audit_table_rows)):
                    if r_i % 2 == 0:
                        audit_cmds.append(("BACKGROUND", (0, r_i), (-1, r_i), _COLOR_ALT_ROW))
            else:
                audit_cmds.append(("SPAN", (0, 1), (-1, 1)))
                audit_cmds.append(("ALIGN", (0, 1), (-1, 1), "CENTER"))
            audit_tbl.setStyle(TableStyle(audit_cmds))
            story.append(audit_tbl)
            story.append(Spacer(1, 8))

    # 4. Collector Personnel Assignment & Operational Status Section (PDF-only)
    collector_pdf = _collector_pdf_report_data(db, start_ph, end_ph)
    roster_headers = collector_pdf["roster_headers"]
    roster_rows = collector_pdf["roster_rows"]
    route_log_headers = collector_pdf["route_log_headers"]
    route_log_rows = collector_pdf["route_log_rows"]
    col_narrative = get_section_narratives("collector_operations", label, summary, [], collector_pdf=collector_pdf)

    story.append(PageBreak())
    col_banner = _build_pdf_section_banner(
        "Collector Personnel Assignment & Operational Status",
        f"{len(roster_rows)} Collector(s) · {len(route_log_rows)} Assigned Route(s) · {label}",
        col_narrative["about"],
        col_narrative["insight"],
        styles,
    )
    story.append(KeepTogether([col_banner, Spacer(1, 6)]))

    # 4A. Collector Roster & Workload Status Summary Table
    roster_props = [26, 30, 28, 16, 15, 16, 20, 17, 22]
    roster_tot_p = sum(roster_props)
    roster_col_widths = [(p / roster_tot_p) * _CONTENT_W for p in roster_props]

    roster_table_rows = [[Paragraph(h, styles["TH"]) for h in roster_headers]]
    if not roster_rows:
        roster_table_rows.append(
            [Paragraph("No registered collectors or assigned personnel found for the selected period.", styles["Empty"])]
            + [""] * (len(roster_headers) - 1)
        )
    else:
        for row in roster_rows:
            cells = []
            for c_idx, val in enumerate(row):
                val_str = str(val) if val is not None else "—"
                if c_idx in (3, 4, 7):
                    c_style = styles["TD_R"]
                elif c_idx in (5, 6, 8):
                    c_style = styles["TD_C"]
                else:
                    c_style = styles["TD_L"]
                cells.append(_style_pdf_cell(val_str, styles, c_style))
            roster_table_rows.append(cells)

    roster_table = Table(roster_table_rows, colWidths=roster_col_widths, repeatRows=1)
    roster_style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), _COLOR_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), _COLOR_WHITE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, _COLOR_BORDER),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, _COLOR_ACCENT),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
    ]
    if roster_rows:
        for r_i in range(1, len(roster_table_rows)):
            if r_i % 2 == 0:
                roster_style_cmds.append(("BACKGROUND", (0, r_i), (-1, r_i), _COLOR_ALT_ROW))
    else:
        roster_style_cmds.append(("SPAN", (0, 1), (-1, 1)))
        roster_style_cmds.append(("ALIGN", (0, 1), (-1, 1), "CENTER"))
    roster_table.setStyle(TableStyle(roster_style_cmds))
    story.append(roster_table)
    story.append(Spacer(1, 12))

    # 4B. Assigned Collector Route & Stop Confirmation Log Table
    story.append(Paragraph("Assigned Collector Route & Stop Confirmation Log", styles["SecHeader"]))
    story.append(
        Paragraph(
            f"Detailed stop-level arrival confirmation and field verification remarks per assigned collector for {label}.",
            styles["SecSub"],
        )
    )

    log_props = [22, 26, 26, 26, 22, 18, 42]
    log_tot_p = sum(log_props)
    log_col_widths = [(p / log_tot_p) * _CONTENT_W for p in log_props]

    log_table_rows = [[Paragraph(h, styles["TH"]) for h in route_log_headers]]
    if not route_log_rows:
        log_table_rows.append(
            [Paragraph("No scheduled collection routes found for the selected period.", styles["Empty"])]
            + [""] * (len(route_log_headers) - 1)
        )
    else:
        for row in route_log_rows:
            cells = []
            for c_idx, val in enumerate(row):
                val_str = str(val) if val is not None else "—"
                if c_idx in (0, 4, 5):
                    c_style = styles["TD_C"]
                else:
                    c_style = styles["TD_L"]
                cells.append(_style_pdf_cell(val_str, styles, c_style))
            log_table_rows.append(cells)

    log_table = Table(log_table_rows, colWidths=log_col_widths, repeatRows=1)
    log_style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), _COLOR_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), _COLOR_WHITE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, _COLOR_BORDER),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, _COLOR_ACCENT),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
    ]
    if route_log_rows:
        for r_i in range(1, len(log_table_rows)):
            if r_i % 2 == 0:
                log_style_cmds.append(("BACKGROUND", (0, r_i), (-1, r_i), _COLOR_ALT_ROW))
    else:
        log_style_cmds.append(("SPAN", (0, 1), (-1, 1)))
        log_style_cmds.append(("ALIGN", (0, 1), (-1, 1), "CENTER"))
    log_table.setStyle(TableStyle(log_style_cmds))
    story.append(log_table)
    story.append(Spacer(1, 10))

    def canvas_maker(*args, **kwargs):
        c = NumberedCanvas(*args, **kwargs)
        c.period_label = label
        c.generated_at = generated_at
        return c

    doc.build(story, canvasmaker=canvas_maker)
    return buf.getvalue()

# ---------------------------------------------------------------------------
# Barangay Recycling & AI Classification Leaderboard
# ---------------------------------------------------------------------------

def get_barangay_recycling_leaderboard(db: Session, year: int | None = None, month: int | None = None) -> dict:
    """Computes the Barangay Recycling & AI Classification Leaderboard.

    Accurately classifies waste submissions into:
      - Recyclable
      - Biodegradable
      - Residual
      - Hazardous
      - Electronic
    """
    if year:
        start_ph, end_ph = resolve_period(year, month)
    else:
        now_ph = ph_now()
        start_ph = datetime(2020, 1, 1, 0, 0, 0)
        end_ph = datetime(now_ph.year, 12, 31, 23, 59, 59)

    start_utc, end_utc = ph_to_utc_range(start_ph, end_ph)

    stats: dict[str, dict] = {}
    for b in OFFICIAL_MUNTINLUPA_BARANGAYS:
        stats[b] = {
            "total_scans": 0,
            "recyclable": 0,
            "biodegradable": 0,
            "residual": 0,
            "hazardous": 0,
            "electronic": 0,
            "residual_special": 0,
            "conf_sum": 0.0,
            "conf_count": 0,
        }

    records = (
        db.query(WasteRecord, User)
        .join(User, WasteRecord.user_id == User.id, isouter=True)
        .filter(
            WasteRecord.classified_at >= start_utc,
            WasteRecord.classified_at <= end_utc,
        )
        .all()
    )

    total_city_scans = 0
    total_city_recyc = 0
    total_city_bio = 0

    for waste, user in records:
        raw_b = user.barangay if user and user.barangay else None
        if not raw_b:
            continue
        matched_b = normalize_barangay(raw_b) or raw_b

        if matched_b not in stats:
            stats[matched_b] = {
                "total_scans": 0,
                "recyclable": 0,
                "biodegradable": 0,
                "residual": 0,
                "hazardous": 0,
                "electronic": 0,
                "residual_special": 0,
                "conf_sum": 0.0,
                "conf_count": 0,
            }

        stats[matched_b]["total_scans"] += 1
        total_city_scans += 1

        wt_lower = (waste.waste_type or "").lower()
        cat_lower = (waste.disposal_category or "").lower()

        # Categorize
        if any(c in cat_lower for c in ["electronic", "e-waste", "ewaste"]) or any(t in wt_lower for t in ["electronic", "e-waste", "ewaste", "appliance", "battery", "gadget", "phone", "cable", "charger"]):
            stats[matched_b]["electronic"] += 1
            stats[matched_b]["residual_special"] += 1
        elif any(c in cat_lower for c in ["hazard", "toxic"]) or any(t in wt_lower for t in ["hazard", "toxic", "chemical", "paint", "syringe", "medical"]):
            stats[matched_b]["hazardous"] += 1
            stats[matched_b]["residual_special"] += 1
        elif "recycl" in cat_lower or any(t in wt_lower for t in ["plastic", "paper", "cardboard", "metal", "glass", "can", "bottle", "carton", "box"]):
            stats[matched_b]["recyclable"] += 1
            total_city_recyc += 1
        elif any(c in cat_lower for c in ["bio", "compost", "organic"]) or any(t in wt_lower for t in ["food", "fruit", "veg", "leaf", "plant", "peel"]):
            stats[matched_b]["biodegradable"] += 1
            total_city_bio += 1
        else:
            stats[matched_b]["residual"] += 1
            stats[matched_b]["residual_special"] += 1

        if waste.confidence is not None:
            stats[matched_b]["conf_sum"] += float(waste.confidence)
            stats[matched_b]["conf_count"] += 1

    items = []
    for b, s in stats.items():
        total = s["total_scans"]
        recyc = s["recyclable"]
        bio = s["biodegradable"]
        resid = s["residual"]
        haz = s["hazardous"]
        elec = s["electronic"]
        resid_special = s["residual_special"]
        conf_count = s["conf_count"]
        conf_sum = s["conf_sum"]

        if total > 0:
            sort_rate = round(((recyc + bio) / total) * 100, 1)
            avg_conf = round(conf_sum / conf_count, 1) if conf_count > 0 else 85.0
        else:
            sort_rate = 0.0
            avg_conf = 0.0

        items.append({
            "barangay": b,
            "total_scans": total,
            "proper_sorting_rate": sort_rate,
            "recyclable_count": recyc,
            "biodegradable_count": bio,
            "residual_count": resid,
            "hazardous_count": haz,
            "electronic_count": elec,
            "residual_special_count": resid_special,
            "avg_confidence": avg_conf,
        })

    # Sort leaderboard by proper sorting rate then total scans
    items.sort(key=lambda x: (x["proper_sorting_rate"], x["total_scans"]), reverse=True)

    leaderboard = []
    award_titles = {
        1: ("Gold", "🥇 1st Place - Top Segregation"),
        2: ("Silver", "🥈 2nd Place - High Compliance"),
        3: ("Bronze", "🥉 3rd Place - Commended Sorter"),
    }

    for idx, item in enumerate(items, start=1):
        if item["total_scans"] > 0:
            tier_info = award_titles.get(idx, ("Standard", "Compliant"))
        else:
            tier_info = ("Standard", "No Submissions")

        leaderboard.append({
            "rank": idx,
            "barangay": item["barangay"],
            "total_scans": item["total_scans"],
            "proper_sorting_rate": item["proper_sorting_rate"],
            "recyclable_count": item["recyclable_count"],
            "biodegradable_count": item["biodegradable_count"],
            "residual_count": item["residual_count"],
            "hazardous_count": item["hazardous_count"],
            "electronic_count": item["electronic_count"],
            "residual_special_count": item["residual_special_count"],
            "avg_confidence": item["avg_confidence"],
            "award_tier": tier_info[0],
            "badge_title": tier_info[1],
        })

    citywide_rate = (
        round(((total_city_recyc + total_city_bio) / total_city_scans) * 100, 1)
        if total_city_scans > 0
        else 0.0
    )
    top_performer = leaderboard[0]["barangay"] if (leaderboard and leaderboard[0]["total_scans"] > 0) else None

    return {
        "period": period_label(year, month) if year else "All Time (Cumulative)",
        "top_performer": top_performer,
        "citywide_sorting_rate": citywide_rate,
        "total_classifications": total_city_scans,
        "total_recyclables": total_city_recyc,
        "total_biodegradable": total_city_bio,
        "leaderboard": leaderboard,
    }

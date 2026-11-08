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
import os
from datetime import date, datetime, time, timedelta

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
    HRFlowable,
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

from apps.models.collection_schedule import CollectionSchedule
from apps.models.collection_stop import CollectionStop
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

LOW_CONFIDENCE_THRESHOLD = 50.0

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
            if getattr(st, "delay_reason", None):
                stop_notes.append(f"{st.stop_name}: Delayed ({st.delay_reason})")
            elif getattr(st, "cancellation_reason", None):
                stop_notes.append(f"{st.stop_name}: Cancelled ({st.cancellation_reason})")
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

# Backward compatibility functions
def waste_stats(db: Session, start: datetime, end: datetime) -> dict:
    summary = get_report_summary_data(db, start.year, start.month if start.month == end.month else None)
    return {
        "total_records": summary["waste_classifications"],
        "total_classified": summary["waste_classifications"],
        "avg_confidence": summary["avg_confidence"],
        "low_confidence": summary["low_confidence_count"],
        "per_class": summary["per_class"],
        "most_common_type": summary["most_common_type"],
        "most_common_count": summary["most_common_count"],
    }

def schedule_stats(db: Session, start: datetime, end: datetime) -> dict:
    summary = get_report_summary_data(db, start.year, start.month if start.month == end.month else None)
    return {
        "total_routes": summary["total_collections"],
        "completed": summary["completed_collections"],
        "cancelled": summary["cancelled_collections"],
        "delayed": summary["delayed_collections"],
        "upcoming": summary["upcoming_collections"],
        "completion_rate": summary["completion_rate"],
        "status_breakdown": {
            "Completed": summary["completed_collections"],
            "Delayed": summary["delayed_collections"],
            "Cancelled": summary["cancelled_collections"],
            "Upcoming": summary["upcoming_collections"],
        },
    }

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

def build_excel(
    db: Session,
    year: int,
    month: int | None,
    types: list[str],
    generated_at: datetime | None = None,
) -> bytes:
    """Generate a publication-grade .xlsx workbook with Executive Summary + Data Sheets."""
    generated_at = generated_at or ph_now()
    start_ph, end_ph = resolve_period(year, month)
    summary = get_report_summary_data(db, year, month)
    data = _data_for(db, start_ph, end_ph, types)

    wb = Workbook()

    # Styling definitions
    NAVY_FILL = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    BLUE_FILL = PatternFill(start_color="1E40AF", end_color="1E40AF", fill_type="solid")
    SECTION_FILL = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    ALT_FILL = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

    TITLE_FONT = Font(bold=True, size=16, color="0F172A", name="Calibri")
    SUBTITLE_FONT = Font(bold=True, size=11, color="2563EB", name="Calibri")
    SECTION_FONT = Font(bold=True, size=11, color="1E3A8A", name="Calibri")
    HEADER_FONT = Font(bold=True, size=11, color="FFFFFF", name="Calibri")
    BODY_FONT = Font(size=10.5, name="Calibri")
    BODY_BOLD = Font(bold=True, size=10.5, name="Calibri")
    MUTED_FONT = Font(italic=True, size=9.5, color="64748B", name="Calibri")

    THIN_BORDER_SIDE = Side(style="thin", color="CBD5E1")
    DOUBLE_BOTTOM_SIDE = Side(style="double", color="1E40AF")
    DATA_BORDER = Border(left=THIN_BORDER_SIDE, right=THIN_BORDER_SIDE, top=THIN_BORDER_SIDE, bottom=THIN_BORDER_SIDE)
    TOTAL_BORDER = Border(top=THIN_BORDER_SIDE, bottom=DOUBLE_BOTTOM_SIDE, left=THIN_BORDER_SIDE, right=THIN_BORDER_SIDE)

    ALIGN_LEFT = Alignment(horizontal="left", vertical="center")
    ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")
    ALIGN_CENTER = Alignment(horizontal="center", vertical="center")

    # -----------------------------------------------------------------------
    # Sheet 1: Executive Summary
    # -----------------------------------------------------------------------
    ws_sum = wb.active
    ws_sum.title = "Executive Summary"
    ws_sum.views.sheetView[0].showGridLines = True

    # Title Banner
    c = ws_sum.cell(row=1, column=1, value="CITY OF MUNTINLUPA — WISE SYSTEM")
    c.font = Font(bold=True, size=14, color="1E40AF", name="Calibri")

    c = ws_sum.cell(row=2, column=1, value="Official Administrative Waste & Operations Report")
    c.font = TITLE_FONT

    c = ws_sum.cell(row=3, column=1, value=f"Reporting Period: {summary['period']}")
    c.font = SUBTITLE_FONT

    curr_row = 5
    summary_sections = [
        (
            "Overall Collection Route Performance",
            [
                ("Total Routes Scheduled", summary["total_collections"], "#,##0"),
                ("Routes Completed Successfully", summary["completed_collections"], "#,##0"),
                ("Routes Delayed / Issues", summary["delayed_collections"], "#,##0"),
                ("Routes Cancelled", summary["cancelled_collections"], "#,##0"),
                ("Routes Upcoming / Pending", summary["upcoming_collections"], "#,##0"),
                ("Overall Fleet Completion Rate", summary["completion_rate"] / 100.0, "0.0%"),
            ],
        ),
        (
            "Waste Classification & Recycling Metrics",
            [
                ("Total AI Waste Classifications", summary["waste_classifications"], "#,##0"),
                ("Properly Sorted Recyclables Count", summary["recyclable_count"], "#,##0"),
                ("Citywide Recycling / Diversion Rate", summary["recycling_rate"] / 100.0, "0.0%"),
                ("Average AI Confidence Accuracy", (summary["avg_confidence"] / 100.0 if summary["avg_confidence"] is not None else None), "0.0%"),
                ("Low Confidence Classifications (<50%)", summary["low_confidence_count"], "#,##0"),
                ("Most Predominant Waste Type", summary["most_common_type"] or "—", None),
            ],
        ),
        (
            "Barangay Operational Overview",
            [
                ("Top Performing Service Barangay", summary["top_barangay"], None),
                ("Total Active Service Barangays", summary["total_barangays"], "#,##0"),
                ("Average Barangay Completion Rate", summary["efficiency_score"] / 100.0, "0.0%"),
                ("New Citizen Registrations in Period", summary["new_users"], "#,##0"),
            ],
        ),
    ]

    for sec_title, items in summary_sections:
        ws_sum.merge_cells(start_row=curr_row, start_column=1, end_row=curr_row, end_column=2)
        sec_c = ws_sum.cell(row=curr_row, column=1, value=sec_title)
        sec_c.font = SECTION_FONT
        sec_c.fill = SECTION_FILL
        curr_row += 1

        for label, val, num_fmt in items:
            c1 = ws_sum.cell(row=curr_row, column=1, value=label)
            c2 = ws_sum.cell(row=curr_row, column=2, value=val)
            c1.font = BODY_FONT
            c2.font = BODY_BOLD
            c1.border = DATA_BORDER
            c2.border = DATA_BORDER
            c1.alignment = ALIGN_LEFT
            c2.alignment = ALIGN_RIGHT if isinstance(val, (int, float)) else ALIGN_LEFT
            if num_fmt and isinstance(val, (int, float)):
                c2.number_format = num_fmt
            curr_row += 1
        curr_row += 1

    gen_cell = ws_sum.cell(row=curr_row, column=1, value=f"Report Generated: {generated_at.strftime('%Y-%m-%d %H:%M')} (PST) · Official Document")
    gen_cell.font = MUTED_FONT
    ws_sum.column_dimensions["A"].width = 42
    ws_sum.column_dimensions["B"].width = 24

    # -----------------------------------------------------------------------
    # Data Sheets for Checked Report Types
    # -----------------------------------------------------------------------
    for t in types:
        if t not in EXCEL_HEADERS:
            continue
        headers = EXCEL_HEADERS[t]
        type_rows = data.get(t, [])
        sheet_title = TYPE_SHEET_NAMES.get(t, t[:31])
        ws = wb.create_sheet(title=sheet_title)
        ws.views.sheetView[0].showGridLines = True

        # Header Row
        ws.row_dimensions[1].height = 26
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = HEADER_FONT
            cell.fill = BLUE_FILL
            cell.border = DATA_BORDER
            cell.alignment = ALIGN_CENTER

        if not type_rows:
            empty_cell = ws.cell(row=2, column=1, value="No records found for the selected period.")
            empty_cell.font = MUTED_FONT
            ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))
            ws.row_dimensions[2].height = 22
        else:
            for row_idx, r_data in enumerate(type_rows, start=2):
                ws.row_dimensions[row_idx].height = 20
                for col_idx, val in enumerate(r_data, start=1):
                    if col_idx > len(headers):
                        break
                    cell = ws.cell(row=row_idx, column=col_idx)
                    cell.border = DATA_BORDER
                    cell.font = BODY_FONT

                    # Alternating row fill
                    if row_idx % 2 == 1:
                        cell.fill = ALT_FILL

                    # Format cell based on type
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
                    else:
                        cell.value = val if val is not None else "—"
                        cell.alignment = ALIGN_LEFT

            # Add Total Row for quantitative tables
            last_row = len(type_rows) + 2
            if t == "barangay_performance":
                ws.row_dimensions[last_row].height = 22
                c_lbl = ws.cell(row=last_row, column=1, value="City Total")
                c_lbl.font = BODY_BOLD
                c_lbl.border = TOTAL_BORDER
                # Formulas for totals
                for c_idx in (2, 3, 4, 5, 7):
                    col_let = get_column_letter(c_idx)
                    tot_c = ws.cell(row=last_row, column=c_idx, value=f"=SUM({col_let}2:{col_let}{last_row-1})")
                    tot_c.font = BODY_BOLD
                    tot_c.border = TOTAL_BORDER
                    tot_c.alignment = ALIGN_RIGHT
                    tot_c.number_format = "#,##0"
                # Average completion rate
                comp_col = get_column_letter(6)
                tot_comp = ws.cell(row=last_row, column=6, value=f"=AVERAGE({comp_col}2:{comp_col}{last_row-1})")
                tot_comp.font = BODY_BOLD
                tot_comp.border = TOTAL_BORDER
                tot_comp.alignment = ALIGN_RIGHT
                tot_comp.number_format = "0.0%"
                # Average confidence
                tot_conf = ws.cell(row=last_row, column=8, value="—")
                tot_conf.font = BODY_BOLD
                tot_conf.border = TOTAL_BORDER
                tot_conf.alignment = ALIGN_RIGHT

            elif t in ("waste_trends", "environmental_impact"):
                ws.row_dimensions[last_row].height = 22
                c_lbl = ws.cell(row=last_row, column=1, value="Total Submissions")
                c_lbl.font = BODY_BOLD
                c_lbl.border = TOTAL_BORDER
                ws.cell(row=last_row, column=2, value="").border = TOTAL_BORDER
                # Sum submissions
                tot_sub = ws.cell(row=last_row, column=3, value=f"=SUM(C2:C{last_row-1})")
                tot_sub.font = BODY_BOLD
                tot_sub.border = TOTAL_BORDER
                tot_sub.alignment = ALIGN_RIGHT
                tot_sub.number_format = "#,##0"
                # Total pct = 100%
                tot_pct = ws.cell(row=last_row, column=4, value=f"=SUM(D2:D{last_row-1})")
                tot_pct.font = BODY_BOLD
                tot_pct.border = TOTAL_BORDER
                tot_pct.alignment = ALIGN_RIGHT
                tot_pct.number_format = "0.0%"
                # Avg conf
                tot_conf = ws.cell(row=last_row, column=5, value=f"=AVERAGE(E2:E{last_row-1})")
                tot_conf.font = BODY_BOLD
                tot_conf.border = TOTAL_BORDER
                tot_conf.alignment = ALIGN_RIGHT
                tot_conf.number_format = "0.0%"
                # Sum flagged
                tot_flg = ws.cell(row=last_row, column=6, value=f"=SUM(F2:F{last_row-1})")
                tot_flg.font = BODY_BOLD
                tot_flg.border = TOTAL_BORDER
                tot_flg.alignment = ALIGN_RIGHT
                tot_flg.number_format = "#,##0"

            elif t in ("notification_analytics", "notification_effectiveness"):
                ws.row_dimensions[last_row].height = 22
                c_lbl = ws.cell(row=last_row, column=1, value="Total")
                c_lbl.font = BODY_BOLD
                c_lbl.border = TOTAL_BORDER
                for c_idx in (2, 3, 4):
                    col_let = get_column_letter(c_idx)
                    tot_c = ws.cell(row=last_row, column=c_idx, value=f"=SUM({col_let}2:{col_let}{last_row-1})")
                    tot_c.font = BODY_BOLD
                    tot_c.border = TOTAL_BORDER
                    tot_c.alignment = ALIGN_RIGHT
                    tot_c.number_format = "#,##0"
                tot_eng = ws.cell(row=last_row, column=5, value=f"=AVERAGE(E2:E{last_row-1})")
                tot_eng.font = BODY_BOLD
                tot_eng.border = TOTAL_BORDER
                tot_eng.alignment = ALIGN_RIGHT
                tot_eng.number_format = "0.0%"
                ws.cell(row=last_row, column=6, value="—").border = TOTAL_BORDER

        # Auto-fit Column Widths cleanly
        for col_idx in range(1, len(headers) + 1):
            col_letter = get_column_letter(col_idx)
            max_len = len(str(headers[col_idx - 1]))
            for r in type_rows:
                if col_idx - 1 < len(r):
                    val_str = str(r[col_idx - 1] if r[col_idx - 1] is not None else "")
                    max_len = max(max_len, len(val_str))
            ws.column_dimensions[col_letter].width = max(max_len + 4, 14)

        # Freeze Panes & AutoFilter
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(len(type_rows) + 1, 2)}"

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


def build_pdf(
    db: Session,
    year: int,
    month: int | None,
    types: list[str],
    generated_at: datetime | None = None,
) -> bytes:
    """Generate a clean, professional landscape A4 PDF report with proper pagination and typography."""
    generated_at = generated_at or ph_now()
    start_ph, end_ph = resolve_period(year, month)
    summary = get_report_summary_data(db, year, month)
    data = _data_for(db, start_ph, end_ph, types)
    label = period_label(year, month)

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
            "CardVal", fontName="Helvetica-Bold", fontSize=12, leading=14, textColor=_COLOR_NAVY, alignment=TA_CENTER,
        ),
        "CardLbl": ParagraphStyle(
            "CardLbl", fontName="Helvetica", fontSize=7, leading=8.5, textColor=_COLOR_MUTED, alignment=TA_CENTER,
        ),
        "SecHeader": ParagraphStyle(
            "SecHeader", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=_COLOR_NAVY, spaceBefore=4, spaceAfter=2,
        ),
        "SecSub": ParagraphStyle(
            "SecSub", fontName="Helvetica", fontSize=8, leading=10, textColor=_COLOR_MUTED, spaceAfter=6,
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
        "Empty": ParagraphStyle(
            "Empty", fontName="Helvetica-Oblique", fontSize=8.5, leading=11, textColor=_COLOR_MUTED, alignment=TA_CENTER, spaceBefore=6, spaceAfter=6,
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
        ("Waste Scans", str(summary["waste_classifications"]), "#FFFBEB", "#B45309"),
        ("Recycling Rate", f"{summary['recycling_rate']:.1f}%", "#ECFEFF", "#0E7490"),
        ("Avg AI Accuracy", f"{summary['avg_confidence']:.1f}%" if summary["avg_confidence"] is not None else "—", "#FEF2F2", "#B91C1C"),
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
    story.append(Spacer(1, 10))

    # 3. Report Section Tables
    for idx, t in enumerate(types):
        if t not in PDF_HEADERS:
            continue

        headers = PDF_HEADERS[t]
        type_rows = data.get(t, [])
        type_label = TYPE_LABELS.get(t, t.replace("_", " ").title())
        col_widths = _get_pdf_col_widths(t)

        # Start on page break only from section 2 onwards (or if section 1 is long)
        if idx > 0:
            story.append(PageBreak())

        # Section Heading
        story.append(Paragraph(type_label, styles["SecHeader"]))
        rec_count_str = f"{len(type_rows)} record{'s' if len(type_rows) != 1 else ''}" if type_rows else "No records"
        story.append(Paragraph(f"{rec_count_str} recorded for {label} · Verified Data", styles["SecSub"]))

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
                    # Alignment logic: text vs numbers vs dates
                    if val_str.endswith("%") or (val_str.isdigit() and len(val_str) <= 6):
                        cell_style = styles["TD_R"]
                    elif val_str.startswith("20") and len(val_str) == 10:
                        cell_style = styles["TD_C"]
                    elif val_str in ("Completed", "Complete", "Scheduled", "In Progress", "Delayed", "Cancelled", "Active", "Verified", "Flagged"):
                        cell_style = styles["TD_C"]
                    else:
                        cell_style = styles["TD_L"]
                    cells.append(Paragraph(val_str, cell_style))
                while len(cells) < len(headers):
                    cells.append(Paragraph("—", styles["TD_C"]))
                table_rows.append(cells)

        table = Table(table_rows, colWidths=col_widths, repeatRows=1)

        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), _COLOR_BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), _COLOR_WHITE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.4, _COLOR_BORDER),
            ("LINEBELOW", (0, 0), (-1, 0), 1.2, _COLOR_ACCENT),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
        ]

        if type_rows:
            for r_i in range(1, len(table_rows)):
                if r_i % 2 == 0:
                    style_cmds.append(("BACKGROUND", (0, r_i), (-1, r_i), _COLOR_ALT_ROW))
        else:
            style_cmds.append(("SPAN", (0, 1), (-1, 1)))
            style_cmds.append(("ALIGN", (0, 1), (-1, 1), "CENTER"))

        table.setStyle(TableStyle(style_cmds))
        story.append(table)
        story.append(Spacer(1, 10))

    # 4. Collector Personnel Assignment & Operational Status Section (PDF-only)
    collector_pdf = _collector_pdf_report_data(db, start_ph, end_ph)
    roster_headers = collector_pdf["roster_headers"]
    roster_rows = collector_pdf["roster_rows"]
    route_log_headers = collector_pdf["route_log_headers"]
    route_log_rows = collector_pdf["route_log_rows"]

    story.append(PageBreak())
    story.append(Paragraph("Collector Personnel Assignment & Operational Status", styles["SecHeader"]))
    story.append(
        Paragraph(
            f"{len(roster_rows)} Collector/Crew record{'s' if len(roster_rows) != 1 else ''} · "
            f"{len(route_log_rows)} Assigned Route{'s' if len(route_log_rows) != 1 else ''} recorded for {label} · Field Operations Verification",
            styles["SecSub"],
        )
    )

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
                cells.append(Paragraph(val_str, c_style))
            roster_table_rows.append(cells)

    roster_table = Table(roster_table_rows, colWidths=roster_col_widths, repeatRows=1)
    roster_style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), _COLOR_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), _COLOR_WHITE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, _COLOR_BORDER),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, _COLOR_ACCENT),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
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
            f"Detailed stop-level arrival confirmation and route status per assigned collector for {label}",
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
                cells.append(Paragraph(val_str, c_style))
            log_table_rows.append(cells)

    log_table = Table(log_table_rows, colWidths=log_col_widths, repeatRows=1)
    log_style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), _COLOR_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), _COLOR_WHITE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, _COLOR_BORDER),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, _COLOR_ACCENT),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
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

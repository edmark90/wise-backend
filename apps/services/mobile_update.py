from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import desc

from apps.models.mobile_update import AppVersion, AppUpdateAnnouncement
from apps.models.notification import Notification
from apps.schemas.mobile_update import AppVersionCreate, SendMobileUpdateIn
from apps.services.notification import _push_app_update
from apps.utils.ph_time import ph_now

REMINDER_MINUTES = {
    "None": 0,
    "10 Minutes": 10,
    "30 Minutes": 30,
    "1 Hour": 60,
    "Next App Open": 0,
    "Daily": 1440
}

def create_version(db: Session, payload: AppVersionCreate, admin_id: int) -> AppVersion:
    existing_ver = db.query(AppVersion).filter(AppVersion.version == payload.version).first()
    if existing_ver:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Version already exists.")

    existing_code = db.query(AppVersion).filter(AppVersion.version_code == payload.version_code).first()
    if existing_code:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Version code already exists.")

    if not (payload.apk_url.startswith("http://") or payload.apk_url.startswith("https://")):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="APK URL must start with http:// or https://")

    version = AppVersion(
        version=payload.version,
        version_code=payload.version_code,
        title=payload.title,
        release_notes=payload.release_notes,
        apk_url=payload.apk_url,
        is_force=payload.is_force,
        created_by=admin_id
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version

def list_versions(db: Session) -> List[AppVersion]:
    return db.query(AppVersion).order_by(desc(AppVersion.version_code)).all()

def get_version(db: Session, version_id: int) -> Optional[AppVersion]:
    return db.query(AppVersion).filter(AppVersion.id == version_id).first()

def list_announcements(db: Session, version_id: Optional[int] = None) -> List[AppUpdateAnnouncement]:
    query = db.query(AppUpdateAnnouncement)
    if version_id is not None:
        query = query.filter(AppUpdateAnnouncement.app_version_id == version_id)
    return query.order_by(desc(AppUpdateAnnouncement.created_at)).all()

def delete_announcement(db: Session, announcement_id: int) -> bool:
    ann = db.query(AppUpdateAnnouncement).filter(AppUpdateAnnouncement.id == announcement_id).first()
    if not ann:
        return False
    # Also delete the linked notification if exists
    if ann.notification_id:
        notif = db.query(Notification).filter(Notification.id == ann.notification_id).first()
        if notif:
            db.delete(notif)
    db.delete(ann)
    db.commit()
    return True

def delete_version(db: Session, version_id: int) -> bool:
    version = get_version(db, version_id)
    if not version:
        return False
    # Check if any announcements reference this version
    from sqlalchemy import func as sqlfunc
    count = db.query(sqlfunc.count(AppUpdateAnnouncement.id)).filter(
        AppUpdateAnnouncement.app_version_id == version_id
    ).scalar()
    if count > 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot delete version that has existing announcements. Delete those first.")
    db.delete(version)
    db.commit()
    return True

def send_mobile_update(db: Session, payload: SendMobileUpdateIn, admin_id: int, admin_name: str):
    version = get_version(db, payload.app_version_id)
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Selected version not found.")

    if not version.apk_url or not version.apk_url.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Selected version has no APK Download Link. Please add an APK link first.")

    interval = REMINDER_MINUTES.get(payload.reminder, 0)
    next_reminder = ph_now() + timedelta(minutes=interval) if interval > 0 else None

    # 1. Create Notification for Citizen Inbox
    notification = Notification(
        title=payload.title,
        message=payload.message,
        notification_type="Mobile Update",
        category="announcement",
        target="All",
        priority="Important" if payload.force else "Normal",
        status="Sent",
        created_by=admin_id,
        created_by_name=admin_name,
        app_version=version.version,
        apk_url=version.apk_url
    )
    db.add(notification)
    db.flush()

    # 2. Persist Announcement & Reminder settings
    announcement = AppUpdateAnnouncement(
        title=payload.title,
        message=payload.message,
        app_version_id=version.id,
        force=payload.force,
        reminder=payload.reminder,
        reminder_interval_minutes=interval,
        next_reminder_at=next_reminder,
        sent_reminders=1,
        max_reminders=3 if interval > 0 else 1,
        notification_id=notification.id,
        created_by=admin_id
    )
    db.add(announcement)
    db.commit()
    db.refresh(announcement)

    # 3. Push FCM Notification
    pushed_count = _push_app_update(
        db, payload.title, payload.message, notification,
        version.version, version.apk_url, payload.force, version.version_code,
        payload.reminder
    )

    return announcement, pushed_count, version.version

def get_latest_update(db: Session) -> Optional[AppVersion]:
    return db.query(AppVersion).order_by(desc(AppVersion.version_code)).first()

def sync_update_reminders(db: Session) -> int:
    now = ph_now()
    due = db.query(AppUpdateAnnouncement).filter(
        AppUpdateAnnouncement.next_reminder_at.isnot(None),
        AppUpdateAnnouncement.next_reminder_at <= now,
        AppUpdateAnnouncement.sent_reminders < AppUpdateAnnouncement.max_reminders
    ).all()

    total_sent = 0
    for ann in due:
        version = ann.app_version
        if version and version.apk_url:
            notif = db.query(Notification).filter(Notification.id == ann.notification_id).first()
            if notif:
                pushed = _push_app_update(
                    db, ann.title, ann.message, notif,
                    version.version, version.apk_url, ann.force, version.version_code,
                    ann.reminder
                )
                total_sent += pushed

        ann.sent_reminders += 1
        if ann.sent_reminders < ann.max_reminders and ann.reminder_interval_minutes > 0:
            ann.next_reminder_at = now + timedelta(minutes=ann.reminder_interval_minutes)
        else:
            ann.next_reminder_at = None

    db.commit()
    return total_sent

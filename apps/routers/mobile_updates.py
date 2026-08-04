from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from apps.database import get_db
from apps.utils.jwt import get_current_admin, get_current_user
from apps.models.user import User
from apps.schemas.mobile_update import (
    AppVersionCreate, AppVersionOut, SendMobileUpdateIn,
    SendUpdateOut, AnnouncementOut, LatestUpdateOut
)
from apps.services import mobile_update as service
from apps.services.push import messaging

router = APIRouter(prefix="/mobile-updates", tags=["Mobile Updates"])

@router.post("/versions", response_model=AppVersionOut, status_code=status.HTTP_201_CREATED)
def create_version(
    payload: AppVersionCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    return service.create_version(db, payload, admin.id)

@router.get("/versions", response_model=List[AppVersionOut])
def list_versions(
    db: Session = Depends(get_db)
):
    return service.list_versions(db)

@router.delete("/versions/{version_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_version_endpoint(
    version_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    success = service.delete_version(db, version_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found.")
    return None

@router.get("/announcements", response_model=List[AnnouncementOut])
def list_announcements(
    version_id: int = None,
    db: Session = Depends(get_db)
):
    return service.list_announcements(db, version_id)

@router.delete("/announcements/{announcement_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_announcement_endpoint(
    announcement_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    success = service.delete_announcement(db, announcement_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found.")
    return None

@router.post("/send", response_model=SendUpdateOut)
def send_update(
    payload: SendMobileUpdateIn,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    announcement, pushed_count, version_str = service.send_mobile_update(
        db, payload, admin.id, admin.fullname
    )
    return SendUpdateOut(
        id=announcement.id,
        title=announcement.title,
        version=version_str,
        force=announcement.force,
        reminder=announcement.reminder,
        devices_pushed=pushed_count,
        fcm_enabled=messaging is not None
    )

@router.get("/latest", response_model=LatestUpdateOut)
def get_latest_update(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    latest = service.get_latest_update(db)
    if not latest:
        return LatestUpdateOut(update_available=False, latest=None, current_version_code=None)
    return LatestUpdateOut(update_available=True, latest=latest, current_version_code=latest.version_code)

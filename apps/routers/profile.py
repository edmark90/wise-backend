"""Self-service profile endpoints for the logged-in citizen.

Routed under /api/users/profile. Registered BEFORE the admin users router
so the static /profile paths are not shadowed by /users/{user_id}.
"""
import asyncio
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from apps.database import get_db
from apps.services.storage_service import delete_stored_image, upload_image
from apps.utils.jwt import get_current_user
from apps.utils.password import verify_password, hash_password
from apps.models.user import User
from apps.schemas.user import ProfileUpdate, ChangePasswordRequest

logger = logging.getLogger("wise.profile")

router = APIRouter()

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPLOAD_ROOT = os.path.join(BACKEND_ROOT, "uploads")
PROFILE_UPLOAD_DIR = os.path.join(UPLOAD_ROOT, "profile")

ALLOWED_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5 MB


def _validate_image(content: bytes) -> str:
    """Validate the image bytes and return the normalized file extension.

    Checks the magic bytes so a disguised file type is rejected.
    """
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="Image must be 5 MB or smaller")

    if content[:3] == b"\xff\xd8\xff":
        return "jpg"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"

    raise HTTPException(status_code=400, detail="Only JPG, JPEG, or PNG images are allowed")


def _delete_old_image(stored_path):
    """Delete a previously stored profile image (Cloudinary URL or local uploads/profile)."""
    delete_stored_image(stored_path)


def _serialize_user(user: User):
    return {
        "id": user.id,
        "fullname": user.fullname,
        "email": user.email,
        "phone": user.phone,
        "role": user.role,
        "barangay": user.barangay,
        "zone": user.zone,
        "profile_image": user.profile_image,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.get("/profile")
def get_my_profile(current_user: User = Depends(get_current_user)):
    """Get the logged-in user's full profile."""
    return _serialize_user(current_user)


@router.put("/profile")
def update_my_profile(
    data: ProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update the logged-in user's own profile (name, phone, barangay, zone)."""
    updates = data.model_dump(exclude_unset=True)
    for key, value in updates.items():
        if value is not None:
            setattr(current_user, key, value)

    db.commit()
    db.refresh(current_user)
    return _serialize_user(current_user)


@router.post("/profile/upload-photo")
async def upload_profile_photo(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a new profile picture. Replaces (and deletes) the old one."""
    content = await file.read()
    ext = _validate_image(content)

    try:
        stored_path = await asyncio.to_thread(
            upload_image,
            content,
            "profile",
            f"user_{current_user.id}",
            ext,
            PROFILE_UPLOAD_DIR,
        )
    except Exception as exc:
        logger.error("Failed to upload profile picture for user #%s: %s", current_user.id, exc)
        raise HTTPException(status_code=502, detail="Failed to store profile image. Please try again.")

    old = current_user.profile_image
    try:
        current_user.profile_image = stored_path
        db.commit()
    except Exception:
        db.rollback()
        await asyncio.to_thread(_delete_old_image, stored_path)
        raise

    if old and old != stored_path:
        await asyncio.to_thread(_delete_old_image, old)

    return {
        "message": "Profile picture uploaded successfully",
        "profile_image": stored_path,
    }


@router.delete("/profile/remove-photo")
def remove_profile_photo(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove the current profile picture and delete the stored file."""
    old = current_user.profile_image
    if old:
        current_user.profile_image = None
        db.commit()
        _delete_old_image(old)

    return {"message": "Profile picture removed"}


@router.post("/profile/change-password")
def change_password(
    data: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Change the logged-in user's password after verifying the current one."""
    if not verify_password(data.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    current_user.password_hash = hash_password(data.new_password)
    db.commit()
    return {"message": "Password changed successfully"}

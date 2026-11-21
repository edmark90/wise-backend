"""Persistent image storage service with Cloudinary primary and local-disk fallback.

When Cloudinary credentials are present in the environment, images are uploaded to
Cloudinary over HTTPS and the permanent ``secure_url`` is returned. When credentials
are absent (e.g. offline local development), images fall back to the local
``uploads/<category>/`` directory so existing workflows keep working.
"""
import io
import logging
import os
import re
import time
from typing import Optional, Tuple
from urllib.parse import urlparse
from uuid import uuid4

from apps.config import (
    BASE_DIR,
    CLOUDINARY_API_KEY,
    CLOUDINARY_API_SECRET,
    CLOUDINARY_CLOUD_NAME,
    CLOUDINARY_ENABLED,
    CLOUDINARY_FOLDER_PREFIX,
    CLOUDINARY_UPLOAD_TIMEOUT,
    CLOUDINARY_URL,
)

logger = logging.getLogger("wise.storage_service")

_UPLOAD_ROOT = os.path.normpath(os.path.join(BASE_DIR, "uploads"))
_VERSION_SEGMENT_RE = re.compile(r"^v\d+$")
_cloudinary_configured = False


def _parse_cloudinary_url(raw_url: str) -> Tuple[str, str, str]:
    """Extract (cloud_name, api_key, api_secret) from a cloudinary:// URL."""
    parsed = urlparse(raw_url)
    cloud_name = (parsed.hostname or "").strip()
    api_key = (parsed.username or "").strip()
    api_secret = (parsed.password or "").strip()
    if not (cloud_name and api_key and api_secret):
        raise ValueError("CLOUDINARY_URL must match cloudinary://<api_key>:<api_secret>@<cloud_name>")
    return cloud_name, api_key, api_secret


def _get_cloudinary_uploader():
    """Import and lazily configure ``cloudinary.uploader`` once."""
    global _cloudinary_configured
    try:
        import cloudinary
        import cloudinary.uploader
    except ImportError as exc:
        raise RuntimeError(
            "Cloudinary credentials are set, but the 'cloudinary' package is not installed."
        ) from exc

    if not _cloudinary_configured:
        if CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET:
            cloud_name, api_key, api_secret = (
                CLOUDINARY_CLOUD_NAME,
                CLOUDINARY_API_KEY,
                CLOUDINARY_API_SECRET,
            )
        elif CLOUDINARY_URL:
            cloud_name, api_key, api_secret = _parse_cloudinary_url(CLOUDINARY_URL)
        else:
            raise RuntimeError("Cloudinary credentials are incomplete.")

        cloudinary.config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret,
            secure=True,
        )
        _cloudinary_configured = True

    return cloudinary.uploader


def upload_image(
    image_bytes: bytes,
    category: str,
    public_id_prefix: str = "img",
    ext: str = "jpg",
    local_dir: Optional[str] = None,
) -> str:
    """Store an image in Cloudinary (if configured) or local disk fallback.

    Returns the public HTTPS ``secure_url`` for Cloudinary uploads, or a relative
    ``uploads/<category>/<filename>`` path when running in local fallback mode.
    """
    clean_category = category.strip("/").lower() or "misc"
    unique_stem = f"{public_id_prefix}_{int(time.time())}_{uuid4().hex[:8]}"

    if CLOUDINARY_ENABLED:
        uploader = _get_cloudinary_uploader()
        folder = f"{CLOUDINARY_FOLDER_PREFIX}/{clean_category}" if CLOUDINARY_FOLDER_PREFIX else clean_category
        result = uploader.upload(
            io.BytesIO(image_bytes),
            folder=folder,
            public_id=unique_stem,
            resource_type="image",
            overwrite=False,
            unique_filename=False,
            timeout=CLOUDINARY_UPLOAD_TIMEOUT,
        )
        secure_url = result.get("secure_url")
        if not secure_url:
            raise RuntimeError("Cloudinary upload did not return a secure_url.")
        return secure_url

    logger.warning(
        "Cloudinary credentials not configured; falling back to local disk for category '%s'.",
        clean_category,
    )
    target_dir = local_dir or os.path.join(_UPLOAD_ROOT, clean_category)
    os.makedirs(target_dir, exist_ok=True)
    clean_ext = ext.lstrip(".").lower() or "jpg"
    filename = f"{unique_stem}.{clean_ext}"
    dest = os.path.join(target_dir, filename)
    with open(dest, "wb") as f:
        f.write(image_bytes)
    return f"uploads/{clean_category}/{filename}"


def extract_cloudinary_public_id(url: str) -> Optional[str]:
    """Extract the Cloudinary public_id (without extension) from a delivery URL."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    parsed = urlparse(url)
    if "res.cloudinary.com" not in (parsed.netloc or "").lower():
        return None

    marker = "/upload/"
    idx = parsed.path.find(marker)
    if idx == -1:
        return None

    after_upload = parsed.path[idx + len(marker):].lstrip("/")
    if not after_upload:
        return None

    segments = [seg for seg in after_upload.split("/") if seg]
    if not segments:
        return None

    # Skip optional transformation segments and version tag (e.g., v1730000000)
    version_idx = next(
        (i for i, seg in enumerate(segments) if _VERSION_SEGMENT_RE.match(seg)),
        None,
    )
    if version_idx is not None:
        id_segments = segments[version_idx + 1:]
    else:
        # Without a version segment, drop leading transformation segments (contain ',' or '=')
        start = 0
        while start < len(segments) - 1 and ("," in segments[start] or "=" in segments[start]):
            start += 1
        id_segments = segments[start:]

    if not id_segments:
        return None

    joined = "/".join(id_segments)
    public_id, _ = os.path.splitext(joined)
    return public_id or None


def delete_stored_image(stored_path: Optional[str]) -> bool:
    """Best-effort delete of a stored image (either Cloudinary URL or local uploads/ path)."""
    if not stored_path:
        return False

    if stored_path.startswith(("http://", "https://")):
        public_id = extract_cloudinary_public_id(stored_path)
        if not public_id or not CLOUDINARY_ENABLED:
            return False
        try:
            uploader = _get_cloudinary_uploader()
            res = uploader.destroy(public_id, resource_type="image", invalidate=True)
            return res.get("result") in ("ok", "not found")
        except Exception as exc:
            logger.warning("Failed to delete Cloudinary image '%s': %s", public_id, exc)
            return False

    full = os.path.normpath(os.path.join(BASE_DIR, stored_path))
    try:
        if os.path.commonpath([full, _UPLOAD_ROOT]) != _UPLOAD_ROOT:
            return False
    except ValueError:
        return False

    if os.path.isfile(full):
        try:
            os.remove(full)
            return True
        except OSError as exc:
            logger.warning("Failed to delete local image '%s': %s", full, exc)
            return False

    return False

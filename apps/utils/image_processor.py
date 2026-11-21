"""Image validation, orientation/color normalization, quality analysis, and
multi-view tensor preprocessing for MobileNetV2 waste classification.
"""
import io
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from fastapi import HTTPException, status
from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from apps.config import (
    AI_MIN_IMAGE_DIM,
    ALLOWED_IMAGE_MIME_TYPES,
    MAX_UPLOAD_SIZE_MB,
    WASTE_UPLOAD_DIR,
)
from apps.services.storage_service import upload_image

logger = logging.getLogger("waste_classifier.image_processor")

TARGET_SIZE: Tuple[int, int] = (224, 224)
MAX_BYTES: int = MAX_UPLOAD_SIZE_MB * 1024 * 1024
_WORKING_MAX_DIM: int = 672  # 3x target dimension; cuts LANCZOS cost on multi-MP photos

_GENERIC_MIME = {"application/octet-stream", "binary/octet-stream", "", None}

_MIME_ALIASES = {
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "image/x-png": "image/png",
    "image/mpo": "image/jpeg",
}

_FORMAT_TO_MIME = {
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "mpo": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "gif": "image/gif",
}

_MIME_TO_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/bmp": "bmp",
}

_STATUS_PAYLOAD_TOO_LARGE = getattr(status, "HTTP_413_CONTENT_TOO_LARGE", 413)
_SRGB_PROFILE = ImageCms.createProfile("sRGB")
# Precomputed mild shadow-lift LUT (gamma 0.65) for underexposed indoor photos
_GAMMA_RESCUE_LUT = [
    int(round(((i / 255.0) ** 0.65) * 255.0)) for i in range(256)
] * 3


def _normalize_mime(content_type: Optional[str]) -> str:
    """Normalize Content-Type headers (strip parameters, lowercase, resolve aliases)."""
    if not content_type:
        return ""
    base = content_type.split(";", 1)[0].strip().lower()
    return _MIME_ALIASES.get(base, base)


def _sniff_mime(image_bytes: bytes) -> str:
    """Sniff image MIME type from magic bytes and Pillow format headers."""
    if not image_bytes:
        return "application/octet-stream"

    head = image_bytes[:16]
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"BM"):
        return "image/bmp"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            fmt = (img.format or "").lower()
            return _FORMAT_TO_MIME.get(fmt, f"image/{fmt}" if fmt else "application/octet-stream")
    except Exception:
        return "application/octet-stream"


def validate_image_metadata(content_type: str, file_size: int, image_bytes: bytes = b"") -> None:
    """Validate upload file size and MIME type (sniffing magic bytes when generic or mismatched)."""
    if file_size <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file provided.",
        )

    if file_size > MAX_BYTES:
        raise HTTPException(
            status_code=_STATUS_PAYLOAD_TOO_LARGE,
            detail=f"File size exceeds maximum allowed size of {MAX_UPLOAD_SIZE_MB}MB.",
        )

    allowed_normalized = {_normalize_mime(m) for m in ALLOWED_IMAGE_MIME_TYPES}
    resolved_type = _normalize_mime(content_type)

    if (resolved_type in _GENERIC_MIME or resolved_type not in allowed_normalized) and image_bytes:
        sniffed = _sniff_mime(image_bytes)
        if sniffed != "application/octet-stream":
            logger.info("Sniffed MIME type from bytes: %s (header: '%s')", sniffed, content_type)
            resolved_type = sniffed

    if resolved_type not in allowed_normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported file type '{resolved_type or content_type}'. "
                f"Allowed MIME types: {', '.join(sorted(ALLOWED_IMAGE_MIME_TYPES))}"
            ),
        )


def save_waste_image(image_bytes: bytes, original_filename: str) -> str:
    """Persist a captured waste photo to Cloudinary (or local WASTE_UPLOAD_DIR fallback).

    Uses the sniffed image format when available so uploads with generic
    filenames keep an accurate image extension.
    """
    ext_map = {"jpg": "jpg", "jpeg": "jpg", "png": "png", "webp": "webp", "bmp": "bmp"}
    sniffed_mime = _sniff_mime(image_bytes)
    ext = _MIME_TO_EXT.get(sniffed_mime)
    if not ext:
        raw_ext = (original_filename or "").split(".")[-1].lower()
        ext = ext_map.get(raw_ext, "jpg")

    return upload_image(
        image_bytes=image_bytes,
        category="waste",
        public_id_prefix="waste",
        ext=ext,
        local_dir=WASTE_UPLOAD_DIR,
    )


def _convert_icc_to_srgb(img: Image.Image) -> Image.Image:
    """Convert embedded wide-gamut ICC profiles (e.g., Display P3) to sRGB when possible."""
    icc = img.info.get("icc_profile")
    if not icc:
        return img
    try:
        src_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        target_mode = "RGBA" if "A" in img.mode else "RGB"
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert(target_mode)
        return ImageCms.profileToProfile(img, src_profile, _SRGB_PROFILE, outputMode=img.mode)
    except Exception:
        return img


def _to_clean_rgb(img: Image.Image) -> Image.Image:
    """Flatten transparency onto white and guarantee an RGB 3-channel image."""
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        white_bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(white_bg, rgba).convert("RGB")
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def _decode_upright_rgb(image_bytes: bytes) -> Tuple[Image.Image, Dict[str, Any]]:
    """Decode raw bytes once, enforce dimension limits, fix EXIF orientation, and normalize to sRGB."""
    if not image_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file provided.",
        )

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()  # Full decode catches truncated/corrupted streams in one pass

        if getattr(img, "is_animated", False):
            img.seek(0)

        exif_orientation = 1
        try:
            exif_orientation = int(img.getexif().get(0x0112, 1))
        except Exception:
            exif_orientation = 1

        img = ImageOps.exif_transpose(img)
        orig_w, orig_h = img.size

        if min(orig_w, orig_h) < AI_MIN_IMAGE_DIM:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Image dimensions ({orig_w}x{orig_h}) too small; minimum side is {AI_MIN_IMAGE_DIM}px.",
            )

        img = _convert_icc_to_srgb(img)
        rgb = _to_clean_rgb(img)

        meta = {
            "orig_width": orig_w,
            "orig_height": orig_h,
            "aspect_ratio": round(max(orig_w, orig_h) / max(1, min(orig_w, orig_h)), 2),
            "exif_corrected": exif_orientation != 1,
        }
        return rgb, meta

    except HTTPException:
        raise
    except Image.DecompressionBombError:
        logger.error("Rejected image exceeding Pillow decompression bomb pixel limit.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Image dimensions are too large to process safely.",
        )
    except UnidentifiedImageError:
        logger.error("Failed to decode image: file is not a valid image format.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid image format or file corrupted.",
        )
    except Exception as e:
        logger.error("Image processing failed: %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Corrupted or unreadable image file: {str(e)}",
        )


def _assess_image_quality(rgb: Image.Image, meta: Dict[str, Any]) -> Dict[str, Any]:
    """Compute fast brightness, contrast, color variance, and sharpness diagnostics."""
    probe = rgb.resize((128, 128), Image.Resampling.BILINEAR)
    arr = np.asarray(probe, dtype=np.float32)
    luma = arr[:, :, 0] * 0.299 + arr[:, :, 1] * 0.587 + arr[:, :, 2] * 0.114

    mean_luma = float(luma.mean())
    rgb_std = float(arr.std())

    # Sort-based 5th/95th percentiles avoid np.percentile/partition issues on older/specific NumPy builds
    sorted_luma = np.sort(luma.ravel())
    n = len(sorted_luma)
    p5 = float(sorted_luma[int(0.05 * n)])
    p95 = float(sorted_luma[min(n - 1, int(0.95 * n))])
    contrast = max(0.0, p95 - p5)

    gx = np.abs(np.diff(luma, axis=1))
    gy = np.abs(np.diff(luma, axis=0))
    sharpness = float((gx.mean() + gy.mean()) * 0.5)

    warnings: List[str] = []
    if mean_luma < 50.0:
        warnings.append("underexposed")
    elif mean_luma > 235.0:
        warnings.append("overexposed")
    if contrast < 45.0:
        warnings.append("low_contrast")
    if rgb_std < 8.0 or sharpness < 2.2:
        warnings.append("low_detail")

    return {
        "width": meta["orig_width"],
        "height": meta["orig_height"],
        "exif_corrected": meta["exif_corrected"],
        "mean_luma": round(mean_luma, 1),
        "p95_luma": round(p95, 1),
        "contrast": round(contrast, 1),
        "sharpness": round(sharpness, 2),
        "warnings": warnings,
    }


def _to_mobilenet_tensor(img: Image.Image) -> np.ndarray:
    """Convert an RGB Pillow image to a (1, 224, 224, 3) MobileNetV2 tensor in [-1, 1]."""
    resized = img if img.size == TARGET_SIZE else img.resize(TARGET_SIZE, Image.Resampling.LANCZOS)
    arr = np.asarray(resized, dtype=np.float32)
    preprocessed = (arr / 127.5) - 1.0
    return np.ascontiguousarray(np.expand_dims(preprocessed, axis=0), dtype=np.float32)


def _center_square_crop(img: Image.Image, frac: float = 1.0) -> Image.Image:
    """Extract an aspect-preserving centered square crop scaled by frac of the shorter side."""
    w, h = img.size
    side = max(1, int(min(w, h) * frac))
    left = max(0, (w - side) // 2)
    top = max(0, (h - side) // 2)
    return img.crop((left, top, left + side, top + side))


def _border_mean_pad(img: Image.Image) -> Image.Image:
    """Letterbox-pad non-square images using the mean perimeter color to preserve aspect ratio."""
    thumb = img.resize((64, 64), Image.Resampling.BILINEAR)
    arr = np.asarray(thumb, dtype=np.float32)
    rim = np.concatenate([arr[0, :], arr[-1, :], arr[:, 0], arr[:, -1]], axis=0)
    fill_rgb = tuple(int(np.clip(round(v), 0, 255)) for v in rim.mean(axis=0))
    return ImageOps.pad(img, TARGET_SIZE, method=Image.Resampling.LANCZOS, color=fill_rgb)


def _foreground_roi_crop(img: Image.Image, min_frac: float = 0.48) -> Image.Image:
    """Compute a center-biased foreground ROI square crop combining border color contrast and edge energy."""
    w, h = img.size
    small = img.resize((96, 96), Image.Resampling.BILINEAR)
    arr = np.asarray(small, dtype=np.float32)

    rim = np.concatenate(
        [
            arr[:6, :].reshape(-1, 3),
            arr[-6:, :].reshape(-1, 3),
            arr[:, :6].reshape(-1, 3),
            arr[:, -6:].reshape(-1, 3),
        ],
        axis=0,
    )
    bg = rim.mean(axis=0)
    color_dist = np.linalg.norm(arr - bg, axis=2)
    color_dist = color_dist / (float(color_dist.max()) + 1e-6)

    gray = arr.mean(axis=2)
    gx = np.abs(np.diff(gray, axis=1, prepend=gray[:, :1]))
    gy = np.abs(np.diff(gray, axis=0, prepend=gray[:1, :]))
    grad = gx + gy
    grad = grad / (float(grad.max()) + 1e-6)

    yy, xx = np.mgrid[0:96, 0:96]
    center_prior = np.exp(-(((xx - 47.5) / 42.0) ** 2 + ((yy - 47.5) / 42.0) ** 2))
    saliency = (0.55 * color_dist + 0.45 * grad) * (0.35 + 0.65 * center_prior)

    thresh = float(saliency.mean()) + 0.65 * float(saliency.std())
    ys, xs = np.where(saliency >= thresh)
    if len(xs) < 20:
        return _center_square_crop(img, 0.85)

    xs_sorted = np.sort(xs)
    ys_sorted = np.sort(ys)
    k0 = max(0, int(0.03 * len(xs_sorted)))
    k1 = min(len(xs_sorted) - 1, int(0.97 * len(xs_sorted)))
    x0, x1 = int(xs_sorted[k0]), int(xs_sorted[k1])
    y0, y1 = int(ys_sorted[k0]), int(ys_sorted[k1])

    bw = (x1 - x0) / 96.0 * w
    bh = (y1 - y0) / 96.0 * h
    cx = ((x0 + x1) / 2.0) / 96.0 * w
    cy = ((y0 + y1) / 2.0) / 96.0 * h

    side = max(bw, bh) * 1.18
    side = max(side, min(w, h) * min_frac)
    side = min(side, float(max(w, h)))

    half = side / 2.0
    left = int(max(0.0, min(w - side, cx - half))) if side <= w else 0
    top = int(max(0.0, min(h - side, cy - half))) if side <= h else 0
    right = int(min(w, left + side))
    bottom = int(min(h, top + side))

    if right <= left or bottom <= top:
        return _center_square_crop(img, 1.0)
    return img.crop((left, top, right, bottom))


def process_image_bytes(image_bytes: bytes) -> np.ndarray:
    """Decode and preprocess raw image bytes into a single (1, 224, 224, 3) MobileNetV2 tensor."""
    rgb, _ = _decode_upright_rgb(image_bytes)
    if max(rgb.size) > _WORKING_MAX_DIM:
        rgb.thumbnail((_WORKING_MAX_DIM, _WORKING_MAX_DIM), Image.Resampling.BILINEAR)
    return _to_mobilenet_tensor(rgb)


def prepare_inference_package(image_bytes: bytes) -> Dict[str, Any]:
    """Build quality diagnostics plus multi-view and adaptive orientation tensors for classification.

    Generates complementary views without horizontal mirroring (preserving labels/text)
    and defers 90/270-degree center checks for low-confidence predictions.
    """
    rgb, meta = _decode_upright_rgb(image_bytes)
    quality = _assess_image_quality(rgb, meta)

    if max(rgb.size) > _WORKING_MAX_DIM:
        rgb.thumbnail((_WORKING_MAX_DIM, _WORKING_MAX_DIM), Image.Resampling.BILINEAR)

    w, h = rgb.size
    aspect = max(w, h) / max(1, min(w, h))
    center_sq = _center_square_crop(rgb, 1.0)
    roi_sq = _foreground_roi_crop(rgb, min_frac=0.48)

    views: List[Tuple[str, np.ndarray, float]] = [
        ("full", _to_mobilenet_tensor(rgb), 0.32),
        ("center_square", _to_mobilenet_tensor(center_sq), 0.38),
        ("foreground_roi", _to_mobilenet_tensor(roi_sq), 0.30),
    ]

    if aspect > 1.15:
        views.append(("border_pad", _to_mobilenet_tensor(_border_mean_pad(rgb)), 0.16))

    # Apply exposure rescue only when genuinely underexposed (not a flash subject on dark background) or low contrast
    center_luma = float(np.asarray(center_sq.resize((64, 64), Image.Resampling.BILINEAR).convert("L")).mean())
    needs_exposure_rescue = (
        (center_luma < 85.0 and quality["p95_luma"] < 210.0)
        or quality["contrast"] < 70.0
    )
    if needs_exposure_rescue:
        rescued = center_sq
        if center_luma < 65.0 and quality["p95_luma"] < 190.0:
            rescued = rescued.point(_GAMMA_RESCUE_LUT)
        rescued = ImageOps.autocontrast(rescued, cutoff=1)
        views.append(("exposure_rescue", _to_mobilenet_tensor(rescued), 0.28))

    fallback_rot_views: List[Tuple[str, np.ndarray]] = [
        ("rot90", _to_mobilenet_tensor(center_sq.rotate(90))),
        ("rot270", _to_mobilenet_tensor(center_sq.rotate(270))),
    ]

    return {
        "views": views,
        "fallback_rot_views": fallback_rot_views,
        "quality": quality,
    }

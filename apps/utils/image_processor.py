import io
import logging
import imghdr
from PIL import Image, UnidentifiedImageError
import numpy as np
from fastapi import HTTPException, status
from apps.config import ALLOWED_IMAGE_MIME_TYPES, MAX_UPLOAD_SIZE_MB

logger = logging.getLogger("waste_classifier.image_processor")

TARGET_SIZE = (224, 224)
MAX_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024


_GENERIC_MIME = {"application/octet-stream", "binary/octet-stream", "", None}

# Map imghdr/Pillow format strings -> MIME types
_FORMAT_TO_MIME = {
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}


def _sniff_mime(image_bytes: bytes) -> str:
    """
    Sniff the real image MIME type from the file magic bytes.
    Returns the detected MIME string or 'application/octet-stream' if unknown.
    """
    detected = imghdr.what(None, h=image_bytes)
    if detected:
        return _FORMAT_TO_MIME.get(detected.lower(), f"image/{detected.lower()}")
    # Fall back to Pillow format detection
    try:
        img = Image.open(io.BytesIO(image_bytes))
        fmt = (img.format or "").lower()
        return _FORMAT_TO_MIME.get(fmt, f"image/{fmt}" if fmt else "application/octet-stream")
    except Exception:
        return "application/octet-stream"


def validate_image_metadata(content_type: str, file_size: int, image_bytes: bytes = b"") -> None:
    """
    Validates MIME type and file size before processing image bytes.
    When content_type is generic (e.g. application/octet-stream sent by mobile
    clients that don't set an explicit MIME), the real type is sniffed from the
    file magic bytes using imghdr + Pillow.
    """
    resolved_type = content_type

    # Sniff real MIME when client sends a generic content-type
    if content_type in _GENERIC_MIME and image_bytes:
        resolved_type = _sniff_mime(image_bytes)
        logger.info(f"Sniffed MIME type from bytes: {resolved_type} (original header: '{content_type}')")

    if resolved_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{resolved_type}'. Allowed MIME types: {', '.join(ALLOWED_IMAGE_MIME_TYPES)}"
        )
    
    if file_size > MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds maximum allowed size of {MAX_UPLOAD_SIZE_MB}MB."
        )


def process_image_bytes(image_bytes: bytes) -> np.ndarray:
    """
    Processes raw image bytes into a preprocessed 4D NumPy tensor for MobileNetV2 inference.
    
    Steps:
    1. Read bytes via PIL.
    2. Validate non-corrupt image.
    3. Convert image to RGB format.
    4. Resize to 224x224 using Bilinear interpolation.
    5. Convert to NumPy float32 array.
    6. Apply MobileNetV2 preprocessing: (x / 127.5) - 1.0 (scales pixel values to [-1, 1]).
    7. Expand batch dimension -> shape (1, 224, 224, 3).
    """
    if not image_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file provided."
        )

    try:
        # Load Pillow Image from in-memory byte buffer
        image_stream = io.BytesIO(image_bytes)
        img = Image.open(image_stream)

        # Verify integrity of file to detect corrupted files
        img.verify()

        # Re-open after verify (Pillow requires re-opening after verify() is called)
        image_stream.seek(0)
        img = Image.open(image_stream)

        # Convert image mode to RGB (handles RGBA, Greyscale, Palette, CMYK, etc.)
        if img.mode != "RGB":
            img = img.convert("RGB")

        # Resize image to target dimension (224x224)
        resample_method = getattr(Image, "Resampling", Image).BILINEAR
        img_resized = img.resize(TARGET_SIZE, resample_method)

        # Convert to numpy float32 array
        img_array = np.asarray(img_resized, dtype=np.float32)

        # Apply standard MobileNetV2 preprocessing: scale [0, 255] -> [-1, 1]
        img_preprocessed = (img_array / 127.5) - 1.0

        # Expand batch dimension: (224, 224, 3) -> (1, 224, 224, 3)
        tensor_batch = np.expand_dims(img_preprocessed, axis=0)

        return tensor_batch

    except UnidentifiedImageError:
        logger.error("Failed to decode image: file is not a valid image format.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid image format or file corrupted."
        )
    except Exception as e:
        logger.error(f"Image processing failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Corrupted or unreadable image file: {str(e)}"
        )

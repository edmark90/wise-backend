"""Firebase Cloud Messaging push delivery.

Push delivery is optional: when Firebase Admin service-account credentials are
not configured, sends are skipped and the system still works through the API
(notifications are stored in the DB and mobile clients sync them).

Credentials are loaded from either:
  - FIREBASE_CREDENTIALS_JSON env var (the full service-account JSON string;
    recommended for Render / cloud deploys), or
  - FIREBASE_CREDENTIALS_PATH env var (a path to the .json file on disk;
    convenient for local dev).
"""
import json
import logging
import os
from typing import List, Optional

from apps.config import FIREBASE_CREDENTIALS_JSON, FIREBASE_CREDENTIALS_PATH

logger = logging.getLogger(__name__)

messaging = None
_firebase_app = None


def _load_credentials():
    """Return firebase_admin credentials from env JSON or a file path."""
    from firebase_admin import credentials

    if FIREBASE_CREDENTIALS_JSON:
        try:
            return credentials.Certificate(json.loads(FIREBASE_CREDENTIALS_JSON))
        except Exception:
            logger.exception("Failed to parse FIREBASE_CREDENTIALS_JSON")
    if FIREBASE_CREDENTIALS_PATH and os.path.exists(FIREBASE_CREDENTIALS_PATH):
        return credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
    return None


try:
    from firebase_admin import initialize_app, messaging as _messaging

    _cred = _load_credentials()
    if _cred is not None:
        _firebase_app = initialize_app(_cred)
        messaging = _messaging
        logger.info("Firebase Admin initialized for push notifications")
        print("[push] Firebase Admin initialized - FCM push ENABLED")
    else:
        logger.info(
            "FIREBASE_CREDENTIALS_JSON / FIREBASE_CREDENTIALS_PATH not set or "
            "missing - FCM push disabled (API sync remains active)"
        )
        print(
            "[push] FIREBASE_CREDENTIALS not set - FCM push DISABLED "
            "(API sync remains active)"
        )
except Exception as e:  # pragma: no cover - depends on environment
    logger.warning("Firebase Admin unavailable: %s", e)
    print(f"[push] Firebase Admin unavailable - FCM push DISABLED: {e}")


def send_push_notifications(
    tokens: List[str],
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> int:
    """Send an FCM multicast push to the given device tokens.

    Returns the number of successfully delivered messages (0 when disabled).
    """
    if not tokens or messaging is None or _firebase_app is None:
        return 0

    message = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data={str(k): str(v) for k, v in (data or {}).items()},
        tokens=tokens,
        android=messaging.AndroidConfig(priority="high"),
    )
    try:
        response = messaging.send_each_for_multicast(message)
        return response.success_count
    except Exception as e:
        logger.warning("FCM send failed: %s", e)
        return 0

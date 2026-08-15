"""Philippines (Asia/Manila) wall-clock helpers.

The API stores date/time values as naive datetimes (no tzinfo) everywhere.
On Render the server clock defaults to UTC, so bare datetime.now() /
date.today() would silently run 8 hours behind the Philippines. These
helpers return the Manila wall-clock time as a *naive* datetime, keeping
every existing DB comparison and datetime.combine() call working unchanged
while always reflecting Philippine time.
"""
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

# Asia/Manila is UTC+8. Falls back to a fixed offset when the tzdata
# database is unavailable on the host (e.g. slim containers).
try:
    PH_TZ = ZoneInfo("Asia/Manila")
    _PH = "Asia/Manila"
except Exception:  # pragma: no cover - fallback for hosts without tzdata
    PH_TZ = timezone(timedelta(hours=8))
    _PH = "UTC+8"


def ph_now() -> datetime:
    """Current date/time in Manila, as a naive datetime."""
    return datetime.now(PH_TZ).replace(tzinfo=None)


def ph_today() -> date:
    """Today's date in Manila."""
    return ph_now().date()
import os
import threading
import time


from contextlib import asynccontextmanager
from apps.routers.prediction import router as prediction_router
from apps.routers.health_model import router as health_model_router
from apps.services.model_service import model_service
from apps.routers.mobile_updates import router as mobile_updates_router
from apps.services.mobile_update import sync_update_reminders
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from apps.database import test_connection, engine, SessionLocal
from apps.services.collection_schedule import sync_auto_status_notifications
from apps.routers.auth import router as auth_router
from apps.routers.profile import router as profile_router, UPLOAD_ROOT
from apps.routers.users import router as users_router
from apps.routers.dashboard import router as dashboard_router
from apps.routers.waste_records import router as waste_records_router
from apps.routers.collection_schedules import router as collection_schedules_router
from apps.routers.collection_history import router as collection_history_router
from apps.routers.notifications import router as notifications_router
from apps.routers.ai_guides import router as ai_guides_router
from apps.middleware.rate_limiter import rate_limiting_middleware
from apps.middleware.error_handler import (
    AppException,
    app_exception_handler,
    validation_exception_handler,
    integrity_error_handler,
    generic_exception_handler
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Load the MobileNetV2 model ONLY ONCE
    try:
        model_service.load_model()
    except Exception as e:
        print(f"[Lifespan Startup Warning] Model loading deferred or error: {e}")
    yield


app = FastAPI(
    title="WISE Backend API & Waste Classifier",
    version="1.0.0",
    lifespan=lifespan
)


def ensure_schema():
    """Idempotent startup migrations for new notification tables/columns."""
    try:
        with engine.connect() as conn:
            #create mobile_updates tables
            conn.execute(text("""
    CREATE TABLE IF NOT EXISTS app_versions (
        id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        version VARCHAR(30) NOT NULL,
        version_code INT NOT NULL,
        title VARCHAR(120) NOT NULL,
        release_notes TEXT NULL,
        apk_url VARCHAR(500) NOT NULL,
        is_force TINYINT(1) NOT NULL DEFAULT 0,
        created_by INT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_ver (version),
        UNIQUE KEY uq_vercode (version_code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""))

            conn.execute(text("""
    CREATE TABLE IF NOT EXISTS app_update_announcements (
        id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        title VARCHAR(120) NOT NULL,
        message TEXT NOT NULL,
        app_version_id INT UNSIGNED NOT NULL,
        is_force TINYINT(1) NOT NULL DEFAULT 0,
        reminder VARCHAR(20) NOT NULL DEFAULT 'None',
        reminder_interval_minutes INT NOT NULL DEFAULT 0,
        next_reminder_at DATETIME NULL,
        sent_reminders INT NOT NULL DEFAULT 0,
        max_reminders INT NOT NULL DEFAULT 3,
        notification_id INT NULL,
        created_by INT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""))





            # New tables
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS device_tokens (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    token VARCHAR(512) NOT NULL UNIQUE,
                    platform VARCHAR(20) DEFAULT 'android',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS notification_reads (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    notification_id INT NOT NULL,
                    user_id INT NOT NULL,
                    read_at DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB
            """))
            conn.commit()
            print("[schema] Ensured device_tokens / notification_reads tables")

            # users columns
            cols = [row[0] for row in conn.execute(text("SHOW COLUMNS FROM users"))]
            if "is_active" not in cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN is_active TINYINT(1) NOT NULL DEFAULT 1"))
                conn.commit()
                print("[schema] Added users.is_active column")
            if "barangay" not in cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN barangay VARCHAR(100) NULL"))
                conn.commit()
                print("[schema] Added users.barangay column")
            if "zone" not in cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN zone VARCHAR(100) NULL"))
                conn.commit()
                print("[schema] Added users.zone column")
            user_prefs = {
                "notif_collection_updates": "TINYINT(1) NOT NULL DEFAULT 1",
                "notif_route_updates": "TINYINT(1) NOT NULL DEFAULT 1",
                "notif_announcements": "TINYINT(1) NOT NULL DEFAULT 1",
                "notif_emergency_alerts": "TINYINT(1) NOT NULL DEFAULT 1",
                "notif_reminders": "TINYINT(1) NOT NULL DEFAULT 1",
                "notif_completed_collection": "TINYINT(1) NOT NULL DEFAULT 1",
            }
            for pref, ddl in user_prefs.items():
                if pref not in cols:
                    conn.execute(text(f"ALTER TABLE users ADD COLUMN {pref} {ddl}"))
            conn.commit()
            print("[schema] Added users notification preference columns")
            if "preferred_barangays" not in cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN preferred_barangays TEXT NULL"))
                conn.commit()
                print("[schema] Added users.preferred_barangays column")

            # notifications columns
            n_cols = [row[0] for row in conn.execute(text("SHOW COLUMNS FROM notifications"))]

            if "app_version" not in n_cols:
                conn.execute(text("ALTER TABLE notifications ADD COLUMN app_version VARCHAR(30) NULL"))
            if "apk_url" not in n_cols:
                conn.execute(text("ALTER TABLE notifications ADD COLUMN apk_url VARCHAR(500) NULL"))
            conn.commit()

            notif_columns = {
                "notification_type": "VARCHAR(50) NULL",
                "category": "VARCHAR(50) NULL",
                "schedule_id": "INT NULL",
                "route_name": "VARCHAR(255) NULL",
                "starting_point": "VARCHAR(255) NULL",
                "affected_barangays": "TEXT NULL",
                "collection_date": "VARCHAR(20) NULL",
                "collection_time": "VARCHAR(20) NULL",
                "assigned_personnel": "VARCHAR(255) NULL",
                "reason": "VARCHAR(255) NULL",
                "reason_other": "VARCHAR(255) NULL",
                "additional_message": "TEXT NULL",
                "priority": "VARCHAR(20) DEFAULT 'Normal'",
                "recipients": "TEXT NULL",
                "status": "VARCHAR(20) DEFAULT 'Sent'",
                "created_by_name": "VARCHAR(100) NULL",
            }
            for col, ddl in notif_columns.items():
                if col not in n_cols:
                    conn.execute(text(f"ALTER TABLE notifications ADD COLUMN {col} {ddl}"))
            if "target" in n_cols:
                conn.execute(text("ALTER TABLE notifications MODIFY COLUMN target TEXT"))
                conn.commit()
                print("[schema] Enlarged notifications.target to TEXT")

            # collection_schedule columns
            s_cols = [row[0] for row in conn.execute(text("SHOW COLUMNS FROM collection_schedule"))]
            if "route_name" not in s_cols:
                conn.execute(text("ALTER TABLE collection_schedule ADD COLUMN route_name VARCHAR(255) NULL"))
            if "starting_point" not in s_cols:
                conn.execute(text("ALTER TABLE collection_schedule ADD COLUMN starting_point VARCHAR(255) NULL"))
            conn.commit()

            # waste_records table (AI classifications from the mobile app)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS waste_records (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NULL,
                    image_url TEXT NOT NULL,
                    waste_type VARCHAR(100) NOT NULL,
                    disposal_category VARCHAR(50) NOT NULL,
                    confidence DECIMAL(5,2) NULL,
                    is_flagged TINYINT(1) NOT NULL DEFAULT 0,
                    classified_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_wr_classified_at (classified_at),
                    INDEX idx_wr_type (waste_type)
                ) ENGINE=InnoDB
            """))
            wr_cols = [row[0] for row in conn.execute(text("SHOW COLUMNS FROM waste_records"))]
            if "is_flagged" not in wr_cols:
                conn.execute(text("ALTER TABLE waste_records ADD COLUMN is_flagged TINYINT(1) NOT NULL DEFAULT 0"))
                conn.commit()
                print("[schema] Added waste_records.is_flagged column")
            # Widen columns (existing table may predate the AI capture feature).
            conn.execute(text(
                "ALTER TABLE waste_records "
                "MODIFY COLUMN waste_type VARCHAR(100) NOT NULL, "
                "MODIFY COLUMN disposal_category VARCHAR(50) NOT NULL"
            ))
            conn.commit()
            print("[schema] Ensured waste_records table")
    except Exception as e:
        print(f"[schema] Migration skipped: {e}")

ensure_schema()

# ---------------------------------------------------------------------------
# Automatic route status notifications (Arriving / Arrived / Completed)
# ---------------------------------------------------------------------------
def start_auto_status_worker():
    """Background loop that emits a notification + FCM push the first time a
    today's route crosses into Arriving / Arrived / Completed.

    Statuses are server-time-derived, so this daemon polls the clock instead
    of relying on an admin action. generate_route_notification dedups by
    (schedule, type, day), so overlapping instances are safe.
    """
    def run():
        while True:
            try:
                db = SessionLocal()
                try:
                    n = sync_auto_status_notifications(db)
                    if n:
                        print(f"[auto-status] emitted {n} automatic route notification(s)")
                    
                    reminders_sent = sync_update_reminders(db)
                    if reminders_sent > 0:
                        print(f"[mobile-update] Processed background update reminders: {reminders_sent} push(es) sent")
                finally:
                    db.close()
            except Exception as e:
                print(f"[auto-status] worker error: {e}")
            time.sleep(60)

    threading.Thread(target=run, daemon=True, name="auto-status-worker").start()

start_auto_status_worker()

# Uploads directory for profile pictures (created automatically if missing)
os.makedirs(os.path.join(UPLOAD_ROOT, "profile"), exist_ok=True)
os.makedirs(os.path.join(UPLOAD_ROOT, "waste"), exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_ROOT), name="uploads")

# Exception handlers
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(IntegrityError, integrity_error_handler)
app.add_exception_handler(Exception, generic_exception_handler)

# Rate limiting middleware (applied first for early rejection)
app.middleware("http")(rate_limiting_middleware)

# CORS configuration
# This API uses JWT Bearer tokens (Authorization header), not cookies,
# so allow_credentials is set to False to allow allow_origins=["*"] per CORS spec.
# In production, replace * with specific frontend domains if needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/auth", tags=["Authentication"])
app.include_router(mobile_updates_router, prefix="/api")
# Profile router first so /api/users/profile/* is not shadowed by /api/users/{user_id}
app.include_router(profile_router, prefix="/api/users", tags=["Profile"])
app.include_router(users_router, prefix="/api/users", tags=["Users"])
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(waste_records_router, prefix="/api/waste-records", tags=["Waste Records"])
app.include_router(collection_schedules_router, prefix="/api/collection-schedules", tags=["Collection Schedules"])
app.include_router(collection_history_router, prefix="/api/collection-history", tags=["Collection History"])
app.include_router(notifications_router, prefix="/api/notifications", tags=["Notifications"])
app.include_router(ai_guides_router, prefix="/api/ai-guides", tags=["AI Guides"])
app.include_router(prediction_router)
app.include_router(health_model_router)


@app.get("/")
def root():
    return {
        "message": "WISE Backend API is Running!"
    }

@app.get("/test-db")
def test_db():
    result = test_connection()

    if result is True:
        return {
            "status": "success",
            "message": "Connected to TiDB Cloud!"
        }

    return {
        "status": "error",
        "message": result
    }
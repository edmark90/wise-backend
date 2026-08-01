import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from apps.database import test_connection, engine
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

app = FastAPI(
    title="WISE Backend API",
    version="1.0.0"
)

def ensure_schema():
    """Idempotent startup migration: add missing columns to the users table."""
    try:
        with engine.connect() as conn:
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
    except Exception as e:
        print(f"[schema] Migration skipped: {e}")

ensure_schema()

# Uploads directory for profile pictures (created automatically if missing)
os.makedirs(os.path.join(UPLOAD_ROOT, "profile"), exist_ok=True)
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
# Profile router first so /api/users/profile/* is not shadowed by /api/users/{user_id}
app.include_router(profile_router, prefix="/api/users", tags=["Profile"])
app.include_router(users_router, prefix="/api/users", tags=["Users"])
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(waste_records_router, prefix="/api/waste-records", tags=["Waste Records"])
app.include_router(collection_schedules_router, prefix="/api/collection-schedules", tags=["Collection Schedules"])
app.include_router(collection_history_router, prefix="/api/collection-history", tags=["Collection History"])
app.include_router(notifications_router, prefix="/api/notifications", tags=["Notifications"])
app.include_router(ai_guides_router, prefix="/api/ai-guides", tags=["AI Guides"])

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
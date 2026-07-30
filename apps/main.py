from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError
from apps.database import test_connection
from apps.routers.auth import router as auth_router
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
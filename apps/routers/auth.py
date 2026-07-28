from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from apps.database import get_db
from apps.schemas.auth import LoginRequest, LoginResponse, UserResponse
from apps.services.auth_service import login_user
from apps.utils.jwt import get_current_admin
from apps.model.user import User

router = APIRouter()

@router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    """Login endpoint for admin users only."""
    result = login_user(db, request.email, request.password)
    
    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password, or not authorized as admin"
        )
    
    return result

@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_admin)):
    """Get current authenticated admin user."""
    return {
        "id": current_user.id,
        "fullname": current_user.fullname,
        "email": current_user.email,
        "phone": current_user.phone,
        "role": current_user.role,
        "profile_image": current_user.profile_image,
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None
    }
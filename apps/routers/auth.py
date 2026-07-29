from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from apps.database import get_db
from apps.schemas.auth import LoginRequest, SignupRequest, LoginResponse, UserResponse, RefreshTokenRequest
from apps.services.auth import login_user
from apps.services.user import create_user
from apps.utils.jwt import get_current_admin, create_access_token, create_refresh_token, decode_access_token
from apps.models.user import User

router = APIRouter()

@router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    """Login endpoint for mobile users."""
    result = login_user(db, request.email, request.password)
    
    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    return result

@router.post("/signup", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
def signup(request: SignupRequest, db: Session = Depends(get_db)):
    """Public signup endpoint for mobile users."""
    user = create_user(db, request.model_dump())
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already exists"
        )
    
    access_token = create_access_token(
        data={"sub": str(user.id), "email": user.email, "role": user.role}
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "fullname": user.fullname,
            "email": user.email,
            "phone": user.phone,
            "role": user.role,
            "profile_image": user.profile_image,
            "created_at": user.created_at.isoformat() if user.created_at else None
        }
    }

@router.post("/refresh")
def refresh_token(request: RefreshTokenRequest, db: Session = Depends(get_db)):
    """Refresh an access token using a refresh token."""
    payload = decode_access_token(request.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )

    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )

    access_token = create_access_token(
        data={"sub": str(user.id), "email": user.email, "role": user.role}
    )
    refresh_token = create_refresh_token(user.id, user.email, user.role)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }


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
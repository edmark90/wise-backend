from sqlalchemy.orm import Session
from apps.model.user import User
from apps.utils.password import verify_password
from apps.utils.jwt import create_access_token

def authenticate_user(db: Session, email: str, password: str):
    """Authenticate a user by email and password."""
    user = db.query(User).filter(User.email == email).first()
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user

def login_user(db: Session, email: str, password: str):
    """Login a user and return access token."""
    user = authenticate_user(db, email, password)
    if not user:
        return None
    
    # Only allow admin role to login to admin website
    if user.role != "admin":
        return None
    
    access_token = create_access_token(data={"sub": str(user.id), "email": user.email, "role": user.role})
    
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

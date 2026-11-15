from apps.models.user import User
from apps.utils.jwt import create_access_token


def build_login_response(user: User) -> dict:
    """Build standard token + serialized user payload for login/signup."""
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
            "barangay": user.barangay,
            "role": user.role,
            "profile_image": user.profile_image,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        },
    }

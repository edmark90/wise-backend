from pydantic import BaseModel, EmailStr
from typing import Optional

class LoginRequest(BaseModel):
    email: str
    password: str

class SignupRequest(BaseModel):
    fullname: str
    email: EmailStr
    password: str
    phone: Optional[str] = None

class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    user: dict


class RefreshTokenRequest(BaseModel):
    refresh_token: str

class UserResponse(BaseModel):
    id: int
    fullname: str
    email: str
    phone: str | None
    role: str
    profile_image: str | None
    created_at: str
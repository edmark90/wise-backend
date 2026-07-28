from pydantic import BaseModel

class LoginRequest(BaseModel):
    email: str
    password: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    user: dict

class UserResponse(BaseModel):
    id: int
    fullname: str
    email: str
    phone: str | None
    role: str
    profile_image: str | None
    created_at: str
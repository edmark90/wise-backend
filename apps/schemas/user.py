from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional

class UserCreate(BaseModel):
    fullname: str
    email: EmailStr
    password: str
    phone: Optional[str] = None
    role: str = "citizen"
    profile_image: Optional[str] = None

    @field_validator('password')
    @classmethod
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
        return v

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v):
        if v and not v.isdigit() and not v.replace('+', '').replace('-', '').replace(' ', '').isdigit():
            raise ValueError('Phone must contain only digits, +, -, or spaces')
        return v

class UserUpdate(BaseModel):
    fullname: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    profile_image: Optional[str] = None

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v):
        if v and not v.isdigit() and not v.replace('+', '').replace('-', '').replace(' ', '').isdigit():
            raise ValueError('Phone must contain only digits, +, -, or spaces')
        return v

class UserResponse(BaseModel):
    id: int
    fullname: str
    email: str
    phone: Optional[str]
    role: str
    profile_image: Optional[str]
    created_at: str

class UserListResponse(BaseModel):
    users: list[UserResponse]
    total: int
    page: int
    page_size: int

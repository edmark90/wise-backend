from datetime import datetime
from pydantic import BaseModel, EmailStr, field_validator, field_serializer, ConfigDict
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


class UserListItem(BaseModel):
    """Lightweight user schema for list views - only essential fields."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    fullname: str
    email: str
    role: str


class UserResponse(BaseModel):
    """Full user schema for detail views."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    fullname: str
    email: str
    phone: Optional[str] = None
    role: str
    profile_image: Optional[str] = None
    created_at: Optional[datetime] = None

    @field_serializer('created_at')
    def serialize_created_at(self, value: Optional[datetime]) -> Optional[str]:
        if value is None:
            return None
        return value.isoformat()


class UserListResponse(BaseModel):
    users: list[UserListItem]
    total: int
    page: int
    page_size: int

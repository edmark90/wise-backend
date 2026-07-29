from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func

from apps.database import Base

class User(Base):

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    fullname = Column(String(100), nullable=False)

    email = Column(String(100), unique=True, nullable=False)

    password_hash = Column(String(255), nullable=False)

    phone = Column(String(20))

    role = Column(String(20), default="citizen")

    profile_image = Column(String(255))

    created_at = Column(DateTime, server_default=func.now())
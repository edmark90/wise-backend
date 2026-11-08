from sqlalchemy.orm import Session, load_only
from sqlalchemy import or_, func
from typing import Optional
from apps.models.user import User
from apps.utils.password import hash_password

def get_users(
    db: Session,
    skip: int = 0,
    limit: int = 10,
    search: Optional[str] = None,
    role: Optional[str] = None
):
    """Get users with pagination, search, and role filtering.
    Only selects essential columns for list performance.
    """
    query = db.query(User).options(
        load_only(User.id, User.fullname, User.email, User.phone, User.barangay, User.role, User.is_active, User.created_at)
    )
    
    if search:
        query = query.filter(
            or_(
                User.fullname.ilike(f"%{search}%"),
                User.email.ilike(f"%{search}%"),
                User.phone.ilike(f"%{search}%")
            )
        )
    
    if role:
        query = query.filter(User.role == role)
    
    total = query.count()
    users = query.order_by(User.id.asc()).offset(skip).limit(limit).all()

    # Global role counts for dashboard stat cards
    role_rows = db.query(User.role, func.count(User.id)).group_by(User.role).all()
    counts = {"admin": 0, "citizen": 0, "collector": 0, "total": 0}
    for r_name, cnt in role_rows:
        key = (r_name or "citizen").lower()
        counts[key] = counts.get(key, 0) + int(cnt)
        counts["total"] += int(cnt)
    
    return {"users": users, "total": total, "role_counts": counts}


def get_active_collectors(db: Session, include_inactive: bool = False):
    """Return collector accounts for assignment dropdowns."""
    query = db.query(User).filter(User.role == "collector")
    if not include_inactive:
        query = query.filter(User.is_active.is_(True))
    return query.order_by(User.fullname.asc()).all()

def get_user_by_id(db: Session, user_id: int):
    """Get a user by ID."""
    return db.query(User).filter(User.id == user_id).first()

def get_user_by_email(db: Session, email: str):
    """Get a user by email."""
    return db.query(User).filter(User.email == email).first()

def create_user(db: Session, user_data: dict):
    """Create a new user."""
    # Check if email already exists
    existing_user = get_user_by_email(db, user_data["email"])
    if existing_user:
        return None
    
    # Hash password
    user_data["password_hash"] = hash_password(user_data.pop("password"))
    
    db_user = User(**user_data)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def update_user(db: Session, user_id: int, user_data: dict):
    """Update a user."""
    db_user = get_user_by_id(db, user_id)
    if not db_user:
        return None
    
    # If updating email, check if it's already taken
    if "email" in user_data and user_data["email"] != db_user.email:
        existing_user = get_user_by_email(db, user_data["email"])
        if existing_user:
            return None
    
    # If updating password, hash and set password_hash
    raw_password = user_data.pop("password", None)
    if raw_password and len(raw_password.strip()) >= 8:
        db_user.password_hash = hash_password(raw_password.strip())

    for key, value in user_data.items():
        if value is not None:
            setattr(db_user, key, value)
    
    db.commit()
    db.refresh(db_user)
    return db_user

def delete_user(db: Session, user_id: int):
    """Delete a user."""
    db_user = get_user_by_id(db, user_id)
    if not db_user:
        return None
    
    db.delete(db_user)
    db.commit()
    return db_user

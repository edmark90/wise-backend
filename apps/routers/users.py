from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional
from apps.database import get_db
from apps.schemas.user import UserCreate, UserUpdate, UserResponse, UserListResponse
from apps.services.user import (
    get_users,
    get_active_collectors,
    get_user_by_id,
    create_user,
    update_user,
    delete_user
)
from apps.utils.jwt import get_current_admin
from apps.models.user import User
from apps.models.collection_schedule import CollectionSchedule
from sqlalchemy import or_

router = APIRouter()

@router.get("/", response_model=UserListResponse)
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    search: Optional[str] = None,
    role: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get all users with pagination, search, and role filtering."""
    skip = (page - 1) * page_size
    result = get_users(db, skip=skip, limit=page_size, search=search, role=role)
    
    return {
        "users": result["users"],
        "total": result["total"],
        "page": page,
        "page_size": page_size,
        "role_counts": result.get("role_counts"),
    }


@router.get("/collectors")
def list_collectors(
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get active collector accounts for schedule assignment dropdowns."""
    collectors = get_active_collectors(db, include_inactive=include_inactive)
    return {
        "collectors": [
            {
                "id": c.id,
                "fullname": c.fullname,
                "email": c.email,
                "phone": c.phone,
                "barangay": c.barangay,
                "is_active": bool(c.is_active),
            }
            for c in collectors
        ]
    }


@router.get("/{user_id}/assignments")
def get_collector_assignments(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get assigned collection schedules for a specific collector."""
    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    schedules = (
        db.query(CollectionSchedule)
        .filter(
            or_(
                CollectionSchedule.assigned_collector_id == user.id,
                CollectionSchedule.assigned_personnel.ilike(user.fullname.strip()),
            )
        )
        .order_by(CollectionSchedule.collection_date.desc(), CollectionSchedule.collection_time.asc())
        .limit(50)
        .all()
    )
    return {
        "collector": {
            "id": user.id,
            "fullname": user.fullname,
            "email": user.email,
            "phone": user.phone,
            "role": user.role,
            "is_active": bool(user.is_active),
        },
        "assignments": [
            {
                "id": s.id,
                "route_name": s.route_name or f"{s.barangay} Route",
                "barangay": s.barangay,
                "truck_number": s.truck_number or "",
                "collection_date": str(s.collection_date),
                "collection_time": s.collection_time.strftime("%I:%M %p") if s.collection_time else "",
                "status": s.status,
            }
            for s in schedules
        ],
    }


@router.get("/{user_id}", response_model=UserResponse)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get a specific user by ID."""
    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    return {
        "id": user.id,
        "fullname": user.fullname,
        "email": user.email,
        "phone": user.phone,
        "barangay": user.barangay,
        "role": user.role,
        "profile_image": user.profile_image,
        "is_active": bool(user.is_active),
        "created_at": user.created_at.isoformat() if user.created_at else None
    }

@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_new_user(
    user_data: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Create a new user."""
    user = create_user(db, user_data.model_dump())
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already exists"
        )
    
    return {
        "id": user.id,
        "fullname": user.fullname,
        "email": user.email,
        "phone": user.phone,
        "barangay": user.barangay,
        "role": user.role,
        "profile_image": user.profile_image,
        "is_active": bool(user.is_active),
        "created_at": user.created_at.isoformat() if user.created_at else None
    }

@router.put("/{user_id}", response_model=UserResponse)
def update_existing_user(
    user_id: int,
    user_data: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Update an existing user."""
    user = update_user(db, user_id, user_data.model_dump(exclude_unset=True))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found or email already exists"
        )
    
    return {
        "id": user.id,
        "fullname": user.fullname,
        "email": user.email,
        "phone": user.phone,
        "barangay": user.barangay,
        "role": user.role,
        "profile_image": user.profile_image,
        "is_active": bool(user.is_active),
        "created_at": user.created_at.isoformat() if user.created_at else None
    }

@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_existing_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Delete a user."""
    user = delete_user(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return None

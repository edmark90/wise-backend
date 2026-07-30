from pydantic import BaseModel

class DashboardStats(BaseModel):
    total_users: int
    total_waste_records: int
    total_ai_classifications: int
    todays_classifications: int
    todays_collection_schedule: int
    upcoming_collections: int
    completed_collections: int
    total_notifications: int

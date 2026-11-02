from pydantic import BaseModel
from typing import List, Optional

class BarangayLeaderboardItem(BaseModel):
    rank: int
    barangay: str
    total_scans: int
    proper_sorting_rate: float        # Percentage e.g. 94.5%
    recyclable_count: int
    biodegradable_count: int
    residual_special_count: int
    avg_confidence: float             # e.g. 91.2%
    award_tier: str                   # "Gold", "Silver", "Bronze", "Standard"
    badge_title: str                  # e.g. "🥇 1st Place - Top Segregation"

class BarangayLeaderboardResponse(BaseModel):
    period: str
    top_performer: Optional[str]
    citywide_sorting_rate: float
    total_classifications: int
    total_recyclables: int
    total_biodegradable: int
    leaderboard: List[BarangayLeaderboardItem]

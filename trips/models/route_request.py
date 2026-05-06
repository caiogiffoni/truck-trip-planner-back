from pydantic import BaseModel, field_validator

class RouteRequest(BaseModel):
    current_location: str
    pickup_location: str
    dropoff_location: str
    current_cycle_used: float
    has_curfew: bool = True

    @field_validator("current_cycle_used")
    @classmethod
    def cycle_used_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("current_cycle_used must be >= 0")
        if v > 70:
            raise ValueError("current_cycle_used cannot exceed 70 hrs")
        return v
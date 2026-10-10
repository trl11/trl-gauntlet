"""Profile model for the DAQ record suite."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DaqRecordProfile(BaseModel):
    """How long to record. What is recorded is chosen on the run form."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    duration_s: float = Field(default=0.0, ge=0, description="How long to record. 0 records until the run is stopped.")

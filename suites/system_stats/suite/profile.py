"""The profile model: what an operator can configure for this suite."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SystemStatsProfile(BaseModel):
    """Cadence and the thresholds every sample is checked against."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    duration_s: float = Field(default=60.0, gt=0, description="How long to keep sampling the host.")
    sample_period_s: float = Field(default=1.0, gt=0, description="Seconds between samples.")
    max_cpu_percent: float = Field(
        default=95.0,
        ge=0,
        le=100,
        description="Fail a sample whose overall CPU utilisation exceeds this.",
    )
    min_available_memory_percent: float = Field(
        default=10.0,
        ge=0,
        le=100,
        description="Fail a sample whose available memory is below this share of total.",
    )
    min_free_disk_percent: float = Field(
        default=10.0,
        ge=0,
        le=100,
        description="Fail a sample where any mounted filesystem has less free space than this.",
    )
    max_load_per_core: float = Field(
        default=4.0,
        gt=0,
        description="Fail a sample whose one-minute load average per core is above this.",
    )
    max_temperature_c: float = Field(
        default=90.0,
        description="Fail a sample whose hottest thermal zone is above this.",
    )
    max_new_interface_errors: int = Field(
        default=0,
        ge=0,
        description="Errors plus drops one interface may accumulate between samples.",
    )
    stop_on_failure: bool = Field(default=False, description="Abandon the run on the first failing sample.")

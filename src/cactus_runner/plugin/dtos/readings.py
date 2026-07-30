from dataclasses import dataclass
from datetime import datetime

__all__ = ["SiteReadingType", "SiteReading"]


@dataclass(slots=True, frozen=True)
class SiteReadingType:
    site_reading_type_id: str
    site_id: int
    mrid: str
    group_mrid: str
    group_id: int
    role_flags: int
    uom: int
    kind: int
    data_qualifier: int
    power_of_ten_multiplier: int


@dataclass(slots=True, frozen=True)
class SiteReading:
    site_reading_id: str
    site_reading_type_id: int
    value: int
    time_period_start: datetime
    time_period_seconds: int

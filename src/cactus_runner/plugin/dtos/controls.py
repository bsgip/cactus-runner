from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

__all__ = ["SiteControlGroup", "SiteControl", "SiteControlGroupDefault"]


@dataclass(slots=True, frozen=True)
class SiteControlGroup:
    site_control_group_id: str | None
    description: str
    primacy: int
    fsa_id: int | None
    display_id: int | None


@dataclass(slots=True, frozen=True)
class SiteControl:
    site_control_id: str | None
    site_id: str
    start_time: datetime
    duration_seconds: int
    randomize_start_seconds: int | None
    display_id: int | None
    set_energized: bool | None
    set_connect: bool | None
    import_limit_watts: Decimal | None
    export_limit_watts: Decimal | None
    generation_limit_watts: Decimal | None
    load_limit_watts: Decimal | None
    set_point_percentage: Decimal | None
    ramp_time_seconds: Decimal | None


@dataclass(slots=True, frozen=True)
class SiteControlGroupDefault:
    import_limit_watts: Decimal | None
    export_limit_watts: Decimal | None
    generation_limit_watts: Decimal | None
    load_limit_watts: Decimal | None
    ramp_rate_percent_per_second: Decimal | None

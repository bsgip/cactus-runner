from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

__all__ = [
    "SiteControlWrite",
    "SiteControlGroup",
    "SiteControlGroupWrite",
    "SiteControl",
    "SiteControlGroupDefault",
    "SiteControlGroupDefaultWrite",
]


@dataclass(slots=True, frozen=True)
class SiteControlGroup:
    """Read DTO — mirrors the envoy SiteControlGroup ORM model."""

    site_control_group_id: str
    description: str
    primacy: int
    fsa_id: str | None
    display_id: int | None


@dataclass(slots=True, frozen=True)
class SiteControlGroupWrite:
    """Write DTO — maps to envoy_schema SiteControlGroupRequest."""

    description: str
    primacy: int
    fsa_id: str | None
    display_id: int | None


@dataclass(slots=True, frozen=True)
class SiteControl:
    site_control_id: str
    site_control_group_id: str
    deleted_time: datetime | None
    created_time: datetime


@dataclass(slots=True, frozen=True)
class SiteControlGroupDefault:
    """Read DTO — mirrors the envoy SiteControlGroupDefault ORM model."""

    import_limit_watts: Decimal | None
    export_limit_watts: Decimal | None
    generation_limit_watts: Decimal | None
    load_limit_watts: Decimal | None
    ramp_rate_percent_per_second: Decimal | None


@dataclass(slots=True, frozen=True)
class SiteControlGroupDefaultWrite:
    """Write DTO — maps to envoy_schema SiteControlGroupDefaultRequest.

    cancelled=True signals all unset limit fields should be explicitly nulled
    rather than left unchanged.
    """

    import_limit_watts: Decimal | None
    export_limit_watts: Decimal | None
    generation_limit_watts: Decimal | None
    load_limit_watts: Decimal | None
    ramp_rate_percent_per_second: Decimal | None
    cancelled: bool = False


@dataclass(slots=True, frozen=True)
class SiteControlWrite:
    """Write DTO — maps to envoy_schema SiteControlRequest."""

    site_id: str
    start_time: datetime
    duration_seconds: int
    randomize_start_seconds: int | None = None
    display_id: int | None = None
    set_energized: bool | None = None
    set_connect: bool | None = None
    import_limit_watts: Decimal | None = None
    export_limit_watts: Decimal | None = None
    generation_limit_watts: Decimal | None = None
    load_limit_watts: Decimal | None = None
    set_point_percentage: Decimal | None = None
    ramp_time_seconds: Decimal | None = None

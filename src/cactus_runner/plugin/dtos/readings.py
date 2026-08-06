from dataclasses import dataclass
from datetime import datetime, timedelta

from envoy_schema.server.schema.sep2.types import (
    AccumulationBehaviourType,
    DataQualifierType,
    FlowDirectionType,
    KindType,
    PhaseCode,
    RoleFlagsType,
    UomType,
)

__all__ = ["SiteReadingType", "SiteReading"]


@dataclass(slots=True, frozen=True)
class SiteReadingType:
    """Aggregates SiteReading by the shared common data type (analogous to sep2 MirrorMeterReading/ReadingType)."""

    site_reading_type_id: str
    aggregator_id: str | None
    site_id: str
    mrid: str  # hex string. Uniquely identifies this SiteReadingType for a specific site
    group_id: str
    group_mrid: str
    uom: UomType
    data_qualifier: DataQualifierType
    flow_direction: FlowDirectionType
    accumulation_behaviour: AccumulationBehaviourType
    kind: KindType
    phase: PhaseCode
    power_of_ten_multiplier: int
    role_flags: RoleFlagsType
    created_time: datetime


@dataclass(slots=True, frozen=True)
class SiteReading:
    """The actual underlying time and value readings."""

    site_reading_type_id: str
    time_period_start: datetime
    time_period_duration: timedelta
    value: int  # actual reading value - type/power of ten are defined in the parent reading set
    created_time: datetime  # the time the reading was recorded within the underlying backend

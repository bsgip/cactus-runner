from dataclasses import dataclass
from datetime import datetime, timedelta

from dataclass_wizard import JSONWizard
from envoy_schema.server.schema.sep2.types import (
    AccumulationBehaviourType,
    CommodityType,
    DataQualifierType,
    FlowDirectionType,
    KindType,
    PhaseCode,
    RoleFlagsType,
    UomType,
)

__all__ = ["SiteReadingType", "SiteReading", "SiteReadingTypeFinalReport"]


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
    archive_time: datetime | None
    deleted_time: datetime | None
    changed_time: datetime


@dataclass(slots=True, frozen=True)
class SiteReadingTypeFinalReport(JSONWizard):
    """All fields as required by the original CACTUS envoy backend for final reporting reasons.

    It is not intended to be something that is used within the tests but just that used for providing
    data for reports.
    """

    # TODO: All ids here are currently integer type, to enable backward compatiblity for reporting.
    # To fit in with different backends the ids should become string.
    site_reading_type_id: int
    aggregator_id: int
    site_id: int
    mrid: str
    group_id: int
    group_mrid: str

    uom: UomType
    data_qualifier: DataQualifierType
    flow_direction: FlowDirectionType
    accumulation_behaviour: AccumulationBehaviourType
    kind: KindType
    phase: PhaseCode
    power_of_ten_multiplier: int
    default_interval_seconds: int
    role_flags: RoleFlagsType

    description: str | None
    group_version: int | None
    group_status: int | None
    commodity: CommodityType | None

    created_time: datetime
    changed_time: datetime

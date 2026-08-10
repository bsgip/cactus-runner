from dataclasses import dataclass
from datetime import datetime

from envoy_schema.server.schema.sep2.types import DeviceCategory

__all__ = ["Site", "SiteWrite"]


@dataclass(slots=True, frozen=True)
class Site:
    site_id: str
    nmi: str | None
    lfdi: str
    sfdi: int
    device_category: DeviceCategory


@dataclass(slots=True, frozen=True)
class SiteWrite:
    nmi: str | None
    aggregator_id: str | None
    timezone_id: str | None
    created_time: datetime
    changed_time: datetime
    lfdi: str
    sfdi: int
    device_category: DeviceCategory
    registration_pin: int | None

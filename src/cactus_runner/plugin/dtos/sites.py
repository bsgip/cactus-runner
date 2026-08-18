from dataclasses import dataclass
from datetime import datetime

from dataclass_wizard import JSONWizard
from envoy_schema.server.schema.sep2.types import DeviceCategory

from cactus_runner.plugin.dtos import SiteDERFinalReport

__all__ = ["Site", "SiteWrite", "SiteFinalReport"]


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



@dataclass(slots=True, frozen=True)
class SiteFinalReport(JSONWizard):
    """A special case Site used only for the final reporting purposes.

    It isn't necessary to be used by tests so this has been separated for plugin development
    convenience.
    """
    site_id: str
    nmi: str | None
    aggregator_id: str
    timezone_id: str
    created_time: datetime
    changed_time: datetime
    lfdi: str
    sfdi: int
    device_category: DeviceCategory
    registration_pin: int
    post_rate_seconds: int | None
    site_ders: list[SiteDERFinalReport]

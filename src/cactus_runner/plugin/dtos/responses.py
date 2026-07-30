from dataclasses import dataclass
from datetime import datetime

__all__ = ["SiteControlResponse"]


@dataclass(slots=True, frozen=True)
class SiteControlResponse:
    response_id: int
    site_control_id: int
    response_type: int
    created_time: datetime

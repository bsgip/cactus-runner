from dataclasses import dataclass
from datetime import datetime

from envoy_schema.server.schema.sep2.response import ResponseType

__all__ = ["SiteControlResponse"]


@dataclass(slots=True, frozen=True)
class SiteControlResponse:
    site_control_id: str
    response_type: ResponseType | None
    created_time: datetime

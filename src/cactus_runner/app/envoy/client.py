"""envoy integration
- admin client
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus

from aiohttp import ClientSession, ClientTimeout, TCPConnector
from aiohttp.typedefs import StrOrURL
from envoy_schema.admin.schema.uri import SiteUri, SiteControlGroupUri, SiteControlUri
from envoy_schema.admin.schema.site import SiteResponse
from envoy_schema.admin.schema.site_control import (
    SiteControlGroupRequest,
    SiteControlGroupResponse,
    SiteControlRequest,
    SiteControlResponse,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@dataclass(frozen=True)
class EnvoyConfig:
    admin_username: str  # admin username
    admin_password: str  # admin password
    admin_base_uri: str  # base uri for the admin server endpoints
    tariff_id: int  # tariff_id that all generated tariff's will be assigned to


class EnvoyAdminClient:
    """To be used as singleton NOTE: ClientSession must be closed manually"""

    def __init__(self, base_url: StrOrURL, timeout: int = 30):
        self._base_url = base_url
        self._timeout = ClientTimeout(total=timeout)
        self._session: ClientSession = ClientSession(
            base_url=self._base_url, timeout=self._timeout, connector=TCPConnector(limit=10)
        )

    async def close_session(self):
        await self._session.close()

    async def get_single_site(self, site_id: int) -> SiteResponse:
        async with self._session.get(SiteUri.format(site_id=site_id)) as resp:
            resp.raise_for_status()
            json = await resp.json()
            return SiteResponse(**json)

    async def delete_single_site(self, site_id: int) -> HTTPStatus:
        resp = await self._session.delete(SiteUri.format(site_id=site_id))
        return HTTPStatus(resp.status)

    async def post_site_control_group(self, site_control_group: SiteControlGroupRequest) -> HTTPStatus:
        resp = await self._session.post(SiteControlGroupUri, json=site_control_group.model_dump())
        return HTTPStatus(resp.status)

    async def get_site_control_group(self) -> SiteControlGroupResponse:
        async with self._session.get(SiteControlGroupUri) as resp:
            resp.raise_for_status()
            json = await resp.json()
            return SiteControlGroupResponse(**json)

    async def create_site_controls(self, group_id: int, control_list: list[SiteControlRequest]) -> HTTPStatus:
        resp = await self._session.post(
            SiteControlUri.format(group_id=group_id), json=[site_control.model_dump() for site_control in control_list]
        )
        resp.raise_for_status()
        return HTTPStatus(resp.status)

    async def get_all_site_controls(
        self,
        group_id: int,
        start: int = 0,
        limit: int = 100,
        after: datetime | None = None,
    ) -> SiteControlResponse:
        async with self._session.get(
            SiteControlUri.format(group_id=group_id),
            params={"start": start, "limit": limit} | {"after": after.isoformat()} if after else {},
        ) as resp:
            resp.raise_for_status()
            json = await resp.json()
            return SiteControlResponse(**json)

    async def delete_site_controls_in_range(
        self, group_id: int, period_start: datetime, period_end: datetime
    ) -> HTTPStatus:
        resp = await self._session.delete(
            SiteControlUri.format(group_id=group_id),
            params={"period_start": period_start.isoformat(), "period_end": period_end.isoformat()},
        )
        resp.raise_for_status()
        return HTTPStatus(resp.status)

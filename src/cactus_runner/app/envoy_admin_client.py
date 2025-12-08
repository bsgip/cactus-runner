"""envoy integration
- admin client
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus

from aiohttp import BasicAuth, ClientSession, ClientTimeout, TCPConnector
from aiohttp.typedefs import StrOrURL
from envoy_schema.admin.schema.aggregator import AggregatorPageResponse
from envoy_schema.admin.schema.config import (
    ControlDefaultRequest,
    ControlDefaultResponse,
    RuntimeServerConfigRequest,
    RuntimeServerConfigResponse,
)
from envoy_schema.admin.schema.site import SiteResponse, SiteUpdateRequest
from envoy_schema.admin.schema.site_control import (
    SiteControlGroupPageResponse,
    SiteControlGroupRequest,
    SiteControlGroupResponse,
    SiteControlRequest,
    SiteControlResponse,
    SiteControlPageResponse,
)
from envoy_schema.admin.schema.uri import (
    AggregatorListUri,
    ServerConfigRuntimeUri,
    SiteControlDefaultConfigUri,
    SiteControlGroupListUri,
    SiteControlGroupUri,
    SiteControlRangeUri,
    SiteControlUri,
    SiteUri,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class SecretString:
    def __init__(self, secret: str):
        self._secret = secret

    def __str__(self) -> str:
        return "REDACTED"

    def __repr__(self) -> str:
        return "SecretString(REDACTED)"

    def reveal(self) -> str:
        """Explicitly return"""
        return self._secret


@dataclass(frozen=True)
class EnvoyAdminClientAuthParams:
    username: str  # admin username
    password: str  # admin password


class EnvoyAdminClient:
    """
    Client for interacting with the Envoy Admin API.

    This class is designed to be used as a dependency that gets injected at application startup.
    It internally manages the lifecycle of an aiohttp.ClientSession and expects a call close_session() during
    application cleanup to ensure proper session teardown.
    """

    def __init__(self, base_url: StrOrURL, auth_params: EnvoyAdminClientAuthParams, timeout: int = 30):
        self._base_url = base_url
        self._timeout = ClientTimeout(total=timeout)
        self._session: ClientSession = ClientSession(
            base_url=self._base_url,
            timeout=self._timeout,
            connector=TCPConnector(limit=10),
            auth=BasicAuth(login=auth_params.username, password=auth_params.password),
        )

    async def close_session(self):
        await self._session.close()

    async def get_aggregators(self) -> AggregatorPageResponse:
        async with self._session.get(AggregatorListUri) as resp:
            resp.raise_for_status()
            json = await resp.json()
            return AggregatorPageResponse(**json)

    async def get_single_site(self, site_id: int) -> SiteResponse:
        async with self._session.get(SiteUri.format(site_id=site_id)) as resp:
            resp.raise_for_status()
            json = await resp.json()
            return SiteResponse(**json)

    async def delete_single_site(self, site_id: int) -> HTTPStatus:
        resp = await self._session.delete(SiteUri.format(site_id=site_id))
        resp.raise_for_status()
        return HTTPStatus(resp.status)

    async def update_single_site(self, site_id: int, update_request: SiteUpdateRequest) -> HTTPStatus:
        resp = await self._session.post(SiteUri.format(site_id=site_id), json=update_request.model_dump())
        resp.raise_for_status()
        return HTTPStatus(resp.status)

    async def post_site_control_group(self, site_control_group: SiteControlGroupRequest) -> int:
        resp = await self._session.post(SiteControlGroupListUri, json=site_control_group.model_dump())
        resp.raise_for_status()
        href = resp.headers["Location"]
        return int(href.split("/")[-1])

    async def post_site_control_default(self, site_id: int, control_default: ControlDefaultRequest) -> HTTPStatus:
        resp = await self._session.post(
            SiteControlDefaultConfigUri.format(site_id=site_id),
            data=control_default.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return HTTPStatus(resp.status)

    async def get_site_control_group(self, group_id: int) -> SiteControlGroupResponse:
        async with self._session.get(SiteControlGroupUri.format(group_id=group_id)) as resp:
            resp.raise_for_status()
            json = await resp.json()
            return SiteControlGroupResponse(**json)

    async def get_all_site_control_groups(
        self, start: int = 0, limit: int = 100, after: datetime | None = None
    ) -> SiteControlGroupPageResponse:
        async with self._session.get(
            SiteControlGroupListUri,
            params={"start": start, "limit": limit} | {"after": after.isoformat()} if after else {},
        ) as resp:
            resp.raise_for_status()
            json = await resp.json()
            return SiteControlGroupPageResponse(**json)

    async def create_site_controls(self, group_id: int, control_list: list[SiteControlRequest]) -> HTTPStatus:
        resp = await self._session.post(
            SiteControlUri.format(group_id=group_id),
            data="[" + ",".join([site_control.model_dump_json() for site_control in control_list]) + "]",
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return HTTPStatus(resp.status)

    async def get_all_site_controls(
        self,
        group_id: int,
        start: int = 0,
        limit: int = 100,
        after: datetime | None = None,
    ) -> list[SiteControlResponse]:
        """Fetch all site controls for a group, handling pagination automatically."""
        all_controls: list[SiteControlResponse] = []
        current_start = start

        while True:
            async with self._session.get(
                SiteControlUri.format(group_id=group_id),
                params={"start": current_start, "limit": limit} | ({"after": after.isoformat()} if after else {}),
            ) as resp:
                resp.raise_for_status()
                json = await resp.json()
                page_response = SiteControlPageResponse(**json)

            all_controls.extend(page_response.controls)

            # Check if we've retrieved all controls
            if len(all_controls) >= page_response.total_count or len(page_response.controls) < limit:
                break

            current_start += limit

        return all_controls

    async def delete_site_controls_in_range(
        self, group_id: int, period_start: datetime, period_end: datetime
    ) -> HTTPStatus:
        resp = await self._session.delete(
            SiteControlRangeUri.format(group_id=group_id, period_start=period_start, period_end=period_end),
        )
        resp.raise_for_status()
        return HTTPStatus(resp.status)

    async def update_runtime_config(self, config: RuntimeServerConfigRequest) -> HTTPStatus:
        resp = await self._session.post(ServerConfigRuntimeUri, json=config.model_dump())
        resp.raise_for_status()
        return HTTPStatus(resp.status)

    async def get_runtime_config(self) -> RuntimeServerConfigResponse:
        async with self._session.get(
            ServerConfigRuntimeUri,
        ) as resp:
            resp.raise_for_status()
            json = await resp.json()
            return RuntimeServerConfigResponse(**json)

    async def get_site_control_default(self, site_id: int) -> ControlDefaultResponse:
        async with self._session.get(
            SiteControlDefaultConfigUri.format(site_id=site_id),
        ) as resp:
            resp.raise_for_status()
            json = await resp.json()
            return ControlDefaultResponse(**json)

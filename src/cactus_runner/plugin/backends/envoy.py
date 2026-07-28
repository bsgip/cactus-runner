import logging
from collections.abc import Sequence

from envoy.server.model import Site, SiteDER
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cactus_runner.app.envoy_admin_client import EnvoyAdminClient
from cactus_runner.plugin import dtos
from cactus_runner.plugin.backends.common import RunnerBackend

logger = logging.getLogger(__name__)


def map_envoy_site_to_dto(site: Site) -> dtos.Site:
    """Maps an envoy site to the plugin dto."""
    return dtos.Site(
        site_id=f"{site.site_id}",
        nmi=site.nmi,
        lfdi=site.lfdi,
        sfdi=site.sfdi,
        aggregator_id=site.aggregator_id,
        device_category=site.device_category,
        timezone_id=site.timezone_id,
    )


class EnvoyBackend(RunnerBackend):
    """Backend implementation for Envoy server including SqlAlchemy and HTTP clients."""

    def __init__(self, session: AsyncSession, envoy_client: EnvoyAdminClient | None) -> None:
        self.session = session
        self.envoy_client = envoy_client

    async def get_active_site(self, include_der_settings: bool = False) -> dtos.Site | None:
        """
        Get the "active" site - interpreted as the last site created/modified by the client.

        Args:
            include_der_settings: If True, eagerly load SiteDER and related settings

        Returns:
            The most recently modified Site, or None if no sites exist
        """
        stmt = select(Site).order_by(Site.changed_time.desc()).limit(1)

        if include_der_settings:
            stmt = stmt.options(
                selectinload(Site.site_ders).selectinload(SiteDER.site_der_rating),
                selectinload(Site.site_ders).selectinload(SiteDER.site_der_setting),
                selectinload(Site.site_ders).selectinload(SiteDER.site_der_status),
            )

        site = (await self.session.execute(stmt)).scalar_one_or_none()

        if site:
            logger.debug(f"get_active_site: Resolved site {site.site_id} as the active site / EndDevice")
            return map_envoy_site_to_dto(site)
        else:
            logger.error("get_active_site: There are no sites registered.")
            return None

    async def get_all_sites(self) -> Sequence[dtos.Site]:
        """Fetches every registered Site - ordered by their PK site_id"""
        sites = (await self.session.execute(select(Site).order_by(Site.site_id.asc()))).scalars().all()

        return [map_envoy_site_to_dto(s) for s in sites]

import itertools
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta

from envoy.server.mapper.sep2.pub_sub import SubscriptionMapper
from envoy.server.model import (
    DynamicOperatingEnvelope,
    DynamicOperatingEnvelopeResponse,
    Site,
    SiteDERRating,
    SiteDERSetting,
    SiteDERStatus,
    SiteReading,
    SiteReadingType,
    Subscription,
    TransmitNotificationLog,
)
from envoy.server.model.archive import ArchiveDynamicOperatingEnvelope
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cactus_runner.app.envoy_admin_client import EnvoyAdminClient
from cactus_runner.plugin import dtos
from cactus_runner.plugin.backends.common import RunnerBackend, RunnerBackendTestContext

logger = logging.getLogger(__name__)


def map_envoy_site_to_dto(site: Site) -> dtos.Site:
    """Maps an envoy site to the plugin dto."""
    return dtos.Site(
        site_id=f"{site.site_id}",
        nmi=site.nmi,
        lfdi=site.lfdi,
        sfdi=site.sfdi,
        device_category=site.device_category,
    )


def map_envoy_site_der_settings_to_dto(site_der_settings: SiteDERSetting) -> dtos.SiteDERSetting:
    """Map an envoy db SiteDERSettings to the plugin dto."""
    return dtos.SiteDERSetting(
        grad_w=site_der_settings.grad_w,
        modes_enabled=site_der_settings.modes_enabled,
        doe_modes_enabled=site_der_settings.doe_modes_enabled,
        max_w_value=site_der_settings.max_w_value,
        max_va_value=site_der_settings.max_va_value,
        max_var_value=site_der_settings.max_var_value,
        max_var_neg_value=site_der_settings.max_var_neg_value,
        max_charge_rate_w_value=site_der_settings.max_charge_rate_w_value,
        max_discharge_rate_w_value=site_der_settings.max_discharge_rate_w_value,
        max_wh_value=site_der_settings.max_wh_value,
        min_pf_over_excited_displacement=site_der_settings.min_pf_over_excited_displacement,
        min_pf_under_excited_displacement=site_der_settings.min_pf_under_excited_displacement,
    )


def map_envoy_site_der_ratings_to_dto(site_der_ratings: SiteDERRating) -> dtos.SiteDERRating:
    """Maps an envoy db model SiteDERRating to a backend dto representation."""
    return dtos.SiteDERRating(
        modes_supported=site_der_ratings.modes_supported,
        doe_modes_supported=site_der_ratings.doe_modes_supported,
        max_w_value=site_der_ratings.max_w_value,
        max_va_value=site_der_ratings.max_va_value,
        max_var_value=site_der_ratings.max_var_value,
        max_var_neg_value=site_der_ratings.max_var_neg_value,
        max_charge_rate_w_value=site_der_ratings.max_charge_rate_w_value,
        max_discharge_rate_w_value=site_der_ratings.max_discharge_rate_w_value,
        max_wh_value=site_der_ratings.max_wh_value,
        min_pf_over_excited_displacement=site_der_ratings.min_pf_over_excited_displacement,
        min_pf_under_excited_displacement=site_der_ratings.min_pf_under_excited_displacement,
    )


def map_envoy_site_der_status_to_dto(site_der_status: SiteDERStatus) -> dtos.SiteDERStatus:
    """Maps an Envoy DB modelled SiteDERStatus to a corresponding backend DTO."""
    return dtos.SiteDERStatus(
        alarm_status=site_der_status.alarm_status,
        generator_connect_status=site_der_status.generator_connect_status,
        operational_mode_status=site_der_status.operational_mode_status,
    )


def map_envoy_site_reading_to_dto(site_reading: SiteReading) -> dtos.SiteReading:
    """Maps an Envoy DB SiteReading to CACTUS backend DTO."""
    return dtos.SiteReading(
        site_reading_type_id=f"{site_reading.site_reading_type_id}",
        time_period_start=site_reading.time_period_start,
        time_period_duration=timedelta(seconds=site_reading.time_period_seconds),
        value=site_reading.value,
        created_time=site_reading.created_time,
    )


def map_envoy_subscription_to_dto(subscription: Subscription) -> dtos.Subscription:
    """Maps an Envoy DB Subsription to CACTUS backend DTO."""
    return dtos.Subscription(
        subscription_id=f"{subscription.subscription_id}",
        scoped_site_id=f"{subscription.scoped_site_id}" if subscription.scoped_site_id is not None else None,
        client_aggregator_id=f"{subscription.aggregator_id}",
        resource_type=subscription.resource_type,
        resource_id=f"{subscription.resource_id}" if subscription.resource_id is not None else None,
        notification_uri=subscription.notification_uri,
    )


def map_envoy_site_control_to_dto(
    site_control: DynamicOperatingEnvelope | ArchiveDynamicOperatingEnvelope,
) -> dtos.SiteControl:
    """Maps either an Envoy DOE or ArchiveDOE to CACTUS backend DTO."""
    return dtos.SiteControl(
        site_control_id=f"{site_control.dynamic_operating_envelope_id}",
        deleted_time=site_control.deleted_time if isinstance(site_control, ArchiveDynamicOperatingEnvelope) else None,
    )


def map_envoy_site_control_response_to_dto(response: DynamicOperatingEnvelopeResponse) -> dtos.SiteControlResponse:
    """Maps either an Envoy DOE Response to a CACTUS backend DTO."""
    return dtos.SiteControlResponse(
        site_control_id=f"{response.dynamic_operating_envelope_id_snapshot}",
        response_type=response.response_type,
        created_time=response.created_time,
    )


def map_envoy_transmit_notification_log_to_dto(
    notification_log: TransmitNotificationLog,
) -> dtos.TransmitNotificationLog:
    """Maps a transmit notification log to a CACTUS backend DTO."""
    return dtos.TransmitNotificationLog(
        subscription_id=f"{notification_log.subscription_id_snapshot}",
        http_status_code=notification_log.http_status_code,
    )


def map_envoy_site_reading_type_to_dto(
    site_reading_type: SiteReadingType,
) -> dtos.SiteReadingType:
    """Maps an envoy site reading type to a CACTUS backend DTO."""
    return dtos.SiteReadingType(
        site_reading_type_id=f"{site_reading_type.site_reading_type_id}",
        aggregator_id=f"{site_reading_type.aggregator_id}",
        site_id=f"{site_reading_type.site_id}",
        mrid=site_reading_type.mrid,
        group_id=f"{site_reading_type.group_id}",
        group_mrid=site_reading_type.group_mrid,
        uom=site_reading_type.uom,
        data_qualifier=site_reading_type.data_qualifier,
        flow_direction=site_reading_type.flow_direction,
        accumulation_behaviour=site_reading_type.accumulation_behaviour,
        kind=site_reading_type.kind,
        phase=site_reading_type.phase,
        power_of_ten_multiplier=site_reading_type.power_of_ten_multiplier,
        role_flags=site_reading_type.role_flags,
        created_time=site_reading_type.created_time,
    )


class EnvoyBackend(RunnerBackend):
    """Backend implementation for Envoy server including SqlAlchemy and HTTP clients.

    This implementation is based on usage within the original CACTUS client testing context.
    The lifetime of the envoy server and associated data is only expected exist for the duration of the test.
    It shouldn't be considered applicable to a long lived instance, and will likely need modification to suit.

    Parameters:
        session: SQLAlchemy async session for interacting with DB
        envoy_client: HTTP client for interacting with the envoy admin api.
        test_context: Context to be passed to the backend constructor, passing information
            to be utilised during the backend's existance (i.e. single cactus test run immutable information)
    """

    def __init__(
        self,
        session: AsyncSession,
        envoy_client: EnvoyAdminClient | None = None,
        test_context: RunnerBackendTestContext | None = None,
    ) -> None:
        """All operations necessary to set the backend up ready for test execution."""
        self.session = session
        self.envoy_client = envoy_client
        self.context = test_context

    async def get_active_site(self, include_der_settings: bool = False) -> dtos.Site | None:
        """
        Get the "active" site - interpreted as the last site created/modified by the client.

        Args:
            include_der_settings: If True, eagerly load the site's DER sub-resources (rating/setting/status)

        Returns:
            The most recently modified Site, or None if no sites exist
        """
        stmt = select(Site).order_by(Site.changed_time.desc()).limit(1)

        if include_der_settings:
            stmt = stmt.options(
                selectinload(Site.site_der_rating),
                selectinload(Site.site_der_setting),
                selectinload(Site.site_der_status),
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

    async def get_der_settings(self, site_id: str) -> dtos.SiteDERSetting | None:
        """Returns the first DERSettings encountered for the Site from DB."""
        response = await self.session.execute(
            select(SiteDERSetting).where(SiteDERSetting.site_id == int(site_id)).limit(1)
        )
        result = response.scalar_one_or_none()

        return map_envoy_site_der_settings_to_dto(result) if result is not None else None

    async def get_der_capability(self, site_id: str) -> dtos.SiteDERRating | None:
        """Returns the first DERCapability encountered for the Site from the DB."""
        response = await self.session.execute(
            select(SiteDERRating).where(SiteDERRating.site_id == int(site_id)).limit(1)
        )
        der_rating = response.scalar_one_or_none()

        return map_envoy_site_der_ratings_to_dto(der_rating) if der_rating is not None else None

    async def get_der_status(self, site_id: str) -> dtos.SiteDERStatus | None:
        response = await self.session.execute(
            select(SiteDERStatus).where(SiteDERStatus.site_id == int(site_id)).limit(1)
        )
        der_status = response.scalar_one_or_none()

        return map_envoy_site_der_status_to_dto(der_status) if der_status is not None else None

    async def get_site_readings(
        self,
        site_reading_type_ids: Sequence[str],
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> Sequence[dtos.SiteReading]:
        """Returns the SiteReadings for the given SiteReadingTypes.

        The original envoy implementation does not utilise the start and end time parameters as the envoy
        instance is only expected to live for as long as the test.

        Args:
            site_reading_type_ids: the ids for all related reading types to query on.
            start_time: UNUSED
            end_time: UNUSED
        """
        srt_ids = [int(srt_id) for srt_id in site_reading_type_ids]
        results = await self.session.execute(select(SiteReading).where(SiteReading.site_reading_type_id.in_(srt_ids)))
        readings = results.scalars().all()
        return [map_envoy_site_reading_to_dto(rdg) for rdg in readings]

    async def get_subscriptions(
        self,
        aggregator_client_id: str | None = None,
    ) -> Sequence[dtos.Subscription]:
        """Returns subscriptions, optionally filtered by aggregator."""

        stmt = select(Subscription)

        if aggregator_client_id is not None:
            stmt = stmt.where(Subscription.aggregator_id == int(aggregator_client_id))

        subscriptions = (await self.session.execute(stmt)).scalars().all()

        return [map_envoy_subscription_to_dto(sub) for sub in subscriptions]

    async def get_notification_logs(self) -> Sequence[dtos.TransmitNotificationLog]:
        """Return all notification logs from the DB."""
        all_logs = (await self.session.execute(select(TransmitNotificationLog))).scalars().all()

        return [map_envoy_transmit_notification_log_to_dto(lg) for lg in all_logs]

    async def get_site_controls(self) -> Sequence[dtos.SiteControl]:
        """Return all site controls issued for test even those deleted or archived."""
        controls = (await self.session.execute(select(DynamicOperatingEnvelope))).scalars().all()
        deleted_controls = (await self.session.execute(select(ArchiveDynamicOperatingEnvelope))).scalars().all()

        all_controls = itertools.chain(controls, deleted_controls)

        all_controls = [map_envoy_site_control_to_dto(c) for c in all_controls]
        return all_controls

    async def get_site_control_responses(self) -> Sequence[dtos.SiteControlResponse]:
        """Return all site control responses from the DB."""
        response_result = await self.session.execute(select(DynamicOperatingEnvelopeResponse))
        responses = response_result.scalars().all()

        return [map_envoy_site_control_response_to_dto(r) for r in responses]

    async def parse_subscription_href(self, href: str) -> dtos.SubscriptionHref:
        """Parses the supplied href and returns the corresponding required parameters.

        It leaves any parsing exceptions flow through to be handled by the calling function.
        """
        resource_type, scoped_site_id, resource_id = SubscriptionMapper.parse_resource_href(href)
        return dtos.SubscriptionHref(
            resource_type=resource_type,
            scoped_site_id=f"{scoped_site_id}" if scoped_site_id is not None else None,
            resource_id=f"{resource_id}" if resource_id is not None else None,
        )

    async def get_site_reading_types(self, site_ids: Sequence[str] | None = None) -> Sequence[dtos.SiteReadingType]:
        """Returns all site ids from the DB for the given site ids."""
        stmt = select(SiteReadingType)
        if site_ids:
            stmt = stmt.where(SiteReadingType.site_id.in_([int(sid) for sid in site_ids]))
        results = await self.session.execute(stmt)

        return [map_envoy_site_reading_type_to_dto(srt) for srt in results.scalars().all()]

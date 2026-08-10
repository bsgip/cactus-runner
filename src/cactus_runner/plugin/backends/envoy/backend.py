import itertools
import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from envoy.server.mapper.sep2.pub_sub import SubscriptionMapper
from envoy.server.model import (
    DynamicOperatingEnvelope,
    DynamicOperatingEnvelopeResponse,
    Site,
    SiteControlGroup,
    SiteDERRating,
    SiteDERSetting,
    SiteDERStatus,
    SiteReading,
    SiteReadingType,
    Subscription,
    TransmitNotificationLog,
)
from envoy.server.model.archive import ArchiveDynamicOperatingEnvelope
from envoy_schema.admin.schema.site_control import SiteControlGroupRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cactus_runner.plugin import dtos
from cactus_runner.plugin.backends.common import RunnerBackend, RunnerBackendTestContext
from cactus_runner.plugin.backends.envoy import EnvoyAdminClient, mappers

logger = logging.getLogger(__name__)


class EnvoyBackend(RunnerBackend):
    """Backend implementation for Envoy server using SQLAlchemy for reads and the admin REST API for writes.

    This implementation is scoped to the CACTUS testing context. The lifetime of the envoy server and
    its associated data is expected to match the duration of a single test run; it should not be treated
    as suitable for long-lived or production use without modification.

    Attributes:
        session: SQLAlchemy async session used for all database read operations.
        admin_client: HTTP client for the envoy admin REST API, used for all write operations.
        context: Immutable test-run context set at test initialisation time.
    """

    def __init__(
        self,
        session: AsyncSession,
        admin_client: EnvoyAdminClient,
        test_context: RunnerBackendTestContext | None = None,
    ) -> None:
        """Initialises the backend with a database session and admin API client.

        Args:
            session: An open SQLAlchemy async session bound to the envoy database.
            admin_client: An authenticated EnvoyAdminClient pointed at the running envoy instance.
            test_context: Optional immutable context describing the test run in progress.
        """
        self.session = session
        self.admin_client = admin_client
        self.context = test_context

    async def get_active_site(self, include_der_settings: bool = False) -> dtos.Site | None:
        """Returns the active site, interpreted as the most recently modified EndDevice in the database.

        Args:
            include_der_settings: If True, eagerly loads the site's DER sub-resources
                (rating, setting, and status) in the same query.

        Returns:
            The most recently modified Site, or None if no sites are registered.
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
            return mappers.map_envoy_site_to_dto(site)
        else:
            logger.error("get_active_site: There are no sites registered.")
            return None

    async def get_all_sites(self) -> Sequence[dtos.Site]:
        """Returns all registered sites ordered by site_id ascending."""
        sites = (await self.session.execute(select(Site).order_by(Site.site_id.asc()))).scalars().all()

        return [mappers.map_envoy_site_to_dto(s) for s in sites]

    async def get_der_settings(self, site_id: str) -> dtos.SiteDERSetting | None:
        """Returns the DERSettings for the given site, or None if none are recorded.

        Args:
            site_id: The string-encoded site identifier.

        Returns:
            The first matching SiteDERSetting, or None if the site has no DER settings.
        """
        response = await self.session.execute(
            select(SiteDERSetting).where(SiteDERSetting.site_id == int(site_id)).limit(1)
        )
        result = response.scalar_one_or_none()

        return mappers.map_envoy_site_der_settings_to_dto(result) if result is not None else None

    async def get_der_capability(self, site_id: str) -> dtos.SiteDERRating | None:
        """Returns the DERCapability (rating) for the given site, or None if none are recorded.

        Args:
            site_id: The string-encoded site identifier.

        Returns:
            The first matching SiteDERRating, or None if the site has no DER capability record.
        """
        response = await self.session.execute(
            select(SiteDERRating).where(SiteDERRating.site_id == int(site_id)).limit(1)
        )
        der_rating = response.scalar_one_or_none()

        return mappers.map_envoy_site_der_ratings_to_dto(der_rating) if der_rating is not None else None

    async def get_der_status(self, site_id: str) -> dtos.SiteDERStatus | None:
        """Returns the DERStatus for the given site, or None if none are recorded.

        Args:
            site_id: The string-encoded site identifier.

        Returns:
            The first matching SiteDERStatus, or None if the site has no DER status record.
        """
        response = await self.session.execute(
            select(SiteDERStatus).where(SiteDERStatus.site_id == int(site_id)).limit(1)
        )
        der_status = response.scalar_one_or_none()

        return mappers.map_envoy_site_der_status_to_dto(der_status) if der_status is not None else None

    async def get_site_readings(
        self,
        site_reading_type_ids: Sequence[str],
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> Sequence[dtos.SiteReading]:
        """Returns all SiteReadings for the given SiteReadingType IDs.

        The ``start_time`` and ``end_time`` window parameters are not applied in this implementation.
        Because the envoy instance is scoped to the test lifetime, all recorded readings are relevant
        and time filtering is deferred to the calling check logic.

        Args:
            site_reading_type_ids: IDs of the SiteReadingTypes whose readings should be returned.
            start_time: Unused in this implementation.
            end_time: Unused in this implementation.

        Returns:
            All SiteReadings associated with the supplied SiteReadingType IDs.
        """
        srt_ids = [int(srt_id) for srt_id in site_reading_type_ids]
        results = await self.session.execute(select(SiteReading).where(SiteReading.site_reading_type_id.in_(srt_ids)))
        readings = results.scalars().all()
        return [mappers.map_envoy_site_reading_to_dto(rdg) for rdg in readings]

    async def get_subscriptions(
        self,
        aggregator_client_id: str | None = None,
    ) -> Sequence[dtos.Subscription]:
        """Returns all subscriptions, optionally filtered to a single aggregator.

        Args:
            aggregator_client_id: If provided, only subscriptions belonging to this aggregator are returned.

        Returns:
            All matching subscriptions.
        """

        stmt = select(Subscription)

        if aggregator_client_id is not None:
            stmt = stmt.where(Subscription.aggregator_id == int(aggregator_client_id))

        subscriptions = (await self.session.execute(stmt)).scalars().all()

        return [mappers.map_envoy_subscription_to_dto(sub) for sub in subscriptions]

    async def get_notification_logs(self) -> Sequence[dtos.TransmitNotificationLog]:
        """Returns all transmit notification logs recorded in the database.

        Returns:
            All TransmitNotificationLog entries, in no guaranteed order.
        """
        all_logs = (await self.session.execute(select(TransmitNotificationLog))).scalars().all()

        return [mappers.map_envoy_transmit_notification_log_to_dto(lg) for lg in all_logs]

    async def get_site_controls(self) -> Sequence[dtos.SiteControl]:
        """Returns all site controls ever issued during the test, including deleted and archived entries.

        Both live DynamicOperatingEnvelope rows and their ArchiveDynamicOperatingEnvelope counterparts
        are included so that checks can reason over the full control lifecycle.

        Returns:
            All active, completed, and archived site controls.
        """
        controls = (await self.session.execute(select(DynamicOperatingEnvelope))).scalars().all()
        deleted_controls = (await self.session.execute(select(ArchiveDynamicOperatingEnvelope))).scalars().all()

        all_controls = itertools.chain(controls, deleted_controls)

        all_controls = [mappers.map_envoy_site_control_to_dto(c) for c in all_controls]
        return all_controls

    async def get_site_control_responses(self) -> Sequence[dtos.SiteControlResponse]:
        """Returns all DERControl responses recorded in the database.

        Returns:
            All DynamicOperatingEnvelopeResponse entries, in no guaranteed order.
        """
        response_result = await self.session.execute(select(DynamicOperatingEnvelopeResponse))
        responses = response_result.scalars().all()

        return [mappers.map_envoy_site_control_response_to_dto(r) for r in responses]

    async def parse_subscription_href(self, href: str) -> dtos.SubscriptionHref:
        """Parses a subscription resource href into its component parts.

        Delegates to the envoy SubscriptionMapper. Any parsing errors are intentionally
        propagated to the caller for handling.

        Args:
            href: A subscription resource href as provided by the test definition.

        Returns:
            A SubscriptionHref containing the resolved resource_type, scoped_site_id, and resource_id.

        Raises:
            InvalidMappingError: If the href cannot be parsed into a valid subscription resource reference.
        """
        resource_type, scoped_site_id, resource_id = SubscriptionMapper.parse_resource_href(href)
        return dtos.SubscriptionHref(
            resource_type=resource_type,
            scoped_site_id=f"{scoped_site_id}" if scoped_site_id is not None else None,
            resource_id=f"{resource_id}" if resource_id is not None else None,
        )

    async def get_site_reading_types(self, site_ids: Sequence[str] | None = None) -> Sequence[dtos.SiteReadingType]:
        """Returns SiteReadingTypes from the database, optionally scoped to specific sites.

        Args:
            site_ids: If provided, only reading types belonging to these sites are returned.
                An empty list returns nothing immediately without querying the database.

        Returns:
            All matching SiteReadingType entries.
        """
        # For completeness
        if site_ids == []:
            return []

        stmt = select(SiteReadingType)
        if site_ids is not None:
            stmt = stmt.where(SiteReadingType.site_id.in_([int(sid) for sid in site_ids]))
        results = await self.session.execute(stmt)

        return [mappers.map_envoy_site_reading_type_to_dto(srt) for srt in results.scalars().all()]

    async def get_site_control_groups(self, fsa_ids: Sequence[str] | None = None) -> Sequence[dtos.SiteControlGroup]:
        """Returns DERPrograms from the database, optionally filtered by Function Set Assignment ID.

        Args:
            fsa_ids: If provided, only groups with a matching fsa_id are returned. An empty list
                returns nothing immediately without querying the database. Pass None to return all groups.

        Returns:
            All matching SiteControlGroup entries.
        """
        # For completeness
        if fsa_ids == []:
            return []

        stmt = select(SiteControlGroup)
        if fsa_ids is not None:
            stmt = stmt.where(SiteControlGroup.fsa_id.in_([int(fid) for fid in fsa_ids]))
        results = await self.session.execute(stmt)

        return [mappers.map_envoy_site_control_group_to_dto(cg) for cg in results.scalars().all()]

    async def update_runtime_config(self, config: dtos.RuntimeConfigWrite) -> None:
        """Applies runtime configuration changes to the envoy server via the admin API.

        Args:
            config: The configuration fields to update. Any field set to None is omitted from
                the request and left unchanged on the server.

        Raises:
            aiohttp.ClientResponseError: If the admin API returns a non-2xx response.
        """
        await self.admin_client.update_runtime_config(mappers.map_dto_runtime_config_to_request(config))

    async def create_site_control_group(self, group: dtos.SiteControlGroupWrite) -> str:
        """Creates a new DERProgram via the admin API.

        Args:
            group: The DERProgram definition to create.

        Returns:
            The server-assigned site_control_group_id for the newly created group.

        Raises:
            aiohttp.ClientResponseError: If the admin API returns a non-2xx response.
        """
        return (
            f"{await self.admin_client.post_site_control_group(mappers.map_dto_site_control_group_to_request(group))}"
        )

    async def update_site_control_group(self, group_id: str, group: dtos.SiteControlGroupWrite) -> None:
        """Updates an existing DERProgram via the admin API.

        Args:
            group_id: The string-encoded identifier of the group to update.
            group: The updated DERProgram definition. All fields are replaced on the server.

        Raises:
            aiohttp.ClientResponseError: If the admin API returns a non-2xx response.
        """
        await self.admin_client.put_site_control_group(
            int(group_id), mappers.map_dto_site_control_group_to_request(group)
        )

    async def set_site_control_default(
        self,
        *,
        site_control_group_id: str,
        default: dtos.SiteControlGroupDefaultWrite,
    ) -> None:
        """Sets or updates the DefaultDERControl for a DERProgram via the admin API.

        Fields set to None in ``default`` are treated as "no change" unless ``default.cancelled``
        is True, in which case they are explicitly nulled out on the server (cancelling any
        previously set default value for that field).

        Args:
            site_control_group_id: The string-encoded ID of the DERProgram to update.
            default: The default limit values to apply. Set ``cancelled=True`` to explicitly
                clear all unset limit fields rather than leaving them unchanged.

        Raises:
            aiohttp.ClientResponseError: If the admin API returns a non-2xx response.
        """
        await self.admin_client.post_site_control_default(
            int(site_control_group_id),
            mappers.map_dto_site_control_group_default_to_request(default),
        )

    async def cancel_active_site_controls(self) -> None:
        """Cancels all active DERControls across every DERProgram known to the server.

        Fetches all DERPrograms and issues a delete-in-range spanning the years 2000–2100
        for each, effectively cancelling any currently active or scheduled controls.

        Raises:
            aiohttp.ClientResponseError: If any admin API call returns a non-2xx response.
        """
        groups_response = await self.admin_client.get_all_site_control_groups()
        if groups_response.site_control_groups:
            for g in groups_response.site_control_groups:
                await self.admin_client.delete_site_controls_in_range(
                    g.site_control_group_id,
                    datetime(2000, 1, 1, tzinfo=UTC),
                    datetime(2100, 1, 1, tzinfo=UTC),
                )

    async def create_site_control(
        self,
        control: dtos.SiteControlWrite,
        *,
        site_control_group_id: str,
    ) -> None:
        """Creates a DERControl under the specified DERProgram via the admin API.

        Args:
            control: The DERControl definition to create, including site, timing, and limit fields.
            site_control_group_id: The string-encoded ID of the parent DERProgram.

        Raises:
            aiohttp.ClientResponseError: If the admin API returns a non-2xx response.
        """
        await self.admin_client.create_site_controls(
            int(site_control_group_id),
            [mappers.map_dto_site_control_create_to_request(control)],
        )

    async def update_site_post_rate(self, site_id: str, post_rate_seconds: int) -> None:
        """Updates the postRate for a site via the admin API.

        All other site fields (nmi, timezone_id, device_category) are left unchanged.

        Args:
            site_id: The string-encoded identifier of the site to update.
            post_rate_seconds: The new postRate interval in seconds.

        Raises:
            aiohttp.ClientResponseError: If the admin API returns a non-2xx response.
        """
        await self.admin_client.update_single_site(
            int(site_id), mappers.map_dto_post_rate_to_site_update_request(post_rate_seconds)
        )

    async def delete_all_site_control_groups(self) -> None:
        """Deletes all DERPrograms and all downstream resources via the admin API.

        This includes DERControls, DefaultDERControls, and Function Set Assignments.
        Deletion notifications are dispatched by the server. Intended for use between
        playlist tests to reset control state without tearing down the full envoy instance.

        Raises:
            aiohttp.ClientResponseError: If the admin API returns a non-2xx response.
        """
        await self.admin_client.delete_all_site_control_groups()

    async def delete_site(self, site_id: str) -> None:
        """Deletes a site via the admin API.

        Args:
            site_id: The string-encoded identifier of the site to delete.

        Raises:
            aiohttp.ClientResponseError: If the admin API returns a non-2xx response.
        """
        await self.admin_client.delete_single_site(int(site_id))

    async def remove_function_set_assignment(self, fsa_id: str) -> None:
        """Removes a function set assignment from all site control groups.

        Args:
            fsa_id: The id to query for when selecting which groups to be unassigned.

        Raises:
            aiohttp.ClientResponseError: If the admin API returns a non-2xx response.
        """
        existing_groups = await self.get_site_control_groups()
        for scg in existing_groups:
            if scg.fsa_id == fsa_id:
                logger.info(f"Removing fsa_id {scg.fsa_id} from SiteControlGroup {scg.site_control_group_id}")
                request = SiteControlGroupRequest(
                    description=scg.description, primacy=scg.primacy, fsa_id=None, display_id=scg.display_id
                )
                await self.admin_client.put_site_control_group(int(scg.site_control_group_id), request)

    async def register_site(self, site: dtos.SiteWrite) -> None:
        """Registers a site.

        Args:
            site: particulars

        Raises:
            DB error if fails to commit result.
        """
        self.session.add(mappers.map_dto_site_write_to_envoy(site))
        await self.session.commit()

import logging
from enum import IntEnum
from itertools import chain
from typing import Sequence

from envoy.server.model.archive.doe import (
    ArchiveDynamicOperatingEnvelope,
)
from envoy.server.model.archive.site import ArchiveDefaultSiteControl
from envoy.server.model.doe import DynamicOperatingEnvelope
from envoy.server.model.site import DefaultSiteControl, Site, SiteDER
from envoy.server.model.site_reading import SiteReading, SiteReadingType
from envoy_schema.server.schema.sep2.types import (
    DataQualifierType,
    KindType,
    RoleFlagsType,
    UomType,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

logger = logging.getLogger(__name__)


class ReadingLocation(IntEnum):
    """This is a bitmask of a MUP roleflags that correspond with a "site" or "device" reading location. Combinations
    of bit masks are read from CSIP-Aus - ANNEX A - Reporting DER Data"""

    SITE_READING = int(RoleFlagsType.IS_MIRROR | RoleFlagsType.IS_PREMISES_AGGREGATION_POINT)
    DEVICE_READING = int(RoleFlagsType.IS_MIRROR | RoleFlagsType.IS_DER | RoleFlagsType.IS_SUBMETER)


async def get_active_site(session: AsyncSession, include_der_settings: bool = False) -> Site | None:
    """
    Get the "active" site - interpreted as the last site created/modified by the client.

    Args:
        session: Database session
        include_der_settings: If True, eagerly load SiteDER and related settings

    Returns:
        The most recently modified Site, or None if no sites exist
    """
    stmt = select(Site).order_by(Site.changed_time.desc()).limit(1)

    if include_der_settings:
        stmt = stmt.options(selectinload(Site.site_ders).selectinload(SiteDER.site_der_setting))

    site = (await session.execute(stmt)).scalar_one_or_none()

    if site:
        logger.debug(f"get_active_site: Resolved site {site.site_id} as the active site / EndDevice")
    else:
        logger.error("get_active_site: There are no sites registered.")

    return site


async def get_csip_aus_site_reading_types(
    session: AsyncSession,
    uom: UomType,
    location: ReadingLocation,
    kind: KindType,
    qualifier: DataQualifierType = DataQualifierType.AVERAGE,
) -> Sequence[SiteReadingType]:
    """Finds all SiteReadingTypes (MirrorUsagePoints) for the active site that matches the CSIP-Aus requirements for
    sending/receiving the specified uom at the specified location (defined in CSIP-Aus - Annex A - Reporting DER Data).

    SiteReadingTypes that DON'T meet the minimum CSIP-Aus specifications will NOT be returned by this function.

    location will filter the returned roleFlags according to CSIP-Aus differentiation of site and sub meters

    uom will filter the returned unit of measure. CSIP-Aus defines the following UOMs

    UomType.REAL_POWER_WATT = MANDATORY
    UomType.REACTIVE_POWER_VAR = MANDATORY
    UomType.FREQUENCY_HZ = OPTIONAL
    UomType.VOLTAGE = MANDATORY (at least 1 site or voltage MUP is required)
    UomType.REAL_ENERGY_WATT_HOUR = MANDATORY (for 1.3 storage extensions)


    qualifier will filter the returned DataQualifierType - certain types are optional/mandatory under CSIP-Aus

    DataQualifierType.AVERAGE = MANDATORY (for any mandatory uom - optional otherwise)
    DataQualifierType.NORMAL = OPTIONAL
    DataQualifierType.MINIMUM = OPTIONAL
    DataQualifierType.MAXIMUM = OPTIONAL
    DataQualifierType.INSTANTANEOUS = OPTIONAL (for 1.3 storage extensions)


    Returns the list of all SiteReadingType's that meet this criteria. Expect multiple if multiple phases or
    accumulation behaviors are being reported."""
    site = await get_active_site(session)
    if not site:
        return []

    response = await session.execute(
        select(SiteReadingType)
        .where(
            (SiteReadingType.site_id == site.site_id)
            & (SiteReadingType.role_flags == location)
            & (SiteReadingType.uom == uom)
            & (SiteReadingType.kind == kind)
            & (SiteReadingType.data_qualifier == qualifier)
        )
        .order_by(SiteReadingType.created_time.asc())
    )

    return response.scalars().all()


async def get_site_readings(session: AsyncSession, site_reading_type: SiteReadingType) -> Sequence[SiteReading]:

    response = await session.execute(
        select(SiteReading)
        .where((SiteReading.site_reading_type_id == site_reading_type.site_reading_type_id))
        .order_by(SiteReading.created_time.asc())
    )

    return response.scalars().all()


async def get_reading_counts_grouped_by_reading_type(session: AsyncSession) -> dict[SiteReadingType, int]:
    """Returns the number of readings for each reading type

    Reading types with no readings are NOT returned.
    """
    # TODO this function works by performing two database queries
    # First to get the number of readings (grouped by reading type id)
    # Second to get the reading types (that have readings)
    # These two queries are combined to produced the final mapping:
    # Reading type -> Count of readings
    #
    # These queries could probably be combined into a single query for efficiency.
    count_statement = (
        select(SiteReading.site_reading_type_id, func.count())
        .select_from(SiteReading)
        .group_by(SiteReading.site_reading_type_id)
    )

    count_resp = await session.execute(
        count_statement,
    )

    count_by_site_reading_type_id: dict[int, int] = {}
    for site_reading_type, count in count_resp.all():
        print(f"{site_reading_type=} {count=}")
        count_by_site_reading_type_id[site_reading_type] = count

    site_reading_type_ids = list(count_by_site_reading_type_id.keys())

    reading_types_resp = await session.execute(
        select(SiteReadingType).where(SiteReadingType.site_reading_type_id.in_(site_reading_type_ids))
    )

    count_by_site_reading_type: dict[SiteReadingType, int] = {}
    for reading_type in reading_types_resp.scalars().all():
        count_by_site_reading_type[reading_type] = count_by_site_reading_type_id[reading_type.site_reading_type_id]

    return count_by_site_reading_type


async def get_sites(session: AsyncSession) -> Sequence[Site]:
    statement = (
        select(Site)
        .order_by(Site.site_id.asc())
        .options(
            selectinload(Site.site_ders).selectinload(SiteDER.site_der_availability),
            selectinload(Site.site_ders).selectinload(SiteDER.site_der_rating),
            selectinload(Site.site_ders).selectinload(SiteDER.site_der_setting),
            selectinload(Site.site_ders).selectinload(SiteDER.site_der_status),
        )
    )
    response = await session.execute(statement)
    return response.scalars().all()


async def get_site_controls_active_archived(
    session: AsyncSession,
) -> list[DynamicOperatingEnvelope | ArchiveDynamicOperatingEnvelope]:
    """Includes both active, deleted/cancelled and updated SiteControls."""
    site = await get_active_site(session)
    if not site:
        return []

    active_controls = (
        (
            await session.execute(
                select(DynamicOperatingEnvelope).where(DynamicOperatingEnvelope.site_id == site.site_id)
            )
        )
        .scalars()
        .all()
    )

    deleted_controls = (
        (
            await session.execute(
                select(ArchiveDynamicOperatingEnvelope).where(ArchiveDynamicOperatingEnvelope.site_id == site.site_id)
            )
        )
        .scalars()
        .all()
    )

    return list(chain(active_controls, deleted_controls))


async def get_site_defaults_with_archive(session: AsyncSession) -> list[DefaultSiteControl | ArchiveDefaultSiteControl]:
    """Fetches all DefaultSiteControl's for the active site, both current and historic (including update values)"""
    site = await get_active_site(session)
    if not site:
        return []

    active_control_groups = (
        (await session.execute(select(DefaultSiteControl).where(DefaultSiteControl.site_id == site.site_id)))
        .scalars()
        .all()
    )
    deleted_control_groups = (
        (
            await session.execute(
                select(ArchiveDefaultSiteControl).where(ArchiveDefaultSiteControl.site_id == site.site_id)
            )
        )
        .scalars()
        .all()
    )

    return list(chain(active_control_groups, deleted_control_groups))

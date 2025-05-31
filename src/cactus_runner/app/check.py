import logging
from dataclasses import dataclass
from typing import Any, Optional

from envoy.server.model.site import (
    SiteDER,
    SiteDERRating,
    SiteDERSetting,
    SiteDERStatus,
)
from envoy.server.model.site_reading import SiteReading
from envoy_schema.server.schema.sep2.types import DataQualifierType, UomType
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.app.envoy_common import (
    ReadingLocation,
    get_active_site,
    get_csip_aus_site_reading_types,
)
from cactus_runner.models import ActiveTestProcedure

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """Represents the results of a running a single check"""

    passed: bool  # True if the check is considered passed or successful. False otherwise
    description: Optional[str]  # Human readable description of what the check "considered" or wants to elaborate about


def check_all_steps_complete(
    active_test_procedure: ActiveTestProcedure, resolved_parameters: dict[str, Any]
) -> CheckResult:
    """Implements the "all-steps-complete" check.

    Returns True if all listeners have been marked as removed"""

    # If there are no more active listeners - shortcircuit out as we are done
    if not active_test_procedure.listeners:
        return CheckResult(True, None)

    ignored_steps: set[str] = set(resolved_parameters.get("ignored_steps", []))

    failing_active_steps: list[str] = []
    for active_listener in active_test_procedure.listeners:
        if active_listener.step in ignored_steps:
            logger.debug(f"check_all_steps_complete: Ignoring {active_listener.step}")
            continue
        failing_active_steps.append(active_listener.step)

    if failing_active_steps:
        return CheckResult(False, f"Steps {failing_active_steps} haven't been completed")
    else:
        return CheckResult(True, None)


async def check_connectionpoint_contents(session: AsyncSession) -> CheckResult:
    """Implements the connectionpoint-contents

    Returns pass if the active test site has a connection point"""

    site = await get_active_site(session)
    if site is None:
        return CheckResult(False, "No EndDevice is currently registered")

    if not site.nmi:
        return CheckResult(False, f"EndDevice {site.site_id} has no ConnectionPoint id specified.")

    return CheckResult(True, None)


async def check_der_settings_contents(session: AsyncSession) -> CheckResult:
    """Implements the der-settings-contents check

    Returns pass if DERSettings has been submitted for the active site"""

    site = await get_active_site(session)
    if site is None:
        return CheckResult(False, "No EndDevice is currently registered")

    response = await session.execute(
        select(SiteDERSetting).join(SiteDER).where(SiteDER.site_id == site.site_id).limit(1)
    )
    der_settings = response.scalar_one_or_none()
    if der_settings is None:
        return CheckResult(False, f"No DERSetting found for EndDevice {site.site_id}")

    return CheckResult(True, None)


async def check_der_capability_contents(session: AsyncSession) -> CheckResult:
    """Implements the der-capability-contents check

    Returns pass if DERCapability has been submitted for the active site"""

    site = await get_active_site(session)
    if site is None:
        return CheckResult(False, "No EndDevice is currently registered")

    response = await session.execute(
        select(SiteDERRating).join(SiteDER).where(SiteDER.site_id == site.site_id).limit(1)
    )
    der_rating = response.scalar_one_or_none()
    if der_rating is None:
        return CheckResult(False, f"No DERCapability found for EndDevice {site.site_id}")

    return CheckResult(True, None)


async def check_der_status_contents(session: AsyncSession, resolved_parameters: dict[str, Any]) -> CheckResult:
    """Implements the der-status-contents check

    Returns pass if DERStatus has been submitted for the active site and optionally has certain fields set"""

    site = await get_active_site(session)
    if site is None:
        return CheckResult(False, "No EndDevice is currently registered")

    response = await session.execute(
        select(SiteDERStatus).join(SiteDER).where(SiteDER.site_id == site.site_id).limit(1)
    )
    der_status = response.scalar_one_or_none()
    if der_status is None:
        return CheckResult(False, f"No DERStatus found for EndDevice {site.site_id}")

    # Compare the settings we have against any parameter requirements
    gc_status: int | None = resolved_parameters.get("genConnectStatus", None)
    if gc_status is not None and gc_status != der_status.generator_connect_status:
        return CheckResult(
            False,
            f"DERStatus.genConnectStatus has value {der_status.generator_connect_status} but expected {gc_status}",
        )

    om_status: int | None = resolved_parameters.get("operationalModeStatus", None)
    if om_status is not None and om_status != der_status.operational_mode_status:
        return CheckResult(
            False,
            f"DERStatus.operationalModeStatus has value {der_status.operational_mode_status} but expected {om_status}",
        )

    return CheckResult(True, None)


async def check_readings_site_active_power(session: AsyncSession, resolved_parameters: dict[str, Any]) -> CheckResult:
    average_reading_types = await get_csip_aus_site_reading_types(
        session, UomType.REAL_POWER_WATT, ReadingLocation.SITE_READING, DataQualifierType.AVERAGE
    )

    if not average_reading_types:
        return CheckResult(False, "No site level AVERAGE/REAL_POWER_WATT MirrorUsagePoint for the active EndDevice")

    minimum_count = resolved_parameters.get("minimum_count", None)
    if minimum_count is not None:
        srt_ids = [srt.site_reading_type_id for srt in average_reading_types]
        results = await session.execute(
            select(SiteReading.site_reading_type_id, func.count(SiteReading.site_reading_id))
            .where(SiteReading.site_reading_type_id.in_(srt_ids))
            .group_by(SiteReading.site_reading_type_id)
        )
        for srt_id, count in results.scalars().all():
            if count < minimum_count:
                return CheckResult(False, f"/mup/{srt_id} has {count} Readings. Expected at least {minimum_count}.")

    return CheckResult(True, None)


#     "readings-site-active-power": {"minimum_count": ParameterSchema(True, ParameterType.Integer)},
#     "readings-site-reactive-power": {"minimum_count": ParameterSchema(True, ParameterType.Integer)},
#     "readings-site-voltage": {"minimum_count": ParameterSchema(True, ParameterType.Integer)},
#     "readings-der-active-power": {"minimum_count": ParameterSchema(True, ParameterType.Integer)},
#     "readings-der-reactive-power": {"minimum_count": ParameterSchema(True, ParameterType.Integer)},
#     "readings-der-voltage": {"minimum_count": ParameterSchema(True, ParameterType.Integer)},

import logging
from dataclasses import dataclass
from typing import Any, Optional

from cactus_test_definitions.checks import Check
from envoy.server.exception import InvalidMappingError
from envoy.server.mapper.sep2.pub_sub import SubscriptionMapper
from envoy.server.model.response import DynamicOperatingEnvelopeResponse
from envoy.server.model.site import (
    SiteDER,
    SiteDERRating,
    SiteDERSetting,
    SiteDERStatus,
)
from envoy.server.model.site_reading import SiteReading
from envoy.server.model.subscription import Subscription, TransmitNotificationLog
from envoy_schema.server.schema.sep2.response import ResponseType
from envoy_schema.server.schema.sep2.types import DataQualifierType, UomType
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.app.envoy_common import (
    ReadingLocation,
    get_active_site,
    get_csip_aus_site_reading_types,
)
from cactus_runner.app.variable_resolver import (
    resolve_variable_expressions_from_parameters,
)
from cactus_runner.models import ActiveTestProcedure

logger = logging.getLogger(__name__)


class UnknownCheckError(Exception):
    """Unknown Cactus Runner Check"""


class FailedCheckError(Exception):
    """Check failed to run (raised an exception)"""


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
        return CheckResult(False, f"Steps {", ".join(failing_active_steps)} have not been completed.")
    else:
        return CheckResult(True, None)


async def check_end_device_contents(session: AsyncSession, resolved_parameters: dict[str, Any]) -> CheckResult:
    """Implements the end-device-contents check

    Returns pass if there is an active test site (an optionally checks the contents of that EndDevice)"""

    site = await get_active_site(session)
    if site is None:
        return CheckResult(False, "No EndDevice is currently registered.")

    has_connection_point_id: bool = resolved_parameters.get("has_connection_point_id", False)
    if has_connection_point_id and not site.nmi:
        return CheckResult(False, f"EndDevice {site.site_id} has no ConnectionPoint id specified.")

    return CheckResult(True, None)


async def check_der_settings_contents(session: AsyncSession, resolved_parameters: dict[str, Any]) -> CheckResult:
    """Implements the der-settings-contents check

    Returns pass if DERSettings has been submitted for the active site"""

    site = await get_active_site(session)
    if site is None:
        return CheckResult(False, "No EndDevice is currently registered.")

    response = await session.execute(
        select(SiteDERSetting).join(SiteDER).where(SiteDER.site_id == site.site_id).limit(1)
    )
    der_settings = response.scalar_one_or_none()
    if der_settings is None:
        return CheckResult(False, f"No DERSetting found for EndDevice {site.site_id}.")

    set_grad_w_value: int | None = resolved_parameters.get("setGradW", None)
    if set_grad_w_value is not None and der_settings.grad_w != set_grad_w_value:
        return CheckResult(
            False, f"DERSetting.setGradW {der_settings.grad_w} doesn't match expected {set_grad_w_value}"
        )

    return CheckResult(True, None)


async def check_der_capability_contents(session: AsyncSession) -> CheckResult:
    """Implements the der-capability-contents check

    Returns pass if DERCapability has been submitted for the active site"""

    site = await get_active_site(session)
    if site is None:
        return CheckResult(False, "No EndDevice is currently registered.")

    response = await session.execute(
        select(SiteDERRating).join(SiteDER).where(SiteDER.site_id == site.site_id).limit(1)
    )
    der_rating = response.scalar_one_or_none()
    if der_rating is None:
        return CheckResult(False, f"No DERCapability found for EndDevice {site.site_id}.")

    return CheckResult(True, None)


async def check_der_status_contents(session: AsyncSession, resolved_parameters: dict[str, Any]) -> CheckResult:
    """Implements the der-status-contents check

    Returns pass if DERStatus has been submitted for the active site and optionally has certain fields set"""

    site = await get_active_site(session)
    if site is None:
        return CheckResult(False, "No EndDevice is currently registered.")

    response = await session.execute(
        select(SiteDERStatus).join(SiteDER).where(SiteDER.site_id == site.site_id).limit(1)
    )
    der_status = response.scalar_one_or_none()
    if der_status is None:
        return CheckResult(False, f"No DERStatus found for EndDevice {site.site_id}.")

    # Compare the settings we have against any parameter requirements
    gc_status: int | None = resolved_parameters.get("genConnectStatus", None)
    if gc_status is not None and gc_status != der_status.generator_connect_status:
        return CheckResult(
            False,
            f"DERStatus.genConnectStatus has value {der_status.generator_connect_status} but expected {gc_status}.",
        )

    om_status: int | None = resolved_parameters.get("operationalModeStatus", None)
    if om_status is not None and om_status != der_status.operational_mode_status:
        return CheckResult(
            False,
            f"DERStatus.operationalModeStatus has value {der_status.operational_mode_status} but expected {om_status}.",
        )

    return CheckResult(True, None)


async def do_check_readings_for_types(
    session: AsyncSession, srt_ids: list[int], minimum_count: Optional[int]
) -> CheckResult:
    """Checks the SiteReading table for a specified set of SiteReadingType ID's. Makes sure that all conditions
    are met.

    session: DB session to query
    srt_ids: list of SiteReadingType.site_reading_type values
    minimum_count: If not None - ensure that every SiteReadingType has at least this many SiteReadings

    """
    if minimum_count is not None:
        results = await session.execute(
            select(SiteReading.site_reading_type_id, func.count(SiteReading.site_reading_id))
            .where(SiteReading.site_reading_type_id.in_(srt_ids))
            .group_by(SiteReading.site_reading_type_id)
        )
        count_by_srt_id: dict[int, int] = {srt_id: count for srt_id, count in results.all()}

        for srt_id in srt_ids:
            count = count_by_srt_id.get(srt_id, 0)  # If there is nothing in the DB, we won't get a count back.
            if count < minimum_count:
                return CheckResult(False, f"/mup/{srt_id} has {count} Readings. Expected at least {minimum_count}.")

    return CheckResult(True, None)


async def do_check_site_readings_and_params(
    session,
    resolved_parameters: dict[str, Any],
    uom: UomType,
    reading_location: ReadingLocation,
    data_qualifier: DataQualifierType,
) -> CheckResult:
    average_reading_types = await get_csip_aus_site_reading_types(session, uom, reading_location, data_qualifier)
    if not average_reading_types:
        return CheckResult(False, f"No site level {data_qualifier}/{uom} MirrorUsagePoint for the active EndDevice.")

    srt_ids = [srt.site_reading_type_id for srt in average_reading_types]
    minimum_count: int | None = resolved_parameters.get("minimum_count", None)
    return await do_check_readings_for_types(session, srt_ids, minimum_count)


async def check_readings_site_active_power(session: AsyncSession, resolved_parameters: dict[str, Any]) -> CheckResult:
    """Implements the readings-site-active-power check.

    Will only consider the mandatory "Average" readings"""
    return await do_check_site_readings_and_params(
        session, resolved_parameters, UomType.REAL_POWER_WATT, ReadingLocation.SITE_READING, DataQualifierType.AVERAGE
    )


async def check_readings_site_reactive_power(session: AsyncSession, resolved_parameters: dict[str, Any]) -> CheckResult:
    """Implements the readings-site-reactive-power check.

    Will only consider the mandatory "Average" readings"""
    return await do_check_site_readings_and_params(
        session,
        resolved_parameters,
        UomType.REACTIVE_POWER_VAR,
        ReadingLocation.SITE_READING,
        DataQualifierType.AVERAGE,
    )


async def check_readings_site_voltage(session: AsyncSession, resolved_parameters: dict[str, Any]) -> CheckResult:
    """Implements the readings-site-voltage check.

    Will only consider the mandatory "Average" readings"""
    return await do_check_site_readings_and_params(
        session,
        resolved_parameters,
        UomType.VOLTAGE,
        ReadingLocation.SITE_READING,
        DataQualifierType.AVERAGE,
    )


async def check_readings_der_active_power(session: AsyncSession, resolved_parameters: dict[str, Any]) -> CheckResult:
    """Implements the readings-der-active-power check.

    Will only consider the mandatory "Average" readings"""
    return await do_check_site_readings_and_params(
        session,
        resolved_parameters,
        UomType.REAL_POWER_WATT,
        ReadingLocation.DEVICE_READING,
        DataQualifierType.AVERAGE,
    )


async def check_readings_der_reactive_power(session: AsyncSession, resolved_parameters: dict[str, Any]) -> CheckResult:
    """Implements the readings-der-reactive-power check.

    Will only consider the mandatory "Average" readings"""
    return await do_check_site_readings_and_params(
        session,
        resolved_parameters,
        UomType.REACTIVE_POWER_VAR,
        ReadingLocation.DEVICE_READING,
        DataQualifierType.AVERAGE,
    )


async def check_readings_der_voltage(session: AsyncSession, resolved_parameters: dict[str, Any]) -> CheckResult:
    """Implements the readings-der-voltage check.

    Will only consider the mandatory "Average" readings"""
    return await do_check_site_readings_and_params(
        session,
        resolved_parameters,
        UomType.VOLTAGE,
        ReadingLocation.DEVICE_READING,
        DataQualifierType.AVERAGE,
    )


async def check_all_notifications_transmitted(session: AsyncSession) -> CheckResult:
    """Implements the all-notifications-transmitted check.

    Will assume that 0 transmission logs is a failure to avoid long running timeouts from being overlooked"""

    all_logs = (await session.execute(select(TransmitNotificationLog))).scalars().all()
    if len(all_logs) == 0:
        return CheckResult(False, "No TransmitNotificationLog entries found. Are there active subscriptions?")

    for log in all_logs:
        if log.http_status_code < 200 or log.http_status_code >= 300:
            sub_id = log.subscription_id_snapshot
            return CheckResult(
                False,
                f"/sub/{sub_id} received a HTTP {log.http_status_code} when sending a notification",
            )

    return CheckResult(True, f"All {len(all_logs)} notifications yielded HTTP success codes")


async def check_subscription_contents(resolved_parameters: dict[str, Any], session: AsyncSession) -> CheckResult:
    """Implements the subscription-contents check"""

    subscribed_resource: str = resolved_parameters["subscribed_resource"]  # mandatory param
    active_site = await get_active_site(session)
    if active_site is None:
        return CheckResult(False, "No EndDevice is currently registered")

    # Decode the href so we know what to look for in the DB
    try:
        resource_type, _, resource_id = SubscriptionMapper.parse_resource_href(subscribed_resource)
    except InvalidMappingError as exc:
        logger.error(f"check_subscription_contents: Caught InvalidMappingError for {subscribed_resource}", exc_info=exc)
        return CheckResult(False, f"Unable to interpret resource {subscribed_resource}: {exc.message}")

    matching_sub = (
        await session.execute(
            select(Subscription).where(
                (Subscription.aggregator_id == active_site.aggregator_id)
                & (Subscription.scoped_site_id == active_site.site_id)
                & (Subscription.resource_type == resource_type)
                & (Subscription.resource_id == resource_id)
            )
        )
    ).scalar_one_or_none()
    if matching_sub is None:
        return CheckResult(False, f"Couldn't find a subscription for {subscribed_resource}")

    return CheckResult(True, f"Matched {subscribed_resource} to /sub/{matching_sub.subscription_id}")


def response_type_to_string(t: int | ResponseType | None) -> str:
    if t is None:
        return "N/A"
    elif isinstance(t, ResponseType):
        return f"{t} ({t.value})"
    elif isinstance(t, int):
        try:
            return response_type_to_string(ResponseType(t))
        except Exception:
            return f"({t})"
    else:
        return f"{t}"


async def check_response_contents(resolved_parameters: dict[str, Any], session: AsyncSession) -> CheckResult:
    """Implements the response-contents check by inspecting the response table for site controls"""

    is_latest: bool = resolved_parameters.get("latest", False)
    status_filter: int | None = resolved_parameters.get("status", None)
    status_filter_string = response_type_to_string(status_filter)

    # Latest queries require evaluating ONLY the latest response object
    if is_latest:
        latest_response = (
            await session.execute(
                (
                    select(DynamicOperatingEnvelopeResponse)
                    .order_by(DynamicOperatingEnvelopeResponse.created_time.desc())
                    .limit(1)
                )
            )
        ).scalar_one_or_none()
        if latest_response is None:
            return CheckResult(False, "No responses have been recorded for any DERControls")

        rt_string = response_type_to_string(latest_response.response_type)
        if status_filter is not None and latest_response.response_type != status_filter:
            return CheckResult(
                False,
                f"Latest response expected a response_type of {status_filter_string} but got {rt_string}",
            )

        return CheckResult(True, f"Latest DERControl response of type {rt_string} matches check.")
    else:
        # Otherwise we look for ANY responses that match our request
        any_query = (
            select(DynamicOperatingEnvelopeResponse)
            .order_by(DynamicOperatingEnvelopeResponse.dynamic_operating_envelope_id)
            .limit(1)
        )
        if status_filter is not None:
            any_query = any_query.where(DynamicOperatingEnvelopeResponse.response_type == status_filter)

        matching_response = (await session.execute(any_query)).scalar_one_or_none()
        if matching_response is None:
            return CheckResult(False, f"No DERControl response of type {status_filter_string} was found.")

        return CheckResult(True, f"At least one DERControl response of type {status_filter_string} was found")


async def run_check(check: Check, active_test_procedure: ActiveTestProcedure, session: AsyncSession) -> CheckResult:
    """Runs the particular check for the active test procedure and returns the CheckResult indicating pass/fail.

    Checks describe boolean (readonly) checks like "has the client sent a valid value".

    Args:
        check: The Check to evaluate against the active test procedure.
        active_test_procedure (ActiveTestProcedure): The currently active test procedure.

    Raises:
        UnknownCheckError: Raised if this function has no implementation for the provided `check.type`.
        FailedCheckError: Raised if this function encounters an exception while running the check.
    """
    resolved_parameters = await resolve_variable_expressions_from_parameters(session, check.parameters)
    check_result: CheckResult | None = None
    try:
        match check.type:

            case "all-steps-complete":
                check_result = check_all_steps_complete(active_test_procedure, resolved_parameters)

            case "end-device-contents":
                check_result = await check_end_device_contents(session, resolved_parameters)

            case "der-settings-contents":
                check_result = await check_der_settings_contents(session, resolved_parameters)

            case "der-capability-contents":
                check_result = await check_der_capability_contents(session)

            case "der-status-contents":
                check_result = await check_der_status_contents(session, resolved_parameters)

            case "readings-site-active-power":
                check_result = await check_readings_site_active_power(session, resolved_parameters)

            case "readings-site-reactive-power":
                check_result = await check_readings_site_reactive_power(session, resolved_parameters)

            case "readings-site-voltage":
                check_result = await check_readings_site_voltage(session, resolved_parameters)

            case "readings-der-active-power":
                check_result = await check_readings_der_active_power(session, resolved_parameters)

            case "readings-der-reactive-power":
                check_result = await check_readings_der_reactive_power(session, resolved_parameters)

            case "readings-der-voltage":
                check_result = await check_readings_der_voltage(session, resolved_parameters)

            case "all-notifications-transmitted":
                check_result = await check_all_notifications_transmitted(session)

            case "subscription-contents":
                check_result = await check_subscription_contents(resolved_parameters, session)

            case "response-contents":
                check_result = await check_response_contents(resolved_parameters, session)

    except Exception as exc:
        logger.error(f"Failed performing check {check}", exc_info=exc)
        raise FailedCheckError(f"Failed performing check {check}. {exc}")

    if check_result is None:
        raise UnknownCheckError(f"Unrecognised check '{check.type}'. This is a problem with the test definition")

    logger.info(f"run_check: {check.type} {resolved_parameters} returned {check_result}")
    return check_result


async def determine_check_results(
    checks: list[Check] | None, active_test_procedure: ActiveTestProcedure, session: AsyncSession
) -> dict[str, CheckResult]:
    check_results: dict[str, CheckResult] = {}
    if checks is None:
        return check_results

    for check in checks:
        result = await run_check(check, active_test_procedure, session)
        check_results[check.type] = result
    return check_results


async def all_checks_passing(
    checks: list[Check] | None, active_test_procedure: ActiveTestProcedure, session: AsyncSession
) -> bool:
    """Returns True if every specified check is passing. An empty/unspecified list will return True.



    Raises:
      UnknownCheckError: Raised if this function has no implementation for the provided `check.type`.
      FailedCheckError: Raised if this function encounters an exception while running the check."""

    if not checks:
        logger.debug("all_checks_passing: No checks specified. Returning True.")
        return True

    for check in checks:
        result = await run_check(check, active_test_procedure, session)
        if not result.passed:
            logger.info(f"all_checks_passing: {check} is not passed. Returning False")
            return False

    logger.debug(f"all_checks_passing: Evaluated {len(checks)} and all passed.")
    return True

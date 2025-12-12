import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import chain
from typing import Annotated, Any, Iterable, Optional, Sequence

import pydantic
import pydantic.alias_generators
import pydantic.fields
from cactus_test_definitions import variable_expressions
from cactus_test_definitions.client import Check
from envoy.server.crud.common import convert_lfdi_to_sfdi
from envoy.server.exception import InvalidMappingError
from envoy.server.mapper.sep2.pub_sub import SubscriptionMapper
from envoy.server.model.archive.doe import ArchiveDynamicOperatingEnvelope
from envoy.server.model.doe import DynamicOperatingEnvelope
from envoy.server.model.response import DynamicOperatingEnvelopeResponse
from envoy.server.model.site import (
    SiteDER,
    SiteDERRating,
    SiteDERSetting,
    SiteDERStatus,
)
from envoy.server.model.site_reading import SiteReading, SiteReadingType
from envoy.server.model.subscription import Subscription, TransmitNotificationLog
from envoy_schema.server.schema.sep2.response import ResponseType
from envoy_schema.server.schema.sep2.types import DataQualifierType, KindType, UomType
from sqlalchemy import ColumnElement, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from cactus_runner.app.envoy_common import (
    ReadingLocation,
    get_active_site,
    get_csip_aus_site_reading_types,
    get_site_readings,
)
from cactus_runner.app.evaluator import (
    ResolvedParam,
    resolve_variable_expressions_from_parameters,
)
from cactus_runner.models import ActiveTestProcedure, ClientCertificateType

logger = logging.getLogger(__name__)


class UnknownCheckError(Exception):
    """Unknown Cactus Runner Check"""


class FailedCheckError(Exception):
    """Check failed to run (raised an exception)"""


class SiteReadingTypeProperty:
    name: str

    def __init__(self, name: str):
        self.name = name


class ParamsDERSettingsContents(pydantic.BaseModel):
    """Represents all parameters that could be provided as part of the DERSettings contents check"""

    model_config = pydantic.ConfigDict(alias_generator=pydantic.alias_generators.to_camel)
    doe_modes_enabled: Annotated[
        bool | None, pydantic.Field(alias="doeModesEnabled"), SiteReadingTypeProperty("doe_modes_enabled")
    ] = None
    doe_modes_enabled_set: Annotated[str | None, pydantic.Field(alias="doeModesEnabled_set")] = None
    doe_modes_enabled_unset: Annotated[str | None, pydantic.Field(alias="doeModesEnabled_unset")] = None
    modes_enabled_set: Annotated[str | None, pydantic.Field(alias="modesEnabled_set")] = None
    modes_enabled_unset: Annotated[str | None, pydantic.Field(alias="modesEnabled_unset")] = None
    set_grad_w: Annotated[int | None, pydantic.Field(alias="setGradW")] = None
    set_max_w: Annotated[bool | None, pydantic.Field(alias="setMaxW"), SiteReadingTypeProperty("max_w_value")] = None
    set_max_va: Annotated[bool | None, pydantic.Field(alias="setMaxVA"), SiteReadingTypeProperty("max_va_value")] = None
    set_max_var: Annotated[bool | None, pydantic.Field(alias="setMaxVar"), SiteReadingTypeProperty("max_var_value")] = (
        None
    )
    set_max_var_neg: Annotated[
        bool | None, pydantic.Field(alias="setMaxVarNeg"), SiteReadingTypeProperty("max_var_neg_value")
    ] = None
    set_max_charge_rate_w: Annotated[
        bool | None, pydantic.Field(alias="setMaxChargeRateW"), SiteReadingTypeProperty("max_charge_rate_w_value")
    ] = None
    set_max_discharge_rate_w: Annotated[
        bool | None, pydantic.Field(alias="setMaxDischargeRateW"), SiteReadingTypeProperty("max_discharge_rate_w_value")
    ] = None
    set_max_wh: Annotated[bool | None, pydantic.Field(alias="setMaxWh"), SiteReadingTypeProperty("max_wh_value")] = None
    set_min_wh: Annotated[bool | None, pydantic.Field(alias="setMinWh"), SiteReadingTypeProperty("min_wh_value")] = None
    set_min_pf_over_excited: Annotated[
        bool | None,
        pydantic.Field(alias="setMinPFOverExcited"),
        SiteReadingTypeProperty("min_pf_over_excited_displacement"),
    ] = None
    set_min_pf_under_excited: Annotated[
        bool | None,
        pydantic.Field(alias="setMinPFUnderExcited"),
        SiteReadingTypeProperty("min_pf_under_excited_displacement"),
    ] = None
    vpp_modes_enabled_set: Annotated[str | None, pydantic.Field(alias="vppModesEnabled_set")] = None
    vpp_modes_enabled_unset: Annotated[str | None, pydantic.Field(alias="vppModesEnabled_unset")] = None


class ParamsDERCapabilityContents(pydantic.BaseModel):
    """Represents all parameters that could be provided as part of the DERCapability contents check"""

    model_config = pydantic.ConfigDict(alias_generator=pydantic.alias_generators.to_camel)
    doe_modes_supported: Annotated[
        bool | None, pydantic.Field(alias="doeModesSupported"), SiteReadingTypeProperty("doe_modes_supported")
    ] = None
    doe_modes_supported_set: Annotated[str | None, pydantic.Field(alias="doeModesSupported_set")] = None
    doe_modes_supported_unset: Annotated[str | None, pydantic.Field(alias="doeModesSupported_unset")] = None
    modes_supported_set: Annotated[str | None, pydantic.Field(alias="modesSupported_set")] = None
    modes_supported_unset: Annotated[str | None, pydantic.Field(alias="modesSupported_unset")] = None
    rtg_max_va: Annotated[bool | None, pydantic.Field(alias="rtgMaxVA"), SiteReadingTypeProperty("max_va_value")] = None
    rtg_max_var: Annotated[bool | None, pydantic.Field(alias="rtgMaxVar"), SiteReadingTypeProperty("max_var_value")] = (
        None
    )
    rtg_max_var_neg: Annotated[
        bool | None, pydantic.Field(alias="rtgMaxVarNeg"), SiteReadingTypeProperty("max_var_neg_value")
    ] = None
    rtg_max_w: Annotated[bool | None, pydantic.Field(alias="rtgMaxW"), SiteReadingTypeProperty("max_w_value")] = None
    rtg_max_charge_rate_w: Annotated[
        bool | None, pydantic.Field(alias="rtgMaxChargeRateW"), SiteReadingTypeProperty("max_charge_rate_w_value")
    ] = None
    rtg_max_discharge_rate_w: Annotated[
        bool | None, pydantic.Field(alias="rtgMaxDischargeRateW"), SiteReadingTypeProperty("max_discharge_rate_w_value")
    ] = None
    rtg_max_wh: Annotated[bool | None, pydantic.Field(alias="rtgMaxWh"), SiteReadingTypeProperty("max_wh_value")] = None
    rtg_min_pf_over_excited: Annotated[
        bool | None,
        pydantic.Field(alias="rtgMinPFOverExcited"),
        SiteReadingTypeProperty("min_pf_over_excited_displacement"),
    ] = None
    rtg_min_pf_under_excited: Annotated[
        bool | None,
        pydantic.Field(alias="rtgMinPFUnderExcited"),
        SiteReadingTypeProperty("min_pf_under_excited_displacement"),
    ] = None
    vpp_modes_supported_set: Annotated[str | None, pydantic.Field(alias="vppModesSupported_set")] = None
    vpp_modes_supported_unset: Annotated[str | None, pydantic.Field(alias="vppModesSupported_unset")] = None


@dataclass
class CheckResult:
    """Represents the results of a running a single check"""

    passed: bool  # True if the check is considered passed or successful. False otherwise
    description: Optional[str]  # Human readable description of what the check "considered" or wants to elaborate about


class SoftChecker:
    """Collects all failed results suppressing them until finalized"""

    _failures: list[CheckResult]

    def __init__(self):
        self._failures = []

    def add(self, msg: str) -> None:
        """Adds a new CheckResult to list of failures"""
        self._failures.append(CheckResult(False, msg))

    def finalize(self) -> CheckResult:
        """Finalizes the state of the soft checker and returns a corresponding check result"""
        if len(self._failures) == 0:
            return CheckResult(True, None)
        msg = "; ".join([f.description for f in self._failures if f.description is not None])
        return CheckResult(False, msg)


def merge_checks(checks: list[CheckResult]) -> CheckResult:
    """Merges many CheckResults into a single overall CheckResult.

    If all checks are True, a True CheckResult is returned with concatenated descriptions of all check results.
    If any of the the checks are False, then a False CheckResult is returned with only the False check result
    descriptions concatenated.
    """
    any_checks_false = any([not check.passed for check in checks])
    if any_checks_false:
        # Only merge false check results
        false_check_descriptions = [
            check.description for check in checks if not check.passed and check.description is not None
        ]
        return CheckResult(False, "\n".join(false_check_descriptions))
    else:
        # All check results must be true so merge all of them
        all_descriptions = [check.description for check in checks if check.description is not None]
        return CheckResult(True, "\n".join(all_descriptions))


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


async def check_end_device_contents(
    active_test_procedure: ActiveTestProcedure, session: AsyncSession, resolved_parameters: dict[str, Any]
) -> CheckResult:
    """Implements the end-device-contents check

    Returns pass if there is an active test site.

    Optionally checks:
    - has connection point id set
    - has a non-zero device category set
    - PEN matches the last 32 bits of the aggregator lfdi (PEN ignored if using device lfdi)
    - LFDI is only uppercase hexadecimal characters [0-9A-F]
    """

    site = await get_active_site(session)
    if site is None:
        return CheckResult(False, "No EndDevice is currently registered.")

    has_connection_point_id: bool = resolved_parameters.get("has_connection_point_id", False)
    if has_connection_point_id and not site.nmi:
        return CheckResult(False, f"EndDevice {site.site_id} has no ConnectionPoint id specified.")

    deviceCategory_anyset: int = int(resolved_parameters.get("deviceCategory_anyset", "0"), 16)
    if deviceCategory_anyset and (deviceCategory_anyset & int(site.device_category)) == 0:
        return CheckResult(
            False,
            f"EndDevice {site.site_id} has none of the expected ({deviceCategory_anyset:b}) deviceCategory bits set.",
        )

    check_lfdi: bool = resolved_parameters.get("check_lfdi", False)
    if check_lfdi:
        # Check the LFDI/SFDI of the site
        if re.search("[^A-F0-9]", site.lfdi) is not None:
            return CheckResult(
                False, f"EndDevice lfdi must consist only of UPPERCASE hexadecimal characters. Got '{site.lfdi}'."
            )
        if len(site.lfdi) != 40:
            return CheckResult(False, f"EndDevice lfdi must be 40 hexadecimal characters long. Got {len(site.lfdi)}.")

        expected_sfdi = convert_lfdi_to_sfdi(site.lfdi)
        if expected_sfdi != site.sfdi:
            return CheckResult(
                False,
                f"EndDevice sfdi should be derived from the lfdi. Expected {expected_sfdi} but found {site.sfdi}.",
            )

        # The last 32 bits (8 hex digits) of the aggregator lfdi should match the pen (in base 10)
        if active_test_procedure.client_certificate_type == ClientCertificateType.AGGREGATOR:
            pen = active_test_procedure.pen
            try:
                pen_from_lfdi = int(site.lfdi[-8:])
            except ValueError:
                return CheckResult(False, "Unable to extract PEN from Aggregator LFDI.")
            if pen != pen_from_lfdi:
                return CheckResult(
                    False,
                    f"PEN from lfdi '{pen_from_lfdi}' (last 8 hex digits) does not match '{pen}'. PEN should be decimal encoded.",  # noqa: E501
                )

    return CheckResult(True, None)


def do_field_boolean_expression_evaluated_check(
    soft_checker: SoftChecker,
    db_entity: SiteDERSetting | SiteDERRating,
    field: pydantic.fields.FieldInfo,
    original_expression: variable_expressions.BaseExpression,
) -> None:
    """Checks that a boolean expression is appropriately evaluated for a field within a specified database entity.

    Depends on the type annotation having a SiteReadingTypeProperty ot allow the mapping of field to a specific property
    in db_entity.

    Args:
        soft_checker: Object for holding errors from the check
        db_entity: The object whose properties are interrogated
        field: The field info with Annotated metadata containing a SiteReadingTypeProperty. If not metadata - no check
        original_expression: The expression that the evaluation occurred on
    """
    if not field.metadata:
        # If we don't have metadata - nothing we can check
        return

    property: SiteReadingTypeProperty | None = None
    for m in field.metadata:
        if isinstance(m, SiteReadingTypeProperty):
            property = m
            break

    if property is None:
        # If we don't have metadata - nothing we can check
        return

    actual_value = getattr(db_entity, property.name, None)
    if actual_value is None:
        soft_checker.add(
            f"{field.alias} must satisfy expression '{original_expression.expression_representation()}' "
            "but is currently not set"
        )
    else:
        soft_checker.add(
            f"{field.alias} must satisfy expression '{original_expression.expression_representation()}' "
            f"but is currently set as: {actual_value}"
        )


def do_field_exists_check(
    soft_checker: SoftChecker,
    db_entity: SiteDERSetting | SiteDERRating,
    field: pydantic.fields.FieldInfo,
    expected_to_be_set: bool,
) -> None:
    """Checks for the existence (or non existence) of field within the specified database entity. Depends on the type
    annotation having a SiteReadingTypeProperty to allow the mapping of field to a specific property in db_entity.

    soft_checker: Will report any failures into this object
    db_entity: The object whose properties are interrogated
    field: The field info with Annotated metadata containing a SiteReadingTypeProperty. If not metadata - no check
    expected_to_be_set: True will assert that the property in db_entity is not None. False will assert that it's None
    """
    if not field.metadata:
        # If we don't have metadata - nothing we can check
        return

    property: SiteReadingTypeProperty | None = None
    for m in field.metadata:
        if isinstance(m, SiteReadingTypeProperty):
            property = m
            break

    if property is None:
        # If we don't have metadata - nothing we can check
        return

    actual_value = getattr(db_entity, property.name, None)
    if expected_to_be_set and actual_value is None:
        soft_checker.add(f"{field.alias} MUST be set but is currently missing")
    elif not expected_to_be_set and actual_value is not None:
        soft_checker.add(f"{field.alias} MUST be unset but is currently specified as: {actual_value}")


async def check_der_settings_contents(
    session: AsyncSession, resolved_parameters: dict[str, ResolvedParam]
) -> CheckResult:
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

    # Validate and return model instance
    params = ParamsDERSettingsContents.model_validate({k: v.value for k, v in resolved_parameters.items()})

    # Create soft checker for parameter checks
    soft_checker = SoftChecker()

    # Perform parameter checks
    for k in params.model_fields_set:
        raw_value: Any = getattr(params, k)
        field = params.__pydantic_fields__[k]
        if k == "set_grad_w" and der_settings.grad_w != params.set_grad_w:
            soft_checker.add(f"DERSetting.setGradW {der_settings.grad_w} doesn't match expected {params.set_grad_w}")
        elif k in ["doe_modes_enabled_set", "modes_enabled_set"]:
            # Bitwise assert hi (==1) checks
            params_val = int(raw_value, 16)
            if (getattr(der_settings, k.rstrip("_set")) & params_val) != params_val:
                field = params.__pydantic_fields__[k]
                soft_checker.add(f"DERSetting.{field.alias} minimum flag setting check hi (==1) failed")
        elif k in ["doe_modes_enabled_unset", "modes_enabled_unset"]:
            # Bitwise assert lo (==0) checks
            params_val = int(raw_value, 16)
            if (getattr(der_settings, k.rstrip("_unset")) & params_val) != 0:
                field = params.__pydantic_fields__[k]
                soft_checker.add(f"DERSetting.{field.alias} minimum flag setting check lo (==0) failed")
        elif (
            raw_value is False
            and field.alias is not None
            and resolved_parameters.get(field.alias) is not None
            and (ogl_exp := resolved_parameters[field.alias].original_expression) is not None
        ):
            # A boolean expression was evaluated for this field and potentially failed
            do_field_boolean_expression_evaluated_check(soft_checker, der_settings, field, ogl_exp)
        elif isinstance(raw_value, bool):
            # A set/unset check
            do_field_exists_check(soft_checker, der_settings, field, raw_value)

    return soft_checker.finalize()


async def check_der_capability_contents(
    session: AsyncSession, resolved_parameters: dict[str, ResolvedParam]
) -> CheckResult:
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

    # Validate and return model instance
    params = ParamsDERCapabilityContents.model_validate({k: v.value for k, v in resolved_parameters.items()})

    # Create soft checker for parameter checks
    soft_checker = SoftChecker()

    # Perform parameter checks
    for k in params.model_fields_set:
        raw_value: Any = getattr(params, k)
        field = params.__pydantic_fields__[k]
        if k in ["doe_modes_supported_set", "modes_supported_set"]:
            # Bitwise-and checks
            params_val = int(raw_value, 16)
            if (getattr(der_rating, k.rstrip("_set")) & params_val) != params_val:
                field = params.__pydantic_fields__[k]
                soft_checker.add(f"DERCapability.{field.alias} minimum flag setting check hi (==1) failed")

        if k in ["doe_modes_supported_unset", "modes_supported_unset"]:
            # Bitwise-and checks
            params_val = int(raw_value, 16)
            if (getattr(der_rating, k.rstrip("_unset")) & params_val) != 0:
                field = params.__pydantic_fields__[k]
                soft_checker.add(f"DERCapability.{field.alias} minimum flag setting check lo (==0) failed")
        elif (
            raw_value is False
            and field.alias is not None
            and resolved_parameters.get(field.alias) is not None
            and (ogl_exp := resolved_parameters[field.alias].original_expression) is not None
        ):
            # A boolean expression was evaluated for this field and failed
            do_field_boolean_expression_evaluated_check(soft_checker, der_rating, field, ogl_exp)
        elif isinstance(raw_value, bool):
            do_field_exists_check(soft_checker, der_rating, field, raw_value)

    return soft_checker.finalize()


def is_nth_bit_set_properly(value: int, nth_bit: int, expected: bool) -> bool:
    """Returns true if the n'th bit of value is set (if expected = true) or unset (if expected = false)"""
    return bool(value & (1 << nth_bit)) is expected


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

    alarm_status_val: int | None = resolved_parameters.get("alarmStatus", None)
    if alarm_status_val is not None and der_status.alarm_status != alarm_status_val:
        return CheckResult(
            False, f"DERStatus.alarmStatus was expecting {alarm_status_val} but found {der_status.alarm_status}."
        )

    # Compare the settings we have against any parameter requirements
    gc_status_val = der_status.generator_connect_status
    gc_status_expected: int | None = resolved_parameters.get("genConnectStatus", None)
    if gc_status_expected is not None and gc_status_expected != gc_status_val:
        return CheckResult(
            False,
            f"DERStatus.genConnectStatus has value {gc_status_val} but expected {gc_status_expected}.",
        )

    gc_status_bit0: bool | None = resolved_parameters.get("genConnectStatus_bit0", None)
    gc_status_bit1: bool | None = resolved_parameters.get("genConnectStatus_bit1", None)
    gc_status_bit2: bool | None = resolved_parameters.get("genConnectStatus_bit2", None)
    if gc_status_val is None:
        if gc_status_bit0 is not None:
            return CheckResult(
                False,
                f"DERStatus.genConnectStatus has no value is expecting bit 0 to be {gc_status_bit0}.",
            )
        if gc_status_bit1 is not None:
            return CheckResult(
                False,
                f"DERStatus.genConnectStatus has no value is expecting bit 1 to be {gc_status_bit1}.",
            )
        if gc_status_bit2 is not None:
            return CheckResult(
                False,
                f"DERStatus.genConnectStatus has no value is expecting bit 2 to be {gc_status_bit2}.",
            )
    else:
        if gc_status_bit0 is not None and not is_nth_bit_set_properly(int(gc_status_val), 0, gc_status_bit0):
            return CheckResult(
                False,
                f"DERStatus.genConnectStatus has value {der_status.generator_connect_status} but expected bit 0 to be {gc_status_bit0}.",  # noqa: E501
            )
        if gc_status_bit1 is not None and not is_nth_bit_set_properly(int(gc_status_val), 1, gc_status_bit1):
            return CheckResult(
                False,
                f"DERStatus.genConnectStatus has value {der_status.generator_connect_status} but expected bit 1 to be {gc_status_bit1}.",  # noqa: E501
            )
        if gc_status_bit2 is not None and not is_nth_bit_set_properly(int(gc_status_val), 2, gc_status_bit2):
            return CheckResult(
                False,
                f"DERStatus.genConnectStatus has value {der_status.generator_connect_status} but expected bit 2 to be {gc_status_bit2}.",  # noqa: E501
            )

    om_status: int | None = resolved_parameters.get("operationalModeStatus", None)
    if om_status is not None and om_status != der_status.operational_mode_status:
        return CheckResult(
            False,
            f"DERStatus.operationalModeStatus has value {der_status.operational_mode_status} but expected {om_status}.",
        )

    return CheckResult(True, None)


async def do_check_readings_for_types(
    session: AsyncSession, site_reading_types: Sequence[SiteReadingType], minimum_count: Optional[int]
) -> CheckResult:
    """Checks the SiteReading table for a specified set of SiteReadingType ID's. Makes sure that all conditions
    are met. "Valid" is that at least ONE of the site_reading_types supplied meets the conditions

    session: DB session to query
    site_reading_types: list of SiteReadingType's to check readings
    minimum_count: If not None - ensure that every SiteReadingType has at least this many SiteReadings

    """
    if minimum_count is not None:

        if site_reading_types:
            srt_ids = [srt.site_reading_type_id for srt in site_reading_types]
            results = await session.execute(
                select(SiteReading.site_reading_type_id, func.count(SiteReading.site_reading_id))
                .where(SiteReading.site_reading_type_id.in_(srt_ids))
                .group_by(SiteReading.site_reading_type_id)
            )
            count_by_srt_id: dict[int, int] = {srt_id: count for srt_id, count in results.all()}
        else:
            count_by_srt_id = {}

        # We will scan through the site_reading_types - trying to find at least one that matches
        highest_found_count = 0
        highest_found_mrid = ""
        highest_found_group = 0
        for srt in site_reading_types:
            count = count_by_srt_id.get(srt.site_reading_type_id, 0)
            if count > highest_found_count:
                highest_found_count = count
                highest_found_mrid = srt.mrid
                highest_found_group = srt.group_id

        # If we are here - we didn't find anything. All we can do is report on the "best" set of readings
        # There is a lot of complexity here (what if there are multiple MUPs / MMRs). We will operate under the
        # following assumptions:
        # 1) Clients might register MANY MUPs/MMRs but only submit a minimal subset (and that's OK)
        # 2) Clients will be submitting readings in lockstep - it would be unusual for a client to have 8 voltage
        #    readings and only 3 active power readings (so they are compliant on at least one MMR)
        #
        # If the client breaks these assumptions - they're still getting marked as failing - the error message will
        # just end up being a little less than perfect.
        total_mups = len(set((srt.group_id for srt in site_reading_types)))
        total_mmrs = len(site_reading_types)

        if highest_found_count >= minimum_count:
            return CheckResult(
                True,
                f"MirrorMeterReading {highest_found_mrid} at /mup/{highest_found_group} has {highest_found_count} Readings.",  # noqa: E501
            )
        else:
            return CheckResult(
                False,
                (
                    f"No MirrorMeterReading has sufficient readings. {total_mups} MirrorUsagePoint(s) and {total_mmrs} MirrorMeterReading(s) checked."  # noqa: E501
                    f"Closest was MirrorMeterReading {highest_found_mrid} at /mup/{highest_found_group} with {highest_found_count}/{minimum_count} readings."  # noqa: E501
                    f"Total: {sum(count_by_srt_id.values())} readings were sent of correct uom, role flag, kind, and data qualifier for this test."  # noqa: E501
                ),
            )

    return CheckResult(True, None)


async def do_check_single_level(
    session: AsyncSession,
    site_reading_types: Sequence[SiteReadingType],
    min_level: float | None,
    max_level: float | None,
) -> CheckResult:
    """Checks the SiteReading table for a specified set of SiteReadingType ID's.

    Makes sure that all levels are met. "Valid" is that ALL of the site_reading_types
    supplied meets the conditions. Min max levels are >= and <= respectively for valid result.
    The query retrieves the latest readings, meaning the latest point a time period window for reading
    has occurred i.e. time_period_start + time_period_seconds

    Args:
        session: DB session to query
        site_reading_types: list of SiteReadingType's to check readings
        min_level: If not None - ensure that at all SiteReadingType last SiteReading's value above this
        max_level: If not None - ensure that at all SiteReadingType last SiteReading's value below this

    Returns:
        CheckResult - True if falls above and/or below limits else False
    """
    srt_ids = [srt.site_reading_type_id for srt in site_reading_types]

    # Expression to retrieve the end of the reading window (the point at which the reading is calculated)
    end_time_expr = SiteReading.time_period_start + SiteReading.time_period_seconds * text("interval '1 second'")

    # Step 1: Create a subquery that ranks readings per type by created_time
    ranked_subquery = (
        select(
            SiteReading,
            func.row_number()
            .over(partition_by=SiteReading.site_reading_type_id, order_by=end_time_expr.desc())
            .label("rank"),
        )
        .where(SiteReading.site_reading_type_id.in_(srt_ids))
        .subquery()
    )

    # Step 2: Alias the subquery to access its columns
    RankedReading = aliased(SiteReading, ranked_subquery)

    # Step 3: Join with SiteReadingType and filter to only the latest reading per type
    query = (
        select(RankedReading, SiteReadingType)
        .join(SiteReadingType, SiteReadingType.site_reading_type_id == ranked_subquery.c.site_reading_type_id)
        .where(ranked_subquery.c.rank == 1)
    )

    # Step 4: Execute
    results = await session.execute(query)
    latest_readings = results.all()

    # No readings returned
    if not latest_readings:
        return CheckResult(False, "No readings found for level comparison")

    latest_values = [sr.value * 10**srt.power_of_ten_multiplier for sr, srt in latest_readings]
    failure_msg = ""

    if min_level is not None and any(v < min_level for v in latest_values):
        failure_msg += f"Not all readings above minimum target level of {min_level}."
    if max_level is not None and any(v > max_level for v in latest_values):
        if failure_msg:
            failure_msg += " "
        failure_msg += f"Not all readings below maximum target level of {max_level}."

    return CheckResult(False, f"{failure_msg} Got {latest_values}.") if failure_msg else CheckResult(True, None)


async def do_check_levels_for_period(
    session: AsyncSession,
    site_reading_types: Sequence[SiteReadingType],
    min_level: float | None,
    max_level: float | None,
    window_period: timedelta,
) -> CheckResult:
    """Performs a level check over a specified window of time.

    The end of the window is found by retrieving the max `created_time` of all readings
    corresponding to the supplied `site_reading_types`. The included readings include those that
    have a reading period that lies wholly within the time_period_start >= latest created_time - window_period

    Args:
        session: DB session to query
        site_reading_types: list of SiteReadingType's to check readings
        min_level: If not None ensure that all SiteReadingType SiteReading values above this
        max_level: If not None ensure that all SiteReadingType SiteReading values below this
        window_period: Period of time since last SiteReadingType SiteReading committed to DB that
            comparison is to occur

    Returns:
        CheckResult - True if all readings for window are above and/or below min max levels else False
    """
    srt_ids = [srt.site_reading_type_id for srt in site_reading_types]

    # Expression to retrieve the end of the reading window (the point at which the reading is calculated)
    end_time_expr: ColumnElement[datetime] = SiteReading.time_period_start + SiteReading.time_period_seconds * text(
        "interval '1 second'"
    )

    # Retrieve latest reading entry creation time - should be trigger time
    latest_time_query = select(func.max(SiteReading.created_time)).where(SiteReading.site_reading_type_id.in_(srt_ids))
    latest_time_result = await session.execute(latest_time_query)
    latest_time = latest_time_result.scalar_one()

    # No readings returned
    if latest_time is None:
        return CheckResult(False, "No readings found for level comparison")

    start_time = latest_time - window_period

    # Retrieve all readings within the window. For this we only count those with "completed" reading periods
    # Those periods that have a time_period_start before the start_time are discarded.
    readings_query = (
        select(SiteReading, SiteReadingType)
        .join(SiteReadingType, SiteReading.site_reading_type_id == SiteReadingType.site_reading_type_id)
        .where(
            SiteReading.site_reading_type_id.in_(srt_ids),
            SiteReading.time_period_start >= start_time,
            end_time_expr <= latest_time,
        )
    )

    results = await session.execute(readings_query)
    readings = results.all()

    # No readings returned
    if not readings:
        return CheckResult(False, "No readings found for level comparison")

    # Convert readings to numbers
    window_values = [sr.value * 10**srt.power_of_ten_multiplier for sr, srt in readings]
    failure_msg = ""

    # Confirm readings fall within the window
    if min_level is not None and any(v < min_level for v in window_values):
        failure_msg += f"Not all readings above minimum target level of {min_level}."
    if max_level is not None and any(v > max_level for v in window_values):
        if failure_msg:
            failure_msg += " "
        failure_msg += f"Not all readings below maximum target level of {max_level}."

    return (
        CheckResult(False, f"{failure_msg} Got {window_values}; for window size {window_period.total_seconds()}s.")
        if failure_msg
        else CheckResult(True, None)
    )


async def do_check_reading_levels_for_types(
    session: AsyncSession, site_reading_types: Sequence[SiteReadingType], resolved_parameters: dict[str, Any]
) -> CheckResult:
    """Performs selected reading value level checks.

    It assumes that reading type checks have been performed prior. The type of check depends whether a window
    period has been provided or not. No window period means only the most recent values are checked for level.
    With window period means all readings are checked to have fallen in the acceptable region for values.

    Args:
        session: DB session
        site_reading_types: all SiteReadingTypes confirmed to meet the type requirements
        resolved_parameters: parameter list provided with the check.

    Returns:
        CheckResult with either True for valid readings combination else False.
    """
    max_level = resolved_parameters.get("maximum_level")
    min_level = resolved_parameters.get("minimum_level")
    window_seconds = resolved_parameters.get("window_seconds")
    if all(el is None for el in [max_level, min_level, window_seconds]):
        # Nothing to do, check passes
        return CheckResult(True, None)
    if not window_seconds:
        return await do_check_single_level(session, site_reading_types, min_level, max_level)
    return await do_check_levels_for_period(
        session, site_reading_types, min_level, max_level, timedelta(seconds=window_seconds)
    )


def timestamp_on_minute_boundary(d: datetime) -> bool:
    delta = d - datetime(d.year, d.month, d.day, d.hour, d.minute, tzinfo=d.tzinfo)
    return delta == timedelta(0)


async def do_check_readings_on_minute_boundary(
    session: AsyncSession, site_reading_types: Sequence[SiteReadingType]
) -> CheckResult:
    if site_reading_types:
        srt_ids = [srt.site_reading_type_id for srt in site_reading_types]
        results = await session.execute(
            select(SiteReading.time_period_start).where(SiteReading.site_reading_type_id.in_(srt_ids))
        )
        on_minute_boundary = [timestamp_on_minute_boundary(time_period_start) for time_period_start, in results.all()]
        aligned_count = on_minute_boundary.count(True)
        total_count = len(on_minute_boundary)

        total_mups = len(set((srt.group_id for srt in site_reading_types)))
        total_mmrs = len(site_reading_types)

        if aligned_count != total_count:
            return CheckResult(
                False,
                f"Only {aligned_count}/{total_count} reading(s) align on minute boundaries from {total_mups} MirrorUsagePoint(s) and {total_mmrs} MirrorMeterReading(s). Seconds and milliseconds fields must be 0.",  # noqa: E501
            )
        return CheckResult(
            True,
            f"All {total_count} reading(s) align on minute boundaries from {total_mups} MirrorUsagePoint(s) and {total_mmrs} MirrorMeterReading(s).",  # noqa: E501
        )

    return CheckResult(True, None)


def mrid_matches_pen(pen: int, mrid: str) -> bool:
    # The last 32 bits (8 hex digits) of mrid should match the pen
    try:
        pen_from_mrid = int(mrid[-8:])
    except ValueError:
        return False

    return pen_from_mrid == pen


async def do_check_reading_type_mrids_match_pen(site_reading_types: Sequence[SiteReadingType], pen: int) -> CheckResult:
    if site_reading_types:
        group_mrid_checks = [mrid_matches_pen(pen, srt.group_mrid) for srt in site_reading_types]
        mrid_checks = [mrid_matches_pen(pen, srt.mrid) for srt in site_reading_types]

        srt_count = len(site_reading_types)
        group_mrid_mismatches = group_mrid_checks.count(False)
        mrid_mismatches = mrid_checks.count(False)

        group_mrid_msg = (
            f"{group_mrid_mismatches}/{srt_count} group MRIDS do not match the supplied PEN. (Ensure decimal encoding)."
            if group_mrid_mismatches
            else ""
        )
        mrid_msg = (
            f"{mrid_mismatches}/{srt_count} MRIDS do not match the supplied PEN. (Ensure decimal encoding)."
            if mrid_mismatches
            else ""
        )
        if group_mrid_msg and mrid_msg:
            mrid_msg = f" {mrid_msg}"

        if group_mrid_mismatches or mrid_mismatches:
            return CheckResult(False, f"{group_mrid_msg}{mrid_msg}")
        return CheckResult(
            True,
            "All MRIDS and group MRIDS for the site readings types match the supplied Private Enterprise Number (PEN).",
        )  # noqa: E501

    return CheckResult(True, None)


async def do_check_site_readings_and_params(
    session,
    resolved_parameters: dict[str, Any],
    pen: int,
    uom: UomType,
    reading_location: ReadingLocation,
    data_qualifier: DataQualifierType,
    kind: KindType = KindType.POWER,
    check_duration: bool = True,
) -> CheckResult:

    site_reading_types = await get_csip_aus_site_reading_types(session, uom, reading_location, kind, data_qualifier)
    if not site_reading_types:
        return CheckResult(False, f"No site level {data_qualifier}/{uom} MirrorUsagePoint for the active EndDevice.")

    check_results: list[CheckResult] = []
    if check_duration:
        check_results.append(await do_check_readings_for_duration(session, site_reading_types))

    minimum_count: int | None = resolved_parameters.get("minimum_count", None)
    check_results.append(await do_check_readings_for_types(session, site_reading_types, minimum_count))
    check_results.append(await do_check_reading_levels_for_types(session, site_reading_types, resolved_parameters))
    check_results.append(await do_check_readings_on_minute_boundary(session, site_reading_types))
    check_results.append(await do_check_reading_type_mrids_match_pen(site_reading_types, pen))
    return merge_checks(check_results)


async def do_check_readings_for_duration(
    session: AsyncSession, site_reading_types: Sequence[SiteReadingType]
) -> CheckResult:
    """Check that all readings have non-zero time_period_seconds divisible by 60."""

    zero_count = 0
    non_divisible_count = 0

    for reading_type in site_reading_types:
        reading_data = await get_site_readings(session=session, site_reading_type=reading_type)
        for reading in reading_data:
            if reading.time_period_seconds == 0:
                zero_count += 1
            elif reading.time_period_seconds % 60 != 0:
                non_divisible_count += 1

    if zero_count > 0 or non_divisible_count > 0:
        error_parts = []
        if zero_count > 0:
            error_parts.append(f"{zero_count} readings with zero time_period_seconds")
        if non_divisible_count > 0:
            error_parts.append(f"{non_divisible_count} readings with time_period_seconds not divisible by 60")

        return CheckResult(False, f"{' and '.join(error_parts)} found")

    return CheckResult(True, "All readings have a valid time_period_seconds set")


async def check_readings_site_active_power(
    session: AsyncSession, resolved_parameters: dict[str, Any], pen: int
) -> CheckResult:
    """Implements the readings-site-active-power check.

    Will only consider the mandatory "Average" readings"""
    return await do_check_site_readings_and_params(
        session,
        resolved_parameters,
        pen,
        UomType.REAL_POWER_WATT,
        ReadingLocation.SITE_READING,
        DataQualifierType.AVERAGE,
    )


async def check_readings_site_reactive_power(
    session: AsyncSession, resolved_parameters: dict[str, Any], pen: int
) -> CheckResult:
    """Implements the readings-site-reactive-power check.

    Will only consider the mandatory "Average" readings"""
    return await do_check_site_readings_and_params(
        session,
        resolved_parameters,
        pen,
        UomType.REACTIVE_POWER_VAR,
        ReadingLocation.SITE_READING,
        DataQualifierType.AVERAGE,
    )


async def check_readings_voltage(session: AsyncSession, resolved_parameters: dict[str, Any], pen: int) -> CheckResult:
    """Implements the readings-voltage check.

    Does a check for SITE AND DER voltage - as long as one valid, then this check is passed

    Will only consider the mandatory "Average" readings"""

    site_check = await do_check_site_readings_and_params(
        session,
        resolved_parameters,
        pen,
        UomType.VOLTAGE,
        ReadingLocation.SITE_READING,
        DataQualifierType.AVERAGE,
    )
    if site_check.passed:
        # If they have sent us valid site data - treat it as a pass
        return site_check

    device_check = await do_check_site_readings_and_params(
        session,
        resolved_parameters,
        pen,
        UomType.VOLTAGE,
        ReadingLocation.DEVICE_READING,
        DataQualifierType.AVERAGE,
    )
    if device_check.passed:
        # If they have sent us valid device data - treat it as a pass
        return device_check

    # At this point - we don't have valid site OR device data
    return merge_checks([site_check, device_check])


async def check_readings_der_active_power(
    session: AsyncSession, resolved_parameters: dict[str, Any], pen: int
) -> CheckResult:
    """Implements the readings-der-active-power check.

    Will only consider the mandatory "Average" readings"""
    return await do_check_site_readings_and_params(
        session,
        resolved_parameters,
        pen,
        UomType.REAL_POWER_WATT,
        ReadingLocation.DEVICE_READING,
        DataQualifierType.AVERAGE,
    )


async def check_readings_der_reactive_power(
    session: AsyncSession, resolved_parameters: dict[str, Any], pen: int
) -> CheckResult:
    """Implements the readings-der-reactive-power check.

    Will only consider the mandatory "Average" readings"""
    return await do_check_site_readings_and_params(
        session,
        resolved_parameters,
        pen,
        UomType.REACTIVE_POWER_VAR,
        ReadingLocation.DEVICE_READING,
        DataQualifierType.AVERAGE,
    )


async def check_readings_der_stored_energy(
    session: AsyncSession, resolved_parameters: dict[str, Any], pen: int
) -> CheckResult:
    """Implements the readings-der-stored-energy check.

    Will only consider the mandatory "Instantaneous" readings"""
    return await do_check_site_readings_and_params(
        session,
        resolved_parameters,
        pen,
        UomType.REAL_ENERGY_WATT_HOURS,
        ReadingLocation.DEVICE_READING,
        DataQualifierType.NOT_APPLICABLE,  # TODO: Currently corresponds to 0 but should be called Instantaneous?
        KindType.ENERGY,
        check_duration=False,  # BESS explicitly allows zero duration readings
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


async def check_subscription_contents(
    resolved_parameters: dict[str, Any], session: AsyncSession, active_test_procedure: ActiveTestProcedure
) -> CheckResult:
    """Implements the subscription-contents check"""

    subscribed_resource: str = resolved_parameters["subscribed_resource"]  # mandatory param

    # Decode the href so we know what to look for in the DB
    try:
        resource_type, scoped_site_id, resource_id = SubscriptionMapper.parse_resource_href(subscribed_resource)
    except InvalidMappingError as exc:
        logger.error(f"check_subscription_contents: Caught InvalidMappingError for {subscribed_resource}", exc_info=exc)
        return CheckResult(False, f"Unable to interpret resource {subscribed_resource}: {exc.message}")

    matching_sub = (
        await session.execute(
            select(Subscription).where(
                (Subscription.aggregator_id == active_test_procedure.client_aggregator_id)
                & (Subscription.scoped_site_id == scoped_site_id)
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


def match_all_responses(
    status_str: str,
    controls: Iterable[DynamicOperatingEnvelope | ArchiveDynamicOperatingEnvelope],
    responses: Sequence[DynamicOperatingEnvelopeResponse],
) -> CheckResult:
    responses_by_doe_id: dict[int, list[DynamicOperatingEnvelopeResponse]] = {}
    for r in responses:
        existing = responses_by_doe_id.get(r.dynamic_operating_envelope_id_snapshot, None)
        if existing is None:
            responses_by_doe_id[r.dynamic_operating_envelope_id_snapshot] = [r]
        else:
            existing.append(r)

    unmatched_controls: int = 0
    for c in controls:
        if c.dynamic_operating_envelope_id not in responses_by_doe_id:
            unmatched_controls += 1

    if unmatched_controls > 0:
        return CheckResult(
            False, f"{unmatched_controls} DERControl(s) failed to receive a Response with a status of {status_str}"
        )
    else:
        return CheckResult(True, f"All DERControl(s) have a Response with a status of {status_str}")


async def check_response_contents(
    resolved_parameters: dict[str, Any], session: AsyncSession, active_test_procedure: ActiveTestProcedure
) -> CheckResult:
    """Implements the response-contents check by inspecting the response table for site controls"""

    is_latest: bool = resolved_parameters.get("latest", False)
    is_all: bool = resolved_parameters.get("all", False)
    status_filter: int | None = resolved_parameters.get("status", None)
    status_filter_string: str = response_type_to_string(status_filter)
    subject_tag: Optional[str] = resolved_parameters.get("subject_tag", None)

    # Handle the "all" case separately
    if is_all:
        controls = (await session.execute(select(DynamicOperatingEnvelope))).scalars().all()
        deleted_controls = (
            (
                await session.execute(
                    select(ArchiveDynamicOperatingEnvelope).where(
                        ArchiveDynamicOperatingEnvelope.deleted_time.is_not(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        response_stmt = select(DynamicOperatingEnvelopeResponse)
        if status_filter is not None:
            response_stmt = response_stmt.where(DynamicOperatingEnvelopeResponse.response_type == status_filter)
        responses = (await session.execute(response_stmt)).scalars().all()
        return match_all_responses(status_filter_string, chain(controls, deleted_controls), responses)

    # For other cases: Start by building base query
    query = select(DynamicOperatingEnvelopeResponse)

    # Apply tag filter
    context_description = ""
    if subject_tag is not None:
        control_id = active_test_procedure.resource_annotations.der_control_ids_by_alias.get(subject_tag)
        if control_id is None:
            return CheckResult(False, f"No DERControl found with tag: {subject_tag}")

        query = query.where(DynamicOperatingEnvelopeResponse.dynamic_operating_envelope_id_snapshot == control_id)
        context_description = f" for tag {subject_tag}"

    # Handle the "latest" case - get latest first, then check status
    if is_latest:
        query = query.order_by(DynamicOperatingEnvelopeResponse.created_time.desc()).limit(1)
        matching_response = (await session.execute(query)).scalar_one_or_none()

        if matching_response is None:
            return CheckResult(False, f"No responses found{context_description}")

        # Now check if it matches the status filter (if provided)
        if status_filter is not None and matching_response.response_type != status_filter:
            rt_string = response_type_to_string(matching_response.response_type)
            expected_string = response_type_to_string(status_filter)
            return CheckResult(
                False, f"Latest response{context_description} is of type {rt_string}, not {expected_string}"
            )

        rt_string = response_type_to_string(matching_response.response_type)
        return CheckResult(True, f"Latest DERControl response{context_description} of type {rt_string} matches check")

    # For non-latest case: Apply status filter before executing
    if status_filter is not None:
        query = query.where(DynamicOperatingEnvelopeResponse.response_type == status_filter)

    query = query.limit(1)
    matching_response = (await session.execute(query)).scalar_one_or_none()

    # Handle no results
    if matching_response is None:
        if status_filter is not None:
            return CheckResult(False, f"No responses of type {status_filter_string} found{context_description}")
        return CheckResult(False, f"No responses found{context_description}")

    # Build success message
    rt_string = response_type_to_string(matching_response.response_type)
    return CheckResult(True, f"At least one DERControl response{context_description} of type {rt_string} was found")


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
    resolved_with_metadata_parameters = await resolve_variable_expressions_from_parameters(session, check.parameters)
    resolved_parameters = {k: v.value for k, v in resolved_with_metadata_parameters.items()}
    check_result: CheckResult | None = None
    pen: int = active_test_procedure.pen
    try:
        match check.type:

            case "all-steps-complete":
                check_result = check_all_steps_complete(active_test_procedure, resolved_parameters)

            case "end-device-contents":
                check_result = await check_end_device_contents(active_test_procedure, session, resolved_parameters)

            case "der-settings-contents":
                check_result = await check_der_settings_contents(session, resolved_with_metadata_parameters)

            case "der-capability-contents":
                check_result = await check_der_capability_contents(session, resolved_with_metadata_parameters)

            case "der-status-contents":
                check_result = await check_der_status_contents(session, resolved_parameters)

            case "readings-site-active-power":
                check_result = await check_readings_site_active_power(session, resolved_parameters, pen)

            case "readings-site-reactive-power":
                check_result = await check_readings_site_reactive_power(session, resolved_parameters, pen)

            case "readings-voltage":
                check_result = await check_readings_voltage(session, resolved_parameters, pen)

            case "readings-der-active-power":
                check_result = await check_readings_der_active_power(session, resolved_parameters, pen)

            case "readings-der-reactive-power":
                check_result = await check_readings_der_reactive_power(session, resolved_parameters, pen)

            case "readings-der-stored-energy":
                check_result = await check_readings_der_stored_energy(session, resolved_parameters, pen)

            case "all-notifications-transmitted":
                check_result = await check_all_notifications_transmitted(session)

            case "subscription-contents":
                check_result = await check_subscription_contents(resolved_parameters, session, active_test_procedure)

            case "response-contents":
                check_result = await check_response_contents(resolved_parameters, session, active_test_procedure)

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


async def first_failing_check(
    checks: list[Check] | None, active_test_procedure: ActiveTestProcedure, session: AsyncSession
) -> CheckResult | None:
    """Iterates through checks - looking for the first Check that returns a failing CheckResult. If all checks are
    passing, returns None

    Raises:
      UnknownCheckError: Raised if this function has no implementation for the provided `check.type`.
      FailedCheckError: Raised if this function encounters an exception while running the check."""

    if not checks:
        return None

    for check in checks:
        result = await run_check(check, active_test_procedure, session)
        if not result.passed:
            logger.info(f"{check} is not passing: {result}.")
            return result

    logger.debug(f"Evaluated {len(checks)} and all passed.")
    return None


async def all_checks_passing(
    checks: list[Check] | None, active_test_procedure: ActiveTestProcedure, session: AsyncSession
) -> bool:
    """Returns True if every specified check is passing. An empty/unspecified list will return True.

    Raises:
      UnknownCheckError: Raised if this function has no implementation for the provided `check.type`.
      FailedCheckError: Raised if this function encounters an exception while running the check."""

    failing_check = await first_failing_check(checks, active_test_procedure, session)
    return failing_check is None

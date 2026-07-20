import datetime as dt
import logging
import secrets

import sqlalchemy as sa
from cactus_test_definitions import errors
from envoy.server import model
from envoy.server.mapper import common
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.models import ActiveTestProcedure

logger = logging.getLogger(__name__)

RANDOM_URI_ATTEMPTS = 20
RANDOM_URI_LENGTH = 16


def resolve_named_variable_now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


async def _select_single_site_der_setting(session: AsyncSession, variable_name: str) -> model.SiteDERSetting:
    # Fetch the most recently edited SiteDERSetting
    try:
        response = await session.execute(
            sa.select(model.SiteDERSetting).order_by(model.SiteDERSetting.changed_time.desc()).limit(1)
        )
        site_der_setting = response.scalar_one_or_none()
    except Exception as err:
        raise errors.UnresolvableVariableError(f"Unable to fetch DERSetting from database: {err}") from err

    if site_der_setting is None:
        raise errors.UnresolvableVariableError(f"Unable to find a suitable DERSetting to resolve {variable_name}")

    return site_der_setting


async def _select_single_site_der_rating(session: AsyncSession, variable_name: str) -> model.SiteDERRating:
    # Fetch the most recently edited SiteDERRating
    try:
        response = await session.execute(
            sa.select(model.SiteDERRating).order_by(model.SiteDERRating.changed_time.desc()).limit(1)
        )
        site_der_rating = response.scalar_one_or_none()
    except Exception as err:
        raise errors.UnresolvableVariableError(f"Unable to fetch DERCapability from database: {err}") from err

    if site_der_rating is None:
        raise errors.UnresolvableVariableError(f"Unable to find a suitable DERCapability to resolve {variable_name}")

    return site_der_rating


"""DER Settings"""


async def resolve_named_variable_der_setting_max_w(session: AsyncSession) -> float:
    site_der_setting = await _select_single_site_der_setting(session, "setMaxW")
    set_max_w = common.pow10_to_decimal_value(site_der_setting.max_w_value, site_der_setting.max_w_multiplier)
    if set_max_w is None:
        raise errors.UnresolvableVariableError("Unable to extract setMaxW from DERSetting")

    return float(set_max_w)


async def resolve_named_variable_der_setting_max_va(session: AsyncSession) -> float:
    site_der_setting = await _select_single_site_der_setting(session, "setMaxVA")
    set_max_va = common.pow10_to_decimal_value(site_der_setting.max_va_value, site_der_setting.max_va_multiplier)
    if set_max_va is None:
        raise errors.UnresolvableVariableError("Unable to extract setMaxVA from DERSetting")

    return float(set_max_va)


async def resolve_named_variable_der_setting_max_var(session: AsyncSession) -> float:
    site_der_setting = await _select_single_site_der_setting(session, "setMaxVar")
    set_max_var = common.pow10_to_decimal_value(site_der_setting.max_var_value, site_der_setting.max_var_multiplier)
    if set_max_var is None:
        raise errors.UnresolvableVariableError("Unable to extract setMaxVar from DERSetting")

    return float(set_max_var)


async def resolve_named_variable_der_setting_max_var_neg(session: AsyncSession) -> float:
    site_der_setting = await _select_single_site_der_setting(session, "setMaxVarNeg")
    set_max_var_neg = common.pow10_to_decimal_value(
        site_der_setting.max_var_neg_value, site_der_setting.max_var_neg_multiplier
    )
    if set_max_var_neg is None:
        raise errors.UnresolvableVariableError("Unable to extract setMaxVar from DERSetting")

    return float(set_max_var_neg)


async def resolve_named_variable_der_setting_max_charge_rate_w(session: AsyncSession) -> float:
    site_der_setting = await _select_single_site_der_setting(session, "setMaxChargeRateW")
    set_max_charge_rate_w = common.pow10_to_decimal_value(
        site_der_setting.max_charge_rate_w_value, site_der_setting.max_charge_rate_w_multiplier
    )
    if set_max_charge_rate_w is None:
        raise errors.UnresolvableVariableError("Unable to extract setMaxChargeRateW from DERSetting")

    return float(set_max_charge_rate_w)


async def resolve_named_variable_der_setting_max_discharge_rate_w(session: AsyncSession) -> float:
    site_der_setting = await _select_single_site_der_setting(session, "setMaxDischargeRateW")
    set_max_discharge_rate_w = common.pow10_to_decimal_value(
        site_der_setting.max_discharge_rate_w_value, site_der_setting.max_discharge_rate_w_multiplier
    )
    if set_max_discharge_rate_w is None:
        raise errors.UnresolvableVariableError("Unable to extract setMaxDischargeRateW from DERSetting")

    return float(set_max_discharge_rate_w)


async def resolve_named_variable_der_setting_max_import_w(session: AsyncSession) -> float:
    # Directional import limit: prefer the asymmetric setMaxChargeRateW, falling back to the mandatory setMaxW
    site_der_setting = await _select_single_site_der_setting(session, "maxImportW")
    max_import_w = common.pow10_to_decimal_value(
        site_der_setting.max_charge_rate_w_value, site_der_setting.max_charge_rate_w_multiplier
    )
    if max_import_w is None:
        max_import_w = common.pow10_to_decimal_value(site_der_setting.max_w_value, site_der_setting.max_w_multiplier)
    if max_import_w is None:
        raise errors.UnresolvableVariableError("Unable to extract setMaxChargeRateW or setMaxW from DERSetting")

    return float(max_import_w)


async def resolve_named_variable_der_setting_max_export_w(session: AsyncSession) -> float:
    # Directional export limit: prefer the asymmetric setMaxDischargeRateW, falling back to the mandatory setMaxW
    site_der_setting = await _select_single_site_der_setting(session, "maxExportW")
    max_export_w = common.pow10_to_decimal_value(
        site_der_setting.max_discharge_rate_w_value, site_der_setting.max_discharge_rate_w_multiplier
    )
    if max_export_w is None:
        max_export_w = common.pow10_to_decimal_value(site_der_setting.max_w_value, site_der_setting.max_w_multiplier)
    if max_export_w is None:
        raise errors.UnresolvableVariableError("Unable to extract setMaxDischargeRateW or setMaxW from DERSetting")

    return float(max_export_w)


async def resolve_named_variable_der_setting_min_pf_over_excited(session: AsyncSession) -> float:
    site_der_setting = await _select_single_site_der_setting(session, "setMinPFOverExcited")
    set_min_pf_over_excited = common.pow10_to_decimal_value(
        site_der_setting.min_pf_over_excited_displacement, site_der_setting.min_pf_over_excited_multiplier
    )
    if set_min_pf_over_excited is None:
        raise errors.UnresolvableVariableError("Unable to extract setMinPFOverExcited from DERSetting")

    return float(set_min_pf_over_excited)


async def resolve_named_variable_der_setting_min_pf_under_excited(session: AsyncSession) -> float:
    site_der_setting = await _select_single_site_der_setting(session, "setMinPFUnderExcited")
    set_min_pf_under_excited = common.pow10_to_decimal_value(
        site_der_setting.min_pf_under_excited_displacement, site_der_setting.min_pf_under_excited_multiplier
    )
    if set_min_pf_under_excited is None:
        raise errors.UnresolvableVariableError("Unable to extract setMinPFUnderExcited from DERSetting")

    return float(set_min_pf_under_excited)


async def resolve_named_variable_der_setting_max_wh(session: AsyncSession) -> float:
    site_der_setting = await _select_single_site_der_setting(session, "setMaxWh")
    set_max_wh = common.pow10_to_decimal_value(site_der_setting.max_wh_value, site_der_setting.max_wh_multiplier)
    if set_max_wh is None:
        raise errors.UnresolvableVariableError("Unable to extract setMaxWh from DERSetting")

    return float(set_max_wh)


"""DER Capability"""


async def resolve_named_variable_der_rating_max_w(session: AsyncSession) -> float:
    site_der_rating = await _select_single_site_der_rating(session, "rtgMaxW")
    rtg_max_w = common.pow10_to_decimal_value(site_der_rating.max_w_value, site_der_rating.max_w_multiplier)
    if rtg_max_w is None:
        raise errors.UnresolvableVariableError("Unable to extract rtgMaxW from DERCapability")

    return float(rtg_max_w)


async def resolve_named_variable_der_rating_max_va(session: AsyncSession) -> float:
    site_der_rating = await _select_single_site_der_rating(session, "rtgMaxVA")
    rtg_max_va = common.pow10_to_decimal_value(site_der_rating.max_va_value, site_der_rating.max_va_multiplier)
    if rtg_max_va is None:
        raise errors.UnresolvableVariableError("Unable to extract rtgMaxVA from DERCapability")

    return float(rtg_max_va)


async def resolve_named_variable_der_rating_max_var(session: AsyncSession) -> float:
    site_der_rating = await _select_single_site_der_rating(session, "rtgMaxVar")
    rtg_max_var = common.pow10_to_decimal_value(site_der_rating.max_var_value, site_der_rating.max_var_multiplier)
    if rtg_max_var is None:
        raise errors.UnresolvableVariableError("Unable to extract rtgMaxVar from DERCapability")

    return float(rtg_max_var)


async def resolve_named_variable_der_rating_max_var_neg(session: AsyncSession) -> float:
    site_der_rating = await _select_single_site_der_rating(session, "rtgMaxVarNeg")
    rtg_max_var_neg = common.pow10_to_decimal_value(
        site_der_rating.max_var_neg_value, site_der_rating.max_var_neg_multiplier
    )
    if rtg_max_var_neg is None:
        raise errors.UnresolvableVariableError("Unable to extract rtgMaxVarNeg from DERCapability")

    return float(rtg_max_var_neg)


async def resolve_named_variable_der_rating_max_charge_rate_w(session: AsyncSession) -> float:
    site_der_rating = await _select_single_site_der_rating(session, "rtgMaxChargeRateW")
    rtg_max_charge_rate_w = common.pow10_to_decimal_value(
        site_der_rating.max_charge_rate_w_value, site_der_rating.max_charge_rate_w_multiplier
    )
    if rtg_max_charge_rate_w is None:
        raise errors.UnresolvableVariableError("Unable to extract rtgMaxChargeRateW from DERCapability")

    return float(rtg_max_charge_rate_w)


async def resolve_named_variable_der_rating_max_discharge_rate_w(session: AsyncSession) -> float:
    site_der_rating = await _select_single_site_der_rating(session, "rtgMaxDischargeRateW")
    rtg_max_discharge_rate_w = common.pow10_to_decimal_value(
        site_der_rating.max_discharge_rate_w_value, site_der_rating.max_discharge_rate_w_multiplier
    )
    if rtg_max_discharge_rate_w is None:
        raise errors.UnresolvableVariableError("Unable to extract rtgMaxDischargeRateW from DERCapability")

    return float(rtg_max_discharge_rate_w)


async def resolve_named_variable_der_rating_min_pf_over_excited(session: AsyncSession) -> float:
    site_der_rating = await _select_single_site_der_rating(session, "rtgMinPFOverExcited")
    rtg_min_pf_over_excited = common.pow10_to_decimal_value(
        site_der_rating.min_pf_over_excited_displacement, site_der_rating.min_pf_over_excited_multiplier
    )
    if rtg_min_pf_over_excited is None:
        raise errors.UnresolvableVariableError("Unable to extract rtgMinPFOverExcited from DERCapability")

    return float(rtg_min_pf_over_excited)


async def resolve_named_variable_der_rating_min_pf_under_excited(session: AsyncSession) -> float:
    site_der_rating = await _select_single_site_der_rating(session, "rtgMinPFUnderExcited")
    rtg_min_pf_under_excited = common.pow10_to_decimal_value(
        site_der_rating.min_pf_under_excited_displacement, site_der_rating.min_pf_under_excited_multiplier
    )
    if rtg_min_pf_under_excited is None:
        raise errors.UnresolvableVariableError("Unable to extract rtgMinPFUnderExcited from DERCapability")

    return float(rtg_min_pf_under_excited)


async def resolve_named_variable_der_rating_max_wh(session: AsyncSession) -> float:
    site_der_rating = await _select_single_site_der_rating(session, "rtgMaxWh")
    rtg_max_wh = common.pow10_to_decimal_value(site_der_rating.max_wh_value, site_der_rating.max_wh_multiplier)
    if rtg_max_wh is None:
        raise errors.UnresolvableVariableError("Unable to extract rtgMaxWh from DERCapability")

    return float(rtg_max_wh)


def candidate_random_uri() -> str:
    return f"/{secrets.token_urlsafe(RANDOM_URI_LENGTH)}"


def resolve_random_uri(active_test_procedure: ActiveTestProcedure, randuri_key: str) -> str:
    random_uri_value = active_test_procedure.random_values.random_uri_by_key.get(randuri_key)
    if random_uri_value is not None:
        return random_uri_value

    # We need to generate a unique candidate value
    existing_random_uris = set(active_test_procedure.random_values.random_uri_by_key.values())
    for i in range(RANDOM_URI_ATTEMPTS):
        candidate_value = candidate_random_uri()
        if candidate_value not in existing_random_uris:
            active_test_procedure.random_values.random_uri_by_key[randuri_key] = candidate_value
            return candidate_value
        logger.warning(
            f"[{i}] Collision in randuri '{randuri_key}' with '{candidate_value}'."
            + f" There are {len(existing_random_uris)} existing entries."
        )

    raise errors.UnresolvableVariableError(
        f"Unable to resolve random uri {randuri_key} after {RANDOM_URI_ATTEMPTS} attempts"
    )

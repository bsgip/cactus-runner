import datetime as dt

import sqlalchemy as sa
from cactus_test_definitions import errors
from envoy.server import model
from envoy.server.mapper import common
from sqlalchemy.ext.asyncio import AsyncSession


def resolve_named_variable_now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


async def _select_single_site_der_setting(session: AsyncSession, variable_name: str) -> model.SiteDERSetting:
    # Fetch the most recently edited SiteDERSetting
    try:
        response = await session.execute(
            sa.select(model.SiteDERSetting).order_by(model.SiteDERSetting.changed_time.desc()).limit(1)
        )
        site_der_setting = response.scalar_one_or_none()
    except Exception as exc:
        raise errors.UnresolvableVariableError(f"Unable to fetch DERSetting from database: {exc}")

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
    except Exception as exc:
        raise errors.UnresolvableVariableError(f"Unable to fetch DERCapability from database: {exc}")

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

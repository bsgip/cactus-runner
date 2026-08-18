from dataclass_wizard import JSONWizard
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from envoy_schema.server.schema.sep2.der import (
    AbnormalCategoryType,
    AlarmStatusType,
    ConnectStatusType,
    DERControlType,
    DERType,
    DOESupportedMode,
    InverterStatusType,
    LocalControlModeStatusType,
    NormalCategoryType,
    OperationalModeStatusType,
    StorageModeStatusType,
)

__all__ = [
    "SiteDERSetting",
    "SiteDERStatus",
    "SiteDERRating",
    "SiteDERStatusFinalReport",
    "SiteDERSettingFinalReport",
    "SiteDERRatingFinalReport",
    "SiteDERAvailabilityFinalReport",
    "SiteDERFinalReport",
]


@dataclass(slots=True, frozen=True)
class SiteDERSetting:
    """Represents the current setting values associated with a SiteDER. The SiteDERRating represents
    the ratings/limits while the settings represent the currently enabled functionality"""

    grad_w: int
    modes_enabled: DERControlType | None = None
    doe_modes_enabled: DOESupportedMode | None = None
    max_w_value: int | None = None
    max_va_value: int | None = None
    max_var_value: int | None = None
    max_var_neg_value: int | None = None
    max_charge_rate_w_value: int | None = None
    max_charge_rate_w_multiplier: int | None = None
    max_discharge_rate_w_value: int | None = None
    max_discharge_rate_w_multiplier: int | None = None
    max_wh_value: int | None = None
    min_pf_over_excited_displacement: int | None = None
    min_pf_under_excited_displacement: int | None = None
    # Placeholders for the storage extension
    min_wh_value: int | None = None
    vpp_modes_enabled: int | None = None


@dataclass(slots=True, frozen=True)
class SiteDERRating:
    """Represents the nameplate rating values associated with a SiteDER. These are not expected to change
    after initially being set (excepting erroneous assignments). Only a single SiteDERRating should be assigned
    to a SiteDER"""

    modes_supported: DERControlType | None = None
    doe_modes_supported: DOESupportedMode | None = None
    max_w_value: int | None = None
    max_va_value: int | None = None
    max_var_value: int | None = None
    max_var_neg_value: int | None = None
    max_charge_rate_w_value: int | None = None
    max_discharge_rate_w_value: int | None = None
    max_wh_value: int | None = None
    min_pf_over_excited_displacement: int | None = None
    min_pf_under_excited_displacement: int | None = None
    # Placeholders for the storage extension
    vpp_modes_supported: int | None = None


@dataclass(slots=True, frozen=True)
class SiteDERStatus:
    """Represents the current status values associated with a SiteDER. Typically used for communicating
    the current snapshot of DER status"""
    # These values correspond to a flattened version of sep2 DERStatus
    alarm_status: AlarmStatusType | None = None
    generator_connect_status: ConnectStatusType | None = None
    operational_mode_status: OperationalModeStatusType | None = None


@dataclass(frozen=True, slots=True)
class SiteDERRatingFinalReport(JSONWizard):
    """A special case SiteDERRating used only for the final reporting purposes.

    It isn't necessary to be used by tests so this has been separated for plugin development
    convenience.
    """
    site_der_rating_id: str
    site_id: str
    created_time: datetime
    changed_time: datetime
    modes_supported: DERControlType | None
    abnormal_category: AbnormalCategoryType | None
    max_a_value: int | None
    max_a_multiplier: int | None
    max_ah_value: int | None
    max_ah_multiplier: int | None
    max_charge_rate_va_value: int | None
    max_charge_rate_va_multiplier: int | None
    max_charge_rate_w_value: int | None
    max_charge_rate_w_multiplier: int | None
    max_discharge_rate_va_value: int | None
    max_discharge_rate_va_multiplier: int | None
    max_discharge_rate_w_value: int | None
    max_discharge_rate_w_multiplier: int | None
    max_v_value: int | None
    max_v_multiplier: int | None
    max_va_value: int | None
    max_va_multiplier: int | None
    max_var_value: int | None
    max_var_multiplier: int | None
    max_var_neg_value: int | None
    max_var_neg_multiplier: int | None
    max_w_value: int
    max_w_multiplier: int
    max_wh_value: int | None
    max_wh_multiplier: int | None
    min_pf_over_excited_displacement: int | None
    min_pf_over_excited_multiplier: int | None
    min_pf_under_excited_displacement: int | None
    min_pf_under_excited_multiplier: int | None
    min_v_value: int | None
    min_v_multiplier: int | None
    normal_category: NormalCategoryType | None
    over_excited_pf_displacement: int | None
    over_excited_pf_multiplier: int | None
    over_excited_w_value: int | None
    over_excited_w_multiplier: int | None
    reactive_susceptance_value: int | None
    reactive_susceptance_multiplier: int | None
    under_excited_pf_displacement: int | None
    under_excited_pf_multiplier: int | None
    under_excited_w_value: int | None
    under_excited_w_multiplier: int | None
    v_nom_value: int | None
    v_nom_multiplier: int | None
    der_type: DERType
    doe_modes_supported: DOESupportedMode | None
    vpp_modes_supported: int | None = None


@dataclass(frozen=True, slots=True)
class SiteDERSettingFinalReport(JSONWizard):
    """A special case SiteDERSetting used only for the final reporting purposes.

    It isn't necessary to be used by tests so this has been separated for plugin development
    convenience.
    """
    site_der_setting_id: str
    site_id: str
    created_time: datetime
    changed_time: datetime
    modes_enabled: DERControlType | None
    es_delay: int | None
    es_high_freq: int | None
    es_high_volt: int | None
    es_low_freq: int | None
    es_low_volt: int | None
    es_ramp_tms: int | None
    es_random_delay: int | None
    grad_w: int
    max_a_value: int | None
    max_a_multiplier: int | None
    max_ah_value: int | None
    max_ah_multiplier: int | None
    max_charge_rate_va_value: int | None
    max_charge_rate_va_multiplier: int | None
    max_charge_rate_w_value: int | None
    max_charge_rate_w_multiplier: int | None
    max_discharge_rate_va_value: int | None
    max_discharge_rate_va_multiplier: int | None
    max_discharge_rate_w_value: int | None
    max_discharge_rate_w_multiplier: int | None
    max_v_value: int | None
    max_v_multiplier: int | None
    max_va_value: int | None
    max_va_multiplier: int | None
    max_var_value: int | None
    max_var_multiplier: int | None
    max_var_neg_value: int | None
    max_var_neg_multiplier: int | None
    max_w_value: int
    max_w_multiplier: int
    max_wh_value: int | None
    max_wh_multiplier: int | None
    min_pf_over_excited_displacement: int | None
    min_pf_over_excited_multiplier: int | None
    min_pf_under_excited_displacement: int | None
    min_pf_under_excited_multiplier: int | None
    min_v_value: int | None
    min_v_multiplier: int | None
    soft_grad_w: int | None
    v_nom_value: int | None
    v_nom_multiplier: int | None
    v_ref_value: int | None
    v_ref_multiplier: int | None
    v_ref_ofs_value: int | None
    v_ref_ofs_multiplier: int | None
    doe_modes_enabled: DOESupportedMode | None
    vpp_modes_enabled: int | None = None
    min_wh_value: int | None = None
    min_wh_multiplier: int | None = None


@dataclass(frozen=True, slots=True)
class SiteDERAvailabilityFinalReport(JSONWizard):
    """A special case SiteDERAvailability used only for the final reporting purposes.

    It isn't necessary to be used by tests so this has been separated for plugin development
    convenience.
    """
    site_der_availability_id: str
    site_id: str
    created_time: datetime
    changed_time: datetime
    availability_duration_sec: int | None
    max_charge_duration_sec: int | None
    reserved_charge_percent: Decimal | None
    reserved_deliver_percent: Decimal | None
    estimated_var_avail_value: int | None
    estimated_var_avail_multiplier: int | None
    estimated_w_avail_value: int | None
    estimated_w_avail_multiplier: int | None


@dataclass(frozen=True, slots=True)
class SiteDERStatusFinalReport(JSONWizard):
    """A special case SiteDERStatus used only for the final reporting purposes.

    It isn't necessary to be used by tests so this has been separated for plugin development
    convenience.
    """
    site_der_status_id: str
    site_id: str
    created_time: datetime
    changed_time: datetime
    alarm_status: AlarmStatusType | None
    generator_connect_status: ConnectStatusType | None
    generator_connect_status_time: datetime | None
    inverter_status: InverterStatusType | None
    inverter_status_time: datetime | None
    local_control_mode_status: LocalControlModeStatusType | None
    local_control_mode_status_time: datetime | None
    manufacturer_status: str | None
    manufacturer_status_time: datetime | None
    operational_mode_status: OperationalModeStatusType | None
    operational_mode_status_time: datetime | None
    state_of_charge_status: int | None
    state_of_charge_status_time: datetime | None
    storage_mode_status: StorageModeStatusType | None
    storage_mode_status_time: datetime | None
    storage_connect_status: ConnectStatusType | None
    storage_connect_status_time: datetime | None


@dataclass(slots=True, frozen=True)
class SiteDERFinalReport(JSONWizard):
    """A special case SiteDER used only for the final reporting purposes.

    It isn't necessary to be used by tests so this has been separated for plugin development
    convenience.
    """
    site_id: str
    created_time: datetime
    changed_time: datetime
    site_der_rating: SiteDERRatingFinalReport | None
    site_der_setting: SiteDERSettingFinalReport | None
    site_der_availability: SiteDERAvailabilityFinalReport | None
    site_der_status: SiteDERStatusFinalReport | None

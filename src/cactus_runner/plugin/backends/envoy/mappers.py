from datetime import timedelta
from decimal import Decimal
from enum import IntEnum, IntFlag

from cactus_schema.runner import DERCapabilityInfo, DERSettingsInfo, DERStatusInfo
from envoy.server.model import (
    DynamicOperatingEnvelope,
    DynamicOperatingEnvelopeResponse,
    Site,
    SiteControlGroup,
    SiteControlGroupDefault,
    SiteDERAvailability,
    SiteDERRating,
    SiteDERSetting,
    SiteDERStatus,
    SiteReading,
    SiteReadingType,
    Subscription,
    TransmitNotificationLog,
)
from envoy.server.model.archive import (
    ArchiveDynamicOperatingEnvelope,
    ArchiveSiteControlGroupDefault,
    ArchiveSiteReading,
)
from envoy_schema.admin.schema.config import RuntimeServerConfigRequest
from envoy_schema.admin.schema.site import SiteUpdateRequest
from envoy_schema.admin.schema.site_control import (
    SiteControlGroupDefaultRequest,
    SiteControlGroupRequest,
    SiteControlRequest,
    UpdateDefaultValue,
)
from envoy_schema.server.schema.sep2.der import (
    AlarmStatusType,
    ConnectStatusType,
    DERControlType,
    DERType,
    DOESupportedMode,
    InverterStatusType,
    LocalControlModeStatusType,
    OperationalModeStatusType,
    StorageModeStatusType,
)

from cactus_runner.plugin import dtos


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


def map_envoy_site_reading_to_dto(site_reading: SiteReading | ArchiveSiteReading) -> dtos.SiteReading:
    """Maps an Envoy DB SiteReading to CACTUS backend DTO."""
    return dtos.SiteReading(
        site_reading_type_id=f"{site_reading.site_reading_type_id}",
        time_period_start=site_reading.time_period_start,
        time_period_duration=timedelta(seconds=site_reading.time_period_seconds),
        value=site_reading.value,
        created_time=site_reading.created_time,
        archive_time=site_reading.archive_time if isinstance(site_reading, ArchiveSiteReading) else None,
        deleted_time=site_reading.deleted_time if isinstance(site_reading, ArchiveSiteReading) else None,
        changed_time=site_reading.changed_time,
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
        site_control_group_id=f"{site_control.site_control_group_id}",
        import_limit_active_watts=site_control.import_limit_active_watts,
        export_limit_active_watts=site_control.export_limit_watts,
        generation_limit_active_watts=site_control.generation_limit_active_watts,
        load_limit_active_watts=site_control.load_limit_active_watts,
        deleted_time=site_control.deleted_time if isinstance(site_control, ArchiveDynamicOperatingEnvelope) else None,
        archive_time=site_control.archive_time if isinstance(site_control, ArchiveDynamicOperatingEnvelope) else None,
        start_time=site_control.start_time,
        duration=timedelta(seconds=site_control.duration_seconds),
        created_time=site_control.created_time,
        superseded=site_control.superseded,
        changed_time=site_control.changed_time,
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


def map_envoy_site_control_group_to_dto(site_control_group: SiteControlGroup) -> dtos.SiteControlGroup:
    """Maps an envoy site control group to a CACTUS backend DTO."""
    return dtos.SiteControlGroup(
        site_control_group_id=f"{site_control_group.site_control_group_id}",
        description=site_control_group.description,
        primacy=site_control_group.primacy,
        fsa_id=f"{site_control_group.fsa_id}",
        display_id=site_control_group.display_id,
    )


def map_envoy_site_control_group_default_to_dto(
    site_control_group_default: SiteControlGroupDefault | ArchiveSiteControlGroupDefault,
) -> dtos.SiteControlGroupDefault:
    """Map an envoy site control group default to a CACTUS backend DTO."""
    return dtos.SiteControlGroupDefault(
        import_limit_active_watts=site_control_group_default.import_limit_active_watts,
        export_limit_active_watts=site_control_group_default.export_limit_active_watts,
        generation_limit_active_watts=site_control_group_default.generation_limit_active_watts,
        load_limit_active_watts=site_control_group_default.load_limit_active_watts,
        ramp_rate_percent_per_second=site_control_group_default.ramp_rate_percent_per_second,
        changed_time=site_control_group_default.changed_time,
        created_time=site_control_group_default.created_time,
        archive_time=site_control_group_default.archive_time
        if isinstance(site_control_group_default, ArchiveSiteControlGroupDefault)
        else None,
        deleted_time=site_control_group_default.deleted_time
        if isinstance(site_control_group_default, ArchiveSiteControlGroupDefault)
        else None,
    )


def map_dto_runtime_config_to_request(config: dtos.RuntimeConfigWrite) -> RuntimeServerConfigRequest:
    """Maps a RuntimeConfig DTO to an admin API request."""
    return RuntimeServerConfigRequest(
        dcap_pollrate_seconds=config.dcap_pollrate_seconds,
        edevl_pollrate_seconds=config.edevl_pollrate_seconds,
        derl_pollrate_seconds=config.derl_pollrate_seconds,
        derpl_pollrate_seconds=config.derpl_pollrate_seconds,
        fsal_pollrate_seconds=config.fsal_pollrate_seconds,
        mup_postrate_seconds=config.mup_postrate_seconds,
        disable_edev_registration=config.disable_edev_registration,
        site_control_pow10_encoding=config.site_control_pow10_encoding,
    )


def map_dto_site_control_group_to_request(group: dtos.SiteControlGroupWrite) -> SiteControlGroupRequest:
    """Maps a SiteControlGroup DTO to an admin API request."""
    return SiteControlGroupRequest(
        description=group.description,
        primacy=group.primacy,
        fsa_id=int(group.fsa_id) if group.fsa_id is not None else None,
        display_id=group.display_id,
    )


def map_dto_site_control_group_default_to_request(
    default: dtos.SiteControlGroupDefaultWrite,
) -> SiteControlGroupDefaultRequest:
    """Maps a SiteControlGroupDefault DTO to an admin API request.

    When default.cancelled is True, any field left as None is treated as an explicit
    null (UpdateDefaultValue(value=None)) rather than "don't update this field".
    """

    def _wrap(value: Decimal | None) -> UpdateDefaultValue | None:
        if value is not None:
            return UpdateDefaultValue(value=value)  # type: ignore[arg-type]
        return UpdateDefaultValue(value=None) if default.cancelled else None

    return SiteControlGroupDefaultRequest(
        import_limit_watts=_wrap(default.import_limit_watts),
        export_limit_watts=_wrap(default.export_limit_watts),
        generation_limit_watts=_wrap(default.generation_limit_watts),
        load_limit_watts=_wrap(default.load_limit_watts),
        ramp_rate_percent_per_second=_wrap(default.ramp_rate_percent_per_second),
    )


def map_dto_site_control_create_to_request(control: dtos.SiteControlWrite) -> SiteControlRequest:
    """Maps a SiteControlCreate DTO to an admin API request."""
    return SiteControlRequest(
        site_id=int(control.site_id),
        calculation_log_id=None,
        duration_seconds=control.duration_seconds,
        start_time=control.start_time,
        randomize_start_seconds=control.randomize_start_seconds,
        display_id=control.display_id,
        set_energized=control.set_energized,
        set_connect=control.set_connect,
        import_limit_watts=control.import_limit_watts,
        export_limit_watts=control.export_limit_watts,
        generation_limit_watts=control.generation_limit_watts,
        load_limit_watts=control.load_limit_watts,
        set_point_percentage=control.set_point_percentage,
        ramp_time_seconds=control.ramp_time_seconds,
    )


def map_dto_post_rate_to_site_update_request(post_rate_seconds: int) -> SiteUpdateRequest:
    """Maps a post rate value to an admin API site update request."""
    return SiteUpdateRequest(nmi=None, timezone_id=None, device_category=None, post_rate_seconds=post_rate_seconds)


def map_dto_site_write_to_envoy(site: dtos.SiteWrite) -> Site:
    """Maps a dto SiteWrite object to a envoy DB model for committing."""
    return Site(
        nmi=site.nmi,
        aggregator_id=int(site.aggregator_id) if site.aggregator_id is not None else None,
        timezone_id=site.timezone_id,
        created_time=site.created_time,
        changed_time=site.changed_time,
        lfdi=site.lfdi,
        sfdi=site.sfdi,
        device_category=site.device_category,
        registration_pin=site.registration_pin,
    )


def _resolve_value_multiplier(value: int | None, multiplier: int | None) -> int | None:
    """Resolve a sep2 value/multiplier pair to an integer (value * 10^multiplier)."""
    if value is None:
        return None
    return int(value * (10 ** (multiplier if multiplier is not None else 0)))


def _resolve_intflag(bitmap: int | None, flag_type: type[IntFlag]) -> list[str] | None:
    """Resolve an IntFlag bitmap to a list of active flag names."""
    if bitmap is None:
        return None
    return [flag.name for flag in flag_type if bitmap & flag and flag.name is not None]


def _resolve_intenum(value: int | None, enum_type: type[IntEnum]) -> str | None:
    """Resolve an IntEnum integer value to its name string."""
    if value is None:
        return None
    try:
        return enum_type(value).name
    except ValueError:
        return None


def build_der_capability(rating: SiteDERRating) -> DERCapabilityInfo:
    return DERCapabilityInfo(
        der_type=_resolve_intenum(rating.der_type, DERType),
        modes_supported=_resolve_intflag(rating.modes_supported, DERControlType),
        max_w=_resolve_value_multiplier(rating.max_w_value, rating.max_w_multiplier),
        max_va=_resolve_value_multiplier(rating.max_va_value, rating.max_va_multiplier),
        max_var=_resolve_value_multiplier(rating.max_var_value, rating.max_var_multiplier),
        max_var_neg=_resolve_value_multiplier(rating.max_var_neg_value, rating.max_var_neg_multiplier),
        max_a=_resolve_value_multiplier(rating.max_a_value, rating.max_a_multiplier),
        max_charge_rate_w=_resolve_value_multiplier(
            rating.max_charge_rate_w_value, rating.max_charge_rate_w_multiplier
        ),
        max_discharge_rate_w=_resolve_value_multiplier(
            rating.max_discharge_rate_w_value, rating.max_discharge_rate_w_multiplier
        ),
        max_wh=_resolve_value_multiplier(rating.max_wh_value, rating.max_wh_multiplier),
        doe_modes_supported=_resolve_intflag(rating.doe_modes_supported, DOESupportedMode),
    )


def build_der_settings(setting: SiteDERSetting) -> DERSettingsInfo:
    return DERSettingsInfo(
        modes_enabled=_resolve_intflag(setting.modes_enabled, DERControlType),
        max_w=_resolve_value_multiplier(setting.max_w_value, setting.max_w_multiplier),
        max_va=_resolve_value_multiplier(setting.max_va_value, setting.max_va_multiplier),
        max_var=_resolve_value_multiplier(setting.max_var_value, setting.max_var_multiplier),
        max_var_neg=_resolve_value_multiplier(setting.max_var_neg_value, setting.max_var_neg_multiplier),
        max_charge_rate_w=_resolve_value_multiplier(
            setting.max_charge_rate_w_value, setting.max_charge_rate_w_multiplier
        ),
        max_discharge_rate_w=_resolve_value_multiplier(
            setting.max_discharge_rate_w_value, setting.max_discharge_rate_w_multiplier
        ),
        grad_w=setting.grad_w,
        doe_modes_enabled=_resolve_intflag(setting.doe_modes_enabled, DOESupportedMode),
    )


def build_der_status(status: SiteDERStatus) -> DERStatusInfo:
    return DERStatusInfo(
        alarm_status=_resolve_intflag(status.alarm_status, AlarmStatusType),
        generator_connect_status=_resolve_intflag(status.generator_connect_status, ConnectStatusType),
        storage_connect_status=_resolve_intflag(status.storage_connect_status, ConnectStatusType),
        inverter_status=_resolve_intenum(status.inverter_status, InverterStatusType),
        operational_mode_status=_resolve_intenum(status.operational_mode_status, OperationalModeStatusType),
        storage_mode_status=_resolve_intenum(status.storage_mode_status, StorageModeStatusType),
        local_control_mode_status=_resolve_intenum(status.local_control_mode_status, LocalControlModeStatusType),
        manufacturer_status=status.manufacturer_status,
        state_of_charge_status=status.state_of_charge_status,
    )


def map_envoy_site_reading_type_to_final_report_dto(
    site_reading_type: SiteReadingType,
) -> dtos.SiteReadingTypeFinalReport:
    """Create the final reporting dto from a DB entry."""
    return dtos.SiteReadingTypeFinalReport(
        site_reading_type_id=site_reading_type.site_reading_type_id,
        aggregator_id=site_reading_type.aggregator_id,
        site_id=site_reading_type.site_id,
        mrid=site_reading_type.mrid,
        group_id=site_reading_type.group_id,
        group_mrid=site_reading_type.group_mrid,
        uom=site_reading_type.uom,
        data_qualifier=site_reading_type.data_qualifier,
        flow_direction=site_reading_type.flow_direction,
        accumulation_behaviour=site_reading_type.accumulation_behaviour,
        kind=site_reading_type.kind,
        phase=site_reading_type.phase,
        power_of_ten_multiplier=site_reading_type.power_of_ten_multiplier,
        default_interval_seconds=site_reading_type.default_interval_seconds,
        role_flags=site_reading_type.role_flags,
        description=site_reading_type.description,
        group_version=site_reading_type.group_version,
        group_status=site_reading_type.group_status,
        commodity=site_reading_type.commodity,
        created_time=site_reading_type.created_time,
        changed_time=site_reading_type.changed_time,
    )


def map_envoy_site_der_rating_to_final_report_dto(site_der_rating: SiteDERRating) -> dtos.SiteDERRatingFinalReport:
    """Create the final reporting dto from a DB entry."""
    return dtos.SiteDERRatingFinalReport(
        site_der_rating_id=f"{site_der_rating.site_der_rating_id}",
        site_id=f"{site_der_rating.site_id}",
        created_time=site_der_rating.created_time,
        changed_time=site_der_rating.changed_time,
        modes_supported=site_der_rating.modes_supported,
        abnormal_category=site_der_rating.abnormal_category,
        max_a_value=site_der_rating.max_a_value,
        max_a_multiplier=site_der_rating.max_a_multiplier,
        max_ah_value=site_der_rating.max_ah_value,
        max_ah_multiplier=site_der_rating.max_ah_multiplier,
        max_charge_rate_va_value=site_der_rating.max_charge_rate_va_value,
        max_charge_rate_va_multiplier=site_der_rating.max_charge_rate_va_multiplier,
        max_charge_rate_w_value=site_der_rating.max_charge_rate_w_value,
        max_charge_rate_w_multiplier=site_der_rating.max_charge_rate_w_multiplier,
        max_discharge_rate_va_value=site_der_rating.max_discharge_rate_va_value,
        max_discharge_rate_va_multiplier=site_der_rating.max_discharge_rate_va_multiplier,
        max_discharge_rate_w_value=site_der_rating.max_discharge_rate_w_value,
        max_discharge_rate_w_multiplier=site_der_rating.max_discharge_rate_w_multiplier,
        max_v_value=site_der_rating.max_v_value,
        max_v_multiplier=site_der_rating.max_v_multiplier,
        max_va_value=site_der_rating.max_va_value,
        max_va_multiplier=site_der_rating.max_va_multiplier,
        max_var_value=site_der_rating.max_var_value,
        max_var_multiplier=site_der_rating.max_var_multiplier,
        max_var_neg_value=site_der_rating.max_var_neg_value,
        max_var_neg_multiplier=site_der_rating.max_var_neg_multiplier,
        max_w_value=site_der_rating.max_w_value,
        max_w_multiplier=site_der_rating.max_w_multiplier,
        max_wh_value=site_der_rating.max_wh_value,
        max_wh_multiplier=site_der_rating.max_wh_multiplier,
        min_pf_over_excited_displacement=site_der_rating.min_pf_over_excited_displacement,
        min_pf_over_excited_multiplier=site_der_rating.min_pf_over_excited_multiplier,
        min_pf_under_excited_displacement=site_der_rating.min_pf_under_excited_displacement,
        min_pf_under_excited_multiplier=site_der_rating.min_pf_under_excited_multiplier,
        min_v_value=site_der_rating.min_v_value,
        min_v_multiplier=site_der_rating.min_v_multiplier,
        normal_category=site_der_rating.normal_category,
        over_excited_pf_displacement=site_der_rating.over_excited_pf_displacement,
        over_excited_pf_multiplier=site_der_rating.over_excited_pf_multiplier,
        over_excited_w_value=site_der_rating.over_excited_w_value,
        over_excited_w_multiplier=site_der_rating.over_excited_w_multiplier,
        reactive_susceptance_value=site_der_rating.reactive_susceptance_value,
        reactive_susceptance_multiplier=site_der_rating.reactive_susceptance_multiplier,
        under_excited_pf_displacement=site_der_rating.under_excited_pf_displacement,
        under_excited_pf_multiplier=site_der_rating.under_excited_pf_multiplier,
        under_excited_w_value=site_der_rating.under_excited_w_value,
        under_excited_w_multiplier=site_der_rating.under_excited_w_multiplier,
        v_nom_value=site_der_rating.v_nom_value,
        v_nom_multiplier=site_der_rating.v_nom_multiplier,
        der_type=site_der_rating.der_type,
        doe_modes_supported=site_der_rating.doe_modes_supported,
        vpp_modes_supported=getattr(site_der_rating, "vpp_modes_supported", None),
    )


def map_envoy_site_der_setting_to_final_report_dto(site_der_setting: SiteDERSetting) -> dtos.SiteDERSettingFinalReport:
    """Create the final reporting dto from a DB entry."""
    return dtos.SiteDERSettingFinalReport(
        site_der_setting_id=f"{site_der_setting.site_der_setting_id}",
        site_id=f"{site_der_setting.site_id}",
        created_time=site_der_setting.created_time,
        changed_time=site_der_setting.changed_time,
        modes_enabled=site_der_setting.modes_enabled,
        es_delay=site_der_setting.es_delay,
        es_high_freq=site_der_setting.es_high_freq,
        es_high_volt=site_der_setting.es_high_volt,
        es_low_freq=site_der_setting.es_low_freq,
        es_low_volt=site_der_setting.es_low_volt,
        es_ramp_tms=site_der_setting.es_ramp_tms,
        es_random_delay=site_der_setting.es_random_delay,
        grad_w=site_der_setting.grad_w,
        max_a_value=site_der_setting.max_a_value,
        max_a_multiplier=site_der_setting.max_a_multiplier,
        max_ah_value=site_der_setting.max_ah_value,
        max_ah_multiplier=site_der_setting.max_ah_multiplier,
        max_charge_rate_va_value=site_der_setting.max_charge_rate_va_value,
        max_charge_rate_va_multiplier=site_der_setting.max_charge_rate_va_multiplier,
        max_charge_rate_w_value=site_der_setting.max_charge_rate_w_value,
        max_charge_rate_w_multiplier=site_der_setting.max_charge_rate_w_multiplier,
        max_discharge_rate_va_value=site_der_setting.max_discharge_rate_va_value,
        max_discharge_rate_va_multiplier=site_der_setting.max_discharge_rate_va_multiplier,
        max_discharge_rate_w_value=site_der_setting.max_discharge_rate_w_value,
        max_discharge_rate_w_multiplier=site_der_setting.max_discharge_rate_w_multiplier,
        max_v_value=site_der_setting.max_v_value,
        max_v_multiplier=site_der_setting.max_v_multiplier,
        max_va_value=site_der_setting.max_va_value,
        max_va_multiplier=site_der_setting.max_va_multiplier,
        max_var_value=site_der_setting.max_var_value,
        max_var_multiplier=site_der_setting.max_var_multiplier,
        max_var_neg_value=site_der_setting.max_var_neg_value,
        max_var_neg_multiplier=site_der_setting.max_var_neg_multiplier,
        max_w_value=site_der_setting.max_w_value,
        max_w_multiplier=site_der_setting.max_w_multiplier,
        max_wh_value=site_der_setting.max_wh_value,
        max_wh_multiplier=site_der_setting.max_wh_multiplier,
        min_pf_over_excited_displacement=site_der_setting.min_pf_over_excited_displacement,
        min_pf_over_excited_multiplier=site_der_setting.min_pf_over_excited_multiplier,
        min_pf_under_excited_displacement=site_der_setting.min_pf_under_excited_displacement,
        min_pf_under_excited_multiplier=site_der_setting.min_pf_under_excited_multiplier,
        min_v_value=site_der_setting.min_v_value,
        min_v_multiplier=site_der_setting.min_v_multiplier,
        soft_grad_w=site_der_setting.soft_grad_w,
        v_nom_value=site_der_setting.v_nom_value,
        v_nom_multiplier=site_der_setting.v_nom_multiplier,
        v_ref_value=site_der_setting.v_ref_value,
        v_ref_multiplier=site_der_setting.v_ref_multiplier,
        v_ref_ofs_value=site_der_setting.v_ref_ofs_value,
        v_ref_ofs_multiplier=site_der_setting.v_ref_ofs_multiplier,
        doe_modes_enabled=site_der_setting.doe_modes_enabled,
        vpp_modes_enabled=getattr(site_der_setting, "vpp_modes_enabled", None),
        min_wh_value=getattr(site_der_setting, "min_wh_value", None),
        min_wh_multiplier=getattr(site_der_setting, "min_wh_multiplier", None),
    )


def map_envoy_site_der_status_to_final_report_dto(site_der_status: SiteDERStatus) -> dtos.SiteDERStatusFinalReport:
    """Create a final report dto from a DB entry."""
    return dtos.SiteDERStatusFinalReport(
        site_der_status_id=f"{site_der_status.site_der_status_id}",
        site_id=f"{site_der_status.site_id}",
        created_time=site_der_status.created_time,
        changed_time=site_der_status.changed_time,
        alarm_status=site_der_status.alarm_status,
        generator_connect_status=site_der_status.generator_connect_status,
        generator_connect_status_time=site_der_status.generator_connect_status_time,
        inverter_status=site_der_status.inverter_status,
        inverter_status_time=site_der_status.inverter_status_time,
        local_control_mode_status=site_der_status.local_control_mode_status,
        local_control_mode_status_time=site_der_status.local_control_mode_status_time,
        manufacturer_status=site_der_status.manufacturer_status,
        manufacturer_status_time=site_der_status.manufacturer_status_time,
        operational_mode_status=site_der_status.operational_mode_status,
        operational_mode_status_time=site_der_status.operational_mode_status_time,
        state_of_charge_status=site_der_status.state_of_charge_status,
        state_of_charge_status_time=site_der_status.state_of_charge_status_time,
        storage_mode_status=site_der_status.storage_mode_status,
        storage_mode_status_time=site_der_status.storage_mode_status_time,
        storage_connect_status=site_der_status.storage_connect_status,
        storage_connect_status_time=site_der_status.storage_connect_status_time,
    )


def map_envoy_site_der_availability_to_final_report_dto(
    site_der_availability: SiteDERAvailability,
) -> dtos.SiteDERAvailabilityFinalReport:
    """Create a final report DTO from a DB entry."""
    return dtos.SiteDERAvailabilityFinalReport(
        site_der_availability_id=f"{site_der_availability.site_der_availability_id}",
        site_id=f"{site_der_availability.site_id}",
        created_time=site_der_availability.created_time,
        changed_time=site_der_availability.changed_time,
        availability_duration_sec=site_der_availability.availability_duration_sec,
        max_charge_duration_sec=site_der_availability.max_charge_duration_sec,
        reserved_charge_percent=site_der_availability.reserved_charge_percent,
        reserved_deliver_percent=site_der_availability.reserved_deliver_percent,
        estimated_var_avail_value=site_der_availability.estimated_var_avail_value,
        estimated_var_avail_multiplier=site_der_availability.estimated_var_avail_multiplier,
        estimated_w_avail_value=site_der_availability.estimated_w_avail_value,
        estimated_w_avail_multiplier=site_der_availability.estimated_w_avail_multiplier,
    )


def map_envoy_site_to_final_report_dto(site: Site) -> dtos.SiteFinalReport:
    """Create a final report DTO from a DB entry."""
    # The DER sub-resources now hang directly off the site
    site_ders = []
    site_der_rating = (
        map_envoy_site_der_rating_to_final_report_dto(site.site_der_rating) if site.site_der_rating else None
    )
    site_der_setting = (
        map_envoy_site_der_setting_to_final_report_dto(site.site_der_setting) if site.site_der_setting else None
    )
    site_der_availability = (
        map_envoy_site_der_availability_to_final_report_dto(site.site_der_availability)
        if site.site_der_availability
        else None
    )
    site_der_status = (
        map_envoy_site_der_status_to_final_report_dto(site.site_der_status) if site.site_der_status else None
    )
    if any([site_der_setting, site_der_rating, site_der_availability, site_der_status]):
        site_ders.append(
            dtos.SiteDERFinalReport(
                site_id=f"{site.site_id}",
                created_time=site.created_time,
                changed_time=site.changed_time,
                site_der_rating=site_der_rating,
                site_der_setting=site_der_setting,
                site_der_availability=site_der_availability,
                site_der_status=site_der_status,
            )
        )

    return dtos.SiteFinalReport(
        site_id=f"{site.site_id}",
        nmi=site.nmi,
        aggregator_id=f"{site.aggregator_id}",
        timezone_id=site.timezone_id,
        created_time=site.created_time,
        changed_time=site.changed_time,
        lfdi=site.lfdi,
        sfdi=site.sfdi,
        device_category=site.device_category,
        registration_pin=site.registration_pin,
        post_rate_seconds=site.post_rate_seconds,
        site_ders=site_ders,
    )

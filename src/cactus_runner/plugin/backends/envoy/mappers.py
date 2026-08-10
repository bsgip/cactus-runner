from datetime import timedelta
from decimal import Decimal

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
from envoy_schema.admin.schema.config import RuntimeServerConfigRequest
from envoy_schema.admin.schema.site import SiteUpdateRequest
from envoy_schema.admin.schema.site_control import (
    SiteControlGroupDefaultRequest,
    SiteControlGroupRequest,
    SiteControlRequest,
    UpdateDefaultValue,
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
        site_control_group_id=f"{site_control.site_control_group_id}",
        deleted_time=site_control.deleted_time if isinstance(site_control, ArchiveDynamicOperatingEnvelope) else None,
        created_time=site_control.created_time,
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

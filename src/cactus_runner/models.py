from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from cactus_schema.runner import (
    ClientInteraction,
    ClientInteractionType,
    RequestEntry,
    RunRequest,
    StepStatus,
)
from cactus_test_definitions import CSIPAusVersion
from cactus_test_definitions.client import Event, TestProcedure
from dataclass_wizard import JSONWizard
from envoy.server.model.site import Site as EnvoySite
from envoy.server.model.site import SiteDER as EnvoySiteDER
from envoy.server.model.site import SiteDERAvailability as EnvoySiteDERAvailability
from envoy.server.model.site import SiteDERRating as EnvoySiteDERRating
from envoy.server.model.site import SiteDERSetting as EnvoySiteDERSetting
from envoy.server.model.site import SiteDERStatus as EnvoySiteDERStatus
from envoy.server.model.site_reading import SiteReadingType
from envoy_schema.server.schema.sep2.der import (
    AbnormalCategoryType,
    AlarmStatusType,
    ConnectStatusType,
    DERControlType,
    DOESupportedMode,
    InverterStatusType,
    LocalControlModeStatusType,
    NormalCategoryType,
    OperationalModeStatusType,
    StorageModeStatusType,
)
from envoy_schema.server.schema.sep2.types import (
    AccumulationBehaviourType,
    CommodityType,
    DataQualifierType,
    DeviceCategory,
    FlowDirectionType,
    KindType,
    PhaseCode,
    RoleFlagsType,
    UomType,
)

from cactus_runner.app.timeline import Timeline


class ClientCertificateType(StrEnum):
    AGGREGATOR = "Aggregator"
    DEVICE = "Device"


@dataclass
class InitialisedCertificates:
    """Certificates shared with the runner during initialisation. These certs should be the ONLY certificates that can
    interact with the runner/underlying envoy instance"""

    client_certificate_type: str | None = None  # Will read as either "aggregator" or "device"
    client_certificate: str | None = None
    client_lfdi: str | None = None
    client_aggregator_id: int | None = None  # Stored for reuse in playlist tests


@dataclass
class Listener:
    step: str
    event: Event
    actions: list[Any]
    enabled_time: datetime | None = None  # Set to the TZ aware datetime when this Listener was enabled. None = disabled


@dataclass
class StepInfo:
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def get_step_status(self) -> StepStatus:
        if self.completed_at:
            return StepStatus.RESOLVED
        elif self.started_at:
            return StepStatus.ACTIVE
        else:
            return StepStatus.PENDING


@dataclass
class ResourceAnnotations:
    der_control_ids_by_alias: dict[str, int] = field(default_factory=dict)


@dataclass
class ActiveTestProcedure:
    name: str
    definition: TestProcedure
    csip_aus_version: CSIPAusVersion  # What CSIP aus version did is this run communicating with?
    initialised_at: datetime  # When did the test initialise - timezone aware
    started_at: datetime | None  # When did the test start (None if it hasn't started yet) - timezone aware
    listeners: list[Listener]
    step_status: dict[str, StepInfo]
    client_certificate_type: ClientCertificateType  # Human readable text to identify source of cert.
    client_aggregator_id: int  # What aggregator ID will be the client operating as? (0 for device certs)
    client_lfdi: str  # The LFDI of the client certificate expected for the test (Either aggregator or device client)
    client_sfdi: int  # The SFDI of the client certificate expected for the test (Either aggregator or device client)
    run_id: str | None  # Metadata about what "id" has been assigned to this test (from external) - if any
    pen: int  # Private Enterprise Number (PEN). A value of 0 means no valid PEN avaiable.
    subscription_domain: str | None = None
    is_static_url: bool | None = None
    run_group_id: str | None = None
    run_group_name: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    communications_disabled: bool = False
    finished_zip_data: bytes | None = (
        None  # Finalised ZIP file. If not None - this test is "done" and shouldn't update any events/state
    )
    resource_annotations: ResourceAnnotations = field(default_factory=ResourceAnnotations)

    def is_finished(self) -> bool:
        """True if the active test procedure has been marked as finished. That is, there is no more test data to
        accumulate and any client events should be ignored"""
        return self.finished_zip_data is not None

    def is_started(self) -> bool:
        """True if any listener has been enabled"""
        return any([True for listener in self.listeners if listener.enabled_time is not None])


@dataclass
class PlaylistItem:
    """A completed test in the playlist"""

    test_name: str
    zip_file_path: Path | None
    completed_at: datetime
    success: bool


@dataclass
class RunnerState:
    """Represents the current state of the Runner.

    This tracks the state of an active test procedure if there is one.

    aiohttp uses the app instance as a means for sharing global data using AppKeys. We use
    this mechanism to share the active test procedure between different requests.

    However aiohttp (rightly) complains when replacing objects pointed to by AppKeys with different
    instances after the app has been started; in other words the app gets frozen.
    The reason for this, is that blindly mutating global state in async handlers could
    get someone into a mess.

    We are a special case in this regard,
    - Each runner will have only one client.
    - Even those the app supports asynchronous handling of requests, it is a reasonable
      expectation that the client will mostly interact synchronously i.e.
      they will wait for a response from the runner before issuing subsequent requests.
    - Finally care has been taken to handle requests in their entirety before returning control back
      to the async loop. We do this by not calling await on subtasks but calling them instead
      synchronously. Examples include,
        1. In 'start_test_procedure' the database operations ('register_aggregator' and 'apply_db_precondition') are
           handled via synchronous function calls.
        2. In 'handle_all_request_types' we update the active test procedure with the synchronous functions
           'apply_action' and 'handle_event'.

    By wrapping the ActiveTestProcedure object within a RunnerState object we are
    free to mutate the `active_test_procedure` when needed and even set it to None
    when no test procedure is active without aiohttp "seeing" the mutation and complaining.
    """

    active_test_procedure: ActiveTestProcedure | None = None
    request_history: list[RequestEntry] = field(default_factory=list)
    client_interactions: list[ClientInteraction] = field(
        default_factory=lambda: [
            ClientInteraction(interaction_type=ClientInteractionType.RUNNER_START, timestamp=datetime.now(timezone.utc))
        ]
    )

    # Playlist support
    playlist: list[RunRequest] | None = None  # All tests in the playlist (full array)
    playlist_index: int = 0  # Current position (0-based index into playlist)
    completed_playlist_items: list[PlaylistItem] = field(default_factory=list)  # Completed tests with ZIP paths

    @property
    def last_client_interaction(self) -> ClientInteraction:
        return self.client_interactions[-1]

    def interaction_timestamp(self, interaction_type: ClientInteractionType) -> datetime | None:
        """Returns the timestamp of the first client interaction of type 'interaction_type'"""
        for client_interaction in self.client_interactions:
            if client_interaction.interaction_type == interaction_type:
                return client_interaction.timestamp
        return None


@dataclass
class CheckResult:
    """Represents the results of a running a single check"""

    passed: bool  # True if the check is considered passed or successful. False otherwise
    description: str | None  # Human readable description of what the check "considered" or wants to elaborate about


@dataclass(frozen=True)
class ReadingType(JSONWizard):
    site_reading_type_id: int
    aggregator_id: int
    site_id: int
    mrid: str
    group_id: int
    group_mrid: str

    uom: UomType
    data_qualifier: DataQualifierType
    flow_direction: FlowDirectionType
    accumulation_behaviour: AccumulationBehaviourType
    kind: KindType
    phase: PhaseCode
    power_of_ten_multiplier: int
    default_interval_seconds: int
    role_flags: RoleFlagsType

    description: str | None
    group_version: int | None
    group_status: int | None
    commodity: CommodityType | None

    created_time: datetime
    changed_time: datetime

    # site: Site | None

    @classmethod
    def from_site_reading_type(cls, srt: SiteReadingType):
        """Converts a sqlalchemy SiteReadingType (from envoy) to a serialisable ReadingType"""
        return cls(
            site_reading_type_id=srt.site_reading_type_id,
            aggregator_id=srt.aggregator_id,
            site_id=srt.site_id,
            mrid=srt.mrid,
            group_id=srt.group_id,
            group_mrid=srt.group_mrid,
            uom=srt.uom,
            data_qualifier=srt.data_qualifier,
            flow_direction=srt.flow_direction,
            accumulation_behaviour=srt.accumulation_behaviour,
            kind=srt.kind,
            phase=srt.phase,
            power_of_ten_multiplier=srt.power_of_ten_multiplier,
            default_interval_seconds=srt.default_interval_seconds,
            role_flags=srt.role_flags,
            description=srt.description,
            group_version=srt.group_version,
            group_status=srt.group_status,
            commodity=srt.commodity,
            created_time=srt.created_time,
            changed_time=srt.changed_time,
        )


@dataclass
class PackedReadings(JSONWizard):
    reading_type: ReadingType
    readings_as_json: str
    reading_counts: int


@dataclass(frozen=True)
class SiteDERRating(JSONWizard):
    site_der_rating_id: int
    site_der_id: int
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
    doe_modes_supported: DOESupportedMode | None

    @classmethod
    def from_site_der_rating(cls, rating: EnvoySiteDERRating | None):
        if rating is None:
            return None
        return cls(
            site_der_rating_id=rating.site_der_rating_id,
            site_der_id=rating.site_der_id,
            created_time=rating.created_time,
            changed_time=rating.changed_time,
            modes_supported=rating.modes_supported,
            abnormal_category=rating.abnormal_category,
            max_a_value=rating.max_a_value,
            max_a_multiplier=rating.max_a_multiplier,
            max_ah_value=rating.max_ah_value,
            max_ah_multiplier=rating.max_ah_multiplier,
            max_charge_rate_va_value=rating.max_charge_rate_va_value,
            max_charge_rate_va_multiplier=rating.max_charge_rate_va_multiplier,
            max_charge_rate_w_value=rating.max_charge_rate_w_value,
            max_charge_rate_w_multiplier=rating.max_charge_rate_w_multiplier,
            max_discharge_rate_va_value=rating.max_discharge_rate_va_value,
            max_discharge_rate_va_multiplier=rating.max_discharge_rate_va_multiplier,
            max_discharge_rate_w_value=rating.max_discharge_rate_w_value,
            max_discharge_rate_w_multiplier=rating.max_discharge_rate_w_multiplier,
            max_v_value=rating.max_v_value,
            max_v_multiplier=rating.max_v_multiplier,
            max_va_value=rating.max_va_value,
            max_va_multiplier=rating.max_va_multiplier,
            max_var_value=rating.max_var_value,
            max_var_multiplier=rating.max_var_multiplier,
            max_var_neg_value=rating.max_var_neg_value,
            max_var_neg_multiplier=rating.max_var_neg_value,
            max_w_value=rating.max_w_value,
            max_w_multiplier=rating.max_w_multiplier,
            max_wh_value=rating.max_wh_value,
            max_wh_multiplier=rating.max_wh_multiplier,
            min_pf_over_excited_displacement=rating.min_pf_over_excited_displacement,
            min_pf_over_excited_multiplier=rating.min_pf_over_excited_multiplier,
            min_pf_under_excited_displacement=rating.min_pf_under_excited_displacement,
            min_pf_under_excited_multiplier=rating.min_pf_under_excited_multiplier,
            min_v_value=rating.min_v_value,
            min_v_multiplier=rating.min_v_multiplier,
            normal_category=rating.normal_category,
            over_excited_pf_displacement=rating.over_excited_pf_displacement,
            over_excited_pf_multiplier=rating.over_excited_pf_multiplier,
            over_excited_w_value=rating.over_excited_w_value,
            over_excited_w_multiplier=rating.over_excited_w_multiplier,
            reactive_susceptance_value=rating.reactive_susceptance_value,
            reactive_susceptance_multiplier=rating.reactive_susceptance_multiplier,
            under_excited_pf_displacement=rating.under_excited_pf_displacement,
            under_excited_pf_multiplier=rating.under_excited_pf_multiplier,
            under_excited_w_value=rating.under_excited_w_value,
            under_excited_w_multiplier=rating.under_excited_w_multiplier,
            v_nom_value=rating.v_nom_value,
            v_nom_multiplier=rating.v_nom_multiplier,
            doe_modes_supported=rating.doe_modes_supported,
        )


@dataclass(frozen=True)
class SiteDERSetting(JSONWizard):
    site_der_setting_id: int
    site_der_id: int
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

    @classmethod
    def from_site_der_setting(cls, setting: EnvoySiteDERSetting | None):
        if setting is None:
            return None

        return cls(
            site_der_setting_id=setting.site_der_setting_id,
            site_der_id=setting.site_der_id,
            created_time=setting.created_time,
            changed_time=setting.changed_time,
            modes_enabled=setting.modes_enabled,
            es_delay=setting.es_delay,
            es_high_freq=setting.es_high_freq,
            es_high_volt=setting.es_high_volt,
            es_low_freq=setting.es_low_freq,
            es_low_volt=setting.es_low_volt,
            es_ramp_tms=setting.es_ramp_tms,
            es_random_delay=setting.es_random_delay,
            grad_w=setting.grad_w,
            max_a_value=setting.max_a_value,
            max_a_multiplier=setting.max_a_multiplier,
            max_ah_value=setting.max_ah_value,
            max_ah_multiplier=setting.max_ah_multiplier,
            max_charge_rate_va_value=setting.max_charge_rate_va_value,
            max_charge_rate_va_multiplier=setting.max_charge_rate_va_multiplier,
            max_charge_rate_w_value=setting.max_charge_rate_w_value,
            max_charge_rate_w_multiplier=setting.max_charge_rate_w_multiplier,
            max_discharge_rate_va_value=setting.max_discharge_rate_va_value,
            max_discharge_rate_va_multiplier=setting.max_discharge_rate_va_multiplier,
            max_discharge_rate_w_value=setting.max_discharge_rate_w_value,
            max_discharge_rate_w_multiplier=setting.max_discharge_rate_w_multiplier,
            max_v_value=setting.max_v_value,
            max_v_multiplier=setting.max_v_multiplier,
            max_va_value=setting.max_va_value,
            max_va_multiplier=setting.max_va_multiplier,
            max_var_value=setting.max_var_value,
            max_var_multiplier=setting.max_var_multiplier,
            max_var_neg_value=setting.max_var_neg_value,
            max_var_neg_multiplier=setting.max_var_neg_multiplier,
            max_w_value=setting.max_w_value,
            max_w_multiplier=setting.max_w_multiplier,
            max_wh_value=setting.max_wh_value,
            max_wh_multiplier=setting.max_wh_multiplier,
            min_pf_over_excited_displacement=setting.min_pf_over_excited_displacement,
            min_pf_over_excited_multiplier=setting.min_pf_over_excited_multiplier,
            min_pf_under_excited_displacement=setting.min_pf_under_excited_displacement,
            min_pf_under_excited_multiplier=setting.min_pf_under_excited_multiplier,
            min_v_value=setting.min_v_value,
            min_v_multiplier=setting.min_v_multiplier,
            soft_grad_w=setting.soft_grad_w,
            v_nom_value=setting.v_nom_value,
            v_nom_multiplier=setting.v_nom_multiplier,
            v_ref_value=setting.v_ref_value,
            v_ref_multiplier=setting.v_ref_multiplier,
            v_ref_ofs_value=setting.v_ref_ofs_value,
            v_ref_ofs_multiplier=setting.v_ref_ofs_multiplier,
            doe_modes_enabled=setting.doe_modes_enabled,
        )


@dataclass(frozen=True)
class SiteDERAvailability(JSONWizard):
    site_der_availability_id: int
    site_der_id: int
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

    @classmethod
    def from_site_der_availability(cls, availability: EnvoySiteDERAvailability | None):
        if availability is None:
            return None
        return cls(
            site_der_availability_id=availability.site_der_availability_id,
            site_der_id=availability.site_der_id,
            created_time=availability.created_time,
            changed_time=availability.changed_time,
            availability_duration_sec=availability.availability_duration_sec,
            max_charge_duration_sec=availability.max_charge_duration_sec,
            reserved_charge_percent=availability.reserved_charge_percent,
            reserved_deliver_percent=availability.reserved_deliver_percent,
            estimated_var_avail_value=availability.estimated_var_avail_value,
            estimated_var_avail_multiplier=availability.estimated_var_avail_multiplier,
            estimated_w_avail_value=availability.estimated_w_avail_value,
            estimated_w_avail_multiplier=availability.estimated_w_avail_multiplier,
        )


@dataclass(frozen=True)
class SiteDERStatus(JSONWizard):
    site_der_status_id: int
    site_der_id: int
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

    @classmethod
    def from_site_der_status(cls, status: EnvoySiteDERStatus | None):
        if status is None:
            return None
        return None


@dataclass(frozen=True)
class SiteDER(JSONWizard):

    site_der_id: int
    site_id: int
    created_time: datetime
    changed_time: datetime
    site_der_rating: SiteDERRating | None
    site_der_setting: SiteDERSetting | None
    site_der_availability: SiteDERAvailability | None
    site_der_status: SiteDERStatus | None

    @classmethod
    def from_site_der(cls, site_der: EnvoySiteDER):
        return cls(
            site_der_id=site_der.site_der_id,
            site_id=site_der.site_id,
            created_time=site_der.created_time,
            changed_time=site_der.changed_time,
            site_der_rating=SiteDERRating.from_site_der_rating(site_der.site_der_rating),
            site_der_setting=SiteDERSetting.from_site_der_setting(site_der.site_der_setting),
            site_der_availability=SiteDERAvailability.from_site_der_availability(site_der.site_der_availability),
            site_der_status=SiteDERStatus.from_site_der_status(site_der.site_der_status),
        )


@dataclass(frozen=True)
class Site(JSONWizard):

    site_id: int
    nmi: str | None
    aggregator_id: int
    timezone_id: str
    created_time: datetime
    changed_time: datetime
    lfdi: str
    sfdi: int
    device_category: DeviceCategory
    registration_pin: int
    post_rate_seconds: int | None
    site_ders: list[SiteDER]

    @classmethod
    def from_site(cls, site: EnvoySite):
        return cls(
            site_id=site.site_id,
            nmi=site.nmi,
            aggregator_id=site.aggregator_id,
            timezone_id=site.timezone_id,
            created_time=site.created_time,
            changed_time=site.changed_time,
            lfdi=site.lfdi,
            sfdi=site.sfdi,
            device_category=site.device_category,
            registration_pin=site.registration_pin,
            post_rate_seconds=site.post_rate_seconds,
            site_ders=[SiteDER.from_site_der(site_der) for site_der in site.site_ders],
        )


@dataclass
class ReportingData:

    @staticmethod
    def v(version: int):
        if version == 1:
            return ReportingData_v1
        raise ValueError(f"Unknown version of ReportingData ({version}).")

    @staticmethod
    def from_json(version, string, **kwargs) -> Any:
        return ReportingData.v(version).from_json(string, **kwargs)


@dataclass(kw_only=True)
class ReportingData_Base(JSONWizard):
    version: int = field(init=False)

    def _classname_to_version(self) -> int:
        CLASS_NAME_PREFIX = "ReportingData_v"
        return int(self.__class__.__name__.split(CLASS_NAME_PREFIX)[1])

    def __post_init__(self):
        # Automatically determine the version from the classname, ReportingData_vXXX
        self.version = self._classname_to_version()


@dataclass(kw_only=True)
class ReportingData_v1(ReportingData_Base):
    """Holds all the data required to generate cactus run report.

    This class is serializable with a `to_json()` call. e.g.

    ```py
    json_str = reportingdata_instance.to_json()
    ```

    To deserialize, use the general method `ReportingData.from_json` e.g.

    ```py
    from cactus_runner.models import ReportingData
    version = 1
    reportingdata_v1_instance = ReportingData.from_json(version, json_str)
    ```

    Versioning - a version attribute (int) is automatically added to all subclasses
    of ReportingDataBase. The version is extracted directly from the classname.
    For example, ReportingData_v1 has version=1 and ReportingData_v27 has version=27

    ```py
    >>> reportingdata_v1_instance.version
    1
    ```
    """

    # The extensive use of type: ignore is required due to a suspected
    # bug in mypy where it incorrectly thinks the version attribute in
    # the parent class ReportingData_Base is being assigned a default value
    # when it is just being set to init=False. This conflicts with the
    # attributes below which don't set any defaults
    created_at: datetime  # type: ignore
    runner_state: RunnerState  # type: ignore
    check_results: dict[str, CheckResult]  # type: ignore
    readings: list[PackedReadings]  # type: ignore
    sites: list[Site]  # type: ignore
    timeline: Timeline | None  # type: ignore

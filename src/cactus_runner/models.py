import http
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, StrEnum, auto
from typing import Any

from cactus_test_definitions import (
    CSIPAusVersion,
    Event,
    TestProcedure,
)
from dataclass_wizard import JSONWizard


@dataclass
class Listener:
    step: str
    event: Event
    actions: list[Any]
    enabled_time: datetime | None = None  # Set to the TZ aware datetime when this Listener was enabled. None = disabled


class StepStatus(Enum):
    PENDING = 0  # The step is not yet active
    ACTIVE = auto()  # The step is currently active but not complete
    RESOLVED = auto()  # The step has been full resolved


class ClientCertificateType(StrEnum):
    AGGREGATOR = "Aggregator"
    DEVICE = "Device"


@dataclass
class ActiveTestProcedure:
    name: str
    definition: TestProcedure
    csip_aus_version: CSIPAusVersion  # What CSIP aus version did is this run communicating with?
    initialised_at: datetime  # When did the test initialise - timezone aware
    started_at: datetime | None  # When did the test start (None if it hasn't started yet) - timezone aware
    listeners: list[Listener]
    step_status: dict[str, StepStatus]
    client_certificate_type: ClientCertificateType  # Human readable text to identify source of cert.
    client_aggregator_id: int  # What aggregator ID will be the client operating as? (0 for device certs)
    client_lfdi: str  # The LFDI of the client certificate expected for the test (Either aggregator or device client)
    client_sfdi: int  # The SFDI of the client certificate expected for the test (Either aggregator or device client)
    run_id: str | None  # Metadata about what "id" has been assigned to this test (from external) - if any
    pen: int  # Private Enterprise Number (PEN). A value of 0 means no valid PEN avaiable.
    communications_disabled: bool = False
    finished_zip_data: bytes | None = (
        None  # Finalised ZIP file. If not None - this test is "done" and shouldn't update any events/state
    )

    def is_finished(self) -> bool:
        """True if the active test procedure has been marked as finished. That is, there is no more test data to
        accumulate and any client events should be ignored"""
        return self.finished_zip_data is not None

    def is_started(self) -> bool:
        """True if any listener has been enabled"""
        return any([True for listener in self.listeners if listener.enabled_time is not None])


@dataclass
class RequestEntry(JSONWizard):
    url: str
    path: str
    method: http.HTTPMethod
    status: http.HTTPStatus
    timestamp: datetime
    step_name: str
    body_xml_errors: list[str]  # Any XML schema errors detected in the incoming body


class ClientInteractionType(StrEnum):
    RUNNER_START = "Runner Started"
    TEST_PROCEDURE_INIT = "Test Procedure Initialised"
    TEST_PROCEDURE_START = "Test Procedure Started"
    PROXIED_REQUEST = "Request Proxied"


@dataclass
class ClientInteraction(JSONWizard):
    interaction_type: ClientInteractionType
    timestamp: datetime


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
class InitialisedCertificates:
    """Certificates shared with the runner during initialisation. These certs should be the ONLY certificates that can
    interact with the runner/underlying envoy instance"""

    client_certificate_type: str | None = None  # Will read as either "aggregator" or "device"
    client_certificate: str | None = None
    client_lfdi: str | None = None


@dataclass
class InitResponseBody(JSONWizard):
    status: str
    test_procedure: str
    timestamp: datetime
    is_started: bool = (
        False  # True if the run has progressed to the started state. False if it's still waiting for a call to start it
    )


@dataclass
class StartResponseBody(JSONWizard):
    status: str
    test_procedure: str
    timestamp: datetime


@dataclass
class CriteriaEntry(JSONWizard):
    success: bool
    type: str
    details: str


@dataclass
class PreconditionCheckEntry(JSONWizard):
    success: bool
    type: str
    details: str


@dataclass
class DataStreamPoint(JSONWizard):
    watts: int | None  # The data point value (in watts)
    offset: str  # Label for identifying the relative start - usually something like "2m20s"


@dataclass
class TimelineDataStreamEntry(JSONWizard):
    label: str  # Descriptive label of this data stream
    data: list[DataStreamPoint]
    stepped: bool  # If True - this data should be presented as a stepped line chart
    dashed: bool  # If True - this data should be a dashed line


@dataclass
class TimelineStatus(JSONWizard):
    data_streams: list[TimelineDataStreamEntry]  # The set of data streams that should be rendered on the timeline
    set_max_w: int | None  # The currently set set_max_w (if any)
    now_offset: str  # The name of the DataStreamPoint.offset that corresponds with "now" (when this was calculated)


@dataclass
class RunnerStatus(JSONWizard):
    timestamp_status: datetime  # when was this status generated?
    timestamp_initialise: datetime | None  # When did the test initialise
    timestamp_start: datetime | None  # When did the test start
    status_summary: str
    last_client_interaction: ClientInteraction
    csip_aus_version: str  # The CSIPAus version that is registered in the active test procedure (can be empty)
    log_envoy: str  # Snapshot of the current envoy logs
    criteria: list[CriteriaEntry] = field(default_factory=list)
    precondition_checks: list[PreconditionCheckEntry] = field(default_factory=list)
    instructions: list[str] | None = field(default=None)
    test_procedure_name: str = field(default="-")  # '-' represents no active procedure
    step_status: dict[str, StepStatus] | None = field(default=None)
    request_history: list[RequestEntry] = field(default_factory=list)
    set_max_w: int | None = None  # Snapshot of the current MaxW DERSetting value of the active end device (if any)
    timeline: TimelineStatus | None = None  # Streaming timeline data snapshot

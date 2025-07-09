import http
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, StrEnum, auto
from typing import Any

from cactus_test_definitions import (
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
    PENDING = 0
    RESOLVED = auto()


@dataclass
class ActiveTestProcedure:
    name: str
    definition: TestProcedure
    listeners: list[Listener]
    step_status: dict[str, StepStatus]
    client_lfdi: str  # The LFDI of the client certificate expected for the test
    client_sfdi: int  # The SFDI of the client certificate expected for the test
    communications_disabled: bool = False
    finished_zip_data: bytes | None = (
        None  # Finalised ZIP file. If not None - this test is "done" and shouldn't update any events/state
    )

    def is_finished(self) -> bool:
        """True if the active test procedure has been marked as finished. That is, there is no more test data to
        accumulate and any client events should be ignored"""
        return self.finished_zip_data is not None


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
    last_client_interaction: ClientInteraction = field(
        default_factory=lambda: ClientInteraction(
            interaction_type=ClientInteractionType.RUNNER_START, timestamp=datetime.now(timezone.utc)
        )
    )


@dataclass
class Aggregator:
    certificate: str | None = None
    lfdi: str | None = None


@dataclass
class InitResponseBody(JSONWizard):
    status: str
    test_procedure: str
    timestamp: datetime


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
class RunnerStatus(JSONWizard):
    timestamp: datetime  # when was this status generated?
    status_summary: str
    last_client_interaction: ClientInteraction
    log_envoy: str  # Snapshot of the current envoy logs
    log_cactus_runner: str  # Snapshot of the current cactus-runner logs
    criteria: list[CriteriaEntry] = field(default_factory=list)
    test_procedure_name: str = field(default="-")  # '-' represents no active procedure
    step_status: dict[str, StepStatus] | None = field(default=None)
    request_history: list[RequestEntry] = field(default_factory=list)

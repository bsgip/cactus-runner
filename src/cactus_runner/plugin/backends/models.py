from dataclasses import dataclass
from datetime import datetime

from cactus_test_definitions import CSIPAusVersion
from cactus_test_definitions.client import TestProcedure

from cactus_runner.models import ClientCertificateType


@dataclass(frozen=True)
class RunnerBackendTestContext:
    name: str
    definition: TestProcedure
    csip_aus_version: CSIPAusVersion  # What CSIP aus version did is this run communicating with?
    initialised_at: datetime  # When did the test initialise - timezone aware
    started_at: datetime | None  # When did the test start (None if it hasn't started yet) - timezone aware
    client_certificate_type: ClientCertificateType  # Human readable text to identify source of cert.
    client_aggregator_id: str  # What aggregator ID will be the client operating as? (0 for device certs)
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

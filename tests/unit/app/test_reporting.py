from datetime import datetime, timedelta, timezone
from decimal import Decimal
import http
import random
import pandas as pd
import pytest
from assertical.fake.generator import generate_class_instance
from cactus_test_definitions.client import TestProcedureConfig
from envoy.server.model import (
    Site,
    SiteDER,
    SiteDERSetting,
    SiteReadingType,
    SiteDERRating,
    SiteDERAvailability,
    SiteDERStatus,
    RoleFlagsType,
    KindType,
    DataQualifierType,
    UomType,
)
from envoy_schema.server.schema.sep2.types import DeviceCategory

from cactus_runner.app.check import CheckResult
from cactus_runner.app.envoy_common import ReadingLocation
from cactus_runner.app.reporting import (
    device_category_to_string,
    pdf_report_as_bytes,
)
from cactus_runner.app.timeline import Timeline, TimelineDataStream
from cactus_runner.models import (
    ActiveTestProcedure,
    ClientInteraction,
    ClientInteractionType,
    RequestEntry,
    RunnerState,
    StepStatus,
)


@pytest.mark.parametrize("no_spacers", [True, False])
def test_pdf_report_as_bytes(no_spacers):
    # Arrange
    definitions = TestProcedureConfig.from_resource()
    test_name = "ALL-01"
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        name=test_name,
        definition=definitions.test_procedures[test_name],
        step_status={"1": StepStatus.PENDING},
        finished_zip_data=None,
        run_id=1056,
    )
    NUM_REQUESTS = 3
    runner_state = RunnerState(
        active_test_procedure=active_test_procedure,
        request_history=[generate_class_instance(RequestEntry) for _ in range(NUM_REQUESTS)],
    )

    NUM_CHECK_RESULTS = 3
    check_results = {f"check{i}": generate_class_instance(CheckResult) for i in range(NUM_CHECK_RESULTS)}

    NUM_READING_TYPES = 3
    sample_readings = pd.DataFrame(
        {
            "scaled_value": [Decimal(1.0)],
            "time_period_start": [datetime.now(timezone.utc)],
        }
    )
    readings = {generate_class_instance(SiteReadingType): sample_readings for _ in range(NUM_READING_TYPES)}

    reading_counts = {generate_class_instance(SiteReadingType): i for i in range(NUM_READING_TYPES)}

    NUM_SITES = 2
    sites = [generate_class_instance(Site) for _ in range(NUM_SITES)]

    timeline = generate_class_instance(Timeline, generate_relationships=True)

    # Act
    report_bytes = pdf_report_as_bytes(
        runner_state=runner_state,
        check_results=check_results,
        readings=readings,
        reading_counts=reading_counts,
        sites=sites,
        timeline=timeline,
        no_spacers=no_spacers,
    )

    # Assert - we are mainly checking that no uncaught exceptions are raised generating the pdf report
    assert len(report_bytes) > 0


def test_pdf_report_as_bytes_does_raise_exception_for_large_amount_of_validation_errors():
    # Arrange
    definitions = TestProcedureConfig.from_resource()
    test_name = "ALL-01"
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        name=test_name,
        definition=definitions.test_procedures[test_name],
        step_status={"1": StepStatus.PENDING},
        finished_zip_data=None,
        run_id=None,
    )

    # Check a request with many validation errors doesn't break the pdf generation
    body_xml_errors = [
        (
            "lorem ipsum lorem ipsum lorem ipsum lorem ipsum lorem ipsum lorem ipsum lorem ipsum\n"
            "lorem ipsum lorem ipsum lorem ipsum lorem ipsum lorem ipsum lorem ipsum lorem ipsum\n"
            "lorem ipsum lorem ipsum lorem ipsum lorem ipsum lorem ipsum lorem ipsum lorem ipsum\n"
        )
    ] * 20
    runner_state = RunnerState(
        active_test_procedure=active_test_procedure,
        request_history=[generate_class_instance(RequestEntry, body_xml_errors=body_xml_errors)],
    )
    NUM_CHECK_RESULTS = 3
    check_results = {f"check{i}": generate_class_instance(CheckResult) for i in range(NUM_CHECK_RESULTS)}

    # Act
    pdf_report_as_bytes(
        runner_state=runner_state,
        check_results=check_results,
        readings={},
        reading_counts={},
        sites=[],
        timeline=None,
    )

    # There should be not exceptions raises do to Flowables being too large


@pytest.mark.parametrize(
    "device_category,expected",
    [
        (DeviceCategory(0), "Unspecified device category (0)"),
        (DeviceCategory.SMART_ENERGY_MODULE, "smart energy module"),
        (DeviceCategory.SMART_ENERGY_MODULE | DeviceCategory.WATER_HEATER, "water heater | smart energy module"),
    ],
)
def test_device_category_to_string(device_category: DeviceCategory, expected: str):
    assert device_category_to_string(device_category=device_category) == expected


@pytest.mark.parametrize("has_set_max_w", [True, False])
def test_pdf_report_as_bytes_with_timeline(has_set_max_w):
    # Arrange
    definitions = TestProcedureConfig.from_resource()
    test_name = "ALL-01"
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        name=test_name,
        definition=definitions.test_procedures[test_name],
        step_status={"1": StepStatus.PENDING},
        finished_zip_data=None,
        run_id=None,
    )
    runner_state = RunnerState(
        active_test_procedure=active_test_procedure,
        request_history=[],
    )

    check_results = {}
    readings = {}
    reading_counts = {}
    site_ders = (
        []
        if not has_set_max_w
        else [
            generate_class_instance(
                SiteDER,
                site_der_setting=generate_class_instance(SiteDERSetting, max_w_value=6, max_w_multiplier=3),
            )
        ]
    )
    sites = [
        generate_class_instance(
            Site,
            site_ders=site_ders,
        )
    ]

    timeline = Timeline(
        datetime(2022, 11, 5, tzinfo=timezone.utc),
        20,
        [
            TimelineDataStream(
                "/derp/1 opModExpLimW", [None, None, None, -3000, -3000, -3000, -2000, -2000], True, False
            ),
            TimelineDataStream("/derp/1 opModImpLimW", [5000, 5000, 5000, None, None, 5000, 5000, None], True, False),
            TimelineDataStream("Site Watts", [1000, 1150, 800, 600, 500, -500, -1000, -2000], False, False),
            TimelineDataStream("Device Watts", [None, None, -1000, -3000, -2250, -100, 0, 0], False, False),
            TimelineDataStream("Default opModImpLimW", [None, None, 1000, 1000, 1500, 1500, 0, 0], True, True),
            TimelineDataStream("Default opModExpLimW", [-5000, -5000, 0, 0, -1000, -1000, -1000], True, True),
        ],
    )

    # Act
    report_bytes = pdf_report_as_bytes(
        runner_state=runner_state,
        check_results=check_results,
        readings=readings,
        reading_counts=reading_counts,
        sites=sites,
        timeline=timeline,
    )

    # Assert - we are mainly checking that no uncaught exceptions are raised generating the pdf report
    assert len(report_bytes) > 0


def test_pdf_report_with_witness_test():
    """Set test definition class to one of those which require witness testing"""

    # Arrange
    definitions = TestProcedureConfig.from_resource()
    test_name = "ALL-01"

    definition = definitions.test_procedures[test_name]
    definition.classes = ["DER-A"]  # Ensures that the tests require witness testing

    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        name=test_name,
        definition=definition,
        step_status={"1": StepStatus.PENDING},
        finished_zip_data=None,
        run_id=None,
    )
    NUM_REQUESTS = 3

    now = datetime.now(timezone.utc)
    client_interactions = [
        generate_class_instance(
            ClientInteraction,
            interaction_type=ClientInteractionType.TEST_PROCEDURE_INIT,
            timestamp=now,
        ),
        generate_class_instance(
            ClientInteraction,
            interaction_type=ClientInteractionType.TEST_PROCEDURE_START,
            timestamp=now + timedelta(seconds=5),
        ),
    ]

    runner_state = RunnerState(
        active_test_procedure=active_test_procedure,
        request_history=[generate_class_instance(RequestEntry) for _ in range(NUM_REQUESTS)],
        client_interactions=client_interactions,
    )

    NUM_CHECK_RESULTS = 3
    check_results = {f"check{i}": generate_class_instance(CheckResult, passed=True) for i in range(NUM_CHECK_RESULTS)}

    NUM_READING_TYPES = 3
    sample_readings = pd.DataFrame({"scaled_value": [Decimal(1.0)], "time_period_start": [datetime.now(timezone.utc)]})
    readings = {generate_class_instance(SiteReadingType): sample_readings for _ in range(NUM_READING_TYPES)}

    reading_counts = {generate_class_instance(SiteReadingType): i for i in range(NUM_READING_TYPES)}

    NUM_SITES = 2
    sites = [generate_class_instance(Site) for _ in range(NUM_SITES)]

    # Act
    report_bytes = pdf_report_as_bytes(
        runner_state=runner_state,
        check_results=check_results,
        readings=readings,
        reading_counts=reading_counts,
        sites=sites,
        timeline=None,
        no_spacers=False,
    )

    # Assert - we are mainly checking that no uncaught exceptions are raised generating the pdf report
    assert len(report_bytes) > 0


def test_pdf_report_unset_params():

    # Arrange
    definitions = TestProcedureConfig.from_resource()
    test_name = "ALL-01"
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        name=test_name,
        definition=definitions.test_procedures[test_name],
        step_status={"1": StepStatus.PENDING},
        finished_zip_data=None,
        run_id=None,
    )
    runner_state = RunnerState(
        active_test_procedure=active_test_procedure,
        request_history=[],
    )

    check_results = {}
    readings = {}
    reading_counts = {}
    site_ders = [
        generate_class_instance(
            SiteDER,
            site_der_setting=generate_class_instance(
                SiteDERSetting, max_w_value=6, max_w_multiplier=3, optional_is_none=True
            ),
            site_der_rating=generate_class_instance(SiteDERRating, optional_is_none=True),
            site_der_availability=generate_class_instance(SiteDERAvailability, optional_is_none=True),
            site_der_status=generate_class_instance(SiteDERStatus, optional_is_none=True),
        )
    ]

    sites = [generate_class_instance(Site, site_ders=site_ders)]

    # Act
    report_bytes = pdf_report_as_bytes(
        runner_state=runner_state,
        check_results=check_results,
        readings=readings,
        reading_counts=reading_counts,
        sites=sites,
        timeline=None,
    )

    # Assert - we are mainly checking that no uncaught exceptions are raised generating the pdf report
    assert len(report_bytes) > 0


def test_pdf_report_char_overflow():

    # Make a long char generator
    words = """nuclear reactor burped electrons confused technician googled how turbine caffeine engineer duct taped
     solar panel batteries achieved sentience demanding snacks retired grid operator stress eating donuts""".split()

    long_description = " ".join(random.choices(words, k=80))

    # Arrange
    definitions = TestProcedureConfig.from_resource()
    test_name = "ALL-01"

    definition = definitions.test_procedures[test_name]
    definition.classes = ["DER-A"]  # Ensures that the tests require witness testing

    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        name=test_name,
        definition=definition,
        step_status={"1": StepStatus.PENDING},
        finished_zip_data=None,
        run_id=None,
    )
    NUM_REQUESTS = 3

    now = datetime.now(timezone.utc)
    client_interactions = [
        generate_class_instance(
            ClientInteraction,
            interaction_type=ClientInteractionType.TEST_PROCEDURE_INIT,
            timestamp=now,
        ),
        generate_class_instance(
            ClientInteraction,
            interaction_type=ClientInteractionType.TEST_PROCEDURE_START,
            timestamp=now + timedelta(seconds=5),
        ),
    ]

    runner_state = RunnerState(
        active_test_procedure=active_test_procedure,
        request_history=[
            generate_class_instance(RequestEntry, body_xml_errors=long_description) for _ in range(NUM_REQUESTS)
        ],
        client_interactions=client_interactions,
    )

    NUM_CHECK_RESULTS = 3
    check_results = {
        f"check{i}": generate_class_instance(CheckResult, description=long_description, passed=False)
        for i in range(NUM_CHECK_RESULTS)
    }

    NUM_READING_TYPES = 3
    sample_readings = pd.DataFrame({"scaled_value": [Decimal(1.0)], "time_period_start": [datetime.now(timezone.utc)]})
    readings = {generate_class_instance(SiteReadingType): sample_readings for _ in range(NUM_READING_TYPES)}

    reading_counts = {generate_class_instance(SiteReadingType): i for i in range(NUM_READING_TYPES)}

    NUM_SITES = 2
    sites = [generate_class_instance(Site) for _ in range(NUM_SITES)]

    # Act
    report_bytes = pdf_report_as_bytes(
        runner_state=runner_state,
        check_results=check_results,
        readings=readings,
        reading_counts=reading_counts,
        sites=sites,
        timeline=None,
        no_spacers=False,
    )

    # Assert - we are mainly checking that no uncaught exceptions are raised generating the pdf report
    assert len(report_bytes) > 0


def test_pdf_report_everything_set():
    """Comprehensive PDF report example for ALL-01"""
    # Arrange
    definitions = TestProcedureConfig.from_resource()
    test_name = "ALL-01"

    definition = definitions.test_procedures[test_name]
    definition.classes = ["DER-A", "DER-B"]  # Include witness testing requirements

    now = datetime.now(timezone.utc)

    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        name=test_name,
        definition=definition,
        step_status={"1": StepStatus.RESOLVED},
        finished_zip_data=None,
        run_id="80085",
        initialised_at=now - timedelta(seconds=350),
        started_at=now - timedelta(seconds=330),
    )

    # Create comprehensive request history matching the test flow
    request_history = [
        # Init phase
        generate_class_instance(
            RequestEntry,
            url="https://example.com/dcap",
            path="/dcap",
            method=http.HTTPMethod.GET,
            status=http.HTTPStatus.OK,
            timestamp=now - timedelta(seconds=320),
            step_name="Init",
            body_xml_errors=[],
        ),
        # Unmatched requests
        generate_class_instance(
            RequestEntry,
            url="https://example.com/edev",
            path="/edev",
            method=http.HTTPMethod.GET,
            status=http.HTTPStatus.OK,
            timestamp=now - timedelta(seconds=310),
            step_name="Unmatched",
            body_xml_errors=[],
        ),
        generate_class_instance(
            RequestEntry,
            url="https://example.com/tm",
            path="/tm",
            method=http.HTTPMethod.GET,
            status=http.HTTPStatus.OK,
            timestamp=now - timedelta(seconds=300),
            step_name="Unmatched",
            body_xml_errors=[],
        ),
        # GET-DCAP step
        generate_class_instance(
            RequestEntry,
            url="https://example.com/dcap",
            path="/dcap",
            method=http.HTTPMethod.GET,
            status=http.HTTPStatus.OK,
            timestamp=now - timedelta(seconds=280),
            step_name="GET-DCAP",
            body_xml_errors=[],
        ),
        # GET-EDEV-LIST step
        generate_class_instance(
            RequestEntry,
            url="https://example.com/edev",
            path="/edev",
            method=http.HTTPMethod.GET,
            status=http.HTTPStatus.OK,
            timestamp=now - timedelta(seconds=260),
            step_name="GET-EDEV-LIST",
            body_xml_errors=[],
        ),
        # GET-TM step
        generate_class_instance(
            RequestEntry,
            url="https://example.com/tm",
            path="/tm",
            method=http.HTTPMethod.GET,
            status=http.HTTPStatus.OK,
            timestamp=now - timedelta(seconds=240),
            step_name="GET-TM",
            body_xml_errors=[],
        ),
        # GET-DER step
        generate_class_instance(
            RequestEntry,
            url="https://example.com/edev/1/der",
            path="/edev/1/der",
            method=http.HTTPMethod.GET,
            status=http.HTTPStatus.OK,
            timestamp=now - timedelta(seconds=220),
            step_name="GET-DER",
            body_xml_errors=[],
        ),
    ]

    # Create comprehensive client interactions
    client_interactions = [
        generate_class_instance(
            ClientInteraction,
            interaction_type=ClientInteractionType.RUNNER_START,
            timestamp=now - timedelta(seconds=400),
        ),
        generate_class_instance(
            ClientInteraction,
            interaction_type=ClientInteractionType.TEST_PROCEDURE_INIT,
            timestamp=now - timedelta(seconds=350),
        ),
        generate_class_instance(
            ClientInteraction,
            interaction_type=ClientInteractionType.TEST_PROCEDURE_START,
            timestamp=now - timedelta(seconds=330),
        ),
    ]

    runner_state = RunnerState(
        active_test_procedure=active_test_procedure,
        request_history=request_history,
        client_interactions=client_interactions,
    )

    # Create comprehensive check results with varied outcomes
    check_results = {
        "check0": generate_class_instance(
            CheckResult, passed=True, description="Initial connection established successfully"
        ),
        "check1": generate_class_instance(CheckResult, passed=True, description="Device registration completed"),
        "check2": generate_class_instance(
            CheckResult, passed=False, description="Response time exceeded threshold (expected <500ms, got 750ms)"
        ),
        "check3": generate_class_instance(CheckResult, passed=True, description="DER control parameters validated"),
        "check4": generate_class_instance(CheckResult, passed=True, description=None),
    }

    # Create comprehensive readings with multiple data points
    NUM_READING_TYPES = 3
    sample_readings = pd.DataFrame(
        {
            "scaled_value": [Decimal(1.0), Decimal(1.5), Decimal(2.0), Decimal(1.8), Decimal(1.2)],
            "time_period_start": [now - timedelta(minutes=i * 2) for i in range(5)],
        }
    )
    readings = {generate_class_instance(SiteReadingType): sample_readings for _ in range(NUM_READING_TYPES)}

    # Must match NUM_READING_TYPES
    reading_counts = {
        generate_class_instance(
            SiteReadingType,
            mrid="longmridfortest",
            uom=UomType.REAL_POWER_WATT,
            kind=KindType.POWER,
            role_flags=ReadingLocation.SITE_READING,
        ): 10,
        generate_class_instance(
            SiteReadingType,
            uom=UomType.FREQUENCY_HZ,
            data_qualifier=DataQualifierType.AVERAGE,
            kind=KindType.POWER,
            role_flags=ReadingLocation.DEVICE_READING,
        ): 20,
        generate_class_instance(
            SiteReadingType,
            uom=UomType.VOLTS_SQUARED,
            kind=KindType.CURRENCY,
            data_qualifier=DataQualifierType.AVERAGE,
            role_flags=RoleFlagsType.IS_PEV,
        ): 30,
    }

    # Create comprehensive sites with full DER information
    NUM_SITES = 1
    sites = []
    for i in range(NUM_SITES):
        site_ders = [
            generate_class_instance(
                SiteDER,
                site_der_setting=generate_class_instance(
                    SiteDERSetting,
                    max_w_value=6000,
                    max_w_multiplier=3,
                ),
                site_der_rating=generate_class_instance(SiteDERRating),
                site_der_availability=generate_class_instance(SiteDERAvailability),
                site_der_status=generate_class_instance(SiteDERStatus),
            )
        ]
        sites.append(
            generate_class_instance(
                Site,
                site_ders=site_ders,
                nmi="NMI0000001",
                timezone_id="Australia/Sydney",
                device_category=DeviceCategory.SMART_ENERGY_MODULE,
            )
        )

    # Create comprehensive timeline with varied data streams
    timeline = Timeline(
        datetime(2022, 11, 5, tzinfo=timezone.utc),
        20,
        [
            TimelineDataStream(
                "/derp/1 opModExpLimW", [None, None, None, -3000, -3000, -3000, -2000, -2000, -1500, -1000], True, False
            ),
            TimelineDataStream(
                "/derp/1 opModImpLimW", [5000, 5000, 5000, None, None, 5000, 5000, None, 4500, 4000], True, False
            ),
            TimelineDataStream(
                "Site Watts", [1000, 1150, 800, 600, 500, -500, -1000, -2000, -1500, -1200], False, False
            ),
            TimelineDataStream("Device Watts", [None, None, -1000, -3000, -2250, -100, 0, 0, 500, 1000], False, False),
            TimelineDataStream(
                "Default opModImpLimW", [None, None, 1000, 1000, 1500, 1500, 0, 0, 2000, 2500], True, True
            ),
            TimelineDataStream(
                "Default opModExpLimW", [-5000, -5000, 0, 0, -1000, -1000, -1000, -2000, -2500, -3000], True, True
            ),
        ],
    )

    # Act
    report_bytes = pdf_report_as_bytes(
        runner_state=runner_state,
        check_results=check_results,
        readings=readings,
        reading_counts=reading_counts,
        sites=sites,
        timeline=timeline,
        no_spacers=False,
    )

    # Assert - we are mainly checking that no uncaught exceptions are raised generating the pdf report
    assert len(report_bytes) > 0

    # Optional: Save and open the PDF for visual inspection
    # import uuid
    # import tempfile
    # import os
    # import subprocess

    # with tempfile.NamedTemporaryFile(
    #     suffix=".pdf", prefix=f"report_{uuid.uuid4().hex[:8]}_", delete=False
    # ) as temp_file:
    #     temp_file.write(report_bytes)
    #     temp_file.flush()
    #     print(f"Saved comprehensive PDF report: {os.path.basename(temp_file.name)}")
    #     subprocess.run(["xdg-open", temp_file.name])
